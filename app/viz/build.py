"""Analysis result → typed VisualizationSpec + render metadata. The chart type is chosen here
by deterministic rules (never by the model); titles, summaries and policy notes are generated
from the accepted plan, so no model prose reaches the output."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.analytics.types import (
    AnalysisResult,
    Bucket,
    CohortTrials,
    CountResult,
    NetworkResult,
    ScatterResult,
    Trial,
)
from app.contracts.enums import ChartType, Dimension, OperationKind, PhasePolicy
from app.contracts.plan import QueryPlan
from app.contracts.response import (
    BarChartSpec,
    CartesianEncoding,
    Channel,
    Citation,
    Datum,
    EdgeDatum,
    Evidence,
    GroupedBarChartSpec,
    HistogramSpec,
    NetworkData,
    NetworkEncoding,
    NetworkGraphSpec,
    NodeDatum,
    PieChartSpec,
    PieEncoding,
    ScatterPlotSpec,
    StackedBarChartSpec,
    TimeSeriesSpec,
    Truncation,
    VisualizationSpec,
)
from app.registry import DATE_LABELS, MEASURES, REGISTRY, study_url

Y = Channel(field="trial_count", type="quantitative", title="Trials", unit="trials")
MAX_PIE_SLICES = 12


@dataclass
class BuiltSpec:
    spec: VisualizationSpec
    truncation: Truncation | None
    sort: str | None
    chart_selection: str
    notes: list[str]
    evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # full sets


class CitationFactory:
    """Inline citations (first N by NCT ID, newest first) plus the full set per datum, which
    the run store keeps for GET /v1/runs/{id}/evidence."""

    def __init__(self, cohorts: Iterable[CohortTrials], per_datum: int) -> None:
        self.trials: dict[str, Trial] = {t.nct_id: t for c in cohorts for t in c.trials}
        self.per_datum = per_datum
        self.full: dict[str, list[dict[str, Any]]] = {}

    def citation(self, nct: str, bucket: Bucket) -> Citation:
        trial = self.trials.get(nct)
        return Citation(
            nct_id=nct,
            url=study_url(nct),
            brief_title=trial.brief_title if trial else None,
            evidence=[Evidence(field_path=p, excerpt=v) for p, v in bucket.contributors[nct]],
        )

    def datum_fields(self, datum_id: str, bucket: Bucket) -> dict[str, Any]:
        ids = sorted(bucket.contributors, reverse=True)  # deterministic; recent trials first
        shown = [self.citation(nct, bucket) for nct in ids[: self.per_datum]]
        self.full[datum_id] = [
            {
                "nct_id": nct,
                "url": study_url(nct),
                "brief_title": self.trials[nct].brief_title if nct in self.trials else None,
                "evidence": [{"field_path": p, "excerpt": v} for p, v in bucket.contributors[nct]],
            }
            for nct in ids
        ]
        return {
            "datum_id": datum_id,
            "trial_count": bucket.count,
            "citation_count": len(ids),
            "citations_truncated": len(shown) < len(ids),
            "citations": shown,
        }


# --- Chart selection ------------------------------------------------------------------------


def choose_chart(
    plan: QueryPlan, result: AnalysisResult, preferred: ChartType | None
) -> tuple[ChartType, str]:
    """Deterministic chart rules. A preference is honored only when the data supports it."""
    if isinstance(result, NetworkResult):
        return ChartType.NETWORK_GRAPH, "network operation → network_graph"
    if isinstance(result, ScatterResult):
        return ChartType.SCATTER_PLOT, "two per-trial measures → scatter_plot (one point/trial)"
    if result.kind == "histogram":
        return ChartType.HISTOGRAM, "binned per-trial measure → histogram"

    has_series = result.series_source is not None
    if result.kind == "time_trend":
        default = ChartType.TIME_SERIES
        reason = "counts per year → time_series" + (" (one line per series)" if has_series else "")
    elif has_series:
        default = ChartType.GROUPED_BAR_CHART
        reason = "counts per category and series → grouped_bar_chart"
    else:
        default = ChartType.BAR_CHART
        reason = "counts per category → bar_chart"
    if preferred is None or preferred is default:
        return default, reason

    allowed: dict[ChartType, str | None] = {}
    if has_series:
        allowed[ChartType.GROUPED_BAR_CHART] = None
        allowed[ChartType.STACKED_BAR_CHART] = (
            None
            if result.series_exclusive
            else "series overlap (a trial can be in several), so stacks would double-count"
        )
    else:
        allowed[ChartType.BAR_CHART] = None
        if result.kind == "count_by":
            allowed[ChartType.PIE_CHART] = (
                "categories overlap (a trial can have several values), so slices would not sum "
                "to the whole"
                if not result.categories_exclusive
                else (
                    f"more than {MAX_PIE_SLICES} categories"
                    if len(result.category_order) > MAX_PIE_SLICES
                    else None
                )
            )
    if result.kind == "time_trend":
        allowed[ChartType.TIME_SERIES] = None

    if preferred in allowed and allowed[preferred] is None:
        return preferred, f"preferred_visualization={preferred.value} honored (compatible)"
    why = allowed.get(preferred) or "not a valid rendering of this analysis"
    return default, f"{reason}; preferred {preferred.value} not used: {why}"


# --- Builders -------------------------------------------------------------------------------


def build_spec(
    plan: QueryPlan,
    result: AnalysisResult,
    cohorts: list[CohortTrials],
    citations_per_datum: int,
    preferred: ChartType | None,
) -> BuiltSpec:
    cite = CitationFactory(cohorts, citations_per_datum)
    chart, reason = choose_chart(plan, result, preferred)
    title = _title(plan)
    if isinstance(result, NetworkResult):
        built = _network(title, result, cite)
    elif isinstance(result, ScatterResult):
        built = _scatter(plan, title, result, cite)
    else:
        built = _counts(plan, title, result, cite, chart)
    built.chart_selection = reason
    built.evidence = cite.full
    if preferred is not None and preferred.value != built.spec.type:
        built.notes.append(f"Chart: {reason}.")
    return built


def _series_channel(plan: QueryPlan, result: CountResult) -> Channel | None:
    if result.series_source == "cohort":
        return Channel(field="cohort", type="nominal", title="Cohort", sort=result.series_order)
    if result.series_source == "dimension" and plan.operation.second_dimension:
        dim = REGISTRY[plan.operation.second_dimension]
        return Channel(
            field=dim.name.value,
            type="ordinal" if dim.order else "nominal",
            title=dim.label,
            sort=result.series_order,
        )
    return None


def _counts(
    plan: QueryPlan, title: str, result: CountResult, cite: CitationFactory, chart: ChartType
) -> BuiltSpec:
    notes: list[str] = []
    series = _series_channel(plan, result)
    horizontal = False
    if result.kind == "time_trend":
        temporal = chart is ChartType.TIME_SERIES
        x = Channel(
            field="year",
            type="temporal" if temporal else "ordinal",
            title=f"Year ({DATE_LABELS[plan.time.date_basis]})",
            sort="ascending",
        )
    elif result.kind == "histogram":
        assert plan.operation.measure is not None
        m = MEASURES[plan.operation.measure]
        x = Channel(
            field="bin",
            type="ordinal",
            title=f"{m.label} ({m.unit})" if m.unit else m.label,
            unit=m.unit,
            sort=result.category_order,
        )
    else:
        assert plan.operation.dimension is not None
        dim = REGISTRY[plan.operation.dimension]
        horizontal = dim.kind == "entity"
        x = Channel(
            field=dim.name.value,
            type="ordinal" if dim.order else "nominal",
            title=dim.label,
            sort=result.category_order,
        )

    data: list[Datum] = []
    for i, row in enumerate(result.rows, start=1):
        fields = cite.datum_fields(f"d{i}", row.bucket)
        if result.kind == "time_trend":
            fields["year"] = int(row.key)
            fields["estimated_date_count"] = row.bucket.extra.get("estimated_date_count", 0)
        else:
            fields[x.field] = row.label
        fields.update(row.fields)
        if series is not None:
            fields[series.field] = row.series
        data.append(Datum(**fields))

    spec: VisualizationSpec
    orientation = "horizontal" if horizontal else "vertical"
    if chart is ChartType.PIE_CHART:
        spec = PieChartSpec(
            title=title, encoding=PieEncoding(theta=Y, color=x.model_copy()), data=data
        )
    else:
        encoding = CartesianEncoding(x=x, y=Y, series=series)
        if chart is ChartType.TIME_SERIES:
            spec = TimeSeriesSpec(title=title, encoding=encoding, data=data)
        elif chart is ChartType.HISTOGRAM:
            assert plan.operation.measure is not None
            edges = MEASURES[plan.operation.measure].bin_edges or ()
            spec = HistogramSpec(title=title, encoding=encoding, bin_edges=list(edges), data=data)
        elif chart is ChartType.STACKED_BAR_CHART:
            spec = StackedBarChartSpec(
                title=title, orientation=orientation, encoding=encoding, data=data
            )
        elif series is not None:
            spec = GroupedBarChartSpec(
                title=title, orientation=orientation, encoding=encoding, data=data
            )
        else:
            spec = BarChartSpec(title=title, orientation=orientation, encoding=encoding, data=data)

    if result.kind == "time_trend" and result.category_order:
        this_year = datetime.now(UTC).year
        if int(result.category_order[-1]) >= this_year:
            notes.append(
                f"{this_year} is the current year, so its count is partial; any later years "
                "contain only trials with estimated (anticipated) dates."
            )
    truncation = Truncation(**vars(result.truncation)) if result.truncation is not None else None
    return BuiltSpec(spec, truncation, result.sort_description, "", notes)


def _scatter(
    plan: QueryPlan, title: str, result: ScatterResult, cite: CitationFactory
) -> BuiltSpec:
    op = plan.operation
    assert op.measure is not None and op.x_measure is not None
    xm, ym = MEASURES[op.x_measure], MEASURES[op.measure]
    x = Channel(field=xm.name.value, type=xm.kind, title=xm.label, unit=xm.unit)
    y = Channel(field=ym.name.value, type=ym.kind, title=ym.label, unit=ym.unit)
    color: Channel | None = None
    if len(plan.cohorts) > 1:
        color = Channel(field="cohort", type="nominal", title="Cohort", sort=result.color_order)
    elif op.dimension is not None:
        dim = REGISTRY[op.dimension]
        color = Channel(
            field=dim.name.value, type="nominal", title=dim.label, sort=result.color_order
        )

    data: list[Datum] = []
    for i, point in enumerate(result.points, start=1):
        fields = cite.datum_fields(f"p{i}", point.bucket)
        fields["nct_id"] = point.trial.nct_id
        fields[x.field] = point.x.value
        fields[y.field] = point.y.value
        if color is not None:
            fields[color.field] = point.color
        data.append(Datum(**fields))
    spec = ScatterPlotSpec(
        title=title, encoding=CartesianEncoding(x=x, y=y, series=color), data=data
    )
    truncation = Truncation(**vars(result.truncation)) if result.truncation else None
    return BuiltSpec(spec, truncation, f"{xm.label} ascending", "", [])


def _network(title: str, result: NetworkResult, cite: CitationFactory) -> BuiltSpec:
    nodes = [
        NodeDatum(
            id=n.id,
            label=n.bucket.label,
            entity_type=n.entity_type,
            **cite.datum_fields(f"n{i}", n.bucket),
        )
        for i, n in enumerate(result.nodes, start=1)
    ]
    edges = [
        EdgeDatum(
            source=e.source,
            target=e.target,
            relation=e.relation,
            **cite.datum_fields(f"e{i}", e.bucket),
        )
        for i, e in enumerate(result.edges, start=1)
    ]
    chart = NetworkGraphSpec(
        title=title,
        bipartite=result.bipartite,
        encoding=NetworkEncoding(
            nodes={"id": "id", "label": "label", "group": "entity_type", "size": "trial_count"},
            edges={"source": "source", "target": "target", "weight": "trial_count"},
        ),
        data=NetworkData(nodes=nodes, edges=edges),
    )
    truncation = Truncation(**vars(result.truncation)) if result.truncation else None
    return BuiltSpec(chart, truncation, "Edges by shared-trial count, descending", "", [])


# --- Deterministic text ---------------------------------------------------------------------


def _scope(plan: QueryPlan) -> str:
    scope = " vs ".join(c.label for c in plan.cohorts)
    t = plan.time
    if t.year_from is not None and t.year_to is not None:
        scope += f", {t.year_from}–{t.year_to}"
    elif t.year_from is not None:
        scope += f", since {t.year_from}"
    elif t.year_to is not None:
        scope += f", through {t.year_to}"
    return scope


def _title(plan: QueryPlan) -> str:
    op = plan.operation
    if op.kind is OperationKind.TIME_TREND:
        head = f"Trials per year by {DATE_LABELS[plan.time.date_basis]}"
        if op.second_dimension:
            head += f", by {REGISTRY[op.second_dimension].label.lower()}"
    elif op.kind is OperationKind.NETWORK:
        assert op.dimension and op.second_dimension
        a, b = REGISTRY[op.dimension].label, REGISTRY[op.second_dimension].label
        head = f"{a} co-occurrence network" if a == b else f"{a} ↔ {b} network"
    elif op.kind is OperationKind.HISTOGRAM:
        assert op.measure
        head = f"Distribution of trials by {MEASURES[op.measure].label.lower()}"
    elif op.kind is OperationKind.SCATTER:
        assert op.measure and op.x_measure
        head = f"{MEASURES[op.measure].label} vs {MEASURES[op.x_measure].label.lower()}"
    else:
        assert op.dimension
        head = f"Trials by {REGISTRY[op.dimension].label.lower()}"
        if op.second_dimension:
            head += f" and {REGISTRY[op.second_dimension].label.lower()}"
    return f"{head} — {_scope(plan)}"


def summarize(plan: QueryPlan) -> str:
    op = plan.operation
    if op.kind is OperationKind.TIME_TREND:
        what = f"distinct trials per year of {DATE_LABELS[plan.time.date_basis]}"
        if op.second_dimension:
            what += f", split by {op.second_dimension.value}"
    elif op.kind is OperationKind.NETWORK:
        assert op.dimension and op.second_dimension
        what = (
            f"a network linking {op.dimension.value} and {op.second_dimension.value} "
            "values that appear in the same trial"
        )
    elif op.kind is OperationKind.HISTOGRAM:
        assert op.measure
        what = f"distinct trials per {op.measure.value} bin"
    elif op.kind is OperationKind.SCATTER:
        assert op.measure and op.x_measure
        what = f"one point per trial: {op.measure.value} against {op.x_measure.value}"
        if op.dimension:
            what += f", coloured by {op.dimension.value}"
    else:
        assert op.dimension
        what = f"distinct trials by {op.dimension.value}"
        if op.second_dimension:
            what += f", split by {op.second_dimension.value}"
    groups = "; ".join(
        f"'{c.label}' ("
        + (
            ", ".join(
                f"{k}={v}" for k, v in c.filters.model_dump(mode="json", exclude_none=True).items()
            )
            or "no filters"
        )
        + ")"
        for c in plan.cohorts
    )
    text = f"Computed {what} for {len(plan.cohorts)} cohort(s): {groups}."
    t = plan.time
    if t.year_from is not None or t.year_to is not None:
        text += (
            f" Restricted to {DATE_LABELS[t.date_basis]} years "
            f"{t.year_from if t.year_from is not None else 'earliest'}–"
            f"{t.year_to if t.year_to is not None else 'latest'}."
        )
    return text


def policies(plan: QueryPlan) -> dict[str, str]:
    op = plan.operation
    dims = {op.dimension, op.second_dimension} - {None}
    measures = {op.measure, op.x_measure} - {None}
    out = {
        "counting": "Every value is a count of distinct trials (NCT IDs); a trial counts at "
        "most once per datum.",
        "missing_values": "Trials with no value for the analysed field are not charted; "
        "see meta.cohorts[].missing.",
    }
    if any(c.filters.drug_name for c in plan.cohorts):
        out["drug_matching"] = (
            "drug_name matches InterventionName or InterventionOtherName (field-scoped search, "
            "with ClinicalTrials.gov synonym expansion). Trials that only mention the drug "
            "elsewhere, e.g. as prior therapy, are not included."
        )
    if any(c.filters.condition for c in plan.cohorts):
        out["condition_matching"] = (
            "condition uses ClinicalTrials.gov condition search (query.cond, phrase), which "
            "includes synonyms and narrower terms."
        )
    if Dimension.PHASE in dims:
        out["phase"] = (
            "Each phase of a multi-phase trial is counted separately (groups overlap)."
            if plan.phase_policy is PhasePolicy.SPLIT
            else "Multi-phase trials form their own combined category, e.g. Phase 1/2."
        )
    if op.kind is OperationKind.TIME_TREND or plan.time.year_from or plan.time.year_to:
        out["date_basis"] = (
            f"Years come from the {DATE_LABELS[plan.time.date_basis]}. Month-precision dates are "
            "bucketed by year. Dates the registry marks ESTIMATED (anticipated, or never "
            "updated to actual) are included and counted per year in estimated_date_count."
        )
    if Dimension.COUNTRY in dims:
        out["country"] = (
            "A multi-country trial counts once in each listed country. Counts are trials, "
            "not sites or patients."
        )
    if Dimension.SITE in dims:
        out["site"] = (
            "Sites are facility names as registered (case-insensitive; sponsor site numbers "
            "removed). The same institution spelled differently stays separate."
        )
    if Dimension.DRUG in dims:
        out["drug_dimension"] = (
            "Drugs are interventions of type DRUG, BIOLOGICAL or COMBINATION_PRODUCT, excluding "
            "placebos. Names are grouped case-insensitively with dose and salt suffixes removed "
            "(e.g. 'Erlotinib Hydrochloride' = 'erlotinib'); brand and generic names are not "
            "merged."
        )
    if "enrollment" in {m.value for m in measures if m}:
        out["enrollment"] = (
            "Enrollment is the registered count: ACTUAL for finished trials, ESTIMATED (planned) "
            "otherwise; both are included and each citation shows which."
        )
    if "duration_months" in {m.value for m in measures if m}:
        out["duration"] = (
            "Duration = start date → primary completion date, in months; needs month-precision "
            "dates on both ends (otherwise counted as missing). Anticipated dates are included."
        )
    if op.kind is OperationKind.HISTOGRAM:
        out["bins"] = (
            "Bins are declared in advance with unequal widths (heavy-tailed values); each bin "
            "is [bin_start, bin_end) and the last bin is open-ended."
        )
    if op.kind is OperationKind.NETWORK:
        out["network"] = (
            "Edge weight = distinct trials listing both entities. Co-listing in one trial "
            "record does not prove the drugs were given together."
        )
    if len(plan.cohorts) > 1:
        out["cohort_overlap"] = (
            "A trial matching several cohorts counts in each; see meta.cohort_overlap."
        )
    return out


def assumptions(plan: QueryPlan) -> list[str]:
    notes = []
    if (
        plan.operation.kind is OperationKind.TIME_TREND
        and plan.time.date_basis.value == "start_date"
    ):
        notes.append("'Over time' is measured by study start year (the default date basis).")
    return notes
