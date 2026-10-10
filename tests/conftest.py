"""The tests run with no deployment: the SDK's `Recorder` stands in for the
sidecar, and the synthetic exchanges this package carries stand in for the
venue (tests/venue_cases.py says which venue)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

import venue_cases as venue


def resolved(params: Any) -> Any:
    """Each asset resolved to a record named for its strongest identifier."""
    return ops.ResolveIdentifierResult(
        found=True, instrument_id=f"LCL-{params.identifiers[0].value}", minted=True
    )


class Lake(Recorder):
    """The SDK's recorder, answering a batch as the lake does: a row key new is
    recorded, the same values again unchanged (by decimal value: 764.2 is
    764.20), a changed value the next version."""

    def __init__(self, instance_id: str = venue.VENUE) -> None:
        super().__init__(instance_id)
        self.versions: dict[tuple[str, str], tuple[int, tuple[Decimal, ...]]] = {}
        self.answer("ResolveIdentifier", resolved)
        self.answer("RecordPrices", self._prices)
        self.answer("RecordBars", self._bars)

    def _prices(self, params: Any) -> Any:
        return self._record(params.prices, "price")

    def _bars(self, params: Any) -> Any:
        return self._record(params.bars, "bar")

    def on(self, row: str) -> list[Any]:
        return list(super().on(row))

    def _record(self, rows: Any, kind: str) -> Any:
        recorded = restated = unchanged = 0
        for row in rows:
            if kind == "price":
                values: tuple[Decimal, ...] = (as_decimal(row.price.amount),)
            else:
                values = tuple(as_decimal(getattr(row, f).amount) for f in ("open", "high",
                               "low", "close")) + (as_decimal(row.volume),)  # fmt: skip
            key = (kind, row.meta.row_key)
            held = self.versions.get(key)
            if held is None:
                self.versions[key] = (1, values)
                recorded += 1
            elif held[1] == values:
                unchanged += 1
            else:
                self.versions[key] = (held[0] + 1, values)
                restated += 1
        result = ops.RecordPricesResult if kind == "price" else ops.RecordBarsResult
        return result(recorded=recorded, restated=restated, unchanged=unchanged)

    def rows(self, row: str = "RecordPrices") -> list[Any]:
        sent = self.on(row)
        return [
            r for params in sent for r in (params.prices if row == "RecordPrices" else params.bars)
        ]


@pytest.fixture
def lake() -> Lake:
    return Lake()


def dgm_on(lake: Recorder, now_ns: int = venue.NOW_NS, **kw: Any) -> Any:
    return venue.Dgm(lake, venue.market(now_ns, **kw), venue.ProductMap(), clock_ns=lambda: now_ns)


def wanted(
    dgm: Any,
    want_id: str,
    key: str,
    subjects: list[str],
    *,
    data_type: str = "meridian.v1.Price",
    kinds: tuple[Any, ...] = (),
    day: Any = None,
    standing: bool = False,
) -> ops.ObservationsWantedEvent:
    """A want as the lake sends one: for a business date, or the latest."""
    return ops.ObservationsWantedEvent(
        want_id=want_id,
        dataset=dgm.dataset(key),
        data_type=data_type,
        kinds=list(kinds),
        subjects=[ops.SubjectRef(entity_id=s) for s in subjects],
        business_date=day.isoformat() if day else "",
        standing=standing,
    )


async def hear(lake: Recorder, dgm: Any, event: ops.ObservationsWantedEvent) -> None:
    """The want delivered to the plugin's own handler, through its receive."""
    lake.answer("ObservationsWanted", lambda _: event)
    await lake.receive(observations_wanted=dgm.on_want)
