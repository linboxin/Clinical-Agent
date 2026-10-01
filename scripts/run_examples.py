"""Generate the submission's example runs: real requests through the real pipeline, saved
verbatim to examples/ (request + response). Never hand-edited.

    uv run python -m scripts.run_examples            # all
    uv run python -m scripts.run_examples 01 03      # selected ids
"""

import asyncio
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.contracts.request import VisualizationRequest
from app.factory import create_http_client, create_pipeline

OUT = Path("examples")

EXAMPLES: list[tuple[str, dict[str, object]]] = [
    # The five headline examples (brief §6: 3–5 example queries with actual outputs).
    (
        "01_time_trend_brief_example",
        {
            "query": "How has the number of trials for this drug changed over time?",
            "drug_name": "Pembrolizumab",
        },
    ),
    (
        "02_comparison_two_drugs",
        {"query": "Compare phases for trials involving pembrolizumab vs nivolumab in melanoma."},
    ),
    (
        "03_geographic_recruiting",
        {"query": "Which countries have the most recruiting trials for breast cancer?"},
    ),
    (
        "04_network_sponsor_drug",
        {"query": "Show a network of sponsors and drugs for phase 3 melanoma trials."},
    ),
    (
        "05_histogram_enrollment",
        {"query": "What is the enrollment size distribution of recruiting Alzheimer's trials?"},
    ),
    # Extra coverage: other chart types and the non-ok statuses.
    (
        "06_network_drug_cooccurrence",
        {"query": "Which drugs frequently co-occur in combination studies for multiple myeloma?"},
    ),
    (
        "07_scatter_enrollment_vs_start",
        {"query": "Plot enrollment vs start date for phase 3 psoriasis trials, by sponsor type"},
    ),
    (
        "08_trend_split_by_phase",
        {"query": "How has the phase mix of interventional obesity trials changed since 2010?"},
    ),
    (
        "09_pie_preferred",
        {
            "query": "What share of COVID-19 vaccine trials are randomized?",
            "preferred_visualization": "pie_chart",
        },
    ),
    ("10_needs_clarification", {"query": "How many trials has this drug had per year?"}),
    ("11_unsupported", {"query": "Which melanoma drug has the best overall survival?"}),
    # Follow-up: refines example 03's plan (parent_run_id is filled in from that run).
    ("12_follow_up_of_03", {"query": "Same, but only phase 3 trials.", "_parent": "03"}),
    # Plan language: a drug class expanded into grounded members, with an exclusion.
    (
        "13_drug_class_with_exclusion",
        {"query": "Excluding Keytruda, which PD-1 inhibitors have the most Phase 3 trials?"},
    ),
    # Too large to fetch (600k+ trials): counted on the server, with verifiable source queries.
    (
        "14_whole_registry_server_counts",
        {"query": "How are all registered clinical trials distributed across phases?"},
    ),
]


async def main(selected: list[str]) -> None:
    settings = get_settings()
    http = create_http_client(settings)
    pipeline = create_pipeline(settings, http)
    if pipeline.planner is None:
        sys.exit("OPENAI_API_KEY is not set; examples must come from the real planner.")
    OUT.mkdir(exist_ok=True)
    try:
        run_ids: dict[str, str] = {}
        for name, body in EXAMPLES:
            if selected and not any(name.startswith(s) for s in selected):
                continue
            fields = {k: v for k, v in body.items() if not k.startswith("_")}
            if "_parent" in body:
                parent = run_ids.get(str(body["_parent"]))
                if parent is None:
                    print(f"{name}: skipped (run its parent {body['_parent']} in the same call)")
                    continue
                fields["parent_run_id"] = parent
            request = VisualizationRequest.model_validate({**fields, "citations_per_datum": 3})
            response = await pipeline.run(request)
            run_ids[name[:2]] = response.run_id
            path = OUT / f"{name}.json"
            path.write_text(
                json.dumps(
                    {
                        "request": request.model_dump(mode="json", exclude_defaults=True)
                        | {"citations_per_datum": 3},
                        "response": response.model_dump(mode="json"),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            v = response.visualization
            print(f"{name}: {response.status.value}  {v.title if v else ''}  → {path}")
    finally:
        await http.aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
