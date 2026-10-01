"""FastAPI entrypoint: `uv run uvicorn app.main:app`. OpenAPI docs at /docs."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response

from app.config import get_settings
from app.contracts.enums import ChartType, OperationKind, Status
from app.contracts.request import VisualizationRequest
from app.contracts.response import ErrorInfo, VisualizationResponse
from app.factory import create_http_client, create_pipeline
from app.pipeline import Pipeline
from app.registry import NETWORK_DIMENSIONS, REGISTRY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FAILURE_HTTP_STATUS = {
    "planner_not_configured": 503,
    "planner_unavailable": 503,
    "upstream_unavailable": 503,
    "deadline_exceeded": 504,
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
    version="0.1.0",
    description="Turns a clinical-trial question into a typed, citation-backed visualization "
    "spec computed from ClinicalTrials.gov API v2 data.",
    lifespan=lifespan,
)


@app.post(
    "/v1/visualizations",
    response_model=VisualizationResponse,
    responses={
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
    stored = request.app.state.store.get(run_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="run not found")
    return stored


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
            }
            for spec in REGISTRY.values()
        ],
        "operations": [k.value for k in OperationKind],
        "chart_types": [c.value for c in ChartType],
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
