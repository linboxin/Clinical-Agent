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
    # Why this trial is in its cohort (matching intervention name, phase, status, …). Prepended
    # to every datum's evidence so a citation explains both cohort membership and placement.
    membership: tuple[EvidenceItem, ...] = ()


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

    def add(self, trial: Trial, label: str, evidence: list[EvidenceItem]) -> bool:
        """Add a contributing trial; False if it already counts here (once per datum)."""
        if trial.nct_id in self.contributors:
            return False
        self.labels[label] += 1
        items: list[EvidenceItem] = []
        for path, raw in (*trial.membership, *evidence):
            if all(path != p for p, _ in items):
                items.append((path, raw))
        self.contributors[trial.nct_id] = items
        return True

    @property
    def label(self) -> str:
        # Most common source spelling; ties broken alphabetically for determinism.
        return min(self.labels.items(), key=lambda kv: (-kv[1], kv[0]))[0] if self.labels else ""

    @property
    def count(self) -> int:
        return len(self.contributors)


@dataclass
class Row:
    """A bar / time bucket / histogram bin. `series` is a cohort or second-dimension label."""

    key: str
    label: str
    series: str | None
    bucket: Bucket
    fields: dict[str, Any] = field(default_factory=dict)  # extra row fields, e.g. bin_start


@dataclass
class TruncationInfo:
    shown: int
    total: int
    rule: str


@dataclass
class CountResult:
    kind: Literal["count_by", "time_trend", "histogram"]
    rows: list[Row]
    category_order: list[str]  # x-axis labels in display order
    series_order: list[str] | None
    series_source: Literal["cohort", "dimension"] | None
    sort_description: str
    missing: dict[str, dict[str, int]]  # cohort label → {field: trials without a value}
    truncation: TruncationInfo | None = None
    # Exclusivity decides which charts are honest: a pie needs exclusive categories and
    # stacked bars need exclusive series (each trial in at most one), or totals double-count.
    categories_exclusive: bool = False
    series_exclusive: bool = False


@dataclass
class Point:
    trial: Trial
    x: Any
    y: Any
    color: str | None
    bucket: Bucket  # exactly one contributor: the trial itself


@dataclass
class ScatterResult:
    points: list[Point]
    color_order: list[str] | None
    missing: dict[str, dict[str, int]]
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
    bipartite: bool
    missing: dict[str, dict[str, int]]
    truncation: TruncationInfo | None = None


AnalysisResult = CountResult | ScatterResult | NetworkResult
