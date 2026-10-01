"""Data shapes passed between retrieval, the operators and the spec builder."""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from app.contracts.plan import Cohort

EvidenceItem = tuple[str, Any]  # (exact JSON path, exact raw value)


@dataclass(frozen=True)
class Trial:
    nct_id: str
    brief_title: str | None
    study: dict[str, Any]  # raw (field-projected) API record — the citation source of truth


@dataclass
class CohortTrials:
    cohort: Cohort
    trials: list[Trial]
    excluded: dict[str, int] = field(default_factory=dict)


@dataclass
class Bucket:
    """One datum under construction. Its count IS the size of its contributor set, so a
    count can never disagree with the citations derived from it."""

    key: str
    labels: Counter[str] = field(default_factory=Counter)
    contributors: dict[str, list[EvidenceItem]] = field(default_factory=dict)
    extra: Counter[str] = field(default_factory=Counter)

    def add(self, nct_id: str, label: str, evidence: list[EvidenceItem]) -> None:
        if nct_id in self.contributors:
            return  # a trial counts once per datum
        self.labels[label] += 1
        self.contributors[nct_id] = evidence

    @property
    def label(self) -> str:
        # Most common source spelling; ties broken alphabetically for determinism.
        return min(self.labels.items(), key=lambda kv: (-kv[1], kv[0]))[0] if self.labels else ""

    @property
    def count(self) -> int:
        return len(self.contributors)


@dataclass
class Row:
    """A bar / time bucket. `series` is a cohort label or a second-dimension label."""

    key: str
    label: str
    series: str | None
    bucket: Bucket


@dataclass
class TruncationInfo:
    shown: int
    total: int
    rule: str


@dataclass
class CountResult:
    kind: Literal["count_by", "time_trend"]
    rows: list[Row]
    category_order: list[str]  # x-axis labels in display order
    series_order: list[str] | None
    series_source: Literal["cohort", "dimension"] | None
    sort_description: str
    missing: dict[str, dict[str, int]]  # cohort label → {field: trials without a value}
    truncation: TruncationInfo | None = None


@dataclass
class Node:
    id: str
    entity_type: str
    bucket: Bucket


@dataclass
class Edge:
    source: str
    target: str
    relation: str
    bucket: Bucket


@dataclass
class NetworkResult:
    nodes: list[Node]
    edges: list[Edge]
    missing: dict[str, dict[str, int]]
    truncation: TruncationInfo | None = None


AnalysisResult = CountResult | NetworkResult
