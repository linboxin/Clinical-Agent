from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.contracts.request import VisualizationRequest
from app.main import app
from app.storage import FileRunStore
from tests.factories import CORPUS, plan
from tests.test_pipeline import FakeRegistry, make_pipeline


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(app) as c:  # runs the real lifespan, then tests swap in a fake pipeline
        store = FileRunStore(tmp_path)
        app.state.pipeline = make_pipeline(FakeRegistry({"": CORPUS}), plan(), store=store)
        app.state.store = store
        yield c


def test_post_returns_typed_response_with_200(client: TestClient) -> None:
    r = client.post("/v1/visualizations", json={"query": "phases?"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["visualization"]["type"] == "bar_chart"
    assert set(body) == {
        "schema_version",
        "run_id",
        "status",
        "visualization",
        "meta",
        "clarification",
        "error",
    }


def test_malformed_request_is_422(client: TestClient) -> None:
    assert client.post("/v1/visualizations", json={"query": "q", "bogus": 1}).status_code == 422
    assert client.post("/v1/visualizations", json={}).status_code == 422


def test_failed_status_maps_to_503(client: TestClient) -> None:
    app.state.pipeline.planner = None  # simulate a missing OPENAI_API_KEY
    r = client.post("/v1/visualizations", json={"query": "q"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "planner_not_configured"


def test_capabilities_and_openapi_are_published(client: TestClient) -> None:
    caps = client.get("/v1/capabilities").json()
    assert {d["name"] for d in caps["dimensions"]} >= {"phase", "drug", "country"}
    assert set(caps["request_fields"]) == set(VisualizationRequest.model_fields)
    assert "/v1/visualizations" in client.get("/openapi.json").json()["paths"]


def test_stored_run_evidence_and_trace_are_served(client: TestClient) -> None:
    body = client.post(
        "/v1/visualizations", json={"query": "phases?", "citations_per_datum": 0}
    ).json()
    run_id = body["run_id"]
    assert client.get(f"/v1/runs/{run_id}").json()["status"] == "ok"

    datum = body["visualization"]["data"][0]
    assert datum["citations"] == [] and datum["citations_truncated"] is True
    ev = client.get(f"/v1/runs/{run_id}/evidence", params={"datum_id": datum["datum_id"]}).json()
    assert ev["total"] == datum["trial_count"] == len(ev["citations"])
    assert ev["citations"][0]["evidence"][0]["field_path"] == "protocolSection.designModule.phases"

    spans = client.get(f"/v1/runs/{run_id}/trace").json()["spans"]
    names = [s["name"] for s in spans]
    assert names[0] == "run" and "llm.propose" in names and "ctgov.request" in names
    stages = [n for n in names if n.startswith("stage.")]
    assert stages == [
        "stage.plan",
        "stage.retrieve",
        "stage.analyze",
        "stage.build",
        "stage.verify",
    ]
    llm = next(s for s in spans if s["name"] == "llm.propose")
    assert llm["attributes"]["input_tokens"] == 100
    assert next(s for s in spans if s["id"] == llm["parent_id"])["name"] == "stage.plan"


def test_unknown_run_and_parent_are_404(client: TestClient) -> None:
    missing = "00000000-0000-4000-8000-000000000000"
    assert client.get(f"/v1/runs/{missing}").status_code == 404
    assert client.get(f"/v1/runs/{missing}/evidence", params={"datum_id": "d1"}).status_code == 404
    r = client.post("/v1/visualizations", json={"query": "q", "parent_run_id": missing})
    assert r.status_code == 404 and r.json()["error"]["code"] == "parent_run_not_found"
    assert client.get("/v1/runs/../../etc/passwd").status_code == 404


def test_demo_page_is_served(client: TestClient) -> None:
    r = client.get("/demo")
    assert r.status_code == 200 and "vega-lite" in r.text and "/v1/visualizations" in r.text
