"""Public response schema: the visualization spec a frontend renders without guessing.

Cartesian charts follow a Vega-Lite-like shape: `data` is a flat list of rows and `encoding`
maps row fields to visual channels by name. Every row/point/node/edge is a datum that carries
the NCT IDs and exact source field values that produced it.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.enums import Status
from app.contracts.plan import Clarification, QueryPlan, TimeScope

# 1.1: list-valued cohort filters, exclusions, expansions, count_method/source_query,
# normalization metadata. Stored 1.0 records are upgraded on read (app.storage.migrate).
SCHEMA_VERSION: Literal["1.1"] = "1.1"


# --- Citations -----------------------------------------------------------------------------


class Evidence(BaseModel):
    field_path: str = Field(
        description="JSON path inside the ClinicalTrials.gov v2 study record, "
        "e.g. protocolSection.designModule.phases or "
        "protocolSection.armsInterventionsModule.interventions[1].name"
    )
    excerpt: Any = Field(
        description="The exact value found at field_path in the API response, verbatim "
        "(a string for text fields; a list or number where the API returns one)."
    )


class Citation(BaseModel):
    nct_id: str
    url: str = Field(description="Study page on clinicaltrials.gov.")
    brief_title: str | None = Field(description="Exact briefTitle from the API response.")
    evidence: list[Evidence] = Field(
        description="Every field value that places this trial in the datum: why it belongs to "
        "the cohort (e.g. the matching intervention name) and why it falls in this bar, bucket, "
        "bin, point, node or edge."
    )


class Datum(BaseModel):
    """Fields common to every bar, time bucket, bin, point, node and edge."""

    model_config = ConfigDict(extra="allow")

    datum_id: str = Field(description="Stable id; GET /v1/runs/{run_id}/evidence?datum_id=…")
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
    series: Channel | None = Field(
        default=None, description="Colour channel: bar groups/stacks, lines, or point colour."
    )


class PieEncoding(BaseModel):
    theta: Channel = Field(description="Slice size.")
    color: Channel = Field(description="Slice category.")


class BarChartSpec(BaseModel):
    type: Literal["bar_chart"] = "bar_chart"
    title: str
    orientation: Literal["vertical", "horizontal"] = Field(
        default="vertical",
        description="horizontal: categories on the vertical axis (long entity names). "
        "Encoding x/y still name the category and value fields.",
    )
    encoding: CartesianEncoding
    data: list[Datum]


class GroupedBarChartSpec(BaseModel):
    type: Literal["grouped_bar_chart"] = "grouped_bar_chart"
    title: str
    orientation: Literal["vertical", "horizontal"] = "vertical"
    encoding: CartesianEncoding = Field(description="series is required: one bar per series.")
    data: list[Datum]


class StackedBarChartSpec(BaseModel):
    type: Literal["stacked_bar_chart"] = "stacked_bar_chart"
    title: str
    orientation: Literal["vertical", "horizontal"] = "vertical"
    encoding: CartesianEncoding = Field(
        description="series is required: one stack segment per series. Only produced when the "
        "series are mutually exclusive, so a stack's height is a real trial total."
    )
    data: list[Datum]


class PieChartSpec(BaseModel):
    type: Literal["pie_chart"] = "pie_chart"
    title: str
    encoding: PieEncoding
    data: list[Datum] = Field(
        description="Slices of mutually exclusive categories (each trial in exactly one); "
        "trials with no value are excluded and counted in meta.cohorts[].missing."
    )


class TimeSeriesSpec(BaseModel):
    type: Literal["time_series"] = "time_series"
    title: str
    time_granularity: Literal["year"] = "year"
    encoding: CartesianEncoding = Field(description="x is the year; series (optional) = lines.")
    data: list[Datum]


class HistogramSpec(BaseModel):
    type: Literal["histogram"] = "histogram"
    title: str
    encoding: CartesianEncoding = Field(
        description="x is bin_label (ordinal, in bin order). Each row also has numeric "
        "bin_start (inclusive) and bin_end (exclusive; null for the open last bin)."
    )
    bin_edges: list[float] = Field(description="Declared bin edges; bins are [edge_i, edge_i+1).")
    data: list[Datum]


class ScatterPlotSpec(BaseModel):
    type: Literal["scatter_plot"] = "scatter_plot"
    title: str
    encoding: CartesianEncoding = Field(description="One point per trial; series = colour.")
    data: list[Datum] = Field(description="Each point is one trial (trial_count = 1).")


class NodeDatum(Datum):
    id: str
    label: str
    entity_type: str = Field(description="Registry dimension this node comes from, e.g. drug.")


class EdgeDatum(Datum):
    source: str = Field(description="Node id.")
    target: str = Field(description="Node id.")
    relation: str = Field(description="e.g. 'lead_sponsor–drug in the same trial'.")


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
    bipartite: bool = Field(description="True when nodes come from two different dimensions.")
    encoding: NetworkEncoding
    data: NetworkData


VisualizationSpec = Annotated[
    BarChartSpec
    | GroupedBarChartSpec
    | StackedBarChartSpec
    | PieChartSpec
    | TimeSeriesSpec
    | HistogramSpec
    | ScatterPlotSpec
    | NetworkGraphSpec,
    Field(discriminator="type"),
]

CARTESIAN_SPECS = (
    BarChartSpec,
    GroupedBarChartSpec,
    StackedBarChartSpec,
    TimeSeriesSpec,
    HistogramSpec,
    ScatterPlotSpec,
)


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
    synonym_matches: dict[str, int] = Field(
        default_factory=dict,
        description="Free-text filter → trials the registry matched through synonym expansion "
        "(e.g. MK-3475 for pembrolizumab); their citations carry no literal match for it.",
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
    repair_feedback: list[str] = Field(
        default_factory=list,
        description="Validation/grounding errors the planner was asked to fix (empty when its "
        "first plan was accepted).",
    )
    parent_run_id: str | None = Field(default=None, description="Set for follow-up questions.")
    plan_diff: dict[str, Any] | None = Field(
        default=None,
        description="Follow-ups only: plan path → {'before': …, 'after': …} vs the parent plan.",
    )


class NameMerge(BaseModel):
    canonical: str
    variants: list[str] = Field(description="Raw registry spellings grouped under canonical.")


class Normalization(BaseModel):
    """What the name normalizer changed for one dimension (citations keep raw values)."""

    dimension: str
    model: str
    names_in: int = Field(description="Distinct raw names seen.")
    names_sent: int = Field(description="Most frequent names sent to the model (rarer keep raw).")
    names_unmapped: int = Field(
        description="Sent names left raw because the model's answer failed validation."
    )
    names_mapped: int = Field(description="Names the model changed (merged, split or dropped).")
    dropped: list[str] = Field(description="Raw names judged not to be a drug/condition.")
    merges: list[NameMerge] = Field(description="Largest merges, most variants first.")


class Meta(BaseModel):
    interpretation: Interpretation | None = None
    chart_selection: str | None = Field(
        default=None, description="Why this chart type was chosen (deterministic rule)."
    )
    cohorts: list[CohortMeta] = Field(default_factory=list)
    time: TimeScope | None = None
    measure: str = "Distinct trials (NCT IDs) per datum"
    count_method: Literal["fetched", "server_count"] = Field(
        default="fetched",
        description="fetched: every trial was downloaded and counted here (full citation sets). "
        "server_count: the cohort was too large to fetch, so each datum is the registry's own "
        "totalCount for its source_query and citations are samples.",
    )
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
    llm_usage: dict[str, int] = Field(
        default_factory=dict, description="Planner tokens: input, output, calls."
    )
    normalization: list[Normalization] = Field(
        default_factory=list,
        description="Drug/condition names normalized by a small model before grouping.",
    )


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[str] = Field(default_factory=list)


class VisualizationResponse(BaseModel):
    """Envelope. `status` decides which of visualization / clarification / error is set:

    ok → visualization; empty → visualization with no data; needs_clarification →
    clarification; unsupported / failed → error.
    """

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    run_id: str
    status: Status
    visualization: VisualizationSpec | None = None
    meta: Meta = Field(default_factory=Meta)
    clarification: Clarification | None = None
    error: ErrorInfo | None = None
