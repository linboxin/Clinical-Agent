"""Closed vocabularies shared by the request, the query plan and the response.

Registry-facing enums (Dimension, OperationKind, ...) are what make the LLM's output
constrained: the planner's JSON schema is generated from these, so the model cannot name
a field or operation the backend does not implement.
"""

import re
from enum import StrEnum


class Phase(StrEnum):
    EARLY_PHASE1 = "EARLY_PHASE1"
    PHASE1 = "PHASE1"
    PHASE2 = "PHASE2"
    PHASE3 = "PHASE3"
    PHASE4 = "PHASE4"
    NA = "NA"


PHASE_LABELS: dict[str, str] = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "Not applicable",
}


class OverallStatus(StrEnum):
    NOT_YET_RECRUITING = "NOT_YET_RECRUITING"
    RECRUITING = "RECRUITING"
    ENROLLING_BY_INVITATION = "ENROLLING_BY_INVITATION"
    ACTIVE_NOT_RECRUITING = "ACTIVE_NOT_RECRUITING"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"
    COMPLETED = "COMPLETED"
    WITHDRAWN = "WITHDRAWN"
    UNKNOWN = "UNKNOWN"
    AVAILABLE = "AVAILABLE"
    NO_LONGER_AVAILABLE = "NO_LONGER_AVAILABLE"
    TEMPORARILY_NOT_AVAILABLE = "TEMPORARILY_NOT_AVAILABLE"
    APPROVED_FOR_MARKETING = "APPROVED_FOR_MARKETING"
    WITHHELD = "WITHHELD"


class StudyType(StrEnum):
    INTERVENTIONAL = "INTERVENTIONAL"
    OBSERVATIONAL = "OBSERVATIONAL"
    EXPANDED_ACCESS = "EXPANDED_ACCESS"


class Dimension(StrEnum):
    """Fields a chart can group by. Each one has an entry in app.registry."""

    PHASE = "phase"
    OVERALL_STATUS = "overall_status"
    STUDY_TYPE = "study_type"
    LEAD_SPONSOR = "lead_sponsor"
    SPONSOR_CLASS = "sponsor_class"
    DRUG = "drug"
    INTERVENTION_TYPE = "intervention_type"
    CONDITION = "condition"
    COUNTRY = "country"
    PRIMARY_PURPOSE = "primary_purpose"
    ALLOCATION = "allocation"
    SITE = "site"
    INVESTIGATOR = "investigator"


class Measure(StrEnum):
    """Per-trial numeric or date values: histogram bins and scatter axes."""

    ENROLLMENT = "enrollment"
    DURATION_MONTHS = "duration_months"
    START_DATE = "start_date"


class OperationKind(StrEnum):
    COUNT_BY = "count_by"
    TIME_TREND = "time_trend"
    HISTOGRAM = "histogram"
    SCATTER = "scatter"
    NETWORK = "network"


class DateBasis(StrEnum):
    START_DATE = "start_date"
    FIRST_POSTED = "first_posted"
    COMPLETION_DATE = "completion_date"


class PhasePolicy(StrEnum):
    COMBINED = "combined"  # a Phase 1/2 trial is one "Phase 1/2" category
    SPLIT = "split"  # a Phase 1/2 trial counts once in Phase 1 and once in Phase 2


class ChartType(StrEnum):
    BAR_CHART = "bar_chart"
    GROUPED_BAR_CHART = "grouped_bar_chart"
    STACKED_BAR_CHART = "stacked_bar_chart"
    PIE_CHART = "pie_chart"
    TIME_SERIES = "time_series"
    HISTOGRAM = "histogram"
    SCATTER_PLOT = "scatter_plot"
    NETWORK_GRAPH = "network_graph"


class Status(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


def parse_phases(value: str) -> list[Phase]:
    """Parse user-facing phase text ("Phase 3", "3", "phase1/2", "N/A") into Phase values."""
    text = value.strip().upper()
    if text in {"NA", "N/A", "NOT APPLICABLE", "NOT_APPLICABLE"}:
        return [Phase.NA]
    compact = re.sub(r"[\s_\-]", "", text)
    if compact in {"EARLYPHASE1", "PHASE0", "0"}:
        return [Phase.EARLY_PHASE1]
    compact = compact.removeprefix("PHASE")
    phases: list[Phase] = []
    for part in compact.split("/"):
        part = part.removeprefix("PHASE")
        roman = {"I": "1", "II": "2", "III": "3", "IV": "4"}
        part = roman.get(part, part)
        if part not in {"1", "2", "3", "4"}:
            raise ValueError(f"unrecognised trial phase: {value!r}")
        phases.append(Phase(f"PHASE{part}"))
    return phases


def parse_status(value: str) -> OverallStatus:
    """Accept "Recruiting", "active, not recruiting", "NOT_YET_RECRUITING", ..."""
    key = re.sub(r"[\s,\-]+", "_", value.strip().upper())
    try:
        return OverallStatus(key)
    except ValueError:
        raise ValueError(f"unrecognised overall status: {value!r}") from None
