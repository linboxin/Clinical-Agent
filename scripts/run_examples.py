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
    (
        "01_time_trend_brief_example",
        {
            "query": "How has the number of trials for this drug changed over time?",
            "drug_name": "Pembrolizumab",
        },
    ),
    ("02_distribution_phases", {"query": "How are lung cancer trials distributed across phases?"}),
    (
        "03_comparison_two_drugs",
        {"query": "Compare phases for trials involving pembrolizumab vs nivolumab in melanoma."},
    ),
    (
        "04_geographic_recruiting",
        {"query": "Which countries have the most recruiting trials for breast cancer?"},
    ),
    (
        "05_network_sponsor_drug",
        {"query": "Show a network of sponsors and drugs for phase 3 melanoma trials."},
    ),
    (
        "06_network_drug_cooccurrence",
        {"query": "Which drugs frequently co-occur in combination studies for multiple myeloma?"},
    ),
    ("07_needs_clarification", {"query": "How many trials has this drug had per year?"}),
    ("08_unsupported", {"query": "Which melanoma drug has the best overall survival?"}),
]


async def main(selected: list[str]) -> None:
    settings = get_settings()
    http = create_http_client(settings)
    pipeline = create_pipeline(settings, http)
    if pipeline.planner is None:
        sys.exit("OPENAI_API_KEY is not set; examples must come from the real planner.")
    OUT.mkdir(exist_ok=True)
    try:
        for name, body in EXAMPLES:
            if selected and not any(name.startswith(s) for s in selected):
                continue
            request = VisualizationRequest.model_validate({**body, "citations_per_datum": 3})
            response = await pipeline.run(request)
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
