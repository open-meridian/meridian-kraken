"""The venue's raw responses, kept as the plugin's raw records (decisions/028).

Every response the venue sends -- an error and a rate limit as much as a
candle -- is kept before anything is converted from it, exactly as its bytes
arrived, with the request that asked for it and the moment it was received.
A row converted from it carries a `RawRecordRef` whose key is the record's
key, then `#` and where in the record the row came from (`#1790985600`, a
candle's time; `#3`, a stream's fourth message), so a person auditing a row
follows it here and finds exactly what the venue said.

Where the records live: `meridian.edge.storage_dir()` when the deployment
grants the instance storage (a `dgm` is an edge role); a temporary directory
in the tests; `recordings/`, git-ignored, for the CLI and the live smoke test
(real recordings stay local: the plan's Q11). No other plugin reads them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import meridian.edge

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def storage_root(fallback: Path) -> Path:
    """Where raw records go: the instance's granted storage, else `fallback`."""
    granted = meridian.edge.storage_dir()
    return (granted / "raw") if granted is not None else fallback


class RawStore:
    """Raw records as JSON files under one directory, by key."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        parts = [_UNSAFE.sub("_", part) or "_" for part in key.split("/")]
        return self.root.joinpath(*parts).with_suffix(".json")

    def keep(self, key: str, record: dict[str, Any]) -> str:
        """Keep one record under `key`; a key already kept is never overwritten."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as out:
            json.dump(record, out, indent=1, sort_keys=True)
        return key

    def append(self, key: str, line: dict[str, Any]) -> int:
        """Add one message to a stream's record; its index in the record."""
        path = self._path(key).with_suffix(".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        index = sum(1 for _ in path.open(encoding="utf-8")) if path.exists() else 0
        with path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(line, sort_keys=True) + "\n")
        return index

    def get(self, ref_key: str) -> dict[str, Any] | None:
        """The record a row's reference names, and where in it: for a stream,
        the one message; else the whole record. None once it is gone."""
        key, _, where = ref_key.partition("#")
        path = self._path(key)
        if path.exists():
            record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return record
        stream = path.with_suffix(".jsonl")
        if stream.exists() and where.isdigit():
            lines = stream.read_text(encoding="utf-8").splitlines()
            if int(where) < len(lines):
                message: dict[str, Any] = json.loads(lines[int(where)])
                return message
        return None

    def records(self) -> Iterator[str]:
        for path in sorted(self.root.rglob("*.json*")):
            yield str(path.relative_to(self.root).with_suffix(""))
