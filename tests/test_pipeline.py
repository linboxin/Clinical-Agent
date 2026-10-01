"""End-to-end pipeline on a mocked ClinicalTrials.gov (httpx.MockTransport) with a scripted
planner: exercises pagination, analysis, spec building, citations and the output gate."""

import json
from typing import Any

import httpx
import pytest

from app.contracts.enums import Status
from app.contracts.request import VisualizationRequest
from app.contracts.response import BarChartSpec, NetworkGraphSpec, TimeSeriesSpec
from app.ctgov.client import CTGovClient
from app.pipeline import Pipeline
from app.planner import Planner
from app.viz.verify import verify
from tests.factories import CORPUS, plan
from tests.test_planner import ScriptedGateway


class FakeRegistry:
    """Serves /version and paginated /studies (2 records per page) from fixed corpora."""

    def __init__(self, corpora: dict[str, list[dict[str, Any]]], total_override: int | None = None):
        self.corpora = corpora  # keyed by query.term ('' for no term)
        self.total_override = total_override
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/version"):
            return httpx.Response(
                200, json={"apiVersion": "2.0.5", "dataTimestamp": "2026-10-01T09:00:05"}
            )
        q = request.url.params
        studies = self.corpora.get(q.get("query.term", ""), [])
        start = int(q.get("pageToken", "0"))
        body: dict[str, Any] = {"studies": studies[start : start + 2]}
        if start + 2 < len(studies):
            body["nextPageToken"] = str(start + 2)
        if q.get("countTotal") == "true":
            body["totalCount"] = self.total_override or len(studies)
        return httpx.Response(200, content=json.dumps(body))


def make_pipeline(fake: FakeRegistry, *plans: Any, max_trials: int = 1000) -> Pipeline:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    client = CTGovClient(http, "https://ctgov.test/api/v2", cache=None, max_attempts=1)
    return Pipeline(
        Planner(ScriptedGateway(*plans)), client, store=None, max_trials_per_cohort=max_trials
    )


async def test_phase_bar_chart_end_to_end_with_verified_citations() -> None:
    fake = FakeRegistry({"": CORPUS})
    response = await make_pipeline(fake, plan(dimension="phase")).run(
        VisualizationRequest(
            query="How are trials distributed across phases?", citations_per_datum=1
        )
    )
    assert response.status is Status.OK, response.error
    spec = response.visualization
    assert isinstance(spec, BarChartSpec)
    assert [(d.model_extra["phase"], d.trial_count) for d in spec.data] == [  # type: ignore[index]
        ("Phase 1/2", 1),
        ("Phase 2", 1),
        ("Phase 3", 1),
        ("Not applicable", 1),
    ]
    assert spec.encoding.x.sort == ["Phase 1/2", "Phase 2", "Phase 3", "Not applicable"]
    citation = spec.data[0].citations[0]
    assert citation.nct_id == "NCT00000002"
    assert citation.evidence[0].field_path == "protocolSection.designModule.phases"
    assert citation.evidence[0].value == ["PHASE1", "PHASE2"]
    # 3 pages of 2 records: pagination was followed to the end.
    assert len([r for r in fake.requests if r.url.path.endswith("/studies")]) == 3
    cohort = response.meta.cohorts[0]
    assert (cohort.total_matches, cohort.trials_analyzed, cohort.complete) == (5, 5, True)
    assert cohort.missing == {"phase": 1}
    assert response.meta.source and response.meta.source.data_timestamp == "2026-10-01T09:00:05"
    assert "phase" in response.meta.policies


async def test_time_series_for_brief_example_request() -> None:
    term = '(AREA[InterventionName]"Pembrolizumab" OR AREA[InterventionOtherName]"Pembrolizumab")'
    fake = FakeRegistry({term: CORPUS})
    planned = plan(kind="time_trend", dimension=None, cohorts=[("Pembrolizumab", {})])
    response = await make_pipeline(fake, planned).run(
        VisualizationRequest(
            query="How has the number of trials for this drug changed over time?",
            drug_name="Pembrolizumab",
        )
    )
    assert response.status is Status.OK, response.error
    spec = response.visualization
    assert isinstance(spec, TimeSeriesSpec)
    assert spec.encoding.x.field == "year" and spec.encoding.x.type == "temporal"
    assert response.meta.cohorts[0].filters == {
        "drug_name": "Pembrolizumab"
    }  # explicit field applied
    assert response.meta.time_granularity == "year"


async def test_network_end_to_end() -> None:
    fake = FakeRegistry({"": CORPUS})
    response = await make_pipeline(
        fake, plan(kind="network", dimension="lead_sponsor", second="drug")
    ).run(VisualizationRequest(query="network of sponsors and drugs"))
    assert response.status is Status.OK, response.error
    spec = response.visualization
    assert isinstance(spec, NetworkGraphSpec)
    assert spec.encoding.edges["weight"] == "trial_count"
    assert {n.entity_type for n in spec.data.nodes} == {"lead_sponsor", "drug"}
    assert len(spec.data.edges[0].citations[0].evidence) == 2  # both endpoints cited


async def test_too_broad_query_asks_to_narrow_without_paging() -> None:
    fake = FakeRegistry({"": CORPUS}, total_override=500_000)
    response = await make_pipeline(fake, plan()).run(
        VisualizationRequest(query="all trials by phase")
    )
    assert response.status is Status.NEEDS_CLARIFICATION
    assert response.clarification and "500,000" in response.clarification.question
    assert len([r for r in fake.requests if r.url.path.endswith("/studies")]) == 1


async def test_zero_hits_for_a_named_drug_asks_instead_of_empty_chart() -> None:
    fake = FakeRegistry({})
    planned = plan(cohorts=[("pembrolizumabb", {"drug_name": "pembrolizumabb"})])
    response = await make_pipeline(fake, planned).run(
        VisualizationRequest(query="pembrolizumabb phases")
    )
    assert response.status is Status.NEEDS_CLARIFICATION
    assert response.clarification and "pembrolizumabb" in response.clarification.question


async def test_zero_hits_without_named_filters_is_empty() -> None:
    fake = FakeRegistry({})
    response = await make_pipeline(fake, plan()).run(VisualizationRequest(query="phases"))
    assert response.status is Status.EMPTY
    assert response.visualization is not None


async def test_upstream_failure_is_reported_not_hidden() -> None:
    def broken(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    http = httpx.AsyncClient(transport=httpx.MockTransport(broken))
    client = CTGovClient(http, "https://ctgov.test/api/v2", cache=None, max_attempts=1)
    pipeline = Pipeline(Planner(ScriptedGateway(plan())), client, None, 1000)
    response = await pipeline.run(VisualizationRequest(query="phases"))
    assert response.status is Status.FAILED
    assert response.error and response.error.code == "upstream_unavailable"


async def test_unsupported_question_returns_alternatives() -> None:
    fake = FakeRegistry({"": CORPUS})
    response = await make_pipeline(fake, plan(unsupported_reason="Needs efficacy results.")).run(
        VisualizationRequest(query="Which drug works best?")
    )
    assert response.status is Status.UNSUPPORTED
    assert response.error and response.error.details
    assert fake.requests == []  # nothing was fetched


@pytest.mark.parametrize("field", ["value", "count"])
async def test_verify_gate_catches_tampered_output(field: str) -> None:
    fake = FakeRegistry({"": CORPUS})
    response = await make_pipeline(fake, plan()).run(VisualizationRequest(query="phases"))
    spec = response.visualization
    assert isinstance(spec, BarChartSpec)
    datum = spec.data[0]
    if field == "value":
        datum.citations[0].evidence[0].value = ["PHASE3"]  # quote something the API never said
    else:
        datum.trial_count += 1
    from app.analytics.types import Trial
    from app.registry import NCT_PATH, get_path

    trials = {get_path(s, NCT_PATH): Trial(get_path(s, NCT_PATH), None, s) for s in CORPUS}
    assert verify(spec, trials)


def test_response_schema_is_publishable() -> None:
    from app.contracts.response import VisualizationResponse

    schema = VisualizationResponse.model_json_schema()
    assert {"BarChartSpec", "GroupedBarChartSpec", "TimeSeriesSpec", "NetworkGraphSpec"} <= set(
        schema["$defs"]
    )
