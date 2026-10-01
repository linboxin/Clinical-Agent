"""Turn raw API studies into the trial set a cohort analyses, recording every exclusion."""

from collections import Counter
from itertools import combinations
from typing import Any

from app.analytics.types import CohortTrials, Trial
from app.contracts.plan import Cohort, TimeScope
from app.registry import NCT_PATH, TITLE_PATH, extract_date, get_path


def prepare_cohort(cohort: Cohort, studies: list[dict[str, Any]], time: TimeScope) -> CohortTrials:
    excluded: Counter[str] = Counter()
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
        if time.year_from is not None or time.year_to is not None:
            # The API filtered by date range already; re-check locally at year precision.
            date = extract_date(study, time.date_basis)
            if date is None:
                excluded[f"no_{time.date_basis.value}"] += 1
                continue
            if (time.year_from is not None and date.year < time.year_from) or (
                time.year_to is not None and date.year > time.year_to
            ):
                excluded["outside_year_range"] += 1
                continue
        title = get_path(study, TITLE_PATH)
        trials.append(Trial(nct_id, title if isinstance(title, str) else None, study))
    return CohortTrials(cohort, trials, dict(excluded))


def cohort_overlap(cohorts: list[CohortTrials]) -> dict[str, int] | None:
    if len(cohorts) < 2:
        return None
    ids = {c.cohort.label: {t.nct_id for t in c.trials} for c in cohorts}
    return {f"{a} ∩ {b}": len(ids[a] & ids[b]) for a, b in combinations(ids, 2)}
