"""Deterministic checks on a proposed plan, and enforcement of explicit request fields.

Structured Outputs guarantees the plan's *shape*; these checks guarantee it *makes sense*.
Error strings are written for the model to read during the single repair attempt.
"""

from typing import Any

from app.contracts.enums import OperationKind
from app.contracts.plan import Clarification, QueryPlan
from app.contracts.request import VisualizationRequest
from app.registry import MEASURES, NETWORK_DIMENSIONS, REGISTRY, SINGLE_VALUED

MAX_COHORTS = 4
FILTER_FIELDS = (
    "drug_name",
    "condition",
    "sponsor",
    "country",
    "trial_phase",
    "overall_status",
    "study_type",
)


def validate_plan(plan: QueryPlan) -> list[str]:
    errors: list[str] = []
    n = len(plan.cohorts)
    if not 1 <= n <= MAX_COHORTS:
        errors.append(f"cohorts must contain 1-{MAX_COHORTS} items, got {n}")
    labels = [c.label.strip() for c in plan.cohorts]
    if any(not label for label in labels):
        errors.append("every cohort needs a non-empty label")
    if len(set(labels)) != len(labels):
        errors.append("cohort labels must be unique")

    op = plan.operation
    unused: dict[OperationKind, tuple[str, ...]] = {
        OperationKind.COUNT_BY: ("measure", "x_measure"),
        OperationKind.TIME_TREND: ("dimension", "measure", "x_measure"),
        OperationKind.HISTOGRAM: ("dimension", "second_dimension", "x_measure"),
        OperationKind.SCATTER: ("second_dimension",),
        OperationKind.NETWORK: ("measure", "x_measure"),
    }
    for name in unused[op.kind]:
        if getattr(op, name) is not None:
            errors.append(f"{op.kind.value} requires operation.{name} to be null")

    if op.kind in (OperationKind.COUNT_BY, OperationKind.TIME_TREND):
        if op.kind is OperationKind.COUNT_BY and op.dimension is None:
            errors.append("count_by requires operation.dimension")
        if op.second_dimension is not None:
            if n > 1:
                errors.append(
                    f"{op.kind.value} with several cohorts already uses cohorts as series; "
                    "set second_dimension to null"
                )
            if op.second_dimension == op.dimension:
                errors.append("second_dimension must differ from dimension")
    elif op.kind is OperationKind.HISTOGRAM:
        binned = ", ".join(m.value for m, spec in MEASURES.items() if spec.bin_edges)
        if op.measure is None or MEASURES[op.measure].bin_edges is None:
            errors.append(f"histogram requires operation.measure to be one of: {binned}")
    elif op.kind is OperationKind.SCATTER:
        if op.measure is None or op.x_measure is None:
            errors.append("scatter requires operation.measure (y) and operation.x_measure (x)")
        elif op.measure == op.x_measure:
            errors.append("scatter requires two different measures")
        elif MEASURES[op.measure].kind != "quantitative":
            errors.append("scatter y (operation.measure) must be a numeric measure")
        if op.dimension is not None:
            if n > 1:
                errors.append("scatter with several cohorts colours by cohort; set dimension null")
            elif not REGISTRY[op.dimension].exclusive(plan.phase_policy):
                single = ", ".join(d.value for d in SINGLE_VALUED)
                errors.append(
                    f"scatter colour needs a single-valued dimension (one of: {single}, or "
                    "phase with phase_policy combined)"
                )
    elif op.kind is OperationKind.NETWORK:
        allowed = ", ".join(d.value for d in NETWORK_DIMENSIONS)
        for name, dim in (("dimension", op.dimension), ("second_dimension", op.second_dimension)):
            if dim not in NETWORK_DIMENSIONS:
                errors.append(f"network requires {name} to be one of: {allowed}")
        if n != 1:
            errors.append("network requires exactly one cohort")

    t = plan.time
    for name, year in (("year_from", t.year_from), ("year_to", t.year_to)):
        if year is not None and not 1900 <= year <= 2100:
            errors.append(f"time.{name} must be between 1900 and 2100")
    if t.year_from is not None and t.year_to is not None and t.year_from > t.year_to:
        errors.append("time.year_from must be <= time.year_to")
    if plan.top_n is not None and not 1 <= plan.top_n <= 100:
        errors.append("top_n must be between 1 and 100, or null")
    return errors


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        x, y = a.casefold().strip(), b.casefold().strip()
        return x == y or x in y or y in x  # "pembrolizumab" vs "pembrolizumab (keytruda)"
    if isinstance(a, list) and isinstance(b, list):
        return set(a) == set(b)
    return bool(a == b)


def apply_request_fields(
    plan: QueryPlan, request: VisualizationRequest
) -> tuple[QueryPlan, Clarification | None]:
    """Explicit structured fields override the prose: fill them into every cohort and the time
    scope. If the plan holds a *different* value, ask instead of silently picking one."""
    conflicts: list[tuple[str, Any, Any, str]] = []
    cohorts = []
    for cohort in plan.cohorts:
        updates: dict[str, Any] = {}
        for name in FILTER_FIELDS:
            wanted = getattr(request, name)
            if wanted is None:
                continue
            current = getattr(cohort.filters, name)
            if current is None or _same(current, wanted):
                updates[name] = wanted  # the explicit value wins over the model's wording
            else:
                conflicts.append((name, wanted, current, cohort.label))
        cohorts.append(
            cohort.model_copy(update={"filters": cohort.filters.model_copy(update=updates)})
        )

    time_updates: dict[str, Any] = {}
    for req_name, plan_name in (("start_year", "year_from"), ("end_year", "year_to")):
        wanted = getattr(request, req_name)
        current = getattr(plan.time, plan_name)
        if wanted is None:
            continue
        if current is None or current == wanted:
            time_updates[plan_name] = wanted
        else:
            conflicts.append((req_name, wanted, current, "time range"))

    if conflicts:
        name, wanted, current, where = conflicts[0]
        return plan, Clarification(
            question=f"The structured field {name}={_show(wanted)} disagrees with the question, "
            f"which implies {_show(current)} ({where}). Which should be used?",
            options=[
                f"Use {name}={_show(wanted)} for every group",
                f"Use {_show(current)} as the question says",
            ],
        )
    updated = plan.model_copy(
        update={"cohorts": cohorts, "time": plan.time.model_copy(update=time_updates)}
    )
    return updated, None


def _show(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(str(getattr(v, "value", v)) for v in value) + "]"
    return repr(getattr(value, "value", value))
