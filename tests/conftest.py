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

#: Another source the deployment holds records from: a custodian's book.
OTHER = "custodian"
#: Bitcoin's CAIP-19, by its SLIP-44 coin type: the global identifier a
#: custodian's record may carry beside its own symbol, as an equity's carries
#: a CUSIP.
BITCOIN = "bip122:000000000019d6689c085ae165831e93/slip44:0"


def _same(one: Any, other: Any) -> bool:
    return bool((one.scheme, one.value, one.source) == (other.scheme, other.value, other.source))


class Instruments:
    """The deployment's instrument store as the tests hold it. ResolveInstrument
    reads a record by its ID (W3.6); ResolveIdentifier answers the one record
    the identifiers meet, the others joined to it, a record minted where they
    meet none, and ambiguous where they meet two (W3.1)."""

    def __init__(self) -> None:
        self.records: dict[str, list[ops.Identifier]] = {}

    def hold(self, instrument_id: str, *identifiers: ops.Identifier) -> str:
        self.records[instrument_id] = list(identifiers)
        return instrument_id

    def read(self, params: Any) -> Any:
        held = self.records.get(params.instrument_id)
        if held is None:
            return ops.ResolveInstrumentResult(found=False)
        record = ops.InstrumentRecord(instrument_id=params.instrument_id, identifiers=held)
        return ops.ResolveInstrumentResult(found=True, instrument=record)

    def resolve(self, params: Any) -> Any:
        given = list(params.identifiers)
        met = [key for key, held in self.records.items()
               if any(_same(g, h) for g in given for h in held)]  # fmt: skip
        if len(met) > 1:
            return ops.ResolveIdentifierResult(found=False, miss_reason=ops.MISS_REASON_AMBIGUOUS)
        if met:
            held = self.records[met[0]]
            held.extend(g for g in given if not any(_same(g, h) for h in held))
            return ops.ResolveIdentifierResult(found=True, instrument_id=met[0])
        minted = self.hold(f"LCL-{given[0].value}", *given)
        return ops.ResolveIdentifierResult(found=True, instrument_id=minted, minted=True)

    def answering(self, recorder: Recorder) -> Instruments:
        recorder.answer("ResolveIdentifier", self.resolve)
        recorder.answer("ResolveInstrument", self.read)
        return self


async def held(store: Instruments, dgm: Any, code: str) -> str:
    """A record the deployment holds for one of the venue's assets, carrying
    the identifiers the venue's own statements give it, as a book's record
    resolved from this venue would."""
    await dgm.ensure_mapped()
    asset = dgm.products.asset(code)
    assert asset is not None, f"the venue trades no {code}"
    return store.hold(f"INS-{code}", *dgm.identifiers(asset))


class Lake(Recorder):
    """The SDK's recorder, answering a batch as the lake does: a row key new is
    recorded, the same values again unchanged (by decimal value: 764.2 is
    764.20), a changed value the next version; and resolving from its own
    instrument store."""

    def __init__(self, instance_id: str = venue.VENUE) -> None:
        super().__init__(instance_id)
        self.versions: dict[tuple[str, str], tuple[int, tuple[Decimal, ...]]] = {}
        self.instruments = Instruments().answering(self)
        self.answer("RecordPrices", self._prices)
        self.answer("RecordBars", self._bars)
        self.answer("RecordTrades", lambda params: self._record(params.trades, "trade"))
        self.answer("RecordQuotes", lambda params: self._record(params.quotes, "quote"))

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
            elif kind == "trade":
                values = (as_decimal(row.price.amount), as_decimal(row.quantity),
                          Decimal(int(row.cancelled)))  # fmt: skip
            elif kind == "quote":
                values = tuple(as_decimal(getattr(row, side).amount) if row.HasField(side)
                               else Decimal(-1) for side in ("bid", "ask"))  # fmt: skip
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
        results = {"price": ops.RecordPricesResult, "bar": ops.RecordBarsResult,
                   "trade": ops.RecordTradesResult, "quote": ops.RecordQuotesResult}  # fmt: skip
        result = results[kind]
        return result(recorded=recorded, restated=restated, unchanged=unchanged)

    def rows(self, row: str = "RecordPrices") -> list[Any]:
        field = {"RecordPrices": "prices", "RecordBars": "bars", "RecordTrades": "trades",
                 "RecordQuotes": "quotes"}[row]  # fmt: skip
        return [r for params in self.on(row) for r in getattr(params, field)]

    async def held(self, dgm: Any, code: str) -> str:
        return await held(self.instruments, dgm, code)


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
