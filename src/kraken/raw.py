"""The venue's raw responses, kept as the plugin's raw records (decisions/028).

The same module in meridian-coinbase and meridian-kraken.

Every response the venue sends -- an error and a rate limit as much as a
candle -- is kept before anything is read from it, exactly as its bytes
arrived, with the request that asked for it and when it was received. A row
converted from it names it by a `RawRecordRef` whose key is the record's
path in the plugin's storage, then `#` and where in it the row came from
(`#1791417600`, a candle's start; `#41`, a line of a day's live responses),
so a person auditing a row follows it here and finds what the venue said.
From contract v19 the stream's messages are kept the same way (stream.py):
each a line of one file per day and message type, under `stream/`, before
anything is read from it; a heartbeat, which states nothing, is not kept.

The plugin declares one kind of raw record, `responses` (declaration.py),
kept in units of a UTC day: `responses/2026-10-09/`, holding that day's
responses as one file each, and its live responses (the ticker and the
level-1 book, asked as often as once a second) as a line each of one file
per product and call, a line added only when the venue's answer changed.

Past the kind's window a day's unit is archived, kept or deleted as its
`responses_past_window` setting says, through the SDK (`tend`), which
reports each move before anything is removed; and what storage holds is said
on every heartbeat (`plugin.stored`).

Where the records live: `meridian.edge.storage_dir()` where the deployment
grants the instance storage (a data plugin is an edge role); otherwise a temporary
directory, which a restart loses.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import meridian
from meridian import edge
from meridian.v1 import sidecar_pb2

from .market import NANOS_PER_SECOND, utc_day

log = logging.getLogger(__name__)

KIND = "responses"
LABEL = "Raw responses"
#: The kind's default window: a month of the venue's answers, enough to
#: re-convert a row a person questions. An admin sets it in Settings.
WINDOW_DAYS = 30

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def storage_root() -> Path:
    """The instance's granted storage, else a temporary directory."""
    granted = edge.storage_dir()
    if granted is not None:
        return granted
    return Path(tempfile.mkdtemp(prefix="meridian-data-"))


def _safe(part: str) -> str:
    return _UNSAFE.sub("_", part) or "_"


@dataclass(frozen=True)
class Unit:
    """One UTC day's responses: its path in storage, how many records it holds
    and when the first and last arrived."""

    unit: str
    day: date
    records: int
    first_ns: int
    last_ns: int


class RawStore:
    """Raw records as files under the storage's `responses/`, one unit a day."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._kept = 0
        self._last: dict[str, str] = {}
        #: The lines each day's file holds, counted once: a stream adds many.
        self._lines: dict[str, int] = {}

    def _day(self, received_ns: int) -> str:
        return utc_day(received_ns // NANOS_PER_SECOND).isoformat()

    def keep(self, call: str, name: str, record: dict[str, Any]) -> str:
        """Keep one response; its key. A key is never written twice."""
        self._kept += 1
        received = int(record["received_at_ns"])
        key = (
            f"{KIND}/{self._day(received)}/{_safe(call)}/{_safe(name)}-{received}-{self._kept}.json"
        )
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as out:
            json.dump(record, out, indent=1, sort_keys=True)
        return key

    def keep_line(self, call: str, name: str, record: dict[str, Any]) -> str:
        """Keep one live response as the next line of its day's file for
        `call` and `name`, unless its body is the one kept last for them, which
        the returned key then names again: `<file>#<line>`."""
        received = int(record["received_at_ns"])
        key = f"{KIND}/{self._day(received)}/{_safe(call)}/{_safe(name)}.jsonl"
        body = str(record.get("body", ""))
        held = self._last.get(key)
        if held is not None and held.startswith(body + "\0"):
            return held.split("\0", 1)[1]
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = self._lines.get(key)
        if lines is None:
            lines = sum(1 for _ in path.open(encoding="utf-8")) if path.exists() else 0
        with path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, sort_keys=True) + "\n")
        self._lines[key] = lines + 1
        named = f"{key}#{lines}"
        self._last[key] = body + "\0" + named
        return named

    def get(self, ref_key: str) -> dict[str, Any] | None:
        """The record a row's reference names: a whole response, or one line of
        a day's live responses. None once it has moved or gone."""
        key, _, where = ref_key.partition("#")
        path = self.root / key
        if not path.is_file():
            return None
        if path.suffix == ".jsonl":
            if not where.isdigit():
                return None
            lines = path.read_text(encoding="utf-8").splitlines()
            if int(where) >= len(lines):
                return None
            line: dict[str, Any] = json.loads(lines[int(where)])
            return line
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return record

    def records(self) -> Iterator[dict[str, Any]]:
        """Every response kept, oldest day first: what a recorded replay reads."""
        base = self.root / KIND
        if not base.is_dir():
            return
        for path in sorted(base.rglob("*")):
            if path.suffix == ".json":
                yield json.loads(path.read_text(encoding="utf-8"))
            elif path.suffix == ".jsonl":
                for line in path.read_text(encoding="utf-8").splitlines():
                    yield json.loads(line)

    def units(self) -> list[Unit]:
        """Each day's unit in storage, oldest first."""
        base = self.root / KIND
        found: list[Unit] = []
        if not base.is_dir():
            return found
        for directory in sorted(p for p in base.iterdir() if p.is_dir()):
            try:
                day = date.fromisoformat(directory.name)
            except ValueError:
                continue
            count, first, last = 0, 0, 0
            for path in directory.rglob("*"):
                if path.suffix == ".json":
                    stamps = [int(json.loads(path.read_text(encoding="utf-8"))["received_at_ns"])]
                elif path.suffix == ".jsonl":
                    stamps = [
                        int(json.loads(line)["received_at_ns"])
                        for line in path.read_text(encoding="utf-8").splitlines()
                    ]
                else:
                    continue
                for at in stamps:
                    count += 1
                    first = at if not first else min(first, at)
                    last = max(last, at)
            if count:
                found.append(Unit(f"{KIND}/{directory.name}", day, count, first, last))
        return found


async def tend(
    plugin: meridian.Plugin, store: RawStore, window_days: int, past: str, today: date
) -> None:
    """Move each day's unit past the window as `past` says -- `archived` or
    `deleted` through the SDK, `kept` left -- never today's, which is still
    being written; then say what storage holds. A move refused (no archive,
    a deletion inside the deployment's hold) keeps the unit, and the next
    pass tries again."""
    cutoff = today - timedelta(days=max(window_days, 0))
    for unit in store.units():
        if unit.day >= cutoff or unit.day >= today or past == "kept":
            continue
        try:
            if past == "archived":
                await plugin.archive_unit(
                    KIND,
                    unit.unit,
                    record_count=unit.records,
                    first_received_ns=unit.first_ns,
                    last_received_ns=unit.last_ns,
                )
            elif past == "deleted":
                await plugin.delete_unit(
                    KIND,
                    unit.unit,
                    record_count=unit.records,
                    first_received_ns=unit.first_ns,
                    last_received_ns=unit.last_ns,
                )
        except (meridian.MeridianError, ValueError, RuntimeError, OSError) as refused:
            log.warning("%s kept, its move refused: %s", unit.unit, refused)
    held = store.units()
    if held:
        plugin.stored = [
            sidecar_pb2.StoredSpan(
                record_kind=KIND,
                record_count=sum(u.records for u in held),
                first_received_ns=min(u.first_ns for u in held),
                last_received_ns=max(u.last_ns for u in held),
            )
        ]
