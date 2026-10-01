"""The bounded agent loop: grounding observations, follow-ups and run persistence."""

from pathlib import Path

from app.contracts.enums import Status
from app.contracts.request import VisualizationRequest
from app.pipeline import plan_diff
from app.storage import FileRunStore
from tests.factories import CORPUS, plan
from tests.test_pipeline import FakeRegistry, make_pipeline


async def test_genuine_zero_is_empty_not_a_clarification() -> None:
    drug = '(AREA[InterventionName]"pembrolizumab" OR AREA[InterventionOtherName]"pembrolizumab")'
    both = drug + ' AND AREA[LocationCountry]"Iceland"'
    country = 'AREA[LocationCountry]"Iceland"'
    fake = FakeRegistry({drug: CORPUS, country: CORPUS[:1], both: []})
    p = plan(cohorts=[("pembro", {"drug_name": "pembrolizumab", "country": "Iceland"})])
    response = await make_pipeline(fake, p).run(VisualizationRequest(query="pembro in Iceland"))
    assert response.status is Status.EMPTY  # both entities exist; the combination has no trials
    assert response.visualization is not None and response.meta.interpretation
    assert response.meta.interpretation.planner_attempts == 1


async def test_too_broad_is_detected_at_grounding_before_any_page_fetch() -> None:
    fake = FakeRegistry({"": CORPUS}, total_override=605_357)
    response = await make_pipeline(fake, plan(), max_trials=30_000).run(
        VisualizationRequest(query="all trials by phase")
    )
    assert response.status is Status.NEEDS_CLARIFICATION
    assert response.clarification and "605,357" in response.clarification.question
    assert not [r for r in fake.requests if r.url.params.get("pageSize") == "1000"]


async def test_follow_up_receives_parent_plan_and_reports_the_diff(tmp_path: Path) -> None:
    store = FileRunStore(tmp_path)
    fake = FakeRegistry({"": CORPUS})
    first = plan(dimension="phase")
    second = plan(dimension="phase", cohorts=[("all", {"overall_status": ["RECRUITING"]})])
    pipeline = make_pipeline(fake, first, second, store=store)
    parent = await pipeline.run(VisualizationRequest(query="trials by phase"))
    child = await pipeline.run(
        VisualizationRequest(query="now only recruiting", parent_run_id=parent.run_id)
    )
    assert child.status is Status.OK, child.error
    sent = pipeline.planner.gateway.calls[1][0]["content"]  # type: ignore[union-attr]
    assert '"previous_plan"' in sent and '"dimension": "phase"' in sent
    interp = child.meta.interpretation
    assert interp and interp.parent_run_id == parent.run_id
    assert interp.plan_diff == {
        "cohorts[0].filters.overall_status": {"before": None, "after": ["RECRUITING"]}
    }
    record = store.get(child.run_id)
    assert record and record.request.parent_run_id == parent.run_id


def test_plan_diff_is_empty_for_identical_plans() -> None:
    assert plan_diff(plan(), plan()) == {}
