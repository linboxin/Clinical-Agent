"""The QueryPlan: the only thing the LLM produces.

This model is sent to OpenAI as a strict Structured Outputs schema, so it follows the strict
mode rules: every field is required (nullable where optional), there are no defaults and no
numeric/string constraints. Range and cross-field checks happen in app.planner.validate.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.enums import (
    DateBasis,
    Dimension,
    OperationKind,
    OverallStatus,
    Phase,
    PhasePolicy,
    StudyType,
)


class CohortFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drug_name: str | None = Field(
        description="Drug or intervention exactly as the user named it (generic, brand or code). "
        "Do not add synonyms; the registry search expands them. Null if unconstrained."
    )
    condition: str | None = Field(description="Condition/disease as named by the user, or null.")
    sponsor: str | None = Field(description="Lead sponsor organisation, or null.")
    country: str | None = Field(description="Country of a trial location (English name), or null.")
    trial_phase: list[Phase] | None = Field(
        description="Keep trials in any of these phases, or null for all phases."
    )
    overall_status: list[OverallStatus] | None = Field(
        description="Keep trials with any of these statuses, or null for all statuses."
    )
    study_type: StudyType | None = Field(description="Restrict study type, or null.")


class Cohort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="Short display label, e.g. 'pembrolizumab' or 'breast cancer'.")
    filters: CohortFilters


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: OperationKind
    dimension: Dimension | None = Field(
        description="count_by: the grouping dimension. network: the first node type. "
        "time_trend: null."
    )
    second_dimension: Dimension | None = Field(
        description="network: the second node type (same as dimension for a co-occurrence "
        "network). count_by: optional series split, only with a single cohort. Otherwise null."
    )


class TimeScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_basis: DateBasis = Field(
        description="Which trial date defines 'year'. start_date unless the user asks about "
        "registration/posting (first_posted) or completion (completion_date)."
    )
    year_from: int | None = Field(description="Inclusive first year, or null.")
    year_to: int | None = Field(description="Inclusive last year, or null.")


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(description="One question that resolves the ambiguity.")
    options: list[str] = Field(description="2-4 concrete answers the user could pick.")


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cohorts: list[Cohort] = Field(
        description="One cohort per compared group (1-4). Shared filters repeat in each cohort."
    )
    operation: Operation
    time: TimeScope
    phase_policy: PhasePolicy = Field(
        description="combined unless the user asks to count each phase of multi-phase trials."
    )
    top_n: int | None = Field(
        description="Show only the N largest categories/nodes (1-100), or null for the default."
    )
    clarification: Clarification | None = Field(
        description="Set ONLY when a missing detail would materially change the answer and no "
        "sensible default exists. Otherwise null."
    )
    unsupported_reason: str | None = Field(
        description="Set when the question needs data or analysis this system does not support "
        "(e.g. efficacy, adverse events, patient eligibility). Otherwise null."
    )
