"""The QueryPlan: the only thing the LLM produces.

This model is sent to OpenAI as a strict Structured Outputs schema, so it follows the strict
mode rules: every field is required (nullable where optional), there are no defaults and no
numeric/string constraints. Range and cross-field checks happen in app.planner.validate.

The plan is a small composable language, not a list of question types: free-text filters take
value lists (any-of) and exclusions, a class name ("PD-1 inhibitors") is expanded into explicit
members that are each grounded, and anything the language cannot express must be declared in
`unhandled_constraints` instead of being dropped.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.enums import (
    DateBasis,
    Dimension,
    Measure,
    OperationKind,
    OverallStatus,
    Phase,
    PhasePolicy,
    StudyType,
)

TextField = Literal["drug_names", "conditions", "sponsors", "countries"]
TEXT_FIELDS: tuple[TextField, ...] = ("drug_names", "conditions", "sponsors", "countries")
EXCLUDE_FIELDS = {name: f"exclude_{name}" for name in TEXT_FIELDS}
# The request uses the brief's singular names; each maps to one list field of the plan.
REQUEST_TO_PLAN: dict[str, TextField] = {
    "drug_name": "drug_names",
    "condition": "conditions",
    "sponsor": "sponsors",
    "country": "countries",
}
# Dimensions whose values can be restricted to the cohort's own list (only_listed_values).
LISTED_DIMENSION: dict[Dimension, TextField] = {
    Dimension.DRUG: "drug_names",
    Dimension.CONDITION: "conditions",
    Dimension.LEAD_SPONSOR: "sponsors",
    Dimension.COUNTRY: "countries",
}


class CohortFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drug_names: list[str] | None = Field(
        description="Trials listing ANY of these drugs/interventions, as the user named them "
        "(generic, brand or code; the registry search expands synonyms). For a drug class "
        "('PD-1 inhibitors') list its member drugs and record the class in plan.expansions. "
        "Null if unconstrained."
    )
    conditions: list[str] | None = Field(
        description="Trials for ANY of these conditions/diseases, or null."
    )
    sponsors: list[str] | None = Field(description="Lead sponsor is ANY of these, or null.")
    countries: list[str] | None = Field(
        description="At least one trial location in ANY of these countries (English names), "
        "or null."
    )
    trial_phase: list[Phase] | None = Field(
        description="Keep trials in any of these phases, or null for all phases."
    )
    overall_status: list[OverallStatus] | None = Field(
        description="Keep trials with any of these statuses, or null for all statuses."
    )
    study_type: StudyType | None = Field(description="Restrict study type, or null.")
    exclude_drug_names: list[str] | None = Field(
        description="Drop trials listing any of these drugs ('excluding Keytruda'), or null."
    )
    exclude_conditions: list[str] | None = Field(
        description="Drop trials for any of these conditions, or null."
    )
    exclude_sponsors: list[str] | None = Field(
        description="Drop trials whose lead sponsor is any of these, or null."
    )
    exclude_countries: list[str] | None = Field(
        description="Drop trials with a location in any of these countries ('outside the US'), "
        "or null."
    )


class Cohort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        description="Short working label. The backend replaces it with a name generated from "
        "the filters."
    )
    filters: CohortFilters


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: OperationKind
    dimension: Dimension | None = Field(
        description="count_by: the grouping dimension. network: the first node type. "
        "scatter: optional colour category. time_trend, histogram: null."
    )
    second_dimension: Dimension | None = Field(
        description="network: the second node type (same as dimension for a co-occurrence "
        "network). count_by, time_trend: optional series split, only with a single cohort. "
        "Otherwise null."
    )
    measure: Measure | None = Field(
        description="histogram: the value to bin. scatter: the y axis. Otherwise null."
    )
    x_measure: Measure | None = Field(description="scatter: the x axis. Otherwise null.")
    only_listed_values: bool = Field(
        description="True when grouping by drug, condition, lead_sponsor or country should count "
        "only the values in the cohort's own list for that field (e.g. rank the listed PD-1 "
        "inhibitors and ignore co-listed chemotherapy). Otherwise false."
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


class Expansion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str = Field(
        description="The class or group as the user wrote it, e.g. 'PD-1 inhibitors'."
    )
    field: Literal["drug_names", "conditions"] = Field(description="Filter list it expands into.")
    members: list[str] = Field(description="The explicit members used, e.g. generic drug names.")


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
    expansions: list[Expansion] = Field(
        description="Every class or group name you replaced by explicit members. Empty if none."
    )
    unhandled_constraints: list[str] = Field(
        description="Parts of the question this plan does NOT apply (e.g. 'only placebo-"
        "controlled trials'). Never drop a constraint silently. Empty if everything is applied."
    )
    clarification: Clarification | None = Field(
        description="Set ONLY when a missing detail would materially change the answer and no "
        "sensible default exists. Otherwise null."
    )
    unsupported_reason: str | None = Field(
        description="Set when the question needs data or analysis this system does not support "
        "(e.g. efficacy, adverse events, patient eligibility). Otherwise null."
    )
