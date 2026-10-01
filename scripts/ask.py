"""Run one question through the full pipeline and print the JSON response.

    uv run python -m scripts.ask "How are lung cancer trials distributed across phases?"
    uv run python -m scripts.ask "How has the number of trials for this drug changed over time?" \
        --field drug_name=Pembrolizumab --citations 2

--plan FILE replays a hand-written QueryPlan instead of calling the model (it is still
validated and grounded): useful for debugging the deterministic stages without an API key.
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.contracts.plan import QueryPlan
from app.contracts.request import VisualizationRequest
from app.factory import create_http_client, create_pipeline
from app.planner import Planner
from app.planner.gateway import ReplayGateway


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("query")
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="structured request field, e.g. drug_name=Pembrolizumab (repeatable)",
    )
    parser.add_argument("--citations", type=int, default=3, help="citations per datum")
    parser.add_argument("--plan", type=Path, help="replay this QueryPlan JSON (no model call)")
    parser.add_argument("--summary", action="store_true", help="print a short summary only")
    args = parser.parse_args()

    body: dict[str, object] = {"query": args.query, "citations_per_datum": args.citations}
    for item in args.field:
        name, _, value = item.partition("=")
        body[name] = int(value) if value.isdigit() else value
    request = VisualizationRequest.model_validate(body)

    settings = get_settings()
    http = create_http_client(settings)
    try:
        pipeline = create_pipeline(settings, http)
        if args.plan:
            plan = QueryPlan.model_validate_json(args.plan.read_text())
            pipeline.planner = Planner(ReplayGateway(plan))
        response = await pipeline.run(request)
    finally:
        await http.aclose()
    if not args.summary:
        print(json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return
    spec = response.visualization
    print(f"run_id={response.run_id} status={response.status.value}")
    print(f"timings_ms={response.meta.timings_ms} chart={response.meta.chart_selection}")
    if response.error:
        print("error:", response.error.model_dump())
    if response.clarification:
        print("clarification:", response.clarification.model_dump())
    for c in response.meta.cohorts:
        print(
            f"  cohort {c.label!r}: matches={c.total_matches} analyzed={c.trials_analyzed} "
            f"excluded={c.excluded} missing={c.missing} synonyms={c.synonym_matches}"
        )
    if spec is not None:
        print(f"  {spec.type}: {spec.title}")
        rows = spec.data.nodes + spec.data.edges if spec.type == "network_graph" else spec.data  # type: ignore[union-attr,operator]
        for d in rows[:12]:
            extra = {k: v for k, v in (d.model_extra or {}).items()}
            print(f"    {d.datum_id} n={d.trial_count} {extra}")
        if response.meta.truncation:
            print("  truncation:", response.meta.truncation.model_dump())


if __name__ == "__main__":
    asyncio.run(main())
