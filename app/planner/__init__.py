"""Planner: question → validated QueryPlan, with at most one repair call."""

from dataclasses import dataclass, field
from typing import Literal

from app.contracts.plan import Clarification, QueryPlan
from app.contracts.request import VisualizationRequest
from app.planner.gateway import Message, PlannerError, PlannerGateway
from app.planner.prompt import repair_message, system_prompt, user_message
from app.planner.validate import apply_request_fields, validate_plan

__all__ = ["PlanOutcome", "Planner", "PlannerError"]


@dataclass
class PlanOutcome:
    status: Literal["accepted", "clarification", "unsupported", "invalid"]
    plan: QueryPlan | None
    attempts: int
    clarification: Clarification | None = None
    unsupported_reason: str | None = None
    errors: list[str] = field(default_factory=list)
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

    async def plan(self, request: VisualizationRequest) -> PlanOutcome:
        messages: list[Message] = [{"role": "user", "content": user_message(request)}]
        outcome = PlanOutcome(status="invalid", plan=None, attempts=0)
        for attempt in range(1, self.max_attempts + 1):
            proposal = await self.gateway.propose(self.instructions, messages)
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
            plan, conflict = apply_request_fields(plan, request)
            if conflict:
                outcome.status, outcome.clarification = "clarification", conflict
                return outcome

            errors = validate_plan(plan)
            outcome.plan, outcome.errors = plan, errors
            if not errors:
                outcome.status = "accepted"
                return outcome
            messages += [
                {"role": "assistant", "content": proposal.plan.model_dump_json()},
                {"role": "user", "content": repair_message(errors)},
            ]
        return outcome
