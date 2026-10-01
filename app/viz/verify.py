"""Output gate: independent checks on the finished spec before it leaves the service.

The most important check re-resolves every citation's field_path in the stored source record
and compares it with the cited excerpt, so a citation can never quote something the API did
not return. Any failure turns the response into status=failed; nothing is patched to pass.
"""

import math
from typing import Any

from app.analytics.types import Trial
from app.contracts.response import (
    CARTESIAN_SPECS,
    Datum,
    GroupedBarChartSpec,
    NetworkGraphSpec,
    PieChartSpec,
    StackedBarChartSpec,
    VisualizationSpec,
)
from app.registry import get_path


def verify(spec: VisualizationSpec, trials: dict[str, Trial]) -> list[str]:
    errors: list[str] = []
    data: list[Datum]
    if isinstance(spec, NetworkGraphSpec):
        errors += _check_network(spec)
        data = [*spec.data.nodes, *spec.data.edges]
    elif isinstance(spec, PieChartSpec):
        data = spec.data
        errors += _check_fields(data, [spec.encoding.theta.field, spec.encoding.color.field])
    else:
        assert isinstance(spec, CARTESIAN_SPECS)
        enc = spec.encoding
        data = spec.data
        errors += _check_fields(
            data, [enc.x.field, enc.y.field] + ([enc.series.field] if enc.series else [])
        )
        if isinstance(spec, GroupedBarChartSpec | StackedBarChartSpec) and enc.series is None:
            errors.append(f"{spec.type} requires a series channel")

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


def _check_fields(data: list[Datum], fields: list[str]) -> list[str]:
    return [
        f"{d.datum_id}: encoded field '{name}' missing"
        for d in data
        for name in fields
        if _value(d, name) is None
    ]


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
            if actual is None or actual != ev.excerpt:
                errors.append(
                    f"{d.datum_id}: {c.nct_id} {ev.field_path} is {actual!r}, cited {ev.excerpt!r}"
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
