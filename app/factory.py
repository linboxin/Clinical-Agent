"""Builds the pipeline from settings; shared by the API (app.main) and the CLI scripts."""

import httpx

from app.config import Settings
from app.ctgov.cache import FileCache
from app.ctgov.client import CTGovClient, RateLimiter
from app.pipeline import Pipeline
from app.planner import Planner
from app.planner.gateway import OpenAIPlannerGateway
from app.storage import FileRunStore

USER_AGENT = "clinical-agent/0.1 (query-to-visualization take-home)"


def create_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.ctgov_timeout_seconds, headers={"User-Agent": USER_AGENT}
    )


def create_planner(settings: Settings) -> Planner | None:
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if not key:
        return None
    return Planner(
        OpenAIPlannerGateway(
            api_key=key,
            model=settings.planner_model,
            base_url=settings.openai_base_url,
            reasoning_effort=settings.planner_reasoning_effort,
        )
    )


def create_ctgov_client(settings: Settings, http: httpx.AsyncClient) -> CTGovClient:
    cache = (
        FileCache(settings.ctgov_cache_dir, settings.ctgov_cache_ttl_seconds)
        if settings.ctgov_cache_dir
        else None
    )
    return CTGovClient(
        http,
        settings.ctgov_base_url,
        cache,
        settings.ctgov_max_attempts,
        RateLimiter(settings.ctgov_requests_per_minute, settings.ctgov_burst),
    )


def create_pipeline(settings: Settings, http: httpx.AsyncClient) -> Pipeline:
    return Pipeline(
        planner=create_planner(settings),
        ctgov=create_ctgov_client(settings, http),
        store=FileRunStore(settings.data_dir / "runs"),
        max_trials_per_cohort=settings.max_trials_per_cohort,
    )
