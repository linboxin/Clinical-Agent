"""Builders for synthetic ClinicalTrials.gov v2 study records and query plans.

Records use the exact v2 JSON shape returned with our field projection, so golden tests
exercise the same paths the live API produces. Counts in tests are hand-computable.
"""

from typing import Any

from app.contracts.enums import DateBasis, Dimension, Measure, OperationKind, PhasePolicy
from app.contracts.plan import (
    Clarification,
    Cohort,
    CohortFilters,
    Operation,
    QueryPlan,
    TimeScope,
)


def study(
    nct: str,
    *,
    title: str | None = None,
    phases: list[str] | None = None,
    status: str = "RECRUITING",
    study_type: str = "INTERVENTIONAL",
    start: str | None = None,
    start_type: str = "ACTUAL",
    sponsor: str = "Acme Pharma",
    sponsor_class: str = "INDUSTRY",
    interventions: list[tuple[str, str]] = (),  # type: ignore[assignment]
    conditions: list[str] = (),  # type: ignore[assignment]
    countries: list[str] = (),  # type: ignore[assignment]
    enrollment: int | None = None,
    enrollment_type: str = "ACTUAL",
    primary_completion: str | None = None,
    facilities: list[str] = (),  # type: ignore[assignment]
    officials: list[str] = (),  # type: ignore[assignment]
    other_names: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    status_module: dict[str, Any] = {"overallStatus": status}
    if start is not None:
        status_module["startDateStruct"] = {"date": start, "type": start_type}
    if primary_completion is not None:
        status_module["primaryCompletionDateStruct"] = {
            "date": primary_completion,
            "type": "ACTUAL",
        }
    design: dict[str, Any] = {"studyType": study_type}
    if phases is not None:
        design["phases"] = phases
    if enrollment is not None:
        design["enrollmentInfo"] = {"count": enrollment, "type": enrollment_type}
    locations: list[dict[str, Any]] = [{"country": c} for c in countries]
    for i, facility in enumerate(facilities):
        if i < len(locations):
            locations[i]["facility"] = facility
        else:
            locations.append({"facility": facility})
    interventions_json = []
    for t, n in interventions:
        item: dict[str, Any] = {"type": t, "name": n}
        if other_names and n in other_names:
            item["otherNames"] = other_names[n]
        interventions_json.append(item)
    contacts: dict[str, Any] = {"locations": locations}
    if officials:
        contacts["overallOfficials"] = [{"name": o} for o in officials]
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct, "briefTitle": title or f"Study {nct}"},
            "statusModule": status_module,
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": sponsor, "class": sponsor_class}
            },
            "conditionsModule": {"conditions": list(conditions)},
            "designModule": design,
            "armsInterventionsModule": {"interventions": interventions_json},
            "contactsLocationsModule": contacts,
        }
    }


def filters(**given: Any) -> CohortFilters:
    base = {name: None for name in CohortFilters.model_fields}
    return CohortFilters(**(base | given))


def plan(
    kind: str = "count_by",
    dimension: str | None = "phase",
    second: str | None = None,
    cohorts: list[tuple[str, dict[str, Any]]] | None = None,
    measure: str | None = None,
    x_measure: str | None = None,
    date_basis: str = "start_date",
    year_from: int | None = None,
    year_to: int | None = None,
    phase_policy: str = "combined",
    top_n: int | None = None,
    clarification: Clarification | None = None,
    unsupported_reason: str | None = None,
) -> QueryPlan:
    cohorts = cohorts if cohorts is not None else [("all", {})]
    return QueryPlan(
        cohorts=[Cohort(label=label, filters=filters(**f)) for label, f in cohorts],
        operation=Operation(
            kind=OperationKind(kind),
            dimension=Dimension(dimension) if dimension else None,
            second_dimension=Dimension(second) if second else None,
            measure=Measure(measure) if measure else None,
            x_measure=Measure(x_measure) if x_measure else None,
        ),
        time=TimeScope(date_basis=DateBasis(date_basis), year_from=year_from, year_to=year_to),
        phase_policy=PhasePolicy(phase_policy),
        top_n=top_n,
        clarification=clarification,
        unsupported_reason=unsupported_reason,
    )


# A small fixed corpus used across analytics, pipeline and API tests.
CORPUS = [
    study(
        "NCT00000001",
        phases=["PHASE3"],
        start="2016-03-01",
        sponsor="Merck Sharp & Dohme LLC",
        interventions=[("DRUG", "Pembrolizumab"), ("DRUG", "Carboplatin")],
        conditions=["Lung Cancer"],
        countries=["United States", "France", "United States"],
    ),
    study(
        "NCT00000002",
        phases=["PHASE1", "PHASE2"],
        start="2016-07",
        sponsor="M.D. Anderson Cancer Center",
        sponsor_class="OTHER",
        interventions=[
            ("BIOLOGICAL", "pembrolizumab"),
            ("DRUG", "Placebo"),
            ("PROCEDURE", "Surgery"),
        ],
        conditions=["Lung Cancer"],
        countries=["United States"],
    ),
    study(
        "NCT00000003",
        phases=["PHASE2"],
        start="2018-01-15",
        sponsor="Merck Sharp & Dohme LLC",
        interventions=[("DRUG", "Pembrolizumab"), ("DRUG", "Carboplatin"), ("DRUG", "Pemetrexed")],
        conditions=["NSCLC"],
        countries=["Japan"],
    ),
    study(
        "NCT00000004",
        phases=None,  # observational: no phase reported
        study_type="OBSERVATIONAL",
        start="2027-02",
        start_type="ESTIMATED",
        status="NOT_YET_RECRUITING",
        sponsor="University Hospital",
        sponsor_class="OTHER",
        interventions=[("OTHER", "Blood sample")],
        conditions=["Lung Cancer"],
        countries=[],
    ),
    study(
        "NCT00000005",
        phases=["NA"],
        start=None,  # missing start date
        sponsor="Merck Sharp & Dohme LLC",
        interventions=[("DRUG", "Pembrolizumab ")],
        conditions=["Lung Cancer"],
        countries=["France"],
    ),
]
