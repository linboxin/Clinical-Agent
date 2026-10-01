"""Public request schema for POST /v1/visualizations.

Structured fields are top-level and use the brief's names, so the brief's example request
works unchanged. Explicit structured fields override what the planner reads from prose.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts.enums import (
    ChartType,
    OverallStatus,
    Phase,
    StudyType,
    parse_phases,
    parse_status,
)


class VisualizationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "query": "How has the number of trials for this drug changed over time?",
                    "drug_name": "Pembrolizumab",
                },
                {
                    "query": "Compare phases for trials involving pembrolizumab vs nivolumab",
                    "condition": "lung cancer",
                },
            ]
        },
    )

    query: str = Field(
        min_length=1,
        max_length=2000,
        description="Natural-language question about clinical trials.",
    )
    drug_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Drug/intervention; matched against intervention names and other names "
        "(ClinicalTrials.gov expands synonyms, e.g. Keytruda / MK-3475).",
    )
    condition: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Condition or disease, matched with ClinicalTrials.gov condition search.",
    )
    sponsor: str | None = Field(
        default=None, min_length=1, max_length=200, description="Lead sponsor name (phrase match)."
    )
    country: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Country of at least one listed trial location.",
    )
    trial_phase: list[Phase] | None = Field(
        default=None,
        description='Keep trials in any of these phases. Accepts "PHASE3", "Phase 3", "3", '
        '"Phase 1/2" (= Phase 1 or Phase 2), a single value or a list.',
    )
    overall_status: list[OverallStatus] | None = Field(
        default=None,
        description='Keep trials with any of these statuses, e.g. "RECRUITING". '
        "Single value or list.",
    )
    study_type: StudyType | None = None
    start_year: int | None = Field(
        default=None,
        ge=1900,
        le=2100,
        description="Inclusive lower bound on the year of the plan's date basis "
        "(study start date unless the question says otherwise).",
    )
    end_year: int | None = Field(
        default=None, ge=1900, le=2100, description="Inclusive upper bound (see start_year)."
    )
    preferred_visualization: ChartType | None = Field(
        default=None, description="Honored only when compatible with the computed analysis."
    )
    citations_per_datum: int = Field(
        default=5,
        ge=0,
        le=100,
        description="Max inline citations per datum. citation_count always reports the total; "
        "GET /v1/runs/{run_id}/evidence?datum_id=… returns the full set.",
    )
    parent_run_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        description="Follow-up: refine the plan of this earlier run (e.g. 'now only recruiting').",
    )

    @field_validator("trial_phase", mode="before")
    @classmethod
    def _parse_phases(cls, value: Any) -> Any:
        if value is None:
            return None
        items = value if isinstance(value, list) else [value]
        phases: list[Phase] = []
        for item in items:
            for phase in parse_phases(str(item)):
                if phase not in phases:
                    phases.append(phase)
        return phases

    @field_validator("overall_status", mode="before")
    @classmethod
    def _parse_statuses(cls, value: Any) -> Any:
        if value is None:
            return None
        items = value if isinstance(value, list) else [value]
        return list(dict.fromkeys(parse_status(str(item)) for item in items))

    @model_validator(mode="after")
    def _check_year_range(self) -> "VisualizationRequest":
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("start_year must be <= end_year")
        return self

    def structured_fields(self) -> dict[str, Any]:
        """Non-null filter fields, in JSON form, as the planner and meta see them."""
        keys = (
            "drug_name",
            "condition",
            "sponsor",
            "country",
            "trial_phase",
            "overall_status",
            "study_type",
            "start_year",
            "end_year",
        )
        dumped = self.model_dump(mode="json", include=set(keys))
        return {k: v for k, v in dumped.items() if v is not None}
