"""Public response schema: the visualization spec a frontend renders without guessing.

Cartesian charts follow a Vega-Lite-like shape: `data` is a flat list of rows and `encoding`
maps row fields to visual channels by name. Every row/node/edge is a datum that carries the
NCT IDs and exact source field values that produced it.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.enums import Status
from app.contracts.plan import Clarification, QueryPlan, TimeScope

SCHEMA_VERSION: Literal["1.0"] = "1.0"


# --- Citations -----------------------------------------------------------------------------


class Evidence(BaseModel):
    field_path: str = Field(
        description="JSON path inside the ClinicalTrials.gov v2 study record, "
        "e.g. protocolSection.designModule.phases"
    )
    value: Any = Field(description="The exact value found at field_path in the API response.")


class Citation(BaseModel):
    nct_id: str
    url: str = Field(description="Study page on clinicaltrials.gov.")
    brief_title: str | None = Field(description="Exact briefTitle from the API response.")
    evidence: list[Evidence] = Field(description="Field values that place this trial in the datum.")


class Datum(BaseModel):
    """Fields common to every bar, time bucket, node and edge."""

    model_config = ConfigDict(extra="allow")

    datum_id: str
    trial_count: int = Field(description="Distinct trials (NCT IDs) behind this datum.")
    citation_count: int = Field(description="Total supporting trials; equals trial_count.")
    citations_truncated: bool = Field(description="True when citations lists fewer than all.")
    citations: list[Citation]


# --- Encodings -----------------------------------------------------------------------------


class Channel(BaseModel):
    field: str = Field(description="Key in each data row.")
    type: Literal["nominal", "ordinal", "quantitative", "temporal"]
    title: str
    unit: str | None = None
    sort: list[str] | Literal["ascending", "descending"] | None = Field(
        default=None, description="Explicit category order, or a direction."
    )


class CartesianEncoding(BaseModel):
    x: Channel
    y: Channel
    series: Channel | None = Field(default=None, description="Colour/grouping channel.")


class BarChartSpec(BaseModel):
    type: Literal["bar_chart"] = "bar_chart"
    title: str
    encoding: CartesianEncoding
    data: list[Datum]


class GroupedBarChartSpec(BaseModel):
    type: Literal["grouped_bar_chart"] = "grouped_bar_chart"
    title: str
    encoding: CartesianEncoding
    data: list[Datum]


class TimeSeriesSpec(BaseModel):
    type: Literal["time_series"] = "time_series"
    title: str
    encoding: CartesianEncoding
    data: list[Datum]


class NodeDatum(Datum):
    id: str
    label: str
    entity_type: str = Field(description="Registry dimension this node comes from, e.g. drug.")


class EdgeDatum(Datum):
    source: str = Field(description="Node id.")
    target: str = Field(description="Node id.")
    relation: str = Field(description="e.g. 'lead_sponsor–drug' or 'drug co-listed with drug'.")


class NetworkEncoding(BaseModel):
    nodes: dict[str, str] = Field(
        description="Node channel → node field: id, label, group, size.",
        examples=[{"id": "id", "label": "label", "group": "entity_type", "size": "trial_count"}],
    )
    edges: dict[str, str] = Field(
        description="Edge channel → edge field: source, target, weight.",
        examples=[{"source": "source", "target": "target", "weight": "trial_count"}],
    )
    directed: bool = False


class NetworkData(BaseModel):
    nodes: list[NodeDatum]
    edges: list[EdgeDatum]


class NetworkGraphSpec(BaseModel):
    type: Literal["network_graph"] = "network_graph"
    title: str
    encoding: NetworkEncoding
    data: NetworkData


VisualizationSpec = Annotated[
    BarChartSpec | GroupedBarChartSpec | TimeSeriesSpec | NetworkGraphSpec,
    Field(discriminator="type"),
]


# --- Metadata ------------------------------------------------------------------------------


class CohortMeta(BaseModel):
    label: str
    filters: dict[str, Any] = Field(description="Effective filters for this cohort.")
    api_params: dict[str, str] = Field(description="Exact ClinicalTrials.gov query parameters.")
    total_matches: int = Field(description="Trials the API reported for this query.")
    records_fetched: int
    trials_analyzed: int = Field(description="Distinct trials left after local post-filters.")
    excluded: dict[str, int] = Field(default_factory=dict, description="Reason → trial count.")
    missing: dict[str, int] = Field(
        default_factory=dict, description="Analyzed field → trials with no value (not charted)."
    )
    complete: bool = Field(description="True when pagination finished and nothing was skipped.")


class Truncation(BaseModel):
    shown: int
    total: int
    rule: str


class SourceInfo(BaseModel):
    name: str = "ClinicalTrials.gov API v2"
    api_version: str | None = None
    data_timestamp: str | None = Field(default=None, description="Registry data refresh time.")
    retrieved_at: str


class Interpretation(BaseModel):
    summary: str = Field(description="Deterministic restatement of the accepted plan.")
    plan: QueryPlan
    planner_model: str
    planner_attempts: int


class Meta(BaseModel):
    interpretation: Interpretation | None = None
    cohorts: list[CohortMeta] = Field(default_factory=list)
    time: TimeScope | None = None
    measure: str = "Distinct trials (NCT IDs) per datum"
    units: dict[str, str] = Field(default_factory=lambda: {"trial_count": "trials"})
    sort: str | None = None
    time_granularity: Literal["year"] | None = None
    policies: dict[str, str] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    cohort_overlap: dict[str, int] | None = Field(
        default=None, description="'A ∩ B' → trials counted in both cohorts."
    )
    truncation: Truncation | None = None
    source: SourceInfo | None = None
    timings_ms: dict[str, int] = Field(default_factory=dict)


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[str] = Field(default_factory=list)


class VisualizationResponse(BaseModel):
    """Envelope. `status` decides which of visualization / clarification / error is set:

    ok → visualization; empty → visualization with no data; needs_clarification →
    clarification; unsupported / failed → error.
    """

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    status: Status
    visualization: VisualizationSpec | None = None
    meta: Meta = Field(default_factory=Meta)
    clarification: Clarification | None = None
    error: ErrorInfo | None = None
