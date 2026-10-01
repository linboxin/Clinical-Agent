"""Async ClinicalTrials.gov v2 client: pagination, bounded retries, caching, size caps."""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.ctgov.cache import FileCache
from app.telemetry import span

log = logging.getLogger(__name__)

PAGE_SIZE = 1000  # API maximum; larger values are silently capped (verified 2026-10-01)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
ADAPTER_VERSION = "1"


class UpstreamError(Exception):
    """ClinicalTrials.gov could not be reached or returned an unusable response."""


class TooBroadError(Exception):
    def __init__(self, total: int, limit: int) -> None:
        super().__init__(f"query matches {total} trials (limit {limit})")
        self.total = total
        self.limit = limit


class RateLimiter:
    """Token bucket shared by every request of one client. ClinicalTrials.gov answers bursts
    with HTTP 429 but publishes no rate-limit headers (observed 2026-10-01), so we pace
    ourselves instead of relying on retries."""

    def __init__(self, requests_per_minute: float, burst: int) -> None:
        self.interval = 60.0 / requests_per_minute
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) / self.interval)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) * self.interval)


@dataclass
class SearchResult:
    params: dict[str, str]
    total: int
    studies: list[dict[str, Any]] = field(default_factory=list)
    pages: int = 0
    complete: bool = False


@dataclass
class ApiVersion:
    api_version: str | None
    data_timestamp: str | None


class CTGovClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        cache: FileCache | None = None,
        max_attempts: int = 4,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.cache = cache
        self.max_attempts = max_attempts
        self.limiter = limiter

    async def version(self) -> ApiVersion:
        try:
            body = await self._get("/version", {}, cache=False)
        except UpstreamError:
            return ApiVersion(None, None)
        return ApiVersion(body.get("apiVersion"), body.get("dataTimestamp"))

    async def count(self, params: dict[str, str], data_timestamp: str | None = None) -> int:
        """Trials matching `params` (one tiny request: used for grounding, not analysis)."""
        body = await self._get(
            "/studies",
            {**params, "fields": "NCTId", "pageSize": "1", "countTotal": "true"},
            scope=data_timestamp,
        )
        return int(body.get("totalCount", 0))

    async def get_study(self, nct_id: str, fields: tuple[str, ...]) -> dict[str, Any]:
        """One study, fresh from the API (no cache): used by the citation audit."""
        return await self._get(f"/studies/{nct_id}", {"fields": ",".join(fields)}, cache=False)

    async def search(
        self,
        params: dict[str, str],
        fields: tuple[str, ...],
        max_records: int,
        data_timestamp: str | None = None,
    ) -> SearchResult:
        """Fetch every study matching `params`. Raises TooBroadError before paging past the
        first page when the reported total exceeds `max_records`."""
        base = {**params, "fields": ",".join(fields), "pageSize": str(PAGE_SIZE)}
        first = await self._get("/studies", {**base, "countTotal": "true"}, scope=data_timestamp)
        total = int(first.get("totalCount", 0))
        result = SearchResult(params=params, total=total)
        if total > max_records:
            raise TooBroadError(total, max_records)

        page = first
        while True:
            result.studies.extend(page.get("studies", []))
            result.pages += 1
            token = page.get("nextPageToken")
            if not token:
                break
            page = await self._get("/studies", {**base, "pageToken": token}, scope=data_timestamp)
        result.complete = True
        return result

    async def _get(
        self, path: str, params: dict[str, str], cache: bool = True, scope: str | None = None
    ) -> dict[str, Any]:
        """GET with caching. `scope` (the registry dataTimestamp) is part of the cache key."""
        shown = {k: v for k, v in params.items() if k != "fields"}
        with span("ctgov.request", path=path, params=shown) as s:
            key = None
            if cache and self.cache is not None:
                key = FileCache.key(ADAPTER_VERSION, path, params, scope)
                cached = self.cache.get(key)
                if cached is not None:
                    s.set(cache="hit", records=len(cached.get("studies", [])))
                    return cached

            body, attempts = await self._get_with_retries(path, params)
            s.set(
                cache="miss" if key else "off",
                attempts=attempts,
                records=len(body.get("studies", [])),
                total=body.get("totalCount"),
            )
            if key is not None and self.cache is not None:
                self.cache.set(key, body)
            return body

    async def _get_with_retries(
        self, path: str, params: dict[str, str]
    ) -> tuple[dict[str, Any], int]:
        url = f"{self.base_url}{path}"
        last_error = "no attempt made"
        for attempt in range(1, self.max_attempts + 1):
            if self.limiter is not None:
                await self.limiter.acquire()
            try:
                response = await self.http.get(url, params=params)
            except httpx.TransportError as exc:  # connect/read timeouts, resets
                last_error = f"{type(exc).__name__}: {exc}"
                delay = _backoff(attempt)
            else:
                if response.status_code == 200:
                    try:
                        return response.json(), attempt
                    except ValueError as exc:
                        raise UpstreamError(f"invalid JSON from {path}") from exc
                if response.status_code not in RETRYABLE_STATUS:
                    # A 4xx here means we compiled a bad query: retrying cannot help.
                    raise UpstreamError(
                        f"HTTP {response.status_code} from {path}: {response.text[:300]}"
                    )
                last_error = f"HTTP {response.status_code}"
                delay = _retry_after(response) or _backoff(attempt)
            if attempt < self.max_attempts:
                log.warning("ctgov %s attempt %d failed (%s); retrying", path, attempt, last_error)
                await asyncio.sleep(delay)
        raise UpstreamError(f"{path} failed after {self.max_attempts} attempts: {last_error}")


def _backoff(attempt: int) -> float:
    """1s, 2s, 4s, 8s (±25% jitter): long enough for a 429 window to pass."""
    return min(8.0, 2.0 ** (attempt - 1)) * (0.75 + random.random() / 2)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        return min(30.0, float(value)) if value else None
    except ValueError:
        return None
