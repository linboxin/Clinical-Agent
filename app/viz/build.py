"""Analysis result → typed VisualizationSpec + render metadata. Titles, summaries and policy
notes are generated deterministically from the accepted plan (no model prose)."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.analytics.types import (
    AnalysisResult,
    Bucket,
    CohortTrials,
    CountResult,
    NetworkResult,
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
    NetworkData,
    NetworkEncoding,
    NetworkGraphSpec,
    NodeDatum,
    TimeSeriesSpec,
    Truncation,
    VisualizationSpec,
)
from app.registry import DATE_LABELS, REGISTRY, study_url

Y = Channel(field="trial_count", type="quantitative", title="Trials", unit="trials")


@dataclass
class BuiltSpec:
    spec: VisualizationSpec
    truncation: Truncation | None
    sort: str | None
    notes: list[str]


class CitationFactory:
    def __init__(self, cohorts: Iterable[CohortTrials], per_datum: int) -> None:
        self.trials: dict[str, Trial] = {t.nct_id: t for c in cohorts for t in c.trials}
        self.per_datum = per_datum

    def datum_fields(self, bucket: Bucket) -> dict[str, object]:
        # Newest NCT IDs first: deterministic, and recent trials are usually most relevant.
        ids = sorted(bucket.contributors, reverse=True)
        shown = ids[: self.per_datum]
        return {
            "trial_count": bucket.count,
            "citation_count": len(ids),
            "citations_truncated": len(shown) < len(ids),
            "citations": [
                Citation(
                    nct_id=nct,
                    url=study_url(nct),
                    brief_title=self.trials[nct].brief_title if nct in self.trials else None,
                    evidence=[Evidence(field_path=p, value=v) for p, v in bucket.contributors[nct]],
                )
                for nct in shown
            ],
        }


def build_spec(
    plan: QueryPlan,
    result: AnalysisResult,
    cohorts: list[CohortTrials],
    citations_per_datum: int,
    preferred: ChartType | None,
) -> BuiltSpec:
    cite = CitationFactory(cohorts, citations_per_datum)
    title = _title(plan)
    if isinstance(result, NetworkResult):
        return _network(title, result, cite)
    return _cartesian(plan, title, result, cite, preferred)


def _cartesian(
    plan: QueryPlan,
    title: str,
    result: CountResult,
    cite: CitationFactory,
    preferred: ChartType | None,
) -> BuiltSpec:
    notes: list[str] = []
    op = plan.operation
    series_field: str | None = None
    series_title = ""
    if result.series_source == "cohort":
        series_field, series_title = "cohort", "Cohort"
    elif result.series_source == "dimension" and op.second_dimension:
        series_field = op.second_dimension.value
        series_title = REGISTRY[op.second_dimension].label

    as_bars = result.kind == "time_trend" and preferred in (
        ChartType.BAR_CHART,
        ChartType.GROUPED_BAR_CHART,
    )
    if result.kind == "time_trend":
        x = Channel(
            field="year",
            type="ordinal" if as_bars else "temporal",
            title=f"Year ({DATE_LABELS[plan.time.date_basis]})",
            sort="ascending",
        )
    else:
        assert op.dimension is not None
        spec = REGISTRY[op.dimension]
        x = Channel(
            field=op.dimension.value,
            type="ordinal" if spec.order else "nominal",
            title=spec.label,
            sort=result.category_order,
        )
    series = (
        Channel(field=series_field, type="nominal", title=series_title, sort=result.series_order)
        if series_field
        else None
    )

    data: list[Datum] = []
    for i, row in enumerate(result.rows, start=1):
        fields: dict[str, object] = {"datum_id": f"d{i}", **cite.datum_fields(row.bucket)}
        if result.kind == "time_trend":
            fields["year"] = int(row.key)
            fields["estimated_date_count"] = row.bucket.extra.get("estimated_date_count", 0)
        else:
            fields[x.field] = row.label
        if series_field:
            fields[series_field] = row.series
        data.append(Datum(**fields))  # type: ignore[arg-type]

    encoding = CartesianEncoding(x=x, y=Y, series=series)
    chart: VisualizationSpec
    if as_bars:
        chart_cls = GroupedBarChartSpec if series else BarChartSpec
        chart = chart_cls(title=title, encoding=encoding, data=data)
    elif result.kind == "time_trend":
        chart = TimeSeriesSpec(title=title, encoding=encoding, data=data)
    elif series:
        chart = GroupedBarChartSpec(title=title, encoding=encoding, data=data)
    else:
        chart = BarChartSpec(title=title, encoding=encoding, data=data)
    if result.kind == "time_trend" and result.category_order:
        this_year = datetime.now(UTC).year
        if int(result.category_order[-1]) >= this_year:
            notes.append(
                f"{this_year} is the current year, so its count is partial; any later years "
                "contain only trials with estimated (anticipated) dates."
            )
    if preferred and preferred.value != chart.type:
        notes.append(
            f"preferred_visualization={preferred.value} is not compatible with this analysis; "
            f"returned {chart.type}."
        )
    truncation = Truncation(**vars(result.truncation)) if result.truncation is not None else None
    return BuiltSpec(chart, truncation, result.sort_description, notes)


def _network(title: str, result: NetworkResult, cite: CitationFactory) -> BuiltSpec:
    nodes = [
        NodeDatum(
            datum_id=f"n{i}",
            id=n.id,
            label=n.bucket.label,
            entity_type=n.entity_type,
            **cite.datum_fields(n.bucket),  # type: ignore[arg-type]
        )
        for i, n in enumerate(result.nodes, start=1)
    ]
    edges = [
        EdgeDatum(
            datum_id=f"e{i}",
            source=e.source,
            target=e.target,
            relation=e.relation,
            **cite.datum_fields(e.bucket),  # type: ignore[arg-type]
        )
        for i, e in enumerate(result.edges, start=1)
    ]
    chart = NetworkGraphSpec(
        title=title,
        encoding=NetworkEncoding(
            nodes={"id": "id", "label": "label", "group": "entity_type", "size": "trial_count"},
            edges={"source": "source", "target": "target", "weight": "trial_count"},
        ),
        data=NetworkData(nodes=nodes, edges=edges),
    )
    truncation = Truncation(**vars(result.truncation)) if result.truncation else None
    return BuiltSpec(chart, truncation, "Edges by shared-trial count, descending", [])


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
    elif op.kind is OperationKind.NETWORK:
        assert op.dimension and op.second_dimension
        a, b = REGISTRY[op.dimension].label, REGISTRY[op.second_dimension].label
        head = f"{a} co-occurrence network" if a == b else f"{a} ↔ {b} network"
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
    elif op.kind is OperationKind.NETWORK:
        assert op.dimension and op.second_dimension
        what = (
            f"a network linking {op.dimension.value} and {op.second_dimension.value} "
            "values that appear in the same trial"
        )
    else:
        assert op.dimension
        what = f"distinct trials by {op.dimension.value}"
        if op.second_dimension:
            what += f", split by {op.second_dimension.value}"
    groups = "; ".join(
        f"'{c.label}' ("
        + ", ".join(
            f"{k}={v}" for k, v in c.filters.model_dump(mode="json", exclude_none=True).items()
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
            "condition uses ClinicalTrials.gov condition search (query.cond), which includes "
            "synonyms and narrower terms."
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
    if Dimension.DRUG in dims:
        out["drug_dimension"] = (
            "Drugs are interventions of type DRUG or BIOLOGICAL, excluding placebos. Names are "
            "grouped case-insensitively; spelling variants and brand/generic names are not "
            "merged."
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
