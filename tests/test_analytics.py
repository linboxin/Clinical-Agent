"""Golden tests: hand-computed counts and contributor sets on the fixed CORPUS."""

from app.analytics import run_analysis
from app.analytics.prepare import cohort_overlap, prepare_cohort
from app.analytics.types import CohortTrials, CountResult, NetworkResult
from app.contracts.plan import QueryPlan
from tests.factories import CORPUS, plan, study


def cohorts_for(p: QueryPlan, *corpora: list[dict]) -> list[CohortTrials]:
    sets = corpora or tuple([CORPUS] * len(p.cohorts))
    return [prepare_cohort(c, s, p.time) for c, s in zip(p.cohorts, sets, strict=True)]


def rows(result: CountResult) -> list[tuple]:
    return [(r.series, r.label, r.bucket.count, sorted(r.bucket.contributors)) for r in result.rows]


def test_count_by_phase_in_canonical_order_with_contributors() -> None:
    p = plan(dimension="phase")
    result = run_analysis(p, cohorts_for(p))
    assert isinstance(result, CountResult)
    assert rows(result) == [
        (None, "Phase 1/2", 1, ["NCT00000002"]),
        (None, "Phase 2", 1, ["NCT00000003"]),
        (None, "Phase 3", 1, ["NCT00000001"]),
        (None, "Not applicable", 1, ["NCT00000005"]),
    ]
    assert result.missing == {"all": {"phase": 1}}  # the observational study


def test_split_phase_counts_multi_phase_trial_in_each_phase() -> None:
    p = plan(dimension="phase", phase_policy="split")
    result = run_analysis(p, cohorts_for(p))
    assert isinstance(result, CountResult)
    counts = {r.label: r.bucket.count for r in result.rows}
    assert counts == {"Phase 1": 1, "Phase 2": 2, "Phase 3": 1, "Not applicable": 1}


def test_count_by_entity_sorts_by_count_and_respects_top_n() -> None:
    p = plan(dimension="lead_sponsor", top_n=1)
    result = run_analysis(p, cohorts_for(p))
    assert isinstance(result, CountResult)
    assert rows(result) == [
        (None, "Merck Sharp & Dohme LLC", 3, ["NCT00000001", "NCT00000003", "NCT00000005"])
    ]
    assert result.truncation is not None and (result.truncation.shown, result.truncation.total) == (
        1,
        3,
    )


def test_country_counts_trials_not_sites() -> None:
    p = plan(dimension="country")
    result = run_analysis(p, cohorts_for(p))
    assert isinstance(result, CountResult)
    counts = {r.label: r.bucket.count for r in result.rows}
    assert counts == {"United States": 2, "France": 2, "Japan": 1}
    assert result.missing == {"all": {"country": 1}}


def test_cohort_comparison_builds_complete_grid_with_zeros() -> None:
    p = plan(dimension="phase", cohorts=[("A", {}), ("B", {})])
    only_phase3 = [
        s for s in CORPUS if s["protocolSection"]["identificationModule"]["nctId"] == "NCT00000001"
    ]
    result = run_analysis(p, cohorts_for(p, CORPUS, only_phase3))
    assert isinstance(result, CountResult)
    grid = {(r.series, r.label): r.bucket.count for r in result.rows}
    assert grid[("A", "Phase 3")] == 1 and grid[("B", "Phase 3")] == 1
    assert grid[("B", "Phase 2")] == 0  # explicit zero for grouped bars
    assert result.series_order == ["A", "B"]


def test_cohort_overlap_reports_shared_trials() -> None:
    p = plan(cohorts=[("A", {}), ("B", {})])
    overlap = cohort_overlap(cohorts_for(p, CORPUS, CORPUS[:2]))
    assert overlap == {"A ∩ B": 2}


def test_time_trend_zero_fills_and_flags_estimated_dates() -> None:
    p = plan(kind="time_trend", dimension=None)
    result = run_analysis(p, cohorts_for(p))
    assert isinstance(result, CountResult)
    by_year = {
        r.label: (r.bucket.count, r.bucket.extra.get("estimated_date_count", 0))
        for r in result.rows
    }
    assert list(by_year) == [str(y) for y in range(2016, 2028)]
    assert by_year["2016"] == (2, 0) and by_year["2017"] == (0, 0)
    assert by_year["2027"] == (1, 1)
    assert result.missing == {"all": {"start_date": 1}}


def test_year_range_post_filter_records_exclusions() -> None:
    p = plan(kind="time_trend", dimension=None, year_from=2017, year_to=2020)
    [ct] = cohorts_for(p)
    assert [t.nct_id for t in ct.trials] == ["NCT00000003"]
    assert ct.excluded == {"outside_year_range": 3, "no_start_date": 1}


def test_prepare_drops_duplicates_and_records_without_nct_id() -> None:
    p = plan()
    broken = study("X")
    del broken["protocolSection"]["identificationModule"]["nctId"]
    [ct] = cohorts_for(p, [CORPUS[0], CORPUS[0], broken])
    assert len(ct.trials) == 1
    assert ct.excluded == {"duplicate_record": 1, "malformed_record_without_nct_id": 1}


def test_bipartite_network_sponsor_drug() -> None:
    p = plan(kind="network", dimension="lead_sponsor", second="drug")
    result = run_analysis(p, cohorts_for(p))
    assert isinstance(result, NetworkResult)
    edges = {(e.source, e.target): e.bucket.count for e in result.edges}
    assert edges[("lead_sponsor:merck sharp & dohme llc", "drug:pembrolizumab")] == 3
    assert edges[("lead_sponsor:merck sharp & dohme llc", "drug:carboplatin")] == 2
    assert edges[("lead_sponsor:m.d. anderson cancer center", "drug:pembrolizumab")] == 1
    node_ids = {n.id for n in result.nodes}
    assert all(s in node_ids and t in node_ids for s, t in edges)
    merck_pembro = next(e for e in result.edges if e.bucket.count == 3)
    # Edge evidence cites both endpoints in each contributing trial.
    assert merck_pembro.bucket.contributors["NCT00000001"] == [
        ("protocolSection.sponsorCollaboratorsModule.leadSponsor.name", "Merck Sharp & Dohme LLC"),
        ("protocolSection.armsInterventionsModule.interventions[0].name", "Pembrolizumab"),
    ]


def test_cooccurrence_network_has_no_duplicate_or_self_edges() -> None:
    p = plan(kind="network", dimension="drug", second="drug")
    result = run_analysis(p, cohorts_for(p))
    assert isinstance(result, NetworkResult)
    pairs = [frozenset((e.source, e.target)) for e in result.edges]
    assert len(pairs) == len(set(pairs))
    assert all(len(pair) == 2 for pair in pairs)
    weights = {tuple(sorted((e.source, e.target))): e.bucket.count for e in result.edges}
    assert weights[("drug:carboplatin", "drug:pembrolizumab")] == 2
    assert weights[("drug:pembrolizumab", "drug:pemetrexed")] == 1
    # Trials with fewer than two drugs cannot form a pair.
    assert result.missing == {"all": {"fewer_than_two_drug_values": 3}}


def test_results_do_not_depend_on_record_order() -> None:
    p = plan(dimension="country")
    forward = run_analysis(p, cohorts_for(p, CORPUS))
    backward = run_analysis(p, cohorts_for(p, list(reversed(CORPUS))))
    assert isinstance(forward, CountResult) and isinstance(backward, CountResult)
    assert rows(forward) == rows(backward)
