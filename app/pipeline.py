"""The request pipeline: plan → retrieve → analyze → build → verify → persist.

Stages are plain async steps with named timings (DESIGN §3). Exactly one stage, `plan`, uses
the model; every later stage is deterministic and receives only the validated plan.
"""

import asyncio
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from app.analytics import run_analysis
from app.analytics.prepare import cohort_overlap, prepare_cohort
from app.analytics.types import NetworkResult
from app.contracts.enums import OperationKind, Status
from app.contracts.plan import Clarification, QueryPlan
from app.contracts.request import VisualizationRequest
from app.contracts.response import (
    CohortMeta,
    ErrorInfo,
    Interpretation,
    Meta,
    NetworkGraphSpec,
    SourceInfo,
    VisualizationResponse,
)
from app.ctgov.client import CTGovClient, SearchResult, TooBroadError, UpstreamError
from app.ctgov.compile import compile_cohort
from app.planner import Planner, PlannerError
from app.registry import API_FIELDS
from app.storage import RunStore
from app.viz.build import assumptions, build_spec, policies, summarize
from app.viz.verify import verify

log = logging.getLogger(__name__)

FREE_TEXT_FILTERS = ("drug_name", "condition", "sponsor", "country")


class Pipeline:
    def __init__(
        self,
        planner: Planner | None,
        ctgov: CTGovClient,
        store: RunStore | None,
        max_trials_per_cohort: int,
    ) -> None:
        self.planner = planner
        self.ctgov = ctgov
        self.store = store
        self.max_trials = max_trials_per_cohort

    async def run(self, request: VisualizationRequest) -> VisualizationResponse:
        run = _Run(str(uuid4()))
        response = await self._run(request, run)
        if self.store is not None:
            self.store.save(response)
        log.info(
            "run %s status=%s timings=%s tokens=%s",
            response.run_id,
            response.status.value,
            response.meta.timings_ms,
            run.tokens,
        )
        return response

    async def _run(self, request: VisualizationRequest, run: "_Run") -> VisualizationResponse:
        meta = run.meta
        if self.planner is None:
            return run.fail("planner_not_configured", "OPENAI_API_KEY is not set.")

        # 1. plan (the only model call; at most one repair)
        with run.stage("plan"):
            try:
                outcome = await self.planner.plan(request)
            except PlannerError as exc:
                return run.fail("planner_unavailable", str(exc))
        run.tokens = {"input": outcome.input_tokens, "output": outcome.output_tokens}
        if outcome.plan is not None:
            meta.interpretation = Interpretation(
                summary=summarize(outcome.plan),
                plan=outcome.plan,
                planner_model=self.planner.model,
                planner_attempts=outcome.attempts,
            )
        if outcome.status == "clarification":
            return run.done(Status.NEEDS_CLARIFICATION, clarification=outcome.clarification)
        if outcome.status == "unsupported":
            return run.done(
                Status.UNSUPPORTED,
                error=ErrorInfo(
                    code="unsupported_question",
                    message=outcome.unsupported_reason or "Unsupported question.",
                    details=SUPPORTED_ALTERNATIVES,
                ),
            )
        if outcome.status == "invalid" or outcome.plan is None:
            return run.done(
                Status.UNSUPPORTED,
                error=ErrorInfo(
                    code="plan_invalid",
                    message="The question could not be mapped to a supported analysis.",
                    details=outcome.errors,
                ),
            )
        plan = outcome.plan
        meta.time = plan.time
        meta.policies = policies(plan)
        meta.assumptions = assumptions(plan)

        # 2. retrieve (ground + fetch every cohort)
        with run.stage("retrieve"):
            version = await self.ctgov.version()
            params = [compile_cohort(c.filters, plan.time) for c in plan.cohorts]
            try:
                results: list[SearchResult] = await asyncio.gather(
                    *(
                        self.ctgov.search(p, API_FIELDS, self.max_trials, version.data_timestamp)
                        for p in params
                    )
                )
            except TooBroadError as exc:
                return run.done(Status.NEEDS_CLARIFICATION, clarification=_too_broad(exc))
            except UpstreamError as exc:
                return run.fail("upstream_unavailable", f"ClinicalTrials.gov: {exc}")
        meta.source = SourceInfo(
            api_version=version.api_version,
            data_timestamp=version.data_timestamp,
            retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        unmatched = _zero_hit_cohort(plan, results)
        if unmatched is not None:
            return run.done(Status.NEEDS_CLARIFICATION, clarification=unmatched)

        # 3. analyze (deterministic)
        with run.stage("analyze"):
            cohorts = [
                prepare_cohort(c, r.studies, plan.time)
                for c, r in zip(plan.cohorts, results, strict=True)
            ]
            result = run_analysis(plan, cohorts)
        meta.cohorts = [
            CohortMeta(
                label=ct.cohort.label,
                filters=ct.cohort.filters.model_dump(mode="json", exclude_none=True),
                api_params=r.params,
                total_matches=r.total,
                records_fetched=len(r.studies),
                trials_analyzed=len(ct.trials),
                excluded=ct.excluded,
                missing=result.missing.get(ct.cohort.label, {}),
                complete=r.complete,
            )
            for ct, r in zip(cohorts, results, strict=True)
        ]
        meta.cohort_overlap = cohort_overlap(cohorts)

        # 4. build the spec
        with run.stage("build"):
            built = build_spec(
                plan, result, cohorts, request.citations_per_datum, request.preferred_visualization
            )
        meta.truncation = built.truncation
        meta.sort = built.sort
        meta.time_granularity = "year" if plan.operation.kind is OperationKind.TIME_TREND else None
        meta.assumptions += built.notes

        # 5. verify (output gate)
        with run.stage("verify"):
            trials = {t.nct_id: t for ct in cohorts for t in ct.trials}
            errors = verify(built.spec, trials)
        if errors:
            log.error("run %s failed verification: %s", run.run_id, errors[:5])
            return run.fail("output_verification_failed", "Output failed verification.", errors)

        empty = (
            not built.spec.data.edges
            if isinstance(built.spec, NetworkGraphSpec)
            else all(d.trial_count == 0 for d in built.spec.data)
        )
        if isinstance(result, NetworkResult) and empty:
            meta.assumptions.append("No pair of entities shares a trial in this cohort.")
        return run.done(Status.EMPTY if empty else Status.OK, visualization=built.spec)


SUPPORTED_ALTERNATIVES = [
    "Trial counts by phase, status, study type, sponsor, sponsor category, drug, "
    "intervention type, condition or country",
    "Trials per year (start, first-posted or completion date)",
    "Comparisons of up to 4 drugs, conditions or sponsors",
    "Networks: sponsor ↔ drug, drug ↔ drug co-occurrence, condition ↔ drug, …",
]


def _too_broad(exc: TooBroadError) -> Clarification:
    return Clarification(
        question=f"This matches {exc.total:,} trials, more than the {exc.limit:,} this service "
        "analyses per group. How should it be narrowed?",
        options=[
            "Add a condition or drug",
            "Restrict to a phase or recruitment status",
            "Restrict the years",
        ],
    )


def _zero_hit_cohort(plan: QueryPlan, results: list[SearchResult]) -> Clarification | None:
    """Zero matches for a named drug/condition/sponsor/country is more likely a spelling or
    naming problem than a real zero, so ask instead of drawing an empty chart."""
    for cohort, result in zip(plan.cohorts, results, strict=True):
        named = {k: v for k in FREE_TEXT_FILTERS if (v := getattr(cohort.filters, k)) is not None}
        if result.total == 0 and named:
            terms = ", ".join(f"{k}='{v}'" for k, v in named.items())
            return Clarification(
                question=f"No trials matched cohort '{cohort.label}' ({terms}). Is the name "
                "spelled as it appears on ClinicalTrials.gov, or should a different term be used?",
                options=["Use a different spelling or the generic name", "Remove this filter"],
            )
    return None


class _Run:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.meta = Meta()
        self.tokens: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.meta.timings_ms[name] = round((time.perf_counter() - start) * 1000)

    def done(self, status: Status, **fields: object) -> VisualizationResponse:
        return VisualizationResponse(run_id=self.run_id, status=status, meta=self.meta, **fields)  # type: ignore[arg-type]

    def fail(
        self, code: str, message: str, details: list[str] | None = None
    ) -> VisualizationResponse:
        return self.done(
            Status.FAILED, error=ErrorInfo(code=code, message=message, details=details or [])
        )
