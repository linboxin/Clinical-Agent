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

from app.contracts.enums import PHASE_LABELS, DateBasis, Dimension, Measure, PhasePolicy

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


# Drug names: dose/route suffixes and salt forms are dropped from the *grouping key* only
# (labels keep the most common source spelling). Measured on lung-cancer trials: raw names
# split "erlotinib" / "erlotinib hydrochloride" and "osimertinib" / "osimertinib 80 mg".
_DOSE = re.compile(
    r"\s*[\(\[]?\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|ug|g|ml|iu|units?|mg/kg|mg/m2|mg/m²|%)(?:/\w+)?\b.*$"
)
_SALT = re.compile(
    r"\s+(?:hydrochloride|dihydrochloride|hcl|mesylate|dimesylate|maleate|tosylate|citrate|"
    r"sulfate|sulphate|phosphate|acetate|besylate|succinate|tartrate|fumarate|malate|"
    r"sodium|potassium|calcium)$"
)


def drug_key(text: str) -> str:
    key = _DOSE.sub("", norm_name(text)).strip(" ,;-")
    key = _SALT.sub("", key).strip()
    return key or norm_name(text)


# Multi-site sponsors suffix facility names with site numbers: "Erasmus MC ( Site 5303)".
_SITE_NUMBER = re.compile(r"\s*\(\s*site\s*[\w-]+\s*\)\s*$", re.IGNORECASE)


def site_key(text: str) -> str:
    return norm_name(_SITE_NUMBER.sub("", text))


def site_label(text: str) -> str:
    return _SITE_NUMBER.sub("", text).strip()


# Placeholder "investigators" registered instead of a person, e.g. "Medical Director".
_PLACEHOLDER_OFFICIAL = re.compile(
    r"\b(director|clinical trials?|study team|medical monitor|call center|sponsor|"
    r"contact|transparency|disclosure)\b",
    re.IGNORECASE,
)


def _is_person(item: dict[str, Any]) -> bool:
    return not _PLACEHOLDER_OFFICIAL.search(item.get("name") or "")


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
DRUG_TYPES = {"DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT"}
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


LOCATIONS_PATH = f"{PS}.contactsLocationsModule.locations"

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

    def exclusive(self, policy: PhasePolicy) -> bool:
        """True when every trial has at most one value, so groups partition the trials and
        may be shown as pie slices or stacked segments without double counting."""
        if self.name is Dimension.PHASE:
            return policy is PhasePolicy.COMBINED
        return self.name in SINGLE_VALUED


# Ordered (not a set) so the generated planner prompt is byte-identical across processes.
SINGLE_VALUED = (
    Dimension.OVERALL_STATUS,
    Dimension.STUDY_TYPE,
    Dimension.LEAD_SPONSOR,
    Dimension.SPONSOR_CLASS,
    Dimension.PRIMARY_PURPOSE,
    Dimension.ALLOCATION,
)


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
            _list_items(INTERVENTIONS_PATH, "name", drug_key, str.strip, keep=_is_drug),
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
            _list_items(LOCATIONS_PATH, "country", norm_name, str.strip),
            default_top_n=25,
        ),
        DimensionSpec(
            Dimension.PRIMARY_PURPOSE,
            "Primary purpose",
            "Primary purpose of the study design: Treatment, Prevention, Diagnostic, …",
            "category",
            _single(f"{PS}.designModule.designInfo.primaryPurpose"),
        ),
        DimensionSpec(
            Dimension.ALLOCATION,
            "Allocation",
            "Randomized vs non-randomized allocation.",
            "category",
            _single(f"{PS}.designModule.designInfo.allocation", {"NA": "Not applicable"}),
        ),
        DimensionSpec(
            Dimension.SITE,
            "Site",
            "Facilities (trial sites) listed as locations; sponsor site numbers are ignored.",
            "entity",
            _list_items(LOCATIONS_PATH, "facility", site_key, site_label),
            default_top_n=20,
        ),
        DimensionSpec(
            Dimension.INVESTIGATOR,
            "Investigator",
            "Overall officials (principal investigators) named on the trial.",
            "entity",
            _list_items(
                f"{PS}.contactsLocationsModule.overallOfficials",
                "name",
                norm_name,
                str.strip,
                keep=_is_person,
            ),
            default_top_n=20,
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


# --- Measures (per-trial numbers and dates: histogram bins, scatter axes) -------------------


@dataclass(frozen=True)
class MeasureValue:
    value: float | str  # a number, or the raw ISO date string for temporal measures
    sort_key: float  # numeric position (binning, ordering); decimal year for dates
    evidence: list[tuple[str, Any]]  # (exact path, exact raw value) pairs that support it


ENROLLMENT_PATH = f"{PS}.designModule.enrollmentInfo"
PRIMARY_COMPLETION_PATH = f"{PS}.statusModule.primaryCompletionDateStruct"
_DATE_PARTS = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def _parse_date(raw: Any) -> tuple[int, int | None, int | None] | None:
    match = _DATE_PARTS.match(raw) if isinstance(raw, str) else None
    if not match:
        return None
    y, m, d = match.groups()
    return int(y), int(m) if m else None, int(d) if d else None


def _enrollment(study: Study) -> MeasureValue | None:
    raw = get_path(study, f"{ENROLLMENT_PATH}.count")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        return None
    evidence: list[tuple[str, Any]] = [(f"{ENROLLMENT_PATH}.count", raw)]
    kind = get_path(study, f"{ENROLLMENT_PATH}.type")
    if isinstance(kind, str):
        evidence.append((f"{ENROLLMENT_PATH}.type", kind))  # ACTUAL vs ESTIMATED
    return MeasureValue(raw, float(raw), evidence)


def _duration_months(study: Study) -> MeasureValue | None:
    """Start → primary completion, in months. Both dates need month precision; a year-only
    date cannot give a duration without inventing a month (DESIGN §8)."""
    start_raw = get_path(study, f"{DATE_PATHS[DateBasis.START_DATE]}.date")
    end_raw = get_path(study, f"{PRIMARY_COMPLETION_PATH}.date")
    start, end = _parse_date(start_raw), _parse_date(end_raw)
    if start is None or end is None or start[1] is None or end[1] is None:
        return None
    months: float = (end[0] - start[0]) * 12 + (end[1] - start[1])
    if start[2] is not None and end[2] is not None:
        months += (end[2] - start[2]) / 30.44
    if months < 0:
        return None  # inconsistent record: completion before start
    months = round(months, 1)
    return MeasureValue(
        months,
        months,
        [
            (f"{DATE_PATHS[DateBasis.START_DATE]}.date", start_raw),
            (f"{PRIMARY_COMPLETION_PATH}.date", end_raw),
        ],
    )


def _start_date(study: Study) -> MeasureValue | None:
    path = f"{DATE_PATHS[DateBasis.START_DATE]}.date"
    raw = get_path(study, path)
    parts = _parse_date(raw)
    if parts is None:
        return None
    y, m, d = parts
    position = y + ((m or 1) - 1) / 12 + ((d or 1) - 1) / 365
    return MeasureValue(str(raw), position, [(path, raw)])


@dataclass(frozen=True)
class MeasureSpec:
    name: Measure
    label: str
    description: str
    kind: Literal["quantitative", "temporal"]
    unit: str | None
    extract: Callable[[Study], MeasureValue | None]
    # Histogram bins [edge_i, edge_i+1); the last bin is open-ended. Unequal widths on purpose:
    # enrollment and duration are heavy-tailed, so equal-width bins would put ~all trials in one.
    bin_edges: tuple[float, ...] | None = None


MEASURES: dict[Measure, MeasureSpec] = {
    spec.name: spec
    for spec in (
        MeasureSpec(
            Measure.ENROLLMENT,
            "Enrollment",
            "Participants enrolled (ACTUAL) or planned (ESTIMATED), as registered.",
            "quantitative",
            "participants",
            _enrollment,
            bin_edges=(0, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
        ),
        MeasureSpec(
            Measure.DURATION_MONTHS,
            "Duration",
            "Months from study start to primary completion (actual or anticipated).",
            "quantitative",
            "months",
            _duration_months,
            bin_edges=(0, 6, 12, 18, 24, 36, 48, 60, 84, 120),
        ),
        MeasureSpec(
            Measure.START_DATE,
            "Start date",
            "Study start date (temporal; scatter x axis).",
            "temporal",
            None,
            _start_date,
        ),
    )
}

assert set(MEASURES) == set(Measure), "every Measure needs a registry entry"


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
    "LocationFacility",
    "OverallOfficialName",
    "DesignPrimaryPurpose",
    "DesignAllocation",
    "EnrollmentCount",
    "EnrollmentType",
    "PrimaryCompletionDate",
    "PrimaryCompletionDateType",
)

NETWORK_DIMENSIONS = tuple(d for d, s in REGISTRY.items() if s.kind == "entity")


def study_url(nct_id: str) -> str:
    return f"https://clinicaltrials.gov/study/{nct_id}"
