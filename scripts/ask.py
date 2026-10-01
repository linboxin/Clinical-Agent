"""Run one question through the full pipeline and print the JSON response.

    uv run python -m scripts.ask "How are lung cancer trials distributed across phases?"
    uv run python -m scripts.ask "How has the number of trials for this drug changed over time?" \
        --field drug_name=Pembrolizumab --citations 2
"""

import argparse
import asyncio
import json

from app.config import get_settings
from app.contracts.request import VisualizationRequest
from app.factory import create_http_client, create_pipeline


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
    args = parser.parse_args()

    body: dict[str, object] = {"query": args.query, "citations_per_datum": args.citations}
    for item in args.field:
        name, _, value = item.partition("=")
        body[name] = int(value) if value.isdigit() else value
    request = VisualizationRequest.model_validate(body)

    settings = get_settings()
    http = create_http_client(settings)
    try:
        response = await create_pipeline(settings, http).run(request)
    finally:
        await http.aclose()
    print(json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
