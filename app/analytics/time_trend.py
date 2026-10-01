"""time_trend: distinct trials per year of the plan's date basis. Series are the cohorts (when
comparing) or the values of a second dimension (e.g. phase mix per year). Feeds time_series.
Years inside the range with no trials are explicit zeros."""

from collections import Counter, defaultdict

from app.analytics.count_by import cohorts_disjoint, order_categories, top_label
from app.analytics.types import Bucket, CohortTrials, CountResult, EvidenceItem, Row
from app.contracts.plan import QueryPlan
from app.registry import REGISTRY, extract_date


def time_trend(plan: QueryPlan, cohorts: list[CohortTrials]) -> CountResult:
    basis = plan.time.date_basis
    by_cohort = len(cohorts) > 1
    split = REGISTRY[plan.operation.second_dimension] if plan.operation.second_dimension else None
    buckets: dict[tuple[str | None, int], Bucket] = {}
    series_labels: dict[str, Counter[str]] = defaultdict(Counter)
    missing: dict[str, dict[str, int]] = {}

    for ct in cohorts:
        miss: Counter[str] = Counter()
        for trial in ct.trials:
            date = extract_date(trial.study, basis)
            if date is None:
                miss[basis.value] += 1
                continue
            series: list[tuple[str | None, list[EvidenceItem]]]
            if by_cohort:
                series = [(ct.cohort.label, [])]
            elif split is not None:
                values = split.extract(trial.study, plan.phase_policy)
                if not values:
                    miss[split.name.value] += 1
                    continue
                series = []
                for v in values:
                    series_labels[v.key][v.label] += 1
                    series.append((v.key, [(v.path, v.raw)]))
            else:
                series = [(None, [])]
            for s_key, s_evidence in series:
                bucket = buckets.setdefault((s_key, date.year), Bucket(str(date.year)))
                added = bucket.add(trial, str(date.year), [(date.path, date.raw), *s_evidence])
                if added and date.estimated:
                    bucket.extra["estimated_date_count"] += 1
        missing[ct.cohort.label] = dict(miss)

    series_keys: list[str | None]
    if by_cohort:
        series_keys = [c.cohort.label for c in cohorts]
    elif split is not None:
        totals = Counter({k: sum(c.values()) for k, c in series_labels.items()})
        series_keys = list(order_categories(split, totals, series_labels)[0])
    else:
        series_keys = [None]

    def series_label(key: str | None) -> str | None:
        if key is None or by_cohort:
            return key
        return top_label(series_labels[key])

    years = [year for _, year in buckets]
    rows: list[Row] = []
    category_order: list[str] = []
    if years:
        lo = plan.time.year_from if plan.time.year_from is not None else min(years)
        hi = plan.time.year_to if plan.time.year_to is not None else max(years)
        for year in range(lo, hi + 1):
            category_order.append(str(year))
            for s in series_keys:
                bucket = buckets.get((s, year)) or Bucket(str(year))
                rows.append(Row(str(year), str(year), series_label(s), bucket))

    named_series = [series_label(k) or "" for k in series_keys if k is not None]
    return CountResult(
        kind="time_trend",
        rows=rows,
        category_order=category_order,
        series_order=named_series or None,
        series_source="cohort" if by_cohort else ("dimension" if split else None),
        sort_description="Year ascending",
        missing=missing,
        series_exclusive=(
            cohorts_disjoint(cohorts)
            if by_cohort
            else split is not None and split.exclusive(plan.phase_policy)
        ),
    )
