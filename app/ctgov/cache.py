"""Tiny file cache for API pages: keeps development and repeated questions fast and cheap
for the public API. Keys include the registry dataTimestamp, so a registry refresh
invalidates entries naturally (DESIGN §7)."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class FileCache:
    def __init__(self, directory: Path, ttl_seconds: int) -> None:
        self.directory = directory
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def key(*parts: Any) -> str:
        blob = json.dumps(parts, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if time.time() - entry["stored_at"] > self.ttl_seconds:
            return None
        return entry["body"]

    def set(self, key: str, body: Any) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"stored_at": time.time(), "body": body}))
        tmp.replace(path)  # atomic: readers never see a half-written page
