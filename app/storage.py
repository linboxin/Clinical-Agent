"""Run store: one JSON record per run (request, response, trace) plus a sidecar with the full
citation set of every datum, which backs GET /v1/runs/{id}/evidence.

Plain files keep the service dependency-free for graders (DESIGN D2); the Protocol is the seam
for a database-backed store in production.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.contracts.request import VisualizationRequest
from app.contracts.response import VisualizationResponse

_RUN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass
class RunRecord:
    run_id: str
    created_at: str
    request: VisualizationRequest
    response: VisualizationResponse
    trace: dict[str, Any]


class RunStore(Protocol):
    def save(
        self,
        request: VisualizationRequest,
        response: VisualizationResponse,
        trace: dict[str, Any],
        evidence: dict[str, list[dict[str, Any]]],
    ) -> None: ...

    def get(self, run_id: str) -> RunRecord | None: ...

    def evidence(self, run_id: str, datum_id: str) -> list[dict[str, Any]] | None: ...


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)  # readers never see a half-written file


class FileRunStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str, suffix: str) -> Path | None:
        if not _RUN_ID.match(run_id):  # also blocks path traversal
            return None
        return self.directory / f"{run_id}{suffix}"

    def save(
        self,
        request: VisualizationRequest,
        response: VisualizationResponse,
        trace: dict[str, Any],
        evidence: dict[str, list[dict[str, Any]]],
    ) -> None:
        record = {
            "run_id": response.run_id,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "request": request.model_dump(mode="json", exclude_none=True),
            "response": response.model_dump(mode="json"),
            "trace": trace,
        }
        _write_atomic(self.directory / f"{response.run_id}.json", json.dumps(record))
        _write_atomic(self.directory / f"{response.run_id}.evidence.json", json.dumps(evidence))

    def get(self, run_id: str) -> RunRecord | None:
        path = self._path(run_id, ".json")
        if path is None or not path.exists():
            return None
        raw = json.loads(path.read_text())
        return RunRecord(
            run_id=raw["run_id"],
            created_at=raw["created_at"],
            request=VisualizationRequest.model_validate(raw["request"]),
            response=VisualizationResponse.model_validate(raw["response"]),
            trace=raw["trace"],
        )

    def evidence(self, run_id: str, datum_id: str) -> list[dict[str, Any]] | None:
        path = self._path(run_id, ".evidence.json")
        if path is None or not path.exists():
            return None
        found: list[dict[str, Any]] | None = json.loads(path.read_text()).get(datum_id)
        return found
