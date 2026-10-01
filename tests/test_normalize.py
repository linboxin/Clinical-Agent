"""Guarded name normalization: the model may only map the names it was given, every answer
is validated, answers are cached, and citations keep quoting the raw registry values."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.analytics import run_analysis
from app.analytics.prepare import prepare_cohort
from app.analytics.types import CountResult
from app.analytics.values import ValueView
from app.contracts.enums import Dimension, Status
from app.contracts.request import VisualizationRequest
from app.normalize import Label, Labels, OpenAINameNormalizer, check_labels
from tests.factories import plan, study
from tests.test_pipeline import FakeRegistry, make_pipeline

DRUGS = [
    study("NCT1", interventions=[("DRUG", "Keytruda")]),
    study("NCT2", interventions=[("BIOLOGICAL", "pembrolizumab 200 mg")]),
    study("NCT3", interventions=[("DRUG", "Nivolumab & Ipilimumab"), ("DRUG", "Saline")]),
]
MAPPING = {
    "Keytruda": ["pembrolizumab"],
    "pembrolizumab 200 mg": ["pembrolizumab"],
    "Nivolumab & Ipilimumab": ["nivolumab", "ipilimumab"],
    "Saline": [],
}


class FakeNormalizer:
    model = "fake-normalizer"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, dimension: Dimension, names: list[str]) -> dict[str, list[str]]:
        self.calls.append(names)
        return {n: MAPPING[n] for n in names if n in MAPPING}


def test_check_labels_keeps_only_valid_answers() -> None:
    ok = Labels(labels=[Label(index=0, canonical=["a"]), Label(index=1, canonical=[])])
    assert check_labels(ok, 2) == (ok.labels, None)
    usable, problem = check_labels(Labels(labels=[Label(index=0, canonical=["a"])]), 2)
    assert [lab.index for lab in usable] == [0] and "not answered" in (problem or "")
    bad = Labels(
        labels=[
            Label(index=0, canonical=[" "]),  # malformed
            Label(index=0, canonical=["x"]),  # duplicate
            Label(index=7, canonical=["y"]),  # out of range
        ]
    )
    usable, problem = check_labels(bad, 1)
    assert usable == [] and "1-80 characters" in (problem or "")


def test_value_view_merges_splits_and_drops_but_cites_raw_values() -> None:
    p = plan(dimension="drug")
    ct, _ = prepare_cohort(p.cohorts[0], DRUGS, p.time)
    keys = {
        "keytruda": ["pembrolizumab"],
        "pembrolizumab": ["pembrolizumab"],
        "nivolumab & ipilimumab": ["nivolumab", "ipilimumab"],
        "saline": [],
    }
    result = run_analysis(p, [ct], ValueView(p, names={Dimension.DRUG: keys}))
    assert isinstance(result, CountResult)
    assert {r.label: sorted(r.bucket.contributors) for r in result.rows} == {
        "pembrolizumab": ["NCT1", "NCT2"],
        "nivolumab": ["NCT3"],
        "ipilimumab": ["NCT3"],
    }
    pembro = next(r for r in result.rows if r.label == "pembrolizumab")
    assert pembro.bucket.contributors["NCT1"] == [
        ("protocolSection.armsInterventionsModule.interventions[0].name", "Keytruda")
    ]


async def test_pipeline_discloses_normalization_and_still_passes_the_gate() -> None:
    pipeline = make_pipeline(FakeRegistry({"": DRUGS}), plan(dimension="drug"))
    pipeline.normalizer = FakeNormalizer()
    response = await pipeline.run(VisualizationRequest(query="most common drugs"))
    assert response.status is Status.OK, response.error  # gate re-checked every raw excerpt
    info = response.meta.normalization[0]
    assert (info.dimension, info.model, info.names_in) == ("drug", "fake-normalizer", 4)
    assert info.dropped == ["Saline"]
    assert info.merges[0].canonical == "pembrolizumab"
    assert info.merges[0].variants == ["Keytruda", "pembrolizumab 200 mg"]
    assert "drug_names" in response.meta.policies


class ScriptedResponses:
    """Stands in for openai.AsyncOpenAI().responses: returns prepared parsed outputs."""

    def __init__(self, *outputs: Labels) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    async def parse(self, **_: Any) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(output_parsed=self.outputs.pop(0))


async def test_openai_normalizer_retries_once_then_caches(tmp_path: Path) -> None:
    incomplete = Labels(labels=[Label(index=0, canonical=["pembrolizumab"])])
    complete = Labels(
        labels=[Label(index=0, canonical=["pembrolizumab"]), Label(index=1, canonical=[])]
    )
    responses = ScriptedResponses(incomplete, complete)
    cache = tmp_path / "names.json"
    normalizer = OpenAINameNormalizer(SimpleNamespace(responses=responses), "m", cache)  # type: ignore[arg-type]
    first = await normalizer(Dimension.DRUG, ["Keytruda", "Placebo"])
    assert first == {"Keytruda": ["pembrolizumab"], "Placebo": []}
    assert responses.calls == 2  # the invalid answer was sent back once

    again = OpenAINameNormalizer(SimpleNamespace(responses=responses), "m", cache)  # type: ignore[arg-type]
    assert await again(Dimension.DRUG, ["Keytruda"]) == {"Keytruda": ["pembrolizumab"]}
    assert responses.calls == 2  # served from the on-disk cache


async def test_a_partial_answer_maps_only_validated_lines(tmp_path: Path) -> None:
    partial = Labels(labels=[Label(index=0, canonical=["pembrolizumab"])])
    normalizer = OpenAINameNormalizer(
        SimpleNamespace(responses=ScriptedResponses(partial, partial)),  # type: ignore[arg-type]
        "m",
        tmp_path / "n.json",
    )
    assert await normalizer(Dimension.DRUG, ["Keytruda", "Opdivo"]) == {
        "Keytruda": ["pembrolizumab"]
    }  # "Opdivo" stays raw


async def test_an_unusable_answer_leaves_names_unmapped(tmp_path: Path) -> None:
    bad = Labels(labels=[])
    normalizer = OpenAINameNormalizer(
        SimpleNamespace(responses=ScriptedResponses(bad, bad)),
        "m",
        tmp_path / "n.json",  # type: ignore[arg-type]
    )
    assert await normalizer(Dimension.DRUG, ["Keytruda"]) == {}
