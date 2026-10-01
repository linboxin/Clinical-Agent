"""The eval harness itself: scoring rules, case-file validity, and one offline case run."""

import yaml

from app.contracts.request import VisualizationRequest
from app.planner import Planner
from evals.run import CASES, run_case
from evals.scoring import score_case
from tests.factories import plan
from tests.test_planner import ScriptedGateway

COMPARE = plan(
    cohorts=[("pembro", {"drug_name": "Pembrolizumab"}), ("nivo", {"drug_name": "nivolumab"})]
)


def test_scoring_paths_sets_and_token_matching() -> None:
    p = COMPARE.model_dump(mode="json")
    expect = {
        "status": "accepted",
        "plan": {
            "operation.kind": "count_by",
            "cohorts.length": 2,
            "cohorts[].filters.drug_names": ["nivolumab", "pembrolizumab"],  # order-free
            "cohorts[*].filters.conditions": None,
        },
    }
    score = score_case(expect, "accepted", p)
    assert score.passed and score.fields_ok == 4

    wrong = score_case(
        {"status": "accepted", "plan": {"operation.dimension": "country"}}, "accepted", p
    )
    assert not wrong.passed and "operation.dimension" in wrong.mismatches[0]
    assert not score_case({"status": "unsupported"}, "accepted", p).passed
    assert score_case({"status": ["too_broad", "clarification"]}, "clarification", None).passed
    one_of = {"status": "accepted", "plan": {"operation.dimension": {"one_of": ["drug", "phase"]}}}
    assert score_case(one_of, "accepted", p).passed


def test_network_nodes_are_order_free() -> None:
    p = plan(kind="network", dimension="drug", second="lead_sponsor").model_dump(mode="json")
    expect = {"status": "accepted", "plan": {"network_nodes": ["lead_sponsor", "drug"]}}
    assert score_case(expect, "accepted", p).passed


def test_every_case_is_well_formed() -> None:
    cases = yaml.safe_load(CASES.read_text())
    assert len({c["id"] for c in cases}) == len(cases) >= 30
    for case in cases:
        VisualizationRequest.model_validate(case["request"])
        assert case["expect"]["status"]
        assert set(case) == {"id", "class", "request", "expect"}


async def test_run_case_offline_with_scripted_planner() -> None:
    case = {
        "id": "cmp",
        "class": "comparison",
        "request": {"query": "Compare phases for pembrolizumab vs nivolumab"},
        "expect": {"status": "accepted", "plan": {"cohorts.length": 2}},
    }
    row = await run_case(Planner(ScriptedGateway(COMPARE)), None, case)
    assert row["passed"] and row["attempts"] == 1 and row["input_tokens"] == 100
