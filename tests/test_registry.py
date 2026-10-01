from app.contracts.enums import DateBasis, Dimension, PhasePolicy
from app.registry import REGISTRY, extract_date, get_path, norm_name
from tests.factories import CORPUS, study

COMBINED, SPLIT = PhasePolicy.COMBINED, PhasePolicy.SPLIT


def keys(dim: Dimension, record: dict, policy: PhasePolicy = COMBINED) -> list[str]:
    return [v.key for v in REGISTRY[dim].extract(record, policy)]


def test_get_path_handles_indexes_and_missing_hops() -> None:
    record = CORPUS[0]
    assert get_path(record, "protocolSection.designModule.phases[0]") == "PHASE3"
    assert get_path(record, "protocolSection.designModule.phases[5]") is None
    assert get_path(record, "protocolSection.nope.x") is None


def test_multi_phase_is_one_combined_category_with_exact_evidence() -> None:
    [value] = REGISTRY[Dimension.PHASE].extract(CORPUS[1], COMBINED)
    assert (value.key, value.label) == ("PHASE1+PHASE2", "Phase 1/2")
    assert value.path == "protocolSection.designModule.phases"
    assert value.raw == ["PHASE1", "PHASE2"]


def test_split_phase_policy_cites_each_list_item() -> None:
    values = REGISTRY[Dimension.PHASE].extract(CORPUS[1], SPLIT)
    assert [(v.key, v.path, v.raw) for v in values] == [
        ("PHASE1", "protocolSection.designModule.phases[0]", "PHASE1"),
        ("PHASE2", "protocolSection.designModule.phases[1]", "PHASE2"),
    ]


def test_missing_phase_yields_no_value_and_na_is_kept() -> None:
    assert keys(Dimension.PHASE, CORPUS[3]) == []
    assert REGISTRY[Dimension.PHASE].extract(CORPUS[4], COMBINED)[0].label == "Not applicable"


def test_drug_dimension_excludes_placebo_and_non_drug_types() -> None:
    assert keys(Dimension.DRUG, CORPUS[1]) == ["pembrolizumab"]


def test_drug_names_group_case_and_whitespace_insensitively() -> None:
    assert keys(Dimension.DRUG, CORPUS[4]) == ["pembrolizumab"]
    assert norm_name("KEYTRUDA®  (pembrolizumab)") == "keytruda (pembrolizumab)"


def test_country_counts_once_per_trial_keeping_first_path() -> None:
    values = REGISTRY[Dimension.COUNTRY].extract(CORPUS[0], COMBINED)
    assert [(v.label, v.path) for v in values] == [
        ("United States", "protocolSection.contactsLocationsModule.locations[0].country"),
        ("France", "protocolSection.contactsLocationsModule.locations[1].country"),
    ]


def test_dates_keep_precision_and_estimated_flag() -> None:
    month_only = extract_date(CORPUS[1], DateBasis.START_DATE)
    assert month_only is not None and (month_only.year, month_only.raw) == (2016, "2016-07")
    estimated = extract_date(CORPUS[3], DateBasis.START_DATE)
    assert estimated is not None and estimated.estimated
    assert extract_date(CORPUS[4], DateBasis.START_DATE) is None


def test_sponsor_class_labels() -> None:
    [value] = REGISTRY[Dimension.SPONSOR_CLASS].extract(
        study("NCT1", sponsor_class="OTHER"), COMBINED
    )
    assert value.label == "Other (academic, hospital, non-profit)"
    assert value.raw == "OTHER"


def test_every_dimension_is_registered() -> None:
    assert set(REGISTRY) == set(Dimension)
