"""count_by: distinct trials per category, optionally split into series (cohorts or a
second dimension). Feeds bar_chart and grouped_bar_chart."""

from collections import Counter, defaultdict

from app.analytics.types import Bucket, CohortTrials, CountResult, EvidenceItem, Row, TruncationInfo
from app.contracts.plan import QueryPlan
from app.registry import REGISTRY, DimensionSpec


def count_by(plan: QueryPlan, cohorts: list[CohortTrials]) -> CountResult:
    op = plan.operation
    assert op.dimension is not None
    spec = REGISTRY[op.dimension]
    split = REGISTRY[op.second_dimension] if op.second_dimension else None
    by_cohort = len(cohorts) > 1

    buckets: dict[tuple[str | None, str], Bucket] = {}
    series_labels: dict[str, Counter[str]] = defaultdict(Counter)
    missing: dict[str, dict[str, int]] = {}

    for ct in cohorts:
        miss: Counter[str] = Counter()
        for trial in ct.trials:
            values = spec.extract(trial.study, plan.phase_policy)
            if not values:
                miss[spec.name.value] += 1
                continue
            # Each entry: (series key, series label, extra evidence for the series)
            series: list[tuple[str | None, str | None, list[EvidenceItem]]]
            if by_cohort:
                series = [(ct.cohort.label, ct.cohort.label, [])]
            elif split is not None:
                split_values = split.extract(trial.study, plan.phase_policy)
                if not split_values:
                    miss[split.name.value] += 1
                    continue
                series = [(s.key, s.label, [(s.path, s.raw)]) for s in split_values]
            else:
                series = [(None, None, [])]
            for s_key, s_label, s_evidence in series:
                if s_key is not None and s_label is not None:
                    series_labels[s_key][s_label] += 1
                for v in values:
                    bucket = buckets.setdefault((s_key, v.key), Bucket(v.key))
                    bucket.add(trial, v.label, [(v.path, v.raw), *s_evidence])
        missing[ct.cohort.label] = dict(miss)

    # Category order: registry order for ordinal fields, otherwise total count descending.
    totals: Counter[str] = Counter()
    labels: dict[str, Counter[str]] = defaultdict(Counter)
    for (_, key), bucket in buckets.items():
        totals[key] += bucket.count
        labels[key].update(bucket.labels)
    keys, sort_description = order_categories(spec, totals, labels)

    top_n = plan.top_n or (None if spec.order else spec.default_top_n)
    truncation = None
    if top_n is not None and len(keys) > top_n:
        by_size = sorted(keys, key=lambda k: (-totals[k], top_label(labels[k])))[:top_n]
        kept = set(by_size)
        truncation = TruncationInfo(
            shown=top_n,
            total=len(keys),
            rule=f"Top {top_n} {spec.label.lower()} categories by trial count",
        )
        keys = [k for k in keys if k in kept]

    # Series order: cohorts as planned; second dimension by registry order or size.
    series_keys: list[str | None]
    if by_cohort:
        series_keys = [c.cohort.label for c in cohorts]
    elif split is not None:
        s_totals = Counter({k: sum(c.values()) for k, c in series_labels.items()})
        series_keys = list(order_categories(split, s_totals, series_labels)[0])
    else:
        series_keys = [None]

    def series_label(s_key: str | None) -> str | None:
        if s_key is None or by_cohort:
            return s_key  # cohort labels are already display labels (even for empty cohorts)
        return top_label(series_labels[s_key])

    rows: list[Row] = []
    for key in keys:
        for s_key in series_keys:
            found = buckets.get((s_key, key))
            if found is None:
                if s_key is None:
                    continue
                found = Bucket(key)  # explicit zero so grouped bars form a complete grid
            rows.append(Row(key, top_label(labels[key]), series_label(s_key), found))

    return CountResult(
        kind="count_by",
        rows=rows,
        category_order=[top_label(labels[k]) for k in keys],
        series_order=[series_label(k) or "" for k in series_keys if k is not None] or None,
        series_source="cohort" if by_cohort else ("dimension" if split else None),
        sort_description=sort_description,
        missing=missing,
        truncation=truncation,
        categories_exclusive=spec.exclusive(plan.phase_policy) and not by_cohort,
        series_exclusive=(
            cohorts_disjoint(cohorts)
            if by_cohort
            else split is not None and split.exclusive(plan.phase_policy)
        ),
    )


def cohorts_disjoint(cohorts: list[CohortTrials]) -> bool:
    seen: set[str] = set()
    for ct in cohorts:
        ids = {t.nct_id for t in ct.trials}
        if seen & ids:
            return False
        seen |= ids
    return True


def top_label(counter: Counter[str]) -> str:
    return min(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0] if counter else ""


def order_categories(
    spec: DimensionSpec, totals: Counter[str], labels: dict[str, Counter[str]]
) -> tuple[list[str], str]:
    if spec.order:
        rank = {k: i for i, k in enumerate(spec.order)}
        keys = sorted(totals, key=lambda k: (rank.get(k, len(rank)), top_label(labels[k])))
        return keys, f"{spec.label} in canonical order"
    keys = sorted(totals, key=lambda k: (-totals[k], top_label(labels[k])))
    return keys, "Trial count, descending"
