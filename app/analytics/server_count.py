"""Server-side counting for cohorts too large to fetch (> MAX_TRIALS_PER_COHORT).

Instead of downloading every record, each bucket (a phase, a status, a year, …) is one API
request that returns the registry's exact `totalCount` for "cohort AND bucket" plus a few
sample records to cite. Every datum carries that request as `source_query`, so anyone can
reproduce the count with one click. The idea comes from comparing implementations; the
tradeoff is stated in the response: counts are exact registry counts, but citations are
samples and local re-checks / name normalization do not apply.

Only dimensions the API can filter exactly are countable; anything else still asks the user to
narrow the question.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from app.analytics.prepare import prepare_cohort
from app.analytics.types import Bucket, CohortTrials, CountResult, Row, Trial
from app.contracts.enums import PHASE_LABELS, Dimension, OperationKind, PhasePolicy
from app.contracts.plan import QueryPlan
from app.ctgov.client import CTGovClient
from app.ctgov.compile import compile_cohort
from app.registry import (
    API_FIELDS,
    DATE_API_FIELDS,
    PHASE_ORDER,
    REGISTRY,
    SPONSOR_CLASS_LABELS,
    STATUS_LABELS,
    extract_date,
)

SAMPLE_SIZE = 3
MAX_COUNT_QUERIES = 48
DEFAULT_YEARS = 20
SITE = "https://clinicaltrials.gov/api/v2/studies"


@dataclass(frozen=True)
class BucketDef:
    key: str  # registry grouping key (e.g. "PHASE1+PHASE2", "RECRUITING", "2019")
    label: str
    clause: str  # Essie clause selecting exactly this bucket


def _enum_buckets(area: str, values: dict[str, str]) -> list[BucketDef]:
    return [BucketDef(v, label, f"AREA[{area}]{v}") for v, label in values.items()]


def _phase_buckets(policy: PhasePolicy) -> list[BucketDef]:
    singles = ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"]
    if policy is PhasePolicy.SPLIT:
        return [BucketDef(p, PHASE_LABELS[p], f"AREA[Phase]{p}") for p in singles]
    buckets = []
    for key in PHASE_ORDER:  # combined categories: exactly these phases and no others
        members = key.split("+")
        clause = " AND ".join(
            [f"AREA[Phase]{p}" for p in members]
            + [f"NOT AREA[Phase]{p}" for p in singles if p not in members]
        )
        nums = [m.removeprefix("PHASE") for m in members]
        label = "Phase " + "/".join(nums) if len(members) > 1 else PHASE_LABELS[key]
        buckets.append(BucketDef(key, label, f"({clause})"))
    return buckets


def _labels(values: list[str], overrides: dict[str, str] | None = None) -> dict[str, str]:
    overrides = overrides or {}
    return {v: overrides.get(v, v.replace("_", " ").capitalize()) for v in values}


# dimension -> (Essie area for MISSING, bucket factory)
COUNTABLE: dict[Dimension, tuple[str, Callable[[PhasePolicy], list[BucketDef]]]] = {
    Dimension.PHASE: ("Phase", _phase_buckets),
    Dimension.OVERALL_STATUS: (
        "OverallStatus",
        lambda _: _enum_buckets(
            "OverallStatus",
            _labels(
                [
                    "RECRUITING",
                    "NOT_YET_RECRUITING",
                    "ENROLLING_BY_INVITATION",
                    "ACTIVE_NOT_RECRUITING",
                    "COMPLETED",
                    "TERMINATED",
                    "SUSPENDED",
                    "WITHDRAWN",
                    "UNKNOWN",
                ],
                STATUS_LABELS,
            ),
        ),
    ),
    Dimension.STUDY_TYPE: (
        "StudyType",
        lambda _: _enum_buckets(
            "StudyType", _labels(["INTERVENTIONAL", "OBSERVATIONAL", "EXPANDED_ACCESS"])
        ),
    ),
    Dimension.SPONSOR_CLASS: (
        "LeadSponsorClass",
        lambda _: _enum_buckets("LeadSponsorClass", dict(SPONSOR_CLASS_LABELS)),
    ),
    Dimension.ALLOCATION: (
        "DesignAllocation",
        lambda _: _enum_buckets(
            "DesignAllocation",
            _labels(["RANDOMIZED", "NON_RANDOMIZED", "NA"], {"NA": "Not applicable"}),
        ),
    ),
    Dimension.PRIMARY_PURPOSE: (
        "DesignPrimaryPurpose",
        lambda _: _enum_buckets(
            "DesignPrimaryPurpose",
            _labels(
                [
                    "TREATMENT",
                    "PREVENTION",
                    "SUPPORTIVE_CARE",
                    "DIAGNOSTIC",
                    "BASIC_SCIENCE",
                    "SCREENING",
                    "HEALTH_SERVICES_RESEARCH",
                    "DEVICE_FEASIBILITY",
                    "ECT",
                    "OTHER",
                ]
            ),
        ),
    ),
    Dimension.INTERVENTION_TYPE: (
        "InterventionType",
        lambda _: _enum_buckets(
            "InterventionType",
            _labels(
                [
                    "DRUG",
                    "BIOLOGICAL",
                    "DEVICE",
                    "PROCEDURE",
                    "BEHAVIORAL",
                    "RADIATION",
                    "DIETARY_SUPPLEMENT",
                    "GENETIC",
                    "DIAGNOSTIC_TEST",
                    "COMBINATION_PRODUCT",
                    "OTHER",
                ]
            ),
        ),
    ),
}


@dataclass
class CountPlan:
    categories: list[BucketDef]  # x axis (years for time_trend)
    series: list[BucketDef] | None  # second dimension, if any
    years: tuple[int, int] | None
    missing_area: str | None  # dimension whose MISSING count is reported
    queries: int


def count_plan(plan: QueryPlan, now_year: int | None = None) -> CountPlan | None:
    """The bucket queries for this plan, or None when it cannot be counted on the server."""
    op = plan.operation
    if op.only_listed_values or op.kind not in (OperationKind.COUNT_BY, OperationKind.TIME_TREND):
        return None
    series: list[BucketDef] | None = None
    if op.second_dimension is not None:
        if op.second_dimension not in COUNTABLE:
            return None
        series = COUNTABLE[op.second_dimension][1](plan.phase_policy)
    years: tuple[int, int] | None = None
    missing_area: str | None = None
    if op.kind is OperationKind.TIME_TREND:
        year = now_year or datetime.now(UTC).year
        hi = plan.time.year_to if plan.time.year_to is not None else year + 1
        lo = plan.time.year_from if plan.time.year_from is not None else hi - DEFAULT_YEARS
        years = (lo, hi)
        area = DATE_API_FIELDS[plan.time.date_basis]
        categories = [
            BucketDef(str(y), str(y), f"AREA[{area}]RANGE[{y}-01-01,{y}-12-31]")
            for y in range(lo, hi + 1)
        ]
    else:
        assert op.dimension is not None
        if op.dimension not in COUNTABLE:
            return None
        missing_area, factory = COUNTABLE[op.dimension]
        categories = factory(plan.phase_policy)
    queries = len(plan.cohorts) * len(categories) * (len(series) if series else 1)
    if queries > MAX_COUNT_QUERIES:
        return None
    return CountPlan(categories, series, years, missing_area, queries)


def source_url(params: dict[str, str]) -> str:
    # A compact, clickable request whose "totalCount" is exactly the datum's trial_count.
    query = {**params, "countTotal": "true", "pageSize": "1", "fields": "NCTId"}
    return f"{SITE}?{urlencode(query)}"


def _with(params: dict[str, str], *clauses: str) -> dict[str, str]:
    terms = [t for t in (params.get("query.term"), *clauses) if t]
    return {**params, "query.term": " AND ".join(terms)}


def _bucket_evidence(dim: Dimension | None, key: str, trial: Trial, plan: QueryPlan) -> list[Any]:
    """The field value that puts a sample trial in this bucket (empty if it no longer does)."""
    if dim is None:  # a year bucket
        date = extract_date(trial.study, plan.time.date_basis)
        return [(date.path, date.raw)] if date and str(date.year) == key else []
    for v in REGISTRY[dim].extract(trial.study, plan.phase_policy):
        if v.key == key:
            return [(v.path, v.raw)]
    return []


@dataclass
class ServerCountResult:
    result: CountResult
    cohorts: list[CohortTrials]  # sample trials (citation sources)
    totals: list[int]
    params: list[dict[str, str]]
    notes: list[str]


async def server_count(
    plan: QueryPlan, ctgov: CTGovClient, scope: str | None, cp: CountPlan
) -> ServerCountResult:
    op = plan.operation
    x_dim = None if op.kind is OperationKind.TIME_TREND else op.dimension
    by_cohort = len(plan.cohorts) > 1
    rows: list[Row] = []
    samples: list[CohortTrials] = []
    totals: list[int] = []
    params_list: list[dict[str, str]] = []
    notes: list[str] = []
    counted: dict[tuple[str, str | None], int] = {}

    for cohort in plan.cohorts:
        params = compile_cohort(cohort.filters, plan.time)
        params_list.append(params)
        totals.append(await ctgov.count(params, scope))
        cohort_samples: dict[str, dict[str, Any]] = {}
        series_defs: list[BucketDef | None] = list(cp.series) if cp.series else [None]
        for category in cp.categories:
            for s in series_defs:
                clauses = [category.clause] + ([s.clause] if s else [])
                query = _with(params, *clauses)
                page = await ctgov.sample(query, API_FIELDS, SAMPLE_SIZE, scope)
                bucket = Bucket(category.key, total=page.total)
                bucket.labels[category.label] += 1
                ct, _ = prepare_cohort(cohort, page.studies, plan.time)
                for trial in ct.trials:
                    evidence = _bucket_evidence(x_dim, category.key, trial, plan)
                    if s is not None and op.second_dimension is not None:
                        evidence += _bucket_evidence(op.second_dimension, s.key, trial, plan)
                    if evidence:
                        bucket.add(trial, category.label, evidence)
                        cohort_samples[trial.nct_id] = trial.study
                series_label = cohort.label if by_cohort else (s.label if s else None)
                counted[(category.key, series_label)] = page.total
                rows.append(
                    Row(
                        category.key,
                        category.label,
                        series_label,
                        bucket,
                        {"source_query": source_url(query)},
                    )
                )
        ct_all, _ = prepare_cohort(cohort, list(cohort_samples.values()), plan.time)
        samples.append(ct_all)
        if cp.missing_area:
            missing = await ctgov.count(_with(params, f"AREA[{cp.missing_area}]MISSING"), scope)
            if missing:
                what = x_dim.value if x_dim else "value"
                notes.append(f"{missing:,} trials in '{cohort.label}' report no {what}.")
        if cp.years is not None:
            area = DATE_API_FIELDS[plan.time.date_basis]
            earlier = await ctgov.count(
                _with(params, f"AREA[{area}]RANGE[MIN,{cp.years[0] - 1}-12-31]"), scope
            )
            if earlier and plan.time.year_from is None:
                notes.append(
                    f"Counted {cp.years[0]}–{cp.years[1]}; {earlier:,} trials in "
                    f"'{cohort.label}' started earlier and are not shown."
                )

    rows = _ordered(rows, x_dim, plan)
    if op.kind is OperationKind.COUNT_BY and not by_cohort and cp.series is None:
        rows = [r for r in rows if r.bucket.count > 0]
    category_order = list(dict.fromkeys(r.label for r in rows))
    series_order = (
        [c.label for c in plan.cohorts]
        if by_cohort
        else ([s.label for s in cp.series] if cp.series else None)
    )
    result = CountResult(
        kind="time_trend" if op.kind is OperationKind.TIME_TREND else "count_by",
        rows=rows,
        category_order=category_order,
        series_order=series_order,
        series_source="cohort" if by_cohort else ("dimension" if cp.series else None),
        sort_description="Year ascending" if x_dim is None else "Canonical order / count",
        missing={},
        categories_exclusive=x_dim is not None
        and REGISTRY[x_dim].exclusive(plan.phase_policy)
        and not by_cohort,
        series_exclusive=False,
    )
    return ServerCountResult(result, samples, totals, params_list, notes)


def _ordered(rows: list[Row], dim: Dimension | None, plan: QueryPlan) -> list[Row]:
    if dim is None or REGISTRY[dim].order:
        return rows  # years and phases are already in canonical order
    totals: dict[str, int] = {}
    for r in rows:
        totals[r.key] = totals.get(r.key, 0) + r.bucket.count
    rank = {k: i for i, k in enumerate(sorted(totals, key=lambda k: (-totals[k], k)))}
    ordered = sorted(rows, key=lambda r: rank[r.key])
    if plan.top_n:
        keep = {k for k, i in rank.items() if i < plan.top_n}
        ordered = [r for r in ordered if r.key in keep]
    return ordered
