"""The request pipeline: plan (+ ground) → retrieve → analyze → build → verify → save.

Stages are plain async steps, each a traced span (DESIGN §3). Exactly one stage, `plan`, uses
the model; every later stage is deterministic and receives only the validated, grounded plan.
"""

import asyncio
import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.analytics import run_analysis
from app.analytics.prepare import cohort_overlap, prepare_cohort
from app.analytics.types import CohortTrials, NetworkResult, ScatterResult
from app.analytics.values import ValueView
from app.contracts.enums import OperationKind, Status
from app.contracts.plan import Clarification, QueryPlan
from app.contracts.request import VisualizationRequest
from app.contracts.response import (
    CohortMeta,
    ErrorInfo,
    Interpretation,
    Meta,
    NameMerge,
    NetworkGraphSpec,
    Normalization,
    SourceInfo,
    VisualizationResponse,
)
from app.ctgov.client import CTGovClient, SearchResult, TooBroadError, UpstreamError
from app.ctgov.compile import compile_cohort
from app.normalize import MAX_NAMES, NORMALIZED_DIMENSIONS, NameNormalizer, report
from app.planner import Planner, PlannerError
from app.planner.grounding import Grounder
from app.registry import API_FIELDS, REGISTRY
from app.storage import RunStore
from app.telemetry import Span, Trace, activate, span
from app.viz.build import BuiltSpec, assumptions, build_spec, policies, relabel, summarize
from app.viz.verify import verify

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        planner: Planner | None,
        ctgov: CTGovClient,
        store: RunStore | None,
        max_trials_per_cohort: int,
        normalizer: NameNormalizer | None = None,
    ) -> None:
        self.planner = planner
        self.normalizer = normalizer
        self.ctgov = ctgov
        self.store = store
        self.max_trials = max_trials_per_cohort

    async def run(self, request: VisualizationRequest) -> VisualizationResponse:
        run = _Run(str(uuid4()))
        with activate(run.trace), run.trace.span("run", query=request.query) as root:
            response = await self._run(request, run)
            root.set(status=response.status.value)
        if self.store is not None:
            self.store.save(request, response, run.trace.to_json(), run.evidence)
        log.info(
            "run %s status=%s timings=%s tokens=%s",
            response.run_id,
            response.status.value,
            response.meta.timings_ms,
            response.meta.llm_usage,
        )
        return response

    async def _run(self, request: VisualizationRequest, run: "_Run") -> VisualizationResponse:
        meta = run.meta
        if self.planner is None:
            return run.fail("planner_not_configured", "OPENAI_API_KEY is not set.")
        parent_plan: QueryPlan | None = None
        if request.parent_run_id:
            parent = self.store.get(request.parent_run_id) if self.store else None
            interp = parent.response.meta.interpretation if parent else None
            if interp is None:
                return run.fail(
                    "parent_run_not_found",
                    f"Run {request.parent_run_id} does not exist or has no accepted plan.",
                )
            parent_plan = interp.plan

        # 1. plan: the only model call (≤ 1 repair), grounded against live hit counts
        with run.stage("plan"):
            version = await self.ctgov.version()
            grounder = Grounder(self.ctgov, self.max_trials, version.data_timestamp)
            try:
                outcome = await self.planner.plan(request, grounder, parent_plan)
            except PlannerError as exc:
                return run.fail("planner_unavailable", str(exc))
            except UpstreamError as exc:
                return run.fail("upstream_unavailable", f"ClinicalTrials.gov: {exc}")
        meta.llm_usage = {
            "calls": outcome.attempts,
            "input_tokens": outcome.input_tokens,
            "output_tokens": outcome.output_tokens,
        }
        if outcome.plan is not None:
            outcome.plan = relabel(outcome.plan)  # labels come from filters, not model prose
            meta.interpretation = Interpretation(
                summary=summarize(outcome.plan),
                plan=outcome.plan,
                planner_model=self.planner.model,
                planner_attempts=outcome.attempts,
                repair_feedback=outcome.repair_feedback,
                parent_run_id=request.parent_run_id,
                plan_diff=plan_diff(parent_plan, outcome.plan) if parent_plan else None,
            )
        if outcome.status == "clarification":
            return run.done(Status.NEEDS_CLARIFICATION, clarification=outcome.clarification)
        if outcome.status == "too_broad" and outcome.grounding is not None:
            label, total = next(iter(outcome.grounding.too_broad.items()))
            return run.done(
                Status.NEEDS_CLARIFICATION, clarification=_too_broad(label, total, self.max_trials)
            )
        if outcome.status == "unsupported":
            return run.done(
                Status.UNSUPPORTED,
                error=ErrorInfo(
                    code="unsupported_question",
                    message=outcome.unsupported_reason or "Unsupported question.",
                    details=SUPPORTED_ALTERNATIVES,
                ),
            )
        if outcome.status != "accepted" or outcome.plan is None:
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
        meta.source = SourceInfo(
            api_version=version.api_version,
            data_timestamp=version.data_timestamp,
            retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )

        # 2. retrieve every cohort (pages are cached; concurrency is paced by the rate limiter)
        with run.stage("retrieve"):
            params = [compile_cohort(c.filters, plan.time) for c in plan.cohorts]
            try:
                results: list[SearchResult] = await asyncio.gather(
                    *(
                        self.ctgov.search(p, API_FIELDS, self.max_trials, version.data_timestamp)
                        for p in params
                    )
                )
            except TooBroadError as exc:  # the registry grew between grounding and fetching
                return run.done(
                    Status.NEEDS_CLARIFICATION,
                    clarification=_too_broad(plan.cohorts[0].label, exc.total, exc.limit),
                )
            except UpstreamError as exc:
                return run.fail("upstream_unavailable", f"ClinicalTrials.gov: {exc}")

        # 3. prepare, normalize names (guarded small model), analyze (deterministic)
        with run.stage("prepare"):
            prepared = [
                prepare_cohort(c, r.studies, plan.time)
                for c, r in zip(plan.cohorts, results, strict=True)
            ]
            cohorts: list[CohortTrials] = [ct for ct, _ in prepared]
        with run.stage("normalize"):
            view = await self._value_view(plan, cohorts, meta)
        with run.stage("analyze") as s:
            result = run_analysis(plan, cohorts, view)
            s.set(trials=sum(len(ct.trials) for ct in cohorts))
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
                synonym_matches=synonyms,
                complete=r.complete,
            )
            for (ct, synonyms), r in zip(prepared, results, strict=True)
        ]
        meta.cohort_overlap = cohort_overlap(cohorts)

        # 4. build the spec (the chart type is chosen here, deterministically)
        with run.stage("build"):
            built = build_spec(
                plan, result, cohorts, request.citations_per_datum, request.preferred_visualization
            )
        run.evidence = built.evidence
        meta.chart_selection = built.chart_selection
        meta.truncation = built.truncation
        meta.sort = built.sort
        meta.time_granularity = "year" if plan.operation.kind is OperationKind.TIME_TREND else None
        meta.units.update(_units(built))
        meta.assumptions += built.notes

        # 5. verify (output gate)
        with run.stage("verify") as s:
            trials = {t.nct_id: t for ct in cohorts for t in ct.trials}
            errors = verify(built.spec, trials)
            s.set(passed=not errors, errors=len(errors))
        if errors:
            log.error("run %s failed verification: %s", run.run_id, errors[:5])
            run.evidence = {}
            return run.fail("output_verification_failed", "Output failed verification.", errors)

        empty = (
            not built.spec.data.edges
            if isinstance(built.spec, NetworkGraphSpec)
            else all(d.trial_count == 0 for d in built.spec.data)
        )
        if empty:
            meta.assumptions.append(_empty_reason(result))
        return run.done(Status.EMPTY if empty else Status.OK, visualization=built.spec)

    async def _value_view(
        self, plan: QueryPlan, cohorts: list[CohortTrials], meta: Meta
    ) -> ValueView:
        """Name maps for the drug/condition dimensions this plan groups by (if enabled)."""
        view = ValueView(plan)
        op = plan.operation
        if self.normalizer is None or op.kind not in GROUPING_KINDS:
            return view
        for dim in {op.dimension, op.second_dimension} & set(NORMALIZED_DIMENSIONS):
            assert dim is not None
            labels: dict[str, Counter[str]] = defaultdict(Counter)
            for ct in cohorts:
                for trial in ct.trials:
                    for v in REGISTRY[dim].extract(trial.study, plan.phase_policy):
                        labels[v.key][v.label] += 1
            frequency = Counter({k: sum(c.values()) for k, c in labels.items()})
            top = [k for k, _ in frequency.most_common(MAX_NAMES)]
            shown = {k: labels[k].most_common(1)[0][0] for k in top}
            try:
                by_name = await self.normalizer(dim, [shown[k] for k in top])
            except Exception as exc:  # normalization is an enhancement: never fail the run
                log.warning("name normalization skipped: %s", exc)
                meta.assumptions.append(f"{dim.value} names were not normalized ({exc}).")
                continue
            mapping = {k: by_name[shown[k]] for k in top if shown[k] in by_name}
            view.names[dim] = mapping
            info = report(dim, self.normalizer.model, frequency, mapping, shown)
            meta.normalization.append(
                Normalization(
                    dimension=dim.value,
                    model=info.model,
                    names_in=info.names_in,
                    names_sent=info.names_sent,
                    names_unmapped=info.names_unmapped,
                    names_mapped=info.names_mapped,
                    dropped=info.dropped,
                    merges=[
                        NameMerge(canonical=m.canonical, variants=m.variants) for m in info.merges
                    ],
                )
            )
            meta.policies[f"{dim.value}_names"] = (
                f"{dim.value.capitalize()} names were normalized by {info.model} before grouping "
                "(brand/code → generic name, spelling variants merged, combinations split, "
                "non-entities dropped); citations still quote the raw registry values and "
                "meta.normalization lists the merges."
            )
        return view


GROUPING_KINDS = (OperationKind.COUNT_BY, OperationKind.NETWORK, OperationKind.TIME_TREND)

SUPPORTED_ALTERNATIVES = [
    "Trial counts by phase, status, study type, sponsor, sponsor category, drug, intervention "
    "type, condition, country, primary purpose, allocation, site or investigator",
    "Trials per year (start, first-posted or completion date), optionally split by a dimension",
    "Distributions of enrollment or trial duration (histogram)",
    "Enrollment or duration against start date (scatter plot)",
    "Comparisons of up to 4 drugs, conditions or sponsors",
    "Networks: sponsor ↔ drug, drug ↔ drug co-occurrence, condition ↔ drug, investigator ↔ site",
]


def _too_broad(label: str, total: int, limit: int) -> Clarification:
    return Clarification(
        question=f"Cohort '{label}' matches {total:,} trials, more than the {limit:,} this "
        "service analyses per group. How should it be narrowed?",
        options=[
            "Add a condition or drug",
            "Restrict to a phase or recruitment status",
            "Restrict the years",
        ],
    )


def _empty_reason(result: Any) -> str:
    if isinstance(result, NetworkResult):
        return "No pair of entities shares a trial in this cohort."
    if isinstance(result, ScatterResult):
        return "No trial in the cohort has both measures."
    return "No trials match these filters (the named entities exist, but not in combination)."


def _units(built: BuiltSpec) -> dict[str, str]:
    spec = built.spec
    encoding = getattr(spec, "encoding", None)
    units: dict[str, str] = {}
    for channel in ("x", "y"):
        ch = getattr(encoding, channel, None)
        if ch is not None and ch.unit:
            units[ch.field] = ch.unit
    return units


def plan_diff(before: QueryPlan, after: QueryPlan) -> dict[str, Any]:
    """Changed leaves between two plans, keyed by dotted path (follow-up transparency)."""
    a, b = _flatten(before.model_dump(mode="json")), _flatten(after.model_dump(mode="json"))
    return {
        k: {"before": a.get(k), "after": b.get(k)}
        for k in sorted(set(a) | set(b))
        if a.get(k) != b.get(k) and not k.endswith(".label")  # labels derive from filters
    }


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out |= _flatten(v, f"{prefix}.{k}" if prefix else k)
        return out
    if isinstance(value, list) and value and isinstance(value[0], dict):
        out = {}
        for i, v in enumerate(value):
            out |= _flatten(v, f"{prefix}[{i}]")
        return out
    return {prefix: value}


class _Run:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.meta = Meta()
        self.trace = Trace(run_id)
        self.evidence: dict[str, list[dict[str, Any]]] = {}

    def stage(self, name: str) -> "_StageSpan":
        return _StageSpan(self, name)

    def done(self, status: Status, **fields: object) -> VisualizationResponse:
        return VisualizationResponse(run_id=self.run_id, status=status, meta=self.meta, **fields)  # type: ignore[arg-type]

    def fail(
        self, code: str, message: str, details: list[str] | None = None
    ) -> VisualizationResponse:
        return self.done(
            Status.FAILED, error=ErrorInfo(code=code, message=message, details=details or [])
        )


class _StageSpan:
    """A traced stage whose duration is also copied into meta.timings_ms."""

    def __init__(self, run: _Run, name: str) -> None:
        self.run, self.name = run, name
        self._cm = span(f"stage.{name}")

    def __enter__(self) -> Span:
        self.span = self._cm.__enter__()
        return self.span

    def __exit__(self, *exc: object) -> None:
        self._cm.__exit__(*exc)  # type: ignore[arg-type]
        self.run.meta.timings_ms[self.name] = round(self.span.duration_ms or 0)
