"""scatter: one point per trial with two measures (e.g. start date × enrollment), optionally
coloured by a single-valued dimension or by cohort. Each point cites its own trial."""

from collections import Counter

from app.analytics.types import Bucket, CohortTrials, Point, ScatterResult, TruncationInfo
from app.contracts.plan import QueryPlan
from app.registry import MEASURES, REGISTRY

MAX_POINTS = 3000


def scatter(plan: QueryPlan, cohorts: list[CohortTrials]) -> ScatterResult:
    op = plan.operation
    assert op.measure is not None and op.x_measure is not None
    x_spec, y_spec = MEASURES[op.x_measure], MEASURES[op.measure]
    color = REGISTRY[op.dimension] if op.dimension else None
    by_cohort = len(cohorts) > 1
    points: list[Point] = []
    color_counts: Counter[str] = Counter()
    missing: dict[str, dict[str, int]] = {}

    for ct in cohorts:
        miss: Counter[str] = Counter()
        for trial in ct.trials:
            x, y = x_spec.extract(trial.study), y_spec.extract(trial.study)
            if x is None or y is None:
                miss[x_spec.name.value if x is None else y_spec.name.value] += 1
                continue
            evidence = [*x.evidence, *y.evidence]
            label: str | None = None
            if by_cohort:
                label = ct.cohort.label
            elif color is not None:
                values = color.extract(trial.study, plan.phase_policy)
                if values:
                    label = values[0].label
                    evidence.append((values[0].path, values[0].raw))
                else:
                    label = "Not reported"
            if label is not None:
                color_counts[label] += 1
            bucket = Bucket(trial.nct_id)
            bucket.add(trial, trial.nct_id, evidence)
            points.append(Point(trial, x, y, label, bucket))
        missing[ct.cohort.label] = dict(miss)

    truncation = None
    if len(points) > MAX_POINTS:
        truncation = TruncationInfo(
            shown=MAX_POINTS,
            total=len(points),
            rule=f"The {MAX_POINTS} most recently registered trials (highest NCT IDs) are shown",
        )
        points = sorted(points, key=lambda p: p.trial.nct_id, reverse=True)[:MAX_POINTS]
    points.sort(key=lambda p: (p.x.sort_key, p.trial.nct_id))

    color_order: list[str] | None = None
    if by_cohort:
        color_order = [c.cohort.label for c in cohorts]
    elif color is not None:
        color_order = sorted(color_counts, key=lambda k: (-color_counts[k], k))
    return ScatterResult(points, color_order, missing, truncation)
