"""Output gate: independent checks on the finished spec before it leaves the service.

The most important check re-resolves every citation's field_path in the stored source record
and compares it with the cited value, so a citation can never quote something the API did not
return. Any failure turns the response into status=failed; nothing is patched to pass.
"""

import math
from typing import Any

from app.analytics.types import Trial
from app.contracts.response import (
    BarChartSpec,
    Datum,
    GroupedBarChartSpec,
    NetworkGraphSpec,
    TimeSeriesSpec,
    VisualizationSpec,
)
from app.registry import get_path


def verify(spec: VisualizationSpec, trials: dict[str, Trial]) -> list[str]:
    errors: list[str] = []
    data: list[Datum]
    if isinstance(spec, NetworkGraphSpec):
        errors += _check_network(spec)
        data = [*spec.data.nodes, *spec.data.edges]
    else:
        assert isinstance(spec, BarChartSpec | GroupedBarChartSpec | TimeSeriesSpec)
        enc = spec.encoding
        fields = [enc.x.field, enc.y.field] + ([enc.series.field] if enc.series else [])
        for d in spec.data:
            for name in fields:
                if _value(d, name) is None:
                    errors.append(f"{d.datum_id}: encoded field '{name}' missing")
        data = spec.data
        if isinstance(spec, GroupedBarChartSpec) and enc.series is None:
            errors.append("grouped_bar_chart requires a series channel")

    seen_ids: set[str] = set()
    for d in data:
        if d.datum_id in seen_ids:
            errors.append(f"duplicate datum_id {d.datum_id}")
        seen_ids.add(d.datum_id)
        errors += _check_datum(d, trials)
    return errors


def _value(d: Datum, name: str) -> Any:
    if name in type(d).model_fields:
        return getattr(d, name)
    return (d.model_extra or {}).get(name)


def _check_datum(d: Datum, trials: dict[str, Trial]) -> list[str]:
    errors = []
    if d.trial_count < 0 or not math.isfinite(d.trial_count):
        errors.append(f"{d.datum_id}: invalid trial_count {d.trial_count}")
    if d.citation_count != d.trial_count:
        errors.append(f"{d.datum_id}: citation_count {d.citation_count} != trial_count")
    if len(d.citations) > d.citation_count:
        errors.append(f"{d.datum_id}: more citations than citation_count")
    if d.citations_truncated != (len(d.citations) < d.citation_count):
        errors.append(f"{d.datum_id}: citations_truncated flag is wrong")
    if len({c.nct_id for c in d.citations}) != len(d.citations):
        errors.append(f"{d.datum_id}: duplicate NCT ID in citations")
    for c in d.citations:
        trial = trials.get(c.nct_id)
        if trial is None:
            errors.append(f"{d.datum_id}: cited {c.nct_id} was not retrieved")
            continue
        if not c.evidence:
            errors.append(f"{d.datum_id}: citation {c.nct_id} has no evidence")
        for ev in c.evidence:
            actual = get_path(trial.study, ev.field_path)
            if actual != ev.value:
                errors.append(
                    f"{d.datum_id}: {c.nct_id} {ev.field_path} is {actual!r}, cited {ev.value!r}"
                )
    return errors


def _check_network(spec: NetworkGraphSpec) -> list[str]:
    errors = []
    node_ids = [n.id for n in spec.data.nodes]
    if len(set(node_ids)) != len(node_ids):
        errors.append("duplicate node ids")
    known = set(node_ids)
    pairs: set[frozenset[str]] = set()
    for e in spec.data.edges:
        if e.source not in known or e.target not in known:
            errors.append(f"{e.datum_id}: dangling edge {e.source} → {e.target}")
        if e.source == e.target:
            errors.append(f"{e.datum_id}: self-loop on {e.source}")
        pair = frozenset((e.source, e.target))
        if pair in pairs:
            errors.append(f"{e.datum_id}: duplicate edge {e.source} — {e.target}")
        pairs.add(pair)
    return errors
