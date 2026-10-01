"""time_trend: distinct trials per year of the plan's date basis, per cohort. Feeds
time_series. Years inside the range with no trials are explicit zeros."""

from collections import Counter

from app.analytics.types import Bucket, CohortTrials, CountResult, Row
from app.contracts.plan import QueryPlan
from app.registry import extract_date


def time_trend(plan: QueryPlan, cohorts: list[CohortTrials]) -> CountResult:
    basis = plan.time.date_basis
    by_cohort = len(cohorts) > 1
    buckets: dict[tuple[str | None, int], Bucket] = {}
    missing: dict[str, dict[str, int]] = {}

    for ct in cohorts:
        series = ct.cohort.label if by_cohort else None
        miss: Counter[str] = Counter()
        for trial in ct.trials:
            date = extract_date(trial.study, basis)
            if date is None:
                miss[basis.value] += 1
                continue
            bucket = buckets.setdefault((series, date.year), Bucket(str(date.year)))
            bucket.add(trial.nct_id, str(date.year), [(date.path, date.raw)])
            if date.estimated:
                bucket.extra["estimated_date_count"] += 1
        missing[ct.cohort.label] = dict(miss)

    years = [year for _, year in buckets]
    rows: list[Row] = []
    category_order: list[str] = []
    if years:
        lo = plan.time.year_from if plan.time.year_from is not None else min(years)
        hi = plan.time.year_to if plan.time.year_to is not None else max(years)
        series_keys: list[str | None] = [c.cohort.label for c in cohorts] if by_cohort else [None]
        for year in range(lo, hi + 1):
            category_order.append(str(year))
            for s in series_keys:
                bucket = buckets.get((s, year)) or Bucket(str(year))
                rows.append(Row(str(year), str(year), s, bucket))

    return CountResult(
        kind="time_trend",
        rows=rows,
        category_order=category_order,
        series_order=[c.cohort.label for c in cohorts] if by_cohort else None,
        series_source="cohort" if by_cohort else None,
        sort_description="Year ascending",
        missing=missing,
    )
