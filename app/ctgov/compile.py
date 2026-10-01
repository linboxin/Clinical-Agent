"""Compile a cohort's semantic filters into ClinicalTrials.gov v2 query parameters.

Only allowlisted templates are emitted; user/LLM text is sanitized and quoted, never
spliced in raw. Every template below was contract-checked against the live API (DESIGN §7).
"""

import re

from app.contracts.plan import CohortFilters, TimeScope
from app.registry import DATE_API_FIELDS

_ESSIE_SPECIAL = re.compile(r'["\[\]()]')


def quote(term: str) -> str:
    """Quote free text as an Essie phrase, stripping characters that could alter the query."""
    cleaned = re.sub(r"\s+", " ", _ESSIE_SPECIAL.sub(" ", term)).strip()
    return f'"{cleaned}"'


def compile_cohort(filters: CohortFilters, time: TimeScope) -> dict[str, str]:
    terms: list[str] = []
    params: dict[str, str] = {}

    if filters.drug_name:
        # Field-scoped (not query.intr): excludes trials that only mention the drug elsewhere,
        # e.g. "prior pembrolizumab" in eligibility, while keeping the API's synonym expansion.
        q = quote(filters.drug_name)
        terms.append(f"(AREA[InterventionName]{q} OR AREA[InterventionOtherName]{q})")
    if filters.condition:
        # Phrase-quoted: "lung cancer" as a phrase (13,362) rather than any-word (14,593);
        # query.cond still applies the registry's condition synonyms.
        params["query.cond"] = quote(filters.condition)
    if filters.sponsor:
        terms.append(f"AREA[LeadSponsorName]{quote(filters.sponsor)}")
    if filters.country:
        terms.append(f"AREA[LocationCountry]{quote(filters.country)}")
    if filters.trial_phase:
        terms.append("AREA[Phase](" + " OR ".join(p.value for p in filters.trial_phase) + ")")
    if filters.study_type:
        terms.append(f"AREA[StudyType]{filters.study_type.value}")
    if time.year_from is not None or time.year_to is not None:
        lo = f"{time.year_from}-01-01" if time.year_from is not None else "MIN"
        hi = f"{time.year_to}-12-31" if time.year_to is not None else "MAX"
        terms.append(f"AREA[{DATE_API_FIELDS[time.date_basis]}]RANGE[{lo},{hi}]")
    if filters.overall_status:
        params["filter.overallStatus"] = ",".join(s.value for s in filters.overall_status)

    if terms:
        params["query.term"] = " AND ".join(terms)
    return params
