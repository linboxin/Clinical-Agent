"""Compile a cohort's semantic filters into ClinicalTrials.gov v2 query parameters.

Only allowlisted templates are emitted; user/LLM text is sanitized and quoted, never spliced in
raw. Every template below was contract-checked against the live API (DESIGN §7), including OR
inside query.cond and NOT clauses, which honour the registry's synonyms ("NOT keytruda" and
"NOT pembrolizumab" exclude the same trials).
"""

import re

from app.contracts.plan import CohortFilters, TimeScope
from app.registry import DATE_API_FIELDS

_ESSIE_SPECIAL = re.compile(r'["\[\]()]')


def quote(term: str) -> str:
    """Quote free text as an Essie phrase, stripping characters that could alter the query."""
    cleaned = re.sub(r"\s+", " ", _ESSIE_SPECIAL.sub(" ", term)).strip()
    return f'"{cleaned}"'


def _drug(name: str) -> str:
    # Field-scoped (not query.intr): excludes trials that only mention the drug elsewhere, e.g.
    # "prior pembrolizumab" in eligibility, while keeping the API's synonym expansion.
    q = quote(name)
    return f"AREA[InterventionName]{q} OR AREA[InterventionOtherName]{q}"


def _any(values: list[str], template: str) -> str:
    if len(values) == 1:
        return template.format(quote(values[0]))
    return "(" + " OR ".join(template.format(quote(v)) for v in values) + ")"


def compile_cohort(filters: CohortFilters, time: TimeScope) -> dict[str, str]:
    terms: list[str] = []
    params: dict[str, str] = {}

    if filters.drug_names:
        terms.append("(" + " OR ".join(_drug(n) for n in filters.drug_names) + ")")
    if filters.conditions:
        # Phrase-quoted: "lung cancer" as a phrase (13,362) rather than any-word (14,593);
        # query.cond still applies the registry's condition synonyms.
        params["query.cond"] = " OR ".join(quote(c) for c in filters.conditions)
    if filters.sponsors:
        terms.append(_any(filters.sponsors, "AREA[LeadSponsorName]{}"))
    if filters.countries:
        terms.append(_any(filters.countries, "AREA[LocationCountry]{}"))
    if filters.trial_phase:
        terms.append("AREA[Phase](" + " OR ".join(p.value for p in filters.trial_phase) + ")")
    if filters.study_type:
        terms.append(f"AREA[StudyType]{filters.study_type.value}")
    if time.year_from is not None or time.year_to is not None:
        lo = f"{time.year_from}-01-01" if time.year_from is not None else "MIN"
        hi = f"{time.year_to}-12-31" if time.year_to is not None else "MAX"
        terms.append(f"AREA[{DATE_API_FIELDS[time.date_basis]}]RANGE[{lo},{hi}]")

    if filters.exclude_drug_names:
        terms.append("NOT (" + " OR ".join(_drug(n) for n in filters.exclude_drug_names) + ")")
    if filters.exclude_conditions:
        terms.append("NOT " + _any(filters.exclude_conditions, "AREA[ConditionSearch]{}"))
    if filters.exclude_sponsors:
        terms.append("NOT " + _any(filters.exclude_sponsors, "AREA[LeadSponsorName]{}"))
    if filters.exclude_countries:
        terms.append("NOT " + _any(filters.exclude_countries, "AREA[LocationCountry]{}"))

    if filters.overall_status:
        params["filter.overallStatus"] = ",".join(s.value for s in filters.overall_status)
    if terms:
        params["query.term"] = " AND ".join(terms)
    return params
