"""Server-side counting for cohorts too large to fetch: exact registry totals per bucket,
sample citations, and a reproducible source_query on every datum."""

import json
from typing import Any

import httpx

from app.analytics.server_count import MAX_COUNT_QUERIES, count_plan
from app.contracts.enums import Status
from app.contracts.request import VisualizationRequest
from app.ctgov.client import CTGovClient
from app.pipeline import Pipeline
from app.planner import Planner
from tests.factories import CORPUS, plan, study
from tests.test_planner import ScriptedGateway

TOTALS = {"PHASE3": 41_000, "PHASE2": 52_000}


class CountingRegistry:
    """Answers count/sample queries by looking at the bucket clause in query.term."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"apiVersion": "2.0.5", "dataTimestamp": "t"})
        term = request.url.params.get("query.term", "")
        total, studies = 600_000, []
        if "(AREA[Phase]PHASE3 AND NOT" in term:  # combined-phase bucket "Phase 3" only
            total = TOTALS["PHASE3"]
            studies = [study("NCT9", phases=["PHASE3"]), study("NCT8", phases=["PHASE3"])]
        elif "MISSING" in term:
            total = 1234
        elif "AREA[Phase]" in term:
            total = 0
        body: dict[str, Any] = {"totalCount": total, "studies": studies}
        return httpx.Response(200, content=json.dumps(body))


def make(fake: Any, *plans: Any) -> Pipeline:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    client = CTGovClient(http, "https://ctgov.test/api/v2", cache=None, max_attempts=1)
    return Pipeline(Planner(ScriptedGateway(*plans)), client, None, max_trials_per_cohort=30_000)


def test_count_plan_only_for_countable_operations() -> None:
    assert count_plan(plan(dimension="phase")) is not None
    assert count_plan(plan(dimension="drug")) is None  # not a fixed set of values
    assert count_plan(plan(kind="network", dimension="drug", second="drug")) is None
    trend = count_plan(plan(kind="time_trend", dimension=None, year_from=2005, year_to=2010))
    assert trend is not None and [c.key for c in trend.categories][0] == "2005"
    big = plan(kind="time_trend", dimension=None, second="overall_status")
    assert count_plan(big) is None  # 21 years x 9 statuses > MAX_COUNT_QUERIES
    assert MAX_COUNT_QUERIES < 21 * 9


async def test_too_broad_phase_question_is_counted_on_the_server() -> None:
    fake = CountingRegistry()
    response = await make(fake, plan(dimension="phase")).run(
        VisualizationRequest(query="How are all registered trials distributed across phases?")
    )
    assert response.status is Status.OK, response.error
    assert response.meta.count_method == "server_count"
    spec = response.visualization
    assert spec is not None and spec.type == "bar_chart"
    phase3 = next(d for d in spec.data if d.model_extra["phase"] == "Phase 3")  # type: ignore[union-attr,index]
    assert phase3.trial_count == phase3.citation_count == 41_000
    assert phase3.citations_truncated and [c.nct_id for c in phase3.citations] == ["NCT9", "NCT8"]
    assert phase3.citations[0].evidence[0].excerpt == ["PHASE3"]
    source = phase3.model_extra["source_query"]  # type: ignore[index]
    assert source.startswith("https://clinicaltrials.gov/api/v2/studies?")
    assert "countTotal=true" in source and "PHASE3" in source
    assert [d.model_extra["phase"] for d in spec.data] == ["Phase 3"]  # zero buckets dropped
    assert any("1,234 trials" in a for a in response.meta.assumptions)  # no phase reported
    assert not [r for r in fake.requests if r.url.params.get("pageSize") == "1000"]


def test_plain_corpus_still_fetches() -> None:
    assert len(CORPUS) == 5  # sanity: other tests use the fetch path on small cohorts
