"""Deterministic scorers for planner evals: expected plan fields vs the produced plan.

No LLM judge: expected plans are structured, so exact comparison is cheaper, reproducible and
cannot itself hallucinate. String values match by tokens (case-insensitive, possessives and
plurals ignored) so "Alzheimer's disease" satisfies an expected "alzheimer".
"""

from dataclasses import dataclass, field
from typing import Any

from app.analytics.prepare import literal_match


@dataclass
class CaseScore:
    status_ok: bool
    fields_total: int
    fields_ok: int
    mismatches: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status_ok and self.fields_ok == self.fields_total


def _get(node: Any, path: str) -> Any:
    for part in path.replace("[", ".[").split("."):
        if not part:
            continue
        if part.startswith("["):
            index = int(part[1:-1])
            node = node[index] if isinstance(node, list) and index < len(node) else None
        else:
            node = node.get(part) if isinstance(node, dict) else None
    return node


def _equal(expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return False
        return all(any(_equal(e, a) for a in actual) for e in expected)
    if isinstance(expected, str) and isinstance(actual, str):
        return literal_match(expected, actual)
    return bool(expected == actual)


def _check(plan: dict[str, Any], key: str, expected: Any) -> bool:
    cohorts = plan.get("cohorts") or []
    if key == "cohorts.length":
        return len(cohorts) == expected
    if key == "network_nodes":
        op = plan.get("operation") or {}
        return {op.get("dimension"), op.get("second_dimension")} == set(expected)
    if key.startswith("cohorts[*]."):
        rest = key.removeprefix("cohorts[*].")
        return bool(cohorts) and all(_equal(expected, _get(c, rest)) for c in cohorts)
    if key.startswith("cohorts[]."):
        rest = key.removeprefix("cohorts[].")
        values = [_get(c, rest) for c in cohorts]
        return len(values) == len(expected) and all(
            any(_equal(e, v) for v in values) for e in expected
        )
    return _equal(expected, _get(plan, key))


def score_case(expect: dict[str, Any], status: str, plan: dict[str, Any] | None) -> CaseScore:
    allowed = expect["status"] if isinstance(expect["status"], list) else [expect["status"]]
    wanted: dict[str, Any] = expect.get("plan") or {}
    score = CaseScore(status_ok=status in allowed, fields_total=len(wanted), fields_ok=0)
    if not score.status_ok:
        score.mismatches.append(f"status {status!r} not in {allowed}")
    for key, value in wanted.items():
        if plan is not None and _check(plan, key, value):
            score.fields_ok += 1
        else:
            actual = None if plan is None else _get(plan, key.replace("[*]", "[0]"))
            score.mismatches.append(f"{key}: expected {value!r}, got {actual!r}")
    return score
