"""Append JSON Lines decision records without storing credentials or raw UI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class TraceWriter:
    def __init__(self, path: Path | None):
        self.path = path
        self._file = None

    def __enter__(self) -> "TraceWriter":
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            self._file = os.fdopen(descriptor, "a", encoding="utf-8")
        return self

    def write(self, record: dict[str, Any]) -> None:
        if self._file is not None:
            self._file.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")
            self._file.flush()

    def __exit__(self, *_: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
