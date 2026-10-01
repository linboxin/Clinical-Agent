from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.contracts.request import VisualizationRequest
from app.main import app
from tests.factories import CORPUS, plan
from tests.test_pipeline import FakeRegistry, make_pipeline


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:  # runs the real lifespan, then tests swap in a fake pipeline
        app.state.pipeline = make_pipeline(FakeRegistry({"": CORPUS}), plan())
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
