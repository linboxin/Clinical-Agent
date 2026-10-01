"""Grounding: check a validated plan against the live registry before any data is fetched.

This is the planner's one tool. It is deterministic (count queries only) and its observations
go back to the model as repair feedback, so a misspelled drug or a made-up condition is fixed
by the planner instead of producing an empty chart. The model sees hit counts, never records.
"""

from dataclasses import dataclass, field

from app.contracts.plan import CohortFilters, QueryPlan, TimeScope
from app.ctgov.client import CTGovClient
from app.ctgov.compile import compile_cohort

FREE_TEXT_FILTERS = ("drug_name", "condition", "sponsor", "country")


@dataclass
class UnknownTerm:
    cohort: str
    field: str
    value: str


@dataclass
class GroundReport:
    totals: dict[str, int]  # cohort label → trials the API reports
    unknown_terms: list[UnknownTerm] = field(default_factory=list)
    too_broad: dict[str, int] = field(default_factory=dict)  # cohort label → total

    def repair_errors(self) -> list[str]:
        return [
            f"{t.field}='{t.value}' (cohort '{t.cohort}') matches no ClinicalTrials.gov trials "
            "on its own. If it is a misspelling or an unofficial name, correct it to the name "
            "the user most likely meant (generic drug name, standard condition name, sponsor "
            "as registered); if the user really asked for it, keep it unchanged."
            for t in self.unknown_terms
        ]


class Grounder:
    def __init__(self, ctgov: CTGovClient, max_trials: int, data_timestamp: str | None) -> None:
        self.ctgov = ctgov
        self.max_trials = max_trials
        self.scope = data_timestamp
        self.calls = 0

    async def count(self, filters: CohortFilters, time: TimeScope) -> int:
        self.calls += 1
        return await self.ctgov.count(compile_cohort(filters, time), self.scope)

    async def __call__(self, plan: QueryPlan) -> GroundReport:
        report = GroundReport(totals={})
        no_time = TimeScope(date_basis=plan.time.date_basis, year_from=None, year_to=None)
        for cohort in plan.cohorts:
            total = await self.count(cohort.filters, plan.time)
            report.totals[cohort.label] = total
            if total > self.max_trials:
                report.too_broad[cohort.label] = total
            if total > 0:
                continue
            # Zero hits: find which named entity the registry does not know. If every entity
            # exists on its own, the combination is a genuine zero (an empty chart is correct).
            for name in FREE_TEXT_FILTERS:
                value = getattr(cohort.filters, name)
                if value is None:
                    continue
                alone = CohortFilters.model_validate(
                    {k: None for k in CohortFilters.model_fields} | {name: value}
                )
                if await self.count(alone, no_time) == 0:
                    report.unknown_terms.append(UnknownTerm(cohort.label, name, value))
        return report
