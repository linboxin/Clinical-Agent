from app.contracts.enums import Dimension
from app.contracts.plan import Clarification, QueryPlan
from app.contracts.request import VisualizationRequest
from app.planner import Planner
from app.planner.gateway import Message, Proposal
from app.planner.prompt import system_prompt
from app.planner.validate import apply_request_fields, validate_plan
from tests.factories import plan


class ScriptedGateway:
    """Returns pre-written plans in order and records what the planner sent."""

    model = "scripted"

    def __init__(self, *plans: QueryPlan) -> None:
        self.plans = list(plans)
        self.calls: list[list[Message]] = []

    async def propose(self, instructions: str, messages: list[Message]) -> Proposal:
        self.calls.append(list(messages))
        return Proposal(self.plans.pop(0), input_tokens=100, output_tokens=20)


# --- semantic validation ---------------------------------------------------------------------


def test_valid_plans_pass() -> None:
    assert validate_plan(plan(dimension="phase")) == []
    assert validate_plan(plan(kind="time_trend", dimension=None, year_from=2015)) == []
    assert validate_plan(plan(kind="network", dimension="lead_sponsor", second="drug")) == []


def test_invalid_combinations_are_rejected() -> None:
    cases = {
        "count_by requires operation.dimension": plan(dimension=None),
        "time_trend requires operation.dimension to be null": plan(
            kind="time_trend", dimension="phase"
        ),
        "network requires dimension": plan(kind="network", dimension="phase", second="drug"),
        "network requires exactly one cohort": plan(
            kind="network", dimension="drug", second="drug", cohorts=[("a", {}), ("b", {})]
        ),
        "already uses cohorts as series": plan(second="country", cohorts=[("a", {}), ("b", {})]),
        "year_from must be <=": plan(year_from=2020, year_to=2010),
        "cohort labels must be unique": plan(cohorts=[("a", {}), ("a", {})]),
        "cohorts must contain": plan(cohorts=[(str(i), {}) for i in range(5)]),
        "top_n must be between": plan(top_n=0),
    }
    for expected, bad in cases.items():
        errors = validate_plan(bad)
        assert any(expected in e for e in errors), (expected, errors)


# --- explicit structured fields --------------------------------------------------------------


def test_request_fields_fill_every_cohort_and_time() -> None:
    req = VisualizationRequest(query="q", condition="melanoma", start_year=2015)
    p = plan(
        cohorts=[("pembro", {"drug_name": "pembrolizumab"}), ("nivo", {"drug_name": "nivolumab"})]
    )
    merged, conflict = apply_request_fields(p, req)
    assert conflict is None
    assert [c.filters.conditions for c in merged.cohorts] == [["melanoma"], ["melanoma"]]
    assert merged.time.year_from == 2015


def test_explicit_field_wins_over_compatible_wording() -> None:
    req = VisualizationRequest(query="q", drug_name="Pembrolizumab")
    merged, conflict = apply_request_fields(
        plan(cohorts=[("x", {"drug_name": "pembrolizumab (Keytruda)"})]), req
    )
    assert conflict is None and merged.cohorts[0].filters.drug_names == ["Pembrolizumab"]


def test_contradiction_between_field_and_question_asks_the_user() -> None:
    req = VisualizationRequest(
        query="compare pembrolizumab vs nivolumab", drug_name="pembrolizumab"
    )
    p = plan(
        cohorts=[("pembro", {"drug_name": "pembrolizumab"}), ("nivo", {"drug_name": "nivolumab"})]
    )
    _, conflict = apply_request_fields(p, req)
    assert conflict is not None and "drug_name" in conflict.question


# --- planner loop -----------------------------------------------------------------------------


async def test_accepts_valid_plan_in_one_call() -> None:
    gateway = ScriptedGateway(plan(dimension="phase"))
    outcome = await Planner(gateway).plan(VisualizationRequest(query="phases?"))
    assert outcome.status == "accepted" and outcome.attempts == 1
    assert outcome.input_tokens == 100


async def test_one_repair_call_with_validation_errors() -> None:
    gateway = ScriptedGateway(plan(dimension=None), plan(dimension="phase"))
    outcome = await Planner(gateway).plan(VisualizationRequest(query="phases?"))
    assert outcome.status == "accepted" and outcome.attempts == 2
    repair_prompt = gateway.calls[1][-1]["content"]
    assert "count_by requires operation.dimension" in repair_prompt


async def test_gives_up_after_bounded_repairs() -> None:
    gateway = ScriptedGateway(plan(dimension=None), plan(dimension=None))
    outcome = await Planner(gateway).plan(VisualizationRequest(query="phases?"))
    assert outcome.status == "invalid" and outcome.attempts == 2 and outcome.errors


async def test_model_clarification_and_unsupported_pass_through() -> None:
    ask = Clarification(question="Which drug?", options=["A", "B"])
    out = await Planner(ScriptedGateway(plan(clarification=ask))).plan(
        VisualizationRequest(query="this drug?")
    )
    assert out.status == "clarification" and out.clarification == ask
    out = await Planner(ScriptedGateway(plan(unsupported_reason="efficacy"))).plan(
        VisualizationRequest(query="which works best?")
    )
    assert out.status == "unsupported" and out.unsupported_reason == "efficacy"


def test_prompt_lists_every_registry_dimension() -> None:
    prompt = system_prompt()
    assert all(f"- {d.value}:" in prompt for d in Dimension)


def test_plan_schema_is_valid_for_openai_strict_mode() -> None:
    """Structured Outputs strict mode: every object lists all properties as required and
    forbids extras. Checked offline with the SDK's own converter."""
    from openai.lib._pydantic import to_strict_json_schema

    def objects(node: object) -> list[dict]:
        found: list[dict] = []
        if isinstance(node, dict):
            if node.get("type") == "object":
                found.append(node)
            for value in node.values():
                found += objects(value)
        elif isinstance(node, list):
            for value in node:
                found += objects(value)
        return found

    schema = to_strict_json_schema(QueryPlan)
    for obj in objects(schema):
        assert set(obj["properties"]) == set(obj["required"])
        assert obj["additionalProperties"] is False
