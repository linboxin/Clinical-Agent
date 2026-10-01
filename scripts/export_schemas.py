"""Write the published JSON Schemas to docs/schemas/ (request, response, query plan).

uv run python -m scripts.export_schemas
"""

import json
from pathlib import Path

from app.contracts.plan import QueryPlan
from app.contracts.request import VisualizationRequest
from app.contracts.response import VisualizationResponse

OUT = Path("docs/schemas")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, model in (
        ("request", VisualizationRequest),
        ("response", VisualizationResponse),
        ("query_plan", QueryPlan),
    ):
        path = OUT / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
