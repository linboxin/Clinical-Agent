"""Field registry: the single place that knows how a dimension maps onto the source record.

Each DimensionSpec says where a value lives in a ClinicalTrials.gov v2 study, how it is
normalized for grouping, how it is labelled and ordered, and what it can be used for.
Extractors return FieldValues that keep the exact JSON path and raw value, so every
grouped count can cite its source field. Adding a dimension = one entry here + tests.
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.contracts.enums import PHASE_LABELS, DateBasis, Dimension, PhasePolicy

Study = dict[str, Any]

PS = "protocolSection"


@dataclass(frozen=True)
class FieldValue:
    key: str  # normalized grouping key
    label: str  # display label
    path: str  # exact JSON path in the study record
    raw: Any  # exact value at that path


@dataclass(frozen=True)
class DateValue:
    year: int
    raw: str
    path: str
    estimated: bool


# --- Generic helpers ------------------------------------------------------------------------

_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def get_path(record: Any, path: str) -> Any:
    """Resolve 'a.b[2].c' inside a JSON-like structure; None if any hop is missing."""
    node = record
    for name, index in _PATH_TOKEN.findall(path):
        if name:
            if not isinstance(node, dict) or name not in node:
                return None
            node = node[name]
        else:
            i = int(index)
            if not isinstance(node, list) or i >= len(node):
                return None
            node = node[i]
    return node


def norm_name(text: str) -> str:
    """Grouping key for free-text names: Unicode-normalized, trademark marks dropped,
    case-folded and whitespace-collapsed. Deliberately conservative: no fuzzy merging."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[®™©]", "", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _enum_label(value: str, overrides: dict[str, str] | None = None) -> str:
    if overrides and value in overrides:
        return overrides[value]
    return value.replace("_", " ").capitalize()


def _single(path: str, labels: dict[str, str] | None = None) -> Callable[..., list[FieldValue]]:
    """Extractor for a scalar enum-like field."""

    def extract(study: Study, _policy: PhasePolicy) -> list[FieldValue]:
        raw = get_path(study, path)
        if not isinstance(raw, str) or not raw:
            return []
        return [FieldValue(raw, _enum_label(raw, labels), path, raw)]

    return extract


def _name(path: str) -> Callable[..., list[FieldValue]]:
    """Extractor for a scalar free-text name (e.g. lead sponsor)."""

    def extract(study: Study, _policy: PhasePolicy) -> list[FieldValue]:
        raw = get_path(study, path)
        if not isinstance(raw, str) or not raw.strip():
            return []
        return [FieldValue(norm_name(raw), raw.strip(), path, raw)]

    return extract


def _list_items(
    list_path: str,
    item_field: str | None,
    keyer: Callable[[str], str],
    labeler: Callable[[str], str],
    keep: Callable[[dict[str, Any]], bool] | None = None,
) -> Callable[..., list[FieldValue]]:
    """Extractor for list fields; one FieldValue per distinct key, first occurrence wins."""

    def extract(study: Study, _policy: PhasePolicy) -> list[FieldValue]:
        items = get_path(study, list_path) or []
        seen: set[str] = set()
        values: list[FieldValue] = []
        for i, item in enumerate(items):
            if item_field is None:
                raw, path = item, f"{list_path}[{i}]"
            else:
                if not isinstance(item, dict) or (keep and not keep(item)):
                    continue
                raw, path = item.get(item_field), f"{list_path}[{i}].{item_field}"
            if not isinstance(raw, str) or not raw.strip():
                continue
            key = keyer(raw)
            if key in seen:
                continue
            seen.add(key)
            values.append(FieldValue(key, labeler(raw), path, raw))
        return values

    return extract


# --- Phase ----------------------------------------------------------------------------------

PHASES_PATH = f"{PS}.designModule.phases"
PHASE_ORDER = (
    "EARLY_PHASE1",
    "PHASE1",
    "PHASE1+PHASE2",
    "PHASE2",
    "PHASE2+PHASE3",
    "PHASE3",
    "PHASE4",
    "NA",
)


def _phase_label(phases: list[str]) -> str:
    nums = [p.removeprefix("PHASE") for p in phases]
    if len(phases) > 1 and all(n.isdigit() for n in nums):
        return "Phase " + "/".join(nums)
    return " / ".join(PHASE_LABELS.get(p, p) for p in phases)


def _extract_phase(study: Study, policy: PhasePolicy) -> list[FieldValue]:
    raw = get_path(study, PHASES_PATH)
    if not isinstance(raw, list) or not raw:
        return []
    if policy is PhasePolicy.SPLIT:
        return [
            FieldValue(p, PHASE_LABELS.get(p, p), f"{PHASES_PATH}[{i}]", p)
            for i, p in enumerate(raw)
        ]
    return [FieldValue("+".join(raw), _phase_label(raw), PHASES_PATH, raw)]


# --- Interventions --------------------------------------------------------------------------

INTERVENTIONS_PATH = f"{PS}.armsInterventionsModule.interventions"
DRUG_TYPES = {"DRUG", "BIOLOGICAL"}
_PLACEBO = re.compile(r"\bplacebo\b", re.IGNORECASE)


def _is_drug(item: dict[str, Any]) -> bool:
    return item.get("type") in DRUG_TYPES and not _PLACEBO.search(item.get("name") or "")


SPONSOR_CLASS_LABELS = {
    "INDUSTRY": "Industry",
    "NIH": "NIH",
    "FED": "U.S. federal (non-NIH)",
    "OTHER_GOV": "Other government",
    "INDIV": "Individual",
    "NETWORK": "Network",
    "OTHER": "Other (academic, hospital, non-profit)",
    "UNKNOWN": "Unknown",
    "AMBIG": "Ambiguous",
}

STATUS_LABELS = {"ACTIVE_NOT_RECRUITING": "Active, not recruiting"}


# --- Registry -------------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionSpec:
    name: Dimension
    label: str
    description: str
    kind: Literal["category", "entity"]  # entity = free-text names, usable as network nodes
    extract: Callable[[Study, PhasePolicy], list[FieldValue]]
    order: tuple[str, ...] | None = None  # fixed display order (ordinal); else by count
    default_top_n: int | None = None  # cap for high-cardinality dimensions


REGISTRY: dict[Dimension, DimensionSpec] = {
    spec.name: spec
    for spec in (
        DimensionSpec(
            Dimension.PHASE,
            "Phase",
            "Trial phase (Early Phase 1 … Phase 4, Not applicable). Multi-phase trials such as "
            "Phase 1/2 are one combined category unless phase_policy is split.",
            "category",
            _extract_phase,
            order=PHASE_ORDER,
        ),
        DimensionSpec(
            Dimension.OVERALL_STATUS,
            "Overall status",
            "Current recruitment status (Recruiting, Completed, Terminated, …).",
            "category",
            _single(f"{PS}.statusModule.overallStatus", STATUS_LABELS),
        ),
        DimensionSpec(
            Dimension.STUDY_TYPE,
            "Study type",
            "Interventional, Observational or Expanded access.",
            "category",
            _single(f"{PS}.designModule.studyType"),
        ),
        DimensionSpec(
            Dimension.LEAD_SPONSOR,
            "Lead sponsor",
            "Lead sponsor organisation name.",
            "entity",
            _name(f"{PS}.sponsorCollaboratorsModule.leadSponsor.name"),
            default_top_n=20,
        ),
        DimensionSpec(
            Dimension.SPONSOR_CLASS,
            "Sponsor category",
            "Lead sponsor category: Industry, NIH, other government, academic/other, …",
            "category",
            _single(f"{PS}.sponsorCollaboratorsModule.leadSponsor.class", SPONSOR_CLASS_LABELS),
        ),
        DimensionSpec(
            Dimension.DRUG,
            "Drug",
            "Drug or biological interventions listed on the trial (placebos excluded).",
            "entity",
            _list_items(INTERVENTIONS_PATH, "name", norm_name, str.strip, keep=_is_drug),
            default_top_n=20,
        ),
        DimensionSpec(
            Dimension.INTERVENTION_TYPE,
            "Intervention type",
            "Type of each listed intervention: Drug, Biological, Device, Procedure, …",
            "category",
            _list_items(INTERVENTIONS_PATH, "type", str, _enum_label),
        ),
        DimensionSpec(
            Dimension.CONDITION,
            "Condition",
            "Conditions/diseases listed on the trial.",
            "entity",
            _list_items(f"{PS}.conditionsModule.conditions", None, norm_name, str.strip),
            default_top_n=20,
        ),
        DimensionSpec(
            Dimension.COUNTRY,
            "Country",
            "Countries of the trial's listed locations (a trial counts once per country).",
            "entity",
            _list_items(f"{PS}.contactsLocationsModule.locations", "country", norm_name, str.strip),
            default_top_n=25,
        ),
    )
}

assert set(REGISTRY) == set(Dimension), "every Dimension needs a registry entry"


# --- Dates ----------------------------------------------------------------------------------

DATE_PATHS: dict[DateBasis, str] = {
    DateBasis.START_DATE: f"{PS}.statusModule.startDateStruct",
    DateBasis.FIRST_POSTED: f"{PS}.statusModule.studyFirstPostDateStruct",
    DateBasis.COMPLETION_DATE: f"{PS}.statusModule.completionDateStruct",
}
DATE_LABELS: dict[DateBasis, str] = {
    DateBasis.START_DATE: "study start date",
    DateBasis.FIRST_POSTED: "first posted date",
    DateBasis.COMPLETION_DATE: "completion date",
}
# ClinicalTrials.gov field names used in Essie AREA[...] date ranges.
DATE_API_FIELDS: dict[DateBasis, str] = {
    DateBasis.START_DATE: "StartDate",
    DateBasis.FIRST_POSTED: "StudyFirstPostDate",
    DateBasis.COMPLETION_DATE: "CompletionDate",
}


def extract_date(study: Study, basis: DateBasis) -> DateValue | None:
    """Year of the chosen date. Keeps the raw string (precision may be YYYY-MM)."""
    struct_path = DATE_PATHS[basis]
    raw = get_path(study, f"{struct_path}.date")
    if not isinstance(raw, str) or not re.match(r"^\d{4}", raw):
        return None
    estimated = get_path(study, f"{struct_path}.type") == "ESTIMATED"
    return DateValue(int(raw[:4]), raw, f"{struct_path}.date", estimated)


# --- Identity / projection ------------------------------------------------------------------

NCT_PATH = f"{PS}.identificationModule.nctId"
TITLE_PATH = f"{PS}.identificationModule.briefTitle"

# Field projection requested from the API: only what the registry can read.
API_FIELDS = (
    "NCTId",
    "BriefTitle",
    "OverallStatus",
    "StudyType",
    "Phase",
    "StartDate",
    "StartDateType",
    "StudyFirstPostDate",
    "StudyFirstPostDateType",
    "CompletionDate",
    "CompletionDateType",
    "LeadSponsorName",
    "LeadSponsorClass",
    "InterventionName",
    "InterventionType",
    "InterventionOtherName",
    "Condition",
    "LocationCountry",
)

NETWORK_DIMENSIONS = tuple(d for d, s in REGISTRY.items() if s.kind == "entity")


def study_url(nct_id: str) -> str:
    return f"https://clinicaltrials.gov/study/{nct_id}"
