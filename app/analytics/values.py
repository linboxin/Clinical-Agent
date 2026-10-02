"""The single place operators read dimension values from.

Raw values come from the registry extractors (exact path + raw value). Two optional layers sit
on top, both keeping that evidence unchanged so citations still quote the source record:

1. name normalization (drug and condition names → canonical names, see app.normalize);
2. only_listed_values: keep just the values that match the cohort's own filter list, relabelled
   with the listed name, so "rank these PD-1 inhibitors" ignores co-listed chemotherapy. A drug
   listed under a code (REGN2810) still matches through the synonyms the registry record itself
   gives for that intervention (otherNames: ["cemiplimab"]): deterministic, no model involved,
   and the citation quotes that otherName.
"""

from dataclasses import dataclass, field
from typing import Any

from app.analytics.prepare import literal_match
from app.analytics.types import Trial
from app.contracts.enums import Dimension
from app.contracts.plan import LISTED_DIMENSION, CohortFilters, QueryPlan
from app.registry import INTERVENTIONS_PATH, REGISTRY, FieldValue, get_path, norm_name

NameMap = dict[str, list[str]]  # registry grouping key → canonical names ([] = not an entity)


@dataclass
class ValueView:
    plan: QueryPlan
    names: dict[Dimension, NameMap] = field(default_factory=dict)

    def values(self, dim: Dimension, trial: Trial, filters: CohortFilters) -> list[FieldValue]:
        values = REGISTRY[dim].extract(trial.study, self.plan.phase_policy)
        mapping = self.names.get(dim)
        if mapping is not None:
            values = _dedupe(
                FieldValue(norm_name(name), name, v.path, v.raw) if v.key in mapping else v
                for v in values
                for name in mapping.get(v.key, [v.label])
            )
        if self.plan.operation.only_listed_values and dim in LISTED_DIMENSION:
            listed = getattr(filters, LISTED_DIMENSION[dim]) or []
            values = _dedupe(
                FieldValue(norm_name(term), term, *hit)
                for v in values
                for term in listed
                if (hit := _listed_match(term, v, trial)) is not None
            )
        return values


def _listed_match(term: str, value: FieldValue, trial: Trial) -> tuple[str, Any] | None:
    """Evidence (path, raw) that `value` is the listed `term`: the value itself, or for an
    intervention name, one of the otherNames the registry lists for that same intervention."""
    if literal_match(term, value.label) or literal_match(term, str(value.raw)):
        return value.path, value.raw
    if value.path.startswith(INTERVENTIONS_PATH) and value.path.endswith(".name"):
        base = value.path.removesuffix(".name")
        for j, other in enumerate(get_path(trial.study, f"{base}.otherNames") or []):
            if isinstance(other, str) and literal_match(term, other):
                return f"{base}.otherNames[{j}]", other
    return None


def _dedupe(values: object) -> list[FieldValue]:
    seen: dict[str, FieldValue] = {}
    for v in values:  # type: ignore[attr-defined]
        seen.setdefault(v.key, v)
    return list(seen.values())
