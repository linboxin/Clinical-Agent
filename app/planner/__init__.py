"""Planner: question → validated, grounded QueryPlan.

A bounded plan → act → observe loop: the model proposes a plan; deterministic tools validate it
(schema rules, explicit request fields) and ground it (live hit counts per cohort); any errors
go back to the model once as repair feedback. The model never sees trial records.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from app.contracts.plan import Clarification, QueryPlan
from app.contracts.request import VisualizationRequest
from app.planner.gateway import Message, PlannerError, PlannerGateway
from app.planner.grounding import GroundReport
from app.planner.prompt import repair_message, system_prompt, user_message
from app.planner.validate import apply_request_fields, normalize_plan, validate_plan
from app.telemetry import span

__all__ = ["GroundFn", "PlanOutcome", "Planner", "PlannerError"]

GroundFn = Callable[[QueryPlan], Awaitable[GroundReport]]


@dataclass
class PlanOutcome:
    status: Literal["accepted", "clarification", "unsupported", "invalid", "too_broad"]
    plan: QueryPlan | None
    attempts: int
    clarification: Clarification | None = None
    unsupported_reason: str | None = None
    errors: list[str] = field(default_factory=list)
    repair_feedback: list[str] = field(default_factory=list)
    grounding: GroundReport | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class Planner:
    def __init__(self, gateway: PlannerGateway, max_attempts: int = 2) -> None:
        self.gateway = gateway
        self.max_attempts = max_attempts
        self.instructions = system_prompt()

    @property
    def model(self) -> str:
        return self.gateway.model

    async def plan(
        self,
        request: VisualizationRequest,
        ground: GroundFn | None = None,
        parent_plan: QueryPlan | None = None,
    ) -> PlanOutcome:
        messages: list[Message] = [{"role": "user", "content": user_message(request, parent_plan)}]
        outcome = PlanOutcome(status="invalid", plan=None, attempts=0)
        for attempt in range(1, self.max_attempts + 1):
            with span("llm.propose", model=self.model, attempt=attempt) as s:
                proposal = await self.gateway.propose(self.instructions, messages)
                s.set(input_tokens=proposal.input_tokens, output_tokens=proposal.output_tokens)
            outcome.attempts = attempt
            outcome.input_tokens += proposal.input_tokens or 0
            outcome.output_tokens += proposal.output_tokens or 0
            plan = proposal.plan
            outcome.plan = plan

            if plan.unsupported_reason:
                outcome.status, outcome.unsupported_reason = "unsupported", plan.unsupported_reason
                return outcome
            if plan.clarification:
                outcome.status, outcome.clarification = "clarification", plan.clarification
                return outcome
            if plan.unhandled_constraints:
                # The plan language cannot apply part of the question: say so instead of
                # answering a different question (DESIGN §2, "never drop a constraint").
                parts = "; ".join(plan.unhandled_constraints)
                outcome.status = "clarification"
                outcome.clarification = Clarification(
                    question=f"This service cannot apply part of the question: {parts}. "
                    "Answer without it, or rephrase?",
                    options=[f"Answer without: {parts}", "I will rephrase the question"],
                )
                return outcome
            plan, conflict = apply_request_fields(normalize_plan(plan), request)
            if conflict:
                outcome.status, outcome.clarification = "clarification", conflict
                return outcome

            errors = validate_plan(plan)
            outcome.plan, outcome.errors = plan, errors
            if not errors and ground is not None:
                with span("ground", cohorts=len(plan.cohorts)) as s:
                    report = await ground(plan)
                    s.set(
                        totals=report.totals,
                        unknown_terms=[f"{t.field}={t.value}" for t in report.unknown_terms],
                    )
                outcome.grounding = report
                if report.too_broad:
                    outcome.status = "too_broad"
                    return outcome
                errors = report.repair_errors()
                outcome.errors = errors
            if not errors:
                outcome.status = "accepted"
                return outcome
            outcome.repair_feedback += errors
            messages += [
                {"role": "assistant", "content": proposal.plan.model_dump_json()},
                {"role": "user", "content": repair_message(errors)},
            ]

        final = outcome.grounding
        if final is not None and final.unknown_terms and outcome.errors == final.repair_errors():
            terms = ", ".join(f"{t.field}='{t.value}'" for t in final.unknown_terms)
            outcome.status = "clarification"
            outcome.clarification = Clarification(
                question=f"ClinicalTrials.gov has no trials matching {terms}. Which name should "
                "be used (as registered, e.g. the generic drug name)?",
                options=["Use a different spelling or the generic name", "Remove this filter"],
            )
        return outcome
