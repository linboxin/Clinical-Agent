"""FastAPI entrypoint: `uv run uvicorn app.main:app`. OpenAPI docs at /docs, demo at /demo."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from app.config import get_settings
from app.contracts.enums import ChartType, OperationKind, Status
from app.contracts.request import VisualizationRequest
from app.contracts.response import ErrorInfo, VisualizationResponse
from app.factory import create_http_client, create_pipeline
from app.pipeline import SUPPORTED_ALTERNATIVES, Pipeline
from app.registry import MEASURES, NETWORK_DIMENSIONS, REGISTRY, SINGLE_VALUED
from app.storage import RunStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DEMO_PAGE = Path(__file__).parent / "static" / "demo.html"
FAILURE_HTTP_STATUS = {
    "planner_not_configured": 503,
    "planner_unavailable": 503,
    "upstream_unavailable": 503,
    "deadline_exceeded": 504,
    "parent_run_not_found": 404,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    http = create_http_client(settings)
    app.state.pipeline = create_pipeline(settings, http)
    app.state.store = app.state.pipeline.store
    yield
    await http.aclose()


app = FastAPI(
    title="ClinicalTrials.gov Query-to-Visualization Agent",
    version="1.0.0",
    description="Turns a clinical-trial question into a typed, citation-backed visualization "
    "spec computed from ClinicalTrials.gov API v2 data.",
    lifespan=lifespan,
)


def _store(request: Request) -> RunStore:
    store: RunStore | None = request.app.state.store
    if store is None:
        raise HTTPException(status_code=404, detail="run storage is disabled")
    return store


@app.post(
    "/v1/visualizations",
    response_model=VisualizationResponse,
    responses={
        404: {"description": "parent_run_id not found (status=failed)"},
        422: {"description": "Malformed request"},
        503: {"description": "Model or ClinicalTrials.gov unavailable (status=failed)"},
        504: {"description": "Run deadline exceeded (status=failed)"},
    },
)
async def create_visualization(
    body: VisualizationRequest, request: Request, response: Response
) -> VisualizationResponse:
    """Run the full pipeline. status ok/empty/needs_clarification/unsupported → HTTP 200."""
    pipeline: Pipeline = request.app.state.pipeline
    try:
        result = await asyncio.wait_for(
            pipeline.run(body), timeout=get_settings().run_deadline_seconds
        )
    except TimeoutError:
        result = VisualizationResponse(
            run_id=str(uuid4()),
            status=Status.FAILED,
            error=ErrorInfo(code="deadline_exceeded", message="The run exceeded its deadline."),
        )
    if result.status is Status.FAILED and result.error:
        response.status_code = FAILURE_HTTP_STATUS.get(result.error.code, 500)
    return result


@app.get("/v1/runs/{run_id}", response_model=VisualizationResponse)
async def get_run(run_id: str, request: Request) -> VisualizationResponse:
    """The stored response of an earlier run (same body POST returned)."""
    record = _store(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    return record.response


@app.get("/v1/runs/{run_id}/evidence")
async def get_evidence(
    run_id: str,
    request: Request,
    datum_id: str = Query(description="datum_id of a bar, bucket, bin, point, node or edge"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Every supporting citation for one datum (inline citations show only the first few)."""
    citations = _store(request).evidence(run_id, datum_id)
    if citations is None:
        raise HTTPException(status_code=404, detail="run or datum not found")
    return {
        "run_id": run_id,
        "datum_id": datum_id,
        "total": len(citations),
        "offset": offset,
        "limit": limit,
        "citations": citations[offset : offset + limit],
    }


@app.get("/v1/runs/{run_id}/trace")
async def get_trace(run_id: str, request: Request) -> dict[str, Any]:
    """Timed spans of the run: stages, model calls (tokens) and registry requests (cache)."""
    record = _store(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    return record.trace


@app.get("/v1/capabilities")
async def capabilities() -> dict[str, Any]:
    """What the planner can express — generated from the field registry."""
    settings = get_settings()
    return {
        "dimensions": [
            {
                "name": spec.name.value,
                "label": spec.label,
                "description": spec.description,
                "kind": spec.kind,
                "network_node": spec.name in NETWORK_DIMENSIONS,
                "single_valued": spec.name in SINGLE_VALUED,
            }
            for spec in REGISTRY.values()
        ],
        "measures": [
            {
                "name": m.name.value,
                "label": m.label,
                "description": m.description,
                "kind": m.kind,
                "unit": m.unit,
                "bin_edges": list(m.bin_edges) if m.bin_edges else None,
            }
            for m in MEASURES.values()
        ],
        "operations": [k.value for k in OperationKind],
        "chart_types": [c.value for c in ChartType],
        "chart_rules": {
            "count_by": "bar_chart; with series: grouped_bar_chart. Preferred pie_chart / "
            "stacked_bar_chart only when groups are mutually exclusive.",
            "time_trend": "time_series (bar/grouped bar if preferred)",
            "histogram": "histogram",
            "scatter": "scatter_plot",
            "network": "network_graph",
        },
        "supported_questions": SUPPORTED_ALTERNATIVES,
        "request_fields": sorted(VisualizationRequest.model_fields),
        "limits": {
            "max_trials_per_cohort": settings.max_trials_per_cohort,
            "max_cohorts": 4,
            "run_deadline_seconds": settings.run_deadline_seconds,
        },
    }


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    pipeline: Pipeline = request.app.state.pipeline
    return {
        "status": "ok",
        "planner_configured": pipeline.planner is not None,
        "planner_model": pipeline.planner.model if pipeline.planner else None,
    }


@app.get("/demo", include_in_schema=False)
async def demo() -> FileResponse:
    """Minimal renderer that draws any response spec (proves the schema is renderable)."""
    return FileResponse(DEMO_PAGE)
