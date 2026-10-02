"""Turn raw API studies into the trial set a cohort analyses, recording every exclusion and
the evidence that puts each trial in its cohort.

Exact-valued filters (phase, status, study type, year range) are re-checked locally; a record
the API returned but that fails the check is excluded with a reason. Free-text filters (drug,
condition, sponsor, country) use the registry's own search, which expands synonyms: we cite
the literal match when the record contains one and count the rest as synonym matches.
"""

import re
from collections import Counter
from itertools import combinations
from typing import Any

from app.analytics.types import CohortTrials, EvidenceItem, Trial
from app.contracts.plan import Cohort, CohortFilters, TimeScope
from app.registry import (
    INTERVENTIONS_PATH,
    LOCATIONS_PATH,
    NCT_PATH,
    PHASES_PATH,
    PS,
    TITLE_PATH,
    extract_date,
    get_path,
    norm_name,
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Word set for literal matching: case-folded, possessives dropped and a trailing plural
    's' removed, so "Alzheimer's disease" matches "Alzheimer Disease"."""
    words = _TOKEN.findall(norm_name(text).replace("'s", "").replace("’s", ""))
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words}


def literal_match(term: str, text: str) -> bool:
    wanted = _tokens(term)
    return bool(wanted) and wanted <= _tokens(text)


STATUS_PATH = f"{PS}.statusModule.overallStatus"
STUDY_TYPE_PATH = f"{PS}.designModule.studyType"
SPONSOR_PATH = f"{PS}.sponsorCollaboratorsModule.leadSponsor.name"
CONDITIONS_PATH = f"{PS}.conditionsModule.conditions"


def prepare_cohort(
    cohort: Cohort, studies: list[dict[str, Any]], time: TimeScope
) -> tuple[CohortTrials, dict[str, int]]:
    """Returns the cohort's trials and, per free-text filter, how many trials matched only
    through the registry's synonym expansion (no literal mention to cite)."""
    excluded: Counter[str] = Counter()
    synonym_only: Counter[str] = Counter()
    seen: set[str] = set()
    trials: list[Trial] = []
    for study in studies:
        nct_id = get_path(study, NCT_PATH)
        if not isinstance(nct_id, str) or not nct_id:
            excluded["malformed_record_without_nct_id"] += 1
            continue
        if nct_id in seen:
            excluded["duplicate_record"] += 1  # pagination over a live index can repeat
            continue
        seen.add(nct_id)

        membership: list[EvidenceItem] = []
        reason = _check_exact_filters(study, cohort.filters, time, membership) or _excluded(
            study, cohort.filters
        )
        if reason is not None:
            excluded[reason] += 1
            continue
        for name, found in _free_text_evidence(study, cohort.filters).items():
            if found is None:
                synonym_only[name] += 1
            else:
                membership.append(found)
        title = get_path(study, TITLE_PATH)
        trials.append(
            Trial(nct_id, title if isinstance(title, str) else None, study, tuple(membership))
        )
    return CohortTrials(cohort, trials, dict(excluded)), dict(synonym_only)


def _check_exact_filters(
    study: dict[str, Any], f: CohortFilters, time: TimeScope, membership: list[EvidenceItem]
) -> str | None:
    if f.trial_phase:
        phases = get_path(study, PHASES_PATH)
        if not isinstance(phases, list) or not {p.value for p in f.trial_phase} & set(phases):
            return "phase_not_in_filter"
        membership.append((PHASES_PATH, phases))
    if f.overall_status:
        status = get_path(study, STATUS_PATH)
        if status not in {s.value for s in f.overall_status}:
            return "status_not_in_filter"
        membership.append((STATUS_PATH, status))
    if f.study_type:
        study_type = get_path(study, STUDY_TYPE_PATH)
        if study_type != f.study_type.value:
            return "study_type_not_in_filter"
        membership.append((STUDY_TYPE_PATH, study_type))
    if time.year_from is not None or time.year_to is not None:
        # The API filtered by date range already; re-check locally at year precision.
        date = extract_date(study, time.date_basis)
        if date is None:
            return f"no_{time.date_basis.value}"
        if (time.year_from is not None and date.year < time.year_from) or (
            time.year_to is not None and date.year > time.year_to
        ):
            return "outside_year_range"
        membership.append((date.path, date.raw))
    return None


def _free_text_evidence(study: dict[str, Any], f: CohortFilters) -> dict[str, EvidenceItem | None]:
    """Per free-text filter, the first literal match among its listed values (any-of)."""
    found: dict[str, EvidenceItem | None] = {}
    if f.drug_names:
        found["drug_names"] = _first(_drug_match(study, t) for t in f.drug_names)
    if f.conditions:
        found["conditions"] = _first(
            _first_match(study, CONDITIONS_PATH, None, t) for t in f.conditions
        )
    if f.sponsors:
        found["sponsors"] = _first(_sponsor_match(study, t) for t in f.sponsors)
    if f.countries:
        found["countries"] = _first(
            _first_match(study, LOCATIONS_PATH, "country", t) for t in f.countries
        )
    return found


def _excluded(study: dict[str, Any], f: CohortFilters) -> str | None:
    """Local double check of exclusions: a record that literally lists an excluded value is
    dropped even if the API's NOT clause let it through."""
    checks = (
        ("excluded_drug_listed", f.exclude_drug_names, lambda t: _drug_match(study, t)),
        (
            "excluded_condition_listed",
            f.exclude_conditions,
            lambda t: _first_match(study, CONDITIONS_PATH, None, t),
        ),
        ("excluded_sponsor", f.exclude_sponsors, lambda t: _sponsor_match(study, t)),
        (
            "excluded_country_listed",
            f.exclude_countries,
            lambda t: _first_match(study, LOCATIONS_PATH, "country", t),
        ),
    )
    for reason, terms, match in checks:
        if any(match(t) for t in terms or []):
            return reason
    return None


def _first(candidates: Any) -> EvidenceItem | None:
    return next((c for c in candidates if c is not None), None)


def _sponsor_match(study: dict[str, Any], term: str) -> EvidenceItem | None:
    name = get_path(study, SPONSOR_PATH)
    return (SPONSOR_PATH, name) if isinstance(name, str) and literal_match(term, name) else None


def _first_match(
    study: dict[str, Any], list_path: str, item_field: str | None, term: str
) -> EvidenceItem | None:
    for i, item in enumerate(get_path(study, list_path) or []):
        raw = item if item_field is None else (item or {}).get(item_field)
        if isinstance(raw, str) and literal_match(term, raw):
            path = f"{list_path}[{i}]" + (f".{item_field}" if item_field else "")
            return path, raw
    return None


def _drug_match(study: dict[str, Any], term: str) -> EvidenceItem | None:
    for i, item in enumerate(get_path(study, INTERVENTIONS_PATH) or []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and literal_match(term, name):
            return f"{INTERVENTIONS_PATH}[{i}].name", name
        for j, other in enumerate(item.get("otherNames") or []):
            if isinstance(other, str) and literal_match(term, other):
                return f"{INTERVENTIONS_PATH}[{i}].otherNames[{j}]", other
    return None


def cohort_overlap(cohorts: list[CohortTrials]) -> dict[str, int] | None:
    if len(cohorts) < 2:
        return None
    ids = {c.cohort.label: {t.nct_id for t in c.trials} for c in cohorts}
    return {f"{a} ∩ {b}": len(ids[a] & ids[b]) for a, b in combinations(ids, 2)}
