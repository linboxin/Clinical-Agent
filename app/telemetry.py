"""Per-run tracing: a tree of timed spans (stages, model calls, registry requests) stored with
the run and served at GET /v1/runs/{run_id}/trace.

Deliberately dependency-free: spans carry the same fields an OpenTelemetry span would (name,
parent, start, duration, attributes, status), so exporting to an OTLP collector such as Arize
Phoenix is a thin adapter (DESIGN §12). Parent tracking uses a ContextVar, so spans opened in
concurrent asyncio tasks (one per cohort) still nest under the right stage.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Span:
    id: int
    name: str
    parent_id: int | None
    start_ms: float
    duration_ms: float | None = None
    status: str = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)

    def set(self, **attributes: Any) -> None:
        self.attributes.update({k: v for k, v in attributes.items() if v is not None})


class Trace:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.spans: list[Span] = []
        self._t0 = time.perf_counter()

    def _now_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 1)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        parent = _current_span.get()
        span = Span(len(self.spans) + 1, name, parent.id if parent else None, self._now_ms())
        span.set(**attributes)
        self.spans.append(span)
        token = _current_span.set(span)
        try:
            yield span
        except BaseException as exc:
            span.status = "error"
            span.set(error=f"{type(exc).__name__}: {exc}"[:300])
            raise
        finally:
            span.duration_ms = round(self._now_ms() - span.start_ms, 1)
            _current_span.reset(token)

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "spans": [
                {
                    "id": s.id,
                    "parent_id": s.parent_id,
                    "name": s.name,
                    "start_ms": s.start_ms,
                    "duration_ms": s.duration_ms,
                    "status": s.status,
                    "attributes": s.attributes,
                }
                for s in self.spans
            ],
        }


_current_trace: ContextVar[Trace | None] = ContextVar("current_trace", default=None)
_current_span: ContextVar[Span | None] = ContextVar("current_span", default=None)


@contextmanager
def activate(trace: Trace) -> Iterator[Trace]:
    token = _current_trace.set(trace)
    try:
        yield trace
    finally:
        _current_trace.reset(token)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """Open a span on the active trace; a detached no-op span when no run is being traced."""
    trace = _current_trace.get()
    if trace is None:
        yield Span(0, name, None, 0.0)
        return
    with trace.span(name, **attributes) as s:
        yield s
