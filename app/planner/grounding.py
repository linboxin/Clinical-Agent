"""Grounding: check a validated plan against the live registry before any data is fetched.

This is the planner's one tool. It is deterministic (count queries only) and its observations
go back to the model as repair feedback, so a misspelled drug, a made-up condition or an
invented member of a drug class is fixed by the planner instead of producing a wrong chart.
The model sees hit counts, never records.
"""

from dataclasses import dataclass, field

from app.contracts.plan import TEXT_FIELDS, CohortFilters, QueryPlan, TimeScope
from app.ctgov.client import CTGovClient
from app.ctgov.compile import compile_cohort
from app.planner.validate import empty_filters


@dataclass
class UnknownTerm:
    cohort: str
    field: str
    value: str
    expansion: str | None = None  # the class it was listed for, e.g. "PD-1 inhibitors"


@dataclass
class GroundReport:
    totals: dict[str, int]  # cohort label → trials the API reports
    unknown_terms: list[UnknownTerm] = field(default_factory=list)
    too_broad: dict[str, int] = field(default_factory=dict)  # cohort label → total
    member_counts: dict[str, int] = field(default_factory=dict)  # expanded member → trials

    def repair_errors(self) -> list[str]:
        errors = []
        for t in self.unknown_terms:
            if t.expansion:
                errors.append(
                    f"{t.field} member '{t.value}' (listed for '{t.expansion}', cohort "
                    f"'{t.cohort}') matches no ClinicalTrials.gov trials. Remove it if it is not "
                    "really a member, or correct its spelling."
                )
            else:
                errors.append(
                    f"{t.field}='{t.value}' (cohort '{t.cohort}') matches no ClinicalTrials.gov "
                    "trials on its own. If it is a misspelling or an unofficial name, correct it "
                    "to the name the user most likely meant (generic drug name, standard "
                    "condition name, sponsor as registered); if the user really asked for it, "
                    "keep it unchanged."
                )
        return errors


def _only(name: str, value: str) -> CohortFilters:
    return empty_filters().model_copy(update={name: [value]})


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
        members = {
            m.casefold(): e.term for e in plan.expansions for m in e.members
        }  # member → class
        for cohort in plan.cohorts:
            total = await self.count(cohort.filters, plan.time)
            report.totals[cohort.label] = total
            if total > self.max_trials:
                report.too_broad[cohort.label] = total
            for name in TEXT_FIELDS:
                for value in getattr(cohort.filters, name) or []:
                    expansion = members.get(value.casefold())
                    # Class members are always checked (the model may invent one); other
                    # values only when the cohort found nothing, to find the unknown term.
                    if expansion is None and total > 0:
                        continue
                    alone = await self.count(_only(name, value), no_time)
                    if expansion is not None:
                        report.member_counts[value] = alone
                    if alone == 0:
                        report.unknown_terms.append(
                            UnknownTerm(cohort.label, name, value, expansion)
                        )
        return report
