"""Deterministic analytics operators. No model output reaches this layer except the
validated QueryPlan; every number is computed here from retrieved records."""

from collections.abc import Callable

from app.analytics.count_by import count_by
from app.analytics.network import network
from app.analytics.time_trend import time_trend
from app.analytics.types import AnalysisResult, CohortTrials
from app.contracts.enums import OperationKind
from app.contracts.plan import QueryPlan

Operator = Callable[[QueryPlan, list[CohortTrials]], AnalysisResult]

OPERATORS: dict[OperationKind, Operator] = {
    OperationKind.COUNT_BY: count_by,
    OperationKind.TIME_TREND: time_trend,
    OperationKind.NETWORK: network,
}


def run_analysis(plan: QueryPlan, cohorts: list[CohortTrials]) -> AnalysisResult:
    return OPERATORS[plan.operation.kind](plan, cohorts)
