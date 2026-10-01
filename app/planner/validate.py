"""Deterministic checks on a proposed plan, and enforcement of explicit request fields.

Structured Outputs guarantees the plan's *shape*; these checks guarantee it *makes sense*.
Error strings are written for the model to read during the single repair attempt.
"""

from typing import Any

from app.contracts.enums import OperationKind
from app.contracts.plan import (
    EXCLUDE_FIELDS,
    LISTED_DIMENSION,
    REQUEST_TO_PLAN,
    TEXT_FIELDS,
    Clarification,
    CohortFilters,
    QueryPlan,
)
from app.contracts.request import VisualizationRequest
from app.registry import MEASURES, NETWORK_DIMENSIONS, REGISTRY, SINGLE_VALUED

MAX_COHORTS = 4
MAX_LIST_VALUES = 12
ENUM_FIELDS = ("trial_phase", "overall_status", "study_type")
LIST_FIELDS = (*TEXT_FIELDS, *EXCLUDE_FIELDS.values())


def normalize_plan(plan: QueryPlan) -> QueryPlan:
    """Tidy free-text lists: strip, drop blanks, de-duplicate case-insensitively, [] -> null."""
    cohorts = []
    for cohort in plan.cohorts:
        updates: dict[str, Any] = {}
        for name in LIST_FIELDS:
            values = getattr(cohort.filters, name)
            if values is None:
                continue
            seen: dict[str, str] = {}
            for value in values:
                text = value.strip()
                if text:
                    seen.setdefault(text.casefold(), text)
            updates[name] = list(seen.values()) or None
        for name in ("trial_phase", "overall_status"):
            if getattr(cohort.filters, name) == []:
                updates[name] = None
        cohorts.append(
            cohort.model_copy(update={"filters": cohort.filters.model_copy(update=updates)})
        )
    return plan.model_copy(update={"cohorts": cohorts})


def _filter_errors(plan: QueryPlan) -> list[str]:
    errors: list[str] = []
    for cohort in plan.cohorts:
        f = cohort.filters
        for name in LIST_FIELDS:
            values = getattr(f, name) or []
            if len(values) > MAX_LIST_VALUES:
                errors.append(f"{name} may list at most {MAX_LIST_VALUES} values")
        for name, excluded in EXCLUDE_FIELDS.items():
            both = {v.casefold() for v in getattr(f, name) or []} & {
                v.casefold() for v in getattr(f, excluded) or []
            }
            if both:
                errors.append(f"{sorted(both)} is both included and excluded in {name}")
    for expansion in plan.expansions:
        if not expansion.members:
            errors.append(f"expansion '{expansion.term}' has no members")
        listed = {
            v.casefold() for c in plan.cohorts for v in getattr(c.filters, expansion.field) or []
        }
        missing = [m for m in expansion.members if m.casefold() not in listed]
        if missing:
            errors.append(
                f"expansion '{expansion.term}' members {missing} must also appear in the "
                f"cohort filters.{expansion.field} list"
            )
    return errors


def _listed_errors(plan: QueryPlan) -> list[str]:
    op = plan.operation
    if not op.only_listed_values:
        return []
    dims = [d for d in (op.dimension, op.second_dimension) if d in LISTED_DIMENSION]
    if not dims:
        allowed = ", ".join(d.value for d in LISTED_DIMENSION)
        return [f"only_listed_values needs dimension or second_dimension in: {allowed}"]
    errors = []
    for dim in dims:
        field = LISTED_DIMENSION[dim]
        if any(not getattr(c.filters, field) for c in plan.cohorts):
            errors.append(
                f"only_listed_values over {dim.value} needs every cohort to set filters.{field}"
            )
    return errors


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
    return errors + _filter_errors(plan) + _listed_errors(plan)


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
        for req_name, plan_name in REQUEST_TO_PLAN.items():
            wanted = getattr(request, req_name)
            if wanted is None:
                continue
            current = getattr(cohort.filters, plan_name)
            if not current or (len(current) == 1 and _same(current[0], wanted)):
                updates[plan_name] = [wanted]  # the explicit value wins over the model's wording
            else:
                conflicts.append((req_name, wanted, current, cohort.label))
        for name in ENUM_FIELDS:
            wanted = getattr(request, name)
            if wanted is None:
                continue
            current = getattr(cohort.filters, name)
            if current is None or _same(current, wanted):
                updates[name] = wanted
            else:
                conflicts.append((name, wanted, current, cohort.label))
        cohorts.append(
            cohort.model_copy(update={"filters": cohort.filters.model_copy(update=updates)})
        )

    time_updates: dict[str, Any] = {}
    for req_name, time_field in (("start_year", "year_from"), ("end_year", "year_to")):
        wanted = getattr(request, req_name)
        current = getattr(plan.time, time_field)
        if wanted is None:
            continue
        if current is None or current == wanted:
            time_updates[time_field] = wanted
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


def empty_filters() -> CohortFilters:
    return CohortFilters.model_validate({name: None for name in CohortFilters.model_fields})


def _show(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(str(getattr(v, "value", v)) for v in value) + "]"
    return repr(getattr(value, "value", value))
