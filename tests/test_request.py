import pytest
from pydantic import ValidationError

from app.contracts.enums import OverallStatus, Phase
from app.contracts.request import VisualizationRequest


def test_brief_example_request_is_valid_unchanged() -> None:
    req = VisualizationRequest.model_validate(
        {
            "query": "How has the number of trials for this drug changed over time?",
            "drug_name": "Pembrolizumab",
        }
    )
    assert req.drug_name == "Pembrolizumab"
    assert req.citations_per_datum == 5


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Phase 3", [Phase.PHASE3]),
        ("3", [Phase.PHASE3]),
        ("PHASE2", [Phase.PHASE2]),
        ("phase 1/2", [Phase.PHASE1, Phase.PHASE2]),
        ("Phase III", [Phase.PHASE3]),
        ("early phase 1", [Phase.EARLY_PHASE1]),
        ("N/A", [Phase.NA]),
        (["Phase 2", "phase 3", "PHASE2"], [Phase.PHASE2, Phase.PHASE3]),
    ],
)
def test_trial_phase_parsing(raw: object, expected: list[Phase]) -> None:
    assert VisualizationRequest(query="q", trial_phase=raw).trial_phase == expected  # type: ignore[arg-type]


def test_status_parsing_accepts_human_text() -> None:
    req = VisualizationRequest(query="q", overall_status=["Recruiting", "active, not recruiting"])  # type: ignore[arg-type]
    assert req.overall_status == [OverallStatus.RECRUITING, OverallStatus.ACTIVE_NOT_RECRUITING]


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "q", "unknown_field": 1},
        {"query": "q", "start_year": 2020, "end_year": 2010},
        {"query": "q", "start_year": 1800},
        {"query": "q", "trial_phase": "Phase 9"},
        {"query": "q", "citations_per_datum": 5000},
        {"query": "q", "drug_name": ""},
    ],
)
def test_invalid_requests_are_rejected(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VisualizationRequest.model_validate(body)


def test_structured_fields_only_lists_set_filters() -> None:
    req = VisualizationRequest(query="q", condition="lung cancer", start_year=2015)
    assert req.structured_fields() == {"condition": "lung cancer", "start_year": 2015}
