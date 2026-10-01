"""Run store. v1 keeps each response as a JSON file; v3 swaps in Postgres behind the same
interface (DESIGN §11, §16)."""

import re
from pathlib import Path
from typing import Protocol

from app.contracts.response import VisualizationResponse

_RUN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class RunStore(Protocol):
    def save(self, response: VisualizationResponse) -> None: ...

    def get(self, run_id: str) -> VisualizationResponse | None: ...


class FileRunStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def save(self, response: VisualizationResponse) -> None:
        path = self.directory / f"{response.run_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(response.model_dump_json(indent=2))
        tmp.replace(path)

    def get(self, run_id: str) -> VisualizationResponse | None:
        if not _RUN_ID.match(run_id):  # also blocks path traversal
            return None
        path = self.directory / f"{run_id}.json"
        if not path.exists():
            return None
        return VisualizationResponse.model_validate_json(path.read_text())
