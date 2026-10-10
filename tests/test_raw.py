"""The raw responses in storage: a unit a day, and each day past its window
archived, kept or deleted through the SDK, never today's.

The same file in meridian-coinbase and meridian-kraken (`make suite-same`)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import venue_cases as venue

KIND = venue.raw.KIND
DAY_NS = 86_400 * 1_000_000_000
OCT_1 = 1_790_812_800 * 1_000_000_000


class Mover:
    """The SDK's moves, as a plugin calls them, recorded."""

    def __init__(self) -> None:
        self.moved: list[tuple[str, str, int]] = []
        self.stored: Any = None

    async def archive_unit(self, kind: str, unit: str, **span: int) -> None:
        self.moved.append(("archived", unit, span["record_count"]))

    async def delete_unit(self, kind: str, unit: str, **span: int) -> None:
        self.moved.append(("deleted", unit, span["record_count"]))


def _kept(root: Path) -> Any:
    store = venue.raw.RawStore(root)
    for day in range(10):
        at = OCT_1 + day * DAY_NS
        store.keep("candles", "BTC-USD", {"received_at_ns": at, "body": "[]", "status": 200})
        store.keep_line("ticker", "BTC-USD", {"received_at_ns": at + 1, "body": str(day)})
    return store


async def test_each_day_is_one_unit_counting_its_records(tmp_path: Path) -> None:
    units = _kept(tmp_path).units()
    assert [u.unit for u in units][:2] == [f"{KIND}/2026-10-01", f"{KIND}/2026-10-02"]
    assert all(u.records == 2 for u in units)


async def test_days_past_the_window_are_archived_and_today_never(tmp_path: Path) -> None:
    store, mover = _kept(tmp_path), Mover()
    await venue.raw.tend(mover, store, 3, "archived", date(2026, 10, 10))  # type: ignore[arg-type]
    assert [m[1] for m in mover.moved] == [f"{KIND}/2026-10-0{d}" for d in range(1, 7)]
    assert mover.stored[0].record_kind == KIND and mover.stored[0].record_count == 20


async def test_kept_moves_nothing_and_deleted_deletes(tmp_path: Path) -> None:
    store, mover = _kept(tmp_path), Mover()
    await venue.raw.tend(mover, store, 3, "kept", date(2026, 10, 10))  # type: ignore[arg-type]
    assert not mover.moved
    await venue.raw.tend(mover, store, 8, "deleted", date(2026, 10, 10))  # type: ignore[arg-type]
    assert mover.moved == [("deleted", f"{KIND}/2026-10-01", 2)]


async def test_a_move_refused_keeps_the_unit(tmp_path: Path) -> None:
    class Refusing(Mover):
        async def archive_unit(self, kind: str, unit: str, **span: int) -> None:
            raise RuntimeError("no archive given")

    store, mover = _kept(tmp_path), Refusing()
    await venue.raw.tend(mover, store, 3, "archived", date(2026, 10, 10))  # type: ignore[arg-type]
    assert len(store.units()) == 10


def test_a_live_line_names_its_place_and_a_repeat_names_it_again(tmp_path: Path) -> None:
    store = venue.raw.RawStore(tmp_path)
    first = store.keep_line("book", "BTC-USD", {"received_at_ns": OCT_1, "body": "a"})
    again = store.keep_line("book", "BTC-USD", {"received_at_ns": OCT_1 + 1, "body": "a"})
    changed = store.keep_line("book", "BTC-USD", {"received_at_ns": OCT_1 + 2, "body": "b"})
    assert first == again and first.endswith("#0") and changed.endswith("#1")
    assert store.get(changed) == {"received_at_ns": OCT_1 + 2, "body": "b"}
    assert store.get("responses/nowhere.json") is None
    assert len(list(store.records())) == 2
