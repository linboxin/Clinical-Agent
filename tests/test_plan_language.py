"""The general plan-language building blocks: value lists, exclusions, class expansion,
counting only listed values, declared unhandled constraints and filter-derived labels.

The PD-1 question ("Excluding Keytruda, which PD-1 inhibitors have the most Phase 3 trials?")
is answered by composing these blocks; nothing in the code knows about PD-1.
"""

from app.analytics import run_analysis
from app.analytics.prepare import prepare_cohort
from app.analytics.types import CountResult
from app.contracts.enums import Status
from app.contracts.plan import Expansion
from app.contracts.request import VisualizationRequest
from app.ctgov.compile import compile_cohort
from app.planner import Planner
from app.planner.validate import normalize_plan, validate_plan
from app.viz.build import cohort_names, relabel
from tests.factories import filters, plan, study
from tests.test_pipeline import FakeRegistry, make_pipeline
from tests.test_planner import ScriptedGateway

PD1 = ["nivolumab", "cemiplimab", "tislelizumab"]
PD1_PLAN = plan(
    dimension="drug",
    cohorts=[
        (
            "x",
            {
                "drug_names": PD1,
                "trial_phase": ["PHASE3"],
                "exclude_drug_names": ["pembrolizumab"],
            },
        )
    ],
    only_listed_values=True,
    expansions=[Expansion(term="PD-1 inhibitors", field="drug_names", members=PD1)],
)
CORPUS = [
    study("NCT1", phases=["PHASE3"], interventions=[("DRUG", "Nivolumab"), ("DRUG", "Cisplatin")]),
    study("NCT2", phases=["PHASE3"], interventions=[("DRUG", "Nivolumab & Ipilimumab")]),
    study("NCT3", phases=["PHASE3"], interventions=[("DRUG", "Cemiplimab 350 mg")]),
    study(  # lists the excluded drug: must be dropped even if the API returned it
        "NCT4",
        phases=["PHASE3"],
        interventions=[("DRUG", "Tislelizumab"), ("DRUG", "Pembrolizumab")],
    ),
]
PD1_TERM = (
    '(AREA[InterventionName]"nivolumab" OR AREA[InterventionOtherName]"nivolumab" OR '
    'AREA[InterventionName]"cemiplimab" OR AREA[InterventionOtherName]"cemiplimab" OR '
    'AREA[InterventionName]"tislelizumab" OR AREA[InterventionOtherName]"tislelizumab") AND '
    "AREA[Phase](PHASE3) AND "
    'NOT (AREA[InterventionName]"pembrolizumab" OR AREA[InterventionOtherName]"pembrolizumab")'
)


def test_lists_compile_to_or_and_exclusions_to_not() -> None:
    p = compile_cohort(PD1_PLAN.cohorts[0].filters, PD1_PLAN.time)
    assert p["query.term"] == PD1_TERM
    both = compile_cohort(
        filters(
            conditions=["breast cancer", "prostate cancer"], exclude_countries=["United States"]
        ),
        PD1_PLAN.time,
    )
    assert both["query.cond"] == '"breast cancer" OR "prostate cancer"'
    assert both["query.term"] == 'NOT AREA[LocationCountry]"United States"'


def test_only_listed_values_ranks_the_listed_drugs_and_ignores_partners() -> None:
    ct, _ = prepare_cohort(PD1_PLAN.cohorts[0], CORPUS, PD1_PLAN.time)
    assert ct.excluded == {"excluded_drug_listed": 1}  # NCT4 lists pembrolizumab
    result = run_analysis(PD1_PLAN, [ct])
    assert isinstance(result, CountResult)
    assert [(r.label, sorted(r.bucket.contributors)) for r in result.rows] == [
        ("nivolumab", ["NCT1", "NCT2"]),  # the combination "Nivolumab & Ipilimumab" counts
        ("cemiplimab", ["NCT3"]),
    ]
    # The citation still quotes the raw registry value that matched.
    assert (
        "protocolSection.armsInterventionsModule.interventions[0].name",
        "Cemiplimab 350 mg",
    ) in (result.rows[1].bucket.contributors["NCT3"])


def test_validation_rules_for_lists_and_expansions() -> None:
    assert validate_plan(PD1_PLAN) == []
    no_list = plan(dimension="drug", only_listed_values=True)
    assert any("needs every cohort to set filters.drug_names" in e for e in validate_plan(no_list))
    wrong_dim = plan(dimension="phase", only_listed_values=True)
    assert any("only_listed_values needs" in e for e in validate_plan(wrong_dim))
    stray = plan(
        cohorts=[("x", {"drug_names": ["nivolumab"]})],
        expansions=[Expansion(term="PD-1", field="drug_names", members=["nivolumab", "made-up"])],
    )
    assert any("['made-up'] must also appear" in e for e in validate_plan(stray))
    clash = plan(cohorts=[("x", {"drug_names": ["a"], "exclude_drug_names": ["A"]})])
    assert any("both included and excluded" in e for e in validate_plan(clash))


def test_normalize_plan_tidies_lists() -> None:
    messy = plan(cohorts=[("x", {"drug_names": [" Nivolumab ", "nivolumab", ""], "countries": []})])
    tidy = normalize_plan(messy).cohorts[0].filters
    assert tidy.drug_names == ["Nivolumab"] and tidy.countries is None


def test_labels_and_titles_come_from_filters_not_the_model() -> None:
    assert cohort_names(PD1_PLAN) == ["PD-1 inhibitors · Phase 3 · excluding pembrolizumab"]
    compare = plan(
        cohorts=[
            ("whatever the model wrote", {"drug_name": "pembrolizumab", "condition": "melanoma"}),
            ("another label", {"drug_name": "nivolumab", "condition": "melanoma"}),
        ]
    )
    relabelled = relabel(compare)
    assert [c.label for c in relabelled.cohorts] == ["pembrolizumab", "nivolumab"]
    from app.viz.build import _title

    assert _title(relabelled) == "Trials by phase — pembrolizumab vs nivolumab (melanoma)"


async def test_unhandled_constraint_becomes_a_clarification_not_a_dropped_clause() -> None:
    p = plan(unhandled_constraints=["only placebo-controlled trials"])
    outcome = await Planner(ScriptedGateway(p)).plan(VisualizationRequest(query="q"))
    assert outcome.status == "clarification" and outcome.clarification
    assert "placebo-controlled" in outcome.clarification.question


async def test_an_invented_class_member_is_caught_by_grounding() -> None:
    real = '(AREA[InterventionName]"nivolumab" OR AREA[InterventionOtherName]"nivolumab")'
    both = (
        '(AREA[InterventionName]"nivolumab" OR AREA[InterventionOtherName]"nivolumab" OR '
        'AREA[InterventionName]"fakemab" OR AREA[InterventionOtherName]"fakemab")'
    )
    fake = FakeRegistry({real: CORPUS[:1], both: CORPUS[:1]})
    invented = plan(
        dimension="drug",
        cohorts=[("x", {"drug_names": ["nivolumab", "fakemab"]})],
        only_listed_values=True,
        expansions=[
            Expansion(term="PD-1 inhibitors", field="drug_names", members=["nivolumab", "fakemab"])
        ],
    )
    fixed = plan(
        dimension="drug",
        cohorts=[("x", {"drug_names": ["nivolumab"]})],
        only_listed_values=True,
        expansions=[Expansion(term="PD-1 inhibitors", field="drug_names", members=["nivolumab"])],
    )
    pipeline = make_pipeline(fake, invented, fixed)
    response = await pipeline.run(VisualizationRequest(query="PD-1 inhibitors by trial count"))
    assert response.status is Status.OK, response.error
    interp = response.meta.interpretation
    assert interp and interp.planner_attempts == 2
    assert "member 'fakemab'" in interp.repair_feedback[0]


async def test_pd1_question_end_to_end() -> None:
    # Grounding also probes each class member on its own.
    alone = {
        f'(AREA[InterventionName]"{m}" OR AREA[InterventionOtherName]"{m}")': CORPUS for m in PD1
    }
    fake = FakeRegistry({PD1_TERM: CORPUS, **alone})
    response = await make_pipeline(fake, PD1_PLAN).run(
        VisualizationRequest(
            query="Excluding Keytruda, which PD-1 inhibitors have the most Phase 3 trials?"
        )
    )
    assert response.status is Status.OK, response.error
    spec = response.visualization
    assert spec is not None and spec.type == "bar_chart"
    assert [(d.model_extra["drug"], d.trial_count) for d in spec.data] == [  # type: ignore[union-attr,index]
        ("nivolumab", 2),
        ("cemiplimab", 1),
    ]
    assert spec.title == "Trials by drug — PD-1 inhibitors · Phase 3 · excluding pembrolizumab"
    assert any("'PD-1 inhibitors' was interpreted as" in a for a in response.meta.assumptions)
    assert response.meta.cohorts[0].excluded == {"excluded_drug_listed": 1}
