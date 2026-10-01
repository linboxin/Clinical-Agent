"""Golden tests for the v2 coverage additions: histogram, scatter, time trend split by a
dimension, chart-selection rules, entity normalization and cohort-membership evidence."""

import pytest

from app.analytics import run_analysis
from app.analytics.histogram import bin_index, bin_labels
from app.analytics.prepare import prepare_cohort
from app.analytics.types import CohortTrials, CountResult, ScatterResult
from app.contracts.enums import ChartType, Measure
from app.contracts.plan import QueryPlan
from app.contracts.response import (
    HistogramSpec,
    PieChartSpec,
    ScatterPlotSpec,
    StackedBarChartSpec,
    TimeSeriesSpec,
)
from app.registry import MEASURES, REGISTRY, Dimension, drug_key, site_key
from app.viz.build import build_spec, choose_chart
from app.viz.verify import verify
from tests.factories import plan, study

SIZED = [
    study("NCT1", enrollment=8, start="2018-01", primary_completion="2019-07", phases=["PHASE1"]),
    study("NCT2", enrollment=40, start="2019-03-01", primary_completion="2019-09-01"),
    study("NCT3", enrollment=40, start="2020", primary_completion="2022-01", phases=["PHASE2"]),
    study("NCT4", enrollment=12000, enrollment_type="ESTIMATED", start="2021-05"),
    study("NCT5", start="2022-02", phases=["PHASE2"]),  # no enrollment
]


def cohorts_for(p: QueryPlan, *corpora: list[dict]) -> list[CohortTrials]:
    sets = corpora or tuple([SIZED] * len(p.cohorts))
    return [prepare_cohort(c, s, p.time)[0] for c, s in zip(p.cohorts, sets, strict=True)]


def trials_of(cohorts: list[CohortTrials]) -> dict:
    return {t.nct_id: t for c in cohorts for t in c.trials}


# --- histogram ---------------------------------------------------------------------------------


def test_bins_are_declared_half_open_and_labelled() -> None:
    edges = MEASURES[Measure.ENROLLMENT].bin_edges
    assert edges is not None
    labels = bin_labels(edges, integer=True)
    assert labels[:3] == ["0–9", "10–24", "25–49"] and labels[-1] == "≥10,000"
    assert bin_index(edges, 9) == 0 and bin_index(edges, 10) == 1 and bin_index(edges, 1e6) == 10


def test_enrollment_histogram_zero_fills_and_cites_count_and_type() -> None:
    p = plan(kind="histogram", dimension=None, measure="enrollment")
    cohorts = cohorts_for(p)
    result = run_analysis(p, cohorts)
    assert isinstance(result, CountResult)
    counts = {r.label: r.bucket.count for r in result.rows}
    assert counts["0–9"] == 1 and counts["25–49"] == 2 and counts["≥10,000"] == 1
    assert sum(counts.values()) == 4 and len(result.rows) == 11  # every declared bin present
    assert result.missing == {"all": {"enrollment": 1}}
    evidence = result.rows[-1].bucket.contributors["NCT4"]
    assert evidence == [
        ("protocolSection.designModule.enrollmentInfo.count", 12000),
        ("protocolSection.designModule.enrollmentInfo.type", "ESTIMATED"),
    ]
    built = build_spec(p, result, cohorts, 2, None)
    assert isinstance(built.spec, HistogramSpec)
    assert built.spec.data[-1].model_extra == {
        "bin": "≥10,000",
        "bin_start": 10000,
        "bin_end": None,
    }
    assert verify(built.spec, trials_of(cohorts)) == []


def test_duration_needs_month_precision_on_both_dates() -> None:
    p = plan(kind="histogram", dimension=None, measure="duration_months")
    result = run_analysis(p, cohorts_for(p))
    assert isinstance(result, CountResult)
    by_trial = {n: r.label for r in result.rows for n in r.bucket.contributors}
    assert by_trial == {"NCT1": "18–24", "NCT2": "6–12"}  # 18 months; 6 months exactly
    assert result.missing == {"all": {"duration_months": 3}}  # year-only start, no completion


# --- scatter -----------------------------------------------------------------------------------


def test_scatter_one_point_per_trial_with_both_values_cited() -> None:
    p = plan(kind="scatter", dimension="phase", measure="enrollment", x_measure="start_date")
    cohorts = cohorts_for(p)
    result = run_analysis(p, cohorts)
    assert isinstance(result, ScatterResult)
    assert [(pt.trial.nct_id, pt.x.value, pt.y.value, pt.color) for pt in result.points] == [
        ("NCT1", "2018-01", 8, "Phase 1"),
        ("NCT2", "2019-03-01", 40, "Not reported"),
        ("NCT3", "2020", 40, "Phase 2"),
        ("NCT4", "2021-05", 12000, "Not reported"),
    ]
    built = build_spec(p, result, cohorts, 1, None)
    assert isinstance(built.spec, ScatterPlotSpec)
    assert built.spec.encoding.x.type == "temporal" and built.spec.encoding.y.unit == "participants"
    paths = [e.field_path for e in built.spec.data[0].citations[0].evidence]
    assert paths[0].endswith("startDateStruct.date") and paths[1].endswith("enrollmentInfo.count")
    assert verify(built.spec, trials_of(cohorts)) == []


# --- time trend split --------------------------------------------------------------------------


def test_time_trend_split_by_phase_counts_each_series_and_zero_fills() -> None:
    p = plan(kind="time_trend", dimension=None, second="phase")
    cohorts = cohorts_for(p)
    result = run_analysis(p, cohorts)
    assert isinstance(result, CountResult)
    assert result.series_order == ["Phase 1", "Phase 2"]
    grid = {(r.label, r.series): r.bucket.count for r in result.rows}
    assert grid[("2018", "Phase 1")] == 1 and grid[("2020", "Phase 2")] == 1
    assert grid[("2022", "Phase 2")] == 1 and grid[("2019", "Phase 1")] == 0
    assert result.missing == {"all": {"phase": 2}}
    built = build_spec(p, result, cohorts, 1, None)
    assert isinstance(built.spec, TimeSeriesSpec)
    assert built.spec.encoding.series and built.spec.encoding.series.field == "phase"


# --- chart selection ---------------------------------------------------------------------------


def test_pie_only_for_mutually_exclusive_categories() -> None:
    by_status = plan(dimension="overall_status")
    result = run_analysis(by_status, cohorts_for(by_status))
    chart, reason = choose_chart(by_status, result, ChartType.PIE_CHART)
    assert chart is ChartType.PIE_CHART and "honored" in reason

    by_country = plan(dimension="country")  # multi-valued: a trial can be in many countries
    result = run_analysis(by_country, cohorts_for(by_country))
    chart, reason = choose_chart(by_country, result, ChartType.PIE_CHART)
    assert chart is ChartType.BAR_CHART and "overlap" in reason


def test_stacked_bars_only_for_exclusive_series() -> None:
    cohorts = [("a", {}), ("b", {})]
    p = plan(dimension="phase", cohorts=cohorts)
    disjoint = cohorts_for(p, SIZED[:2], SIZED[2:])
    chart, _ = choose_chart(p, run_analysis(p, disjoint), ChartType.STACKED_BAR_CHART)
    assert chart is ChartType.STACKED_BAR_CHART
    overlapping = cohorts_for(p, SIZED[:3], SIZED[2:])
    chart, reason = choose_chart(p, run_analysis(p, overlapping), ChartType.STACKED_BAR_CHART)
    assert chart is ChartType.GROUPED_BAR_CHART and "double-count" in reason


def test_pie_and_stacked_specs_pass_the_gate() -> None:
    p = plan(dimension="overall_status")
    cohorts = cohorts_for(p)
    built = build_spec(p, run_analysis(p, cohorts), cohorts, 1, ChartType.PIE_CHART)
    assert isinstance(built.spec, PieChartSpec) and verify(built.spec, trials_of(cohorts)) == []
    p2 = plan(dimension="phase", cohorts=[("a", {}), ("b", {})])
    cohorts2 = cohorts_for(p2, SIZED[:2], SIZED[2:])
    built2 = build_spec(p2, run_analysis(p2, cohorts2), cohorts2, 1, ChartType.STACKED_BAR_CHART)
    assert isinstance(built2.spec, StackedBarChartSpec)
    assert verify(built2.spec, trials_of(cohorts2)) == []


def test_bar_charts_of_entities_are_horizontal() -> None:
    p = plan(dimension="lead_sponsor")
    cohorts = cohorts_for(p)
    built = build_spec(p, run_analysis(p, cohorts), cohorts, 0, None)
    assert built.spec.type == "bar_chart" and built.spec.orientation == "horizontal"  # type: ignore[union-attr]


# --- normalization -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("Osimertinib 80 MG", "osimertinib"),
        ("Erlotinib Hydrochloride", "erlotinib"),
        ("KEYTRUDA®", "keytruda"),
        ("Cisplatin 75 mg/m2 IV", "cisplatin"),
        ("5-Fluorouracil", "5-fluorouracil"),
        ("Interleukin-2", "interleukin-2"),
    ],
)
def test_drug_key_strips_dose_and_salt_only(raw: str, key: str) -> None:
    assert drug_key(raw) == key


def test_site_numbers_and_placeholder_investigators_are_normalized() -> None:
    assert site_key("Erasmus MC ( Site 5303)") == site_key("erasmus mc")
    s = study("NCT9", officials=["Medical Director", "Jane Doe, MD"], facilities=["X ( Site 1)"])
    names = [v.label for v in REGISTRY[Dimension.INVESTIGATOR].extract(s, plan().phase_policy)]
    assert names == ["Jane Doe, MD"]
    sites = [v.label for v in REGISTRY[Dimension.SITE].extract(s, plan().phase_policy)]
    assert sites == ["X"]


# --- membership evidence -----------------------------------------------------------------------


def test_citations_explain_cohort_membership_and_count_synonym_matches() -> None:
    corpus = [
        study("NCT1", phases=["PHASE3"], interventions=[("DRUG", "Pembrolizumab")]),
        study(
            "NCT2",
            phases=["PHASE3"],
            interventions=[("BIOLOGICAL", "MK-3475")],
            other_names={"MK-3475": ["Keytruda"]},
        ),
        study("NCT3", phases=["PHASE2"], interventions=[("DRUG", "Pembrolizumab")]),
    ]
    p = plan(cohorts=[("pembro", {"drug_name": "pembrolizumab", "trial_phase": ["PHASE3"]})])
    ct, synonyms = prepare_cohort(p.cohorts[0], corpus, p.time)
    assert [t.nct_id for t in ct.trials] == ["NCT1", "NCT2"]
    assert ct.excluded == {"phase_not_in_filter": 1}  # API result re-checked locally
    assert synonyms == {"drug_name": 1}  # NCT2 matched only via the registry's synonym search
    assert ct.trials[0].membership == (
        ("protocolSection.designModule.phases", ["PHASE3"]),
        ("protocolSection.armsInterventionsModule.interventions[0].name", "Pembrolizumab"),
    )
    built = build_spec(p, run_analysis(p, [ct]), [ct], 5, None)
    assert verify(built.spec, trials_of([ct])) == []
    full = built.evidence["d1"]
    assert [c["nct_id"] for c in full] == ["NCT2", "NCT1"]


@pytest.mark.parametrize(
    ("term", "text", "hit"),
    [
        ("Alzheimer's disease", "Alzheimer Disease", True),
        ("lung cancer", "Non-small Cell Lung Cancers", True),
        ("pembrolizumab", "Pembrolizumab (MK-3475)", True),
        ("Korea", "Korea, Republic of", True),
        ("breast cancer", "Breast Neoplasms", False),  # a registry synonym, not a literal match
    ],
)
def test_literal_match_is_token_based(term: str, text: str, hit: bool) -> None:
    from app.analytics.prepare import literal_match

    assert literal_match(term, text) is hit
