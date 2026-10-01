"""Deterministic analytics operators. No model output reaches this layer except the
validated QueryPlan; every number is computed here from retrieved records."""

from collections.abc import Callable

from app.analytics.count_by import count_by
from app.analytics.histogram import histogram
from app.analytics.network import network
from app.analytics.scatter import scatter
from app.analytics.time_trend import time_trend
from app.analytics.types import AnalysisResult, CohortTrials
from app.analytics.values import ValueView
from app.contracts.enums import OperationKind
from app.contracts.plan import QueryPlan

Operator = Callable[[QueryPlan, list[CohortTrials], ValueView], AnalysisResult]

OPERATORS: dict[OperationKind, Operator] = {
    OperationKind.COUNT_BY: count_by,
    OperationKind.TIME_TREND: time_trend,
    OperationKind.HISTOGRAM: histogram,
    OperationKind.SCATTER: scatter,
    OperationKind.NETWORK: network,
}


def run_analysis(
    plan: QueryPlan, cohorts: list[CohortTrials], view: ValueView | None = None
) -> AnalysisResult:
    return OPERATORS[plan.operation.kind](plan, cohorts, view or ValueView(plan))
