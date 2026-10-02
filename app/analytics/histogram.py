"""histogram: distinct trials per bin of a per-trial measure (enrollment, duration). Bins are
declared in the registry and zero-filled, so the shape never depends on the data. Several
cohorts become series (one bar per cohort in each bin)."""

from collections import Counter

from app.analytics.count_by import cohorts_disjoint
from app.analytics.types import Bucket, CohortTrials, CountResult, Row
from app.analytics.values import ValueView
from app.contracts.plan import QueryPlan
from app.registry import MEASURES


def bin_labels(edges: tuple[float, ...], integer: bool) -> list[str]:
    labels = []
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else None
        if hi is None:
            labels.append(f"≥{lo:,g}")
        elif integer and hi - lo > 1:
            labels.append(f"{lo:,g}–{hi - 1:,g}")
        elif integer:
            labels.append(f"{lo:,g}")
        else:
            labels.append(f"{lo:,g}–{hi:,g}")
    return labels


def bin_index(edges: tuple[float, ...], value: float) -> int | None:
    if value < edges[0]:
        return None
    index = 0
    for i, edge in enumerate(edges):
        if value >= edge:
            index = i
    return index


def histogram(plan: QueryPlan, cohorts: list[CohortTrials], _view: ValueView) -> CountResult:
    assert plan.operation.measure is not None
    spec = MEASURES[plan.operation.measure]
    assert spec.bin_edges is not None, "validated upstream: histogram needs a binned measure"
    edges = spec.bin_edges
    labels = bin_labels(edges, integer=spec.name.value == "enrollment")
    by_cohort = len(cohorts) > 1
    buckets: dict[tuple[str | None, int], Bucket] = {}
    missing: dict[str, dict[str, int]] = {}

    for ct in cohorts:
        series = ct.cohort.label if by_cohort else None
        miss: Counter[str] = Counter()
        for trial in ct.trials:
            measured = spec.extract(trial.study)
            index = bin_index(edges, measured.sort_key) if measured else None
            if measured is None or index is None:
                miss[spec.name.value] += 1
                continue
            bucket = buckets.setdefault((series, index), Bucket(str(index)))
            bucket.add(trial, labels[index], measured.evidence)
        missing[ct.cohort.label] = dict(miss)

    series_keys: list[str | None] = [c.cohort.label for c in cohorts] if by_cohort else [None]
    rows = [
        Row(
            str(i),
            labels[i],
            s,
            buckets.get((s, i)) or Bucket(str(i)),
            {"bin_start": edges[i], "bin_end": edges[i + 1] if i + 1 < len(edges) else None},
        )
        for i in range(len(edges))
        for s in series_keys
    ]
    return CountResult(
        kind="histogram",
        rows=rows,
        category_order=labels,
        series_order=[c.cohort.label for c in cohorts] if by_cohort else None,
        series_source="cohort" if by_cohort else None,
        sort_description="Bins in ascending order",
        missing=missing,
        categories_exclusive=True,
        series_exclusive=cohorts_disjoint(cohorts),
    )
