"""The SDK's `dgm` suite, run unchanged against this plugin's own conversion.

The same file in meridian-coinbase and meridian-kraken (`make suite-same`
holds the two copies to each other): the plan's "one suite unchanged between
the two". Each of the suite's cases (meridian.suites, contract v18) is mapped
to the plugin's own synthetic exchange for it, named in tests/venue_cases.py,
the one module that knows which venue this is; the plugin's real client,
raw records, conversion and dgm run against the SDK's `Recorder`, its typed
operations recorded rather than sent, and the runner compares what it sent
with the case. The outcome is tested, never the route.

A case the venue does not present -- Coinbase Exchange lists no product
between two currencies, so it states no FX rate -- has no producer, and the
suite says so: the one failure this file allows, named with why, and none
other (README, "What it cannot serve").
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from typing import Any

import meridian
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder, names, run

import venue_cases as venue
from conftest import BITCOIN, OTHER, Instruments, held

Producer = Callable[[Recorder], Awaitable[None]]

#: Why a case has no producer, where this venue's source never presents it.
NOT_PRESENTED = {"an-fx-rate": "no product between two currencies"} if not venue.FX_BASE else {}


def _dgm(
    recorder: Recorder, market: Any = None, now_ns: int = venue.NOW_NS
) -> tuple[Any, Instruments]:
    """The plugin's dgm on the recorder, which resolves from an instrument
    store of the deployment's records."""
    store = Instruments().answering(recorder)
    dgm = venue.Dgm(recorder, market or venue.market(now_ns), venue.ProductMap(),
                    clock_ns=lambda: now_ns)  # fmt: skip
    return dgm, store


def _want(
    want_id: str, dataset: str, subjects: list[str], day: date, standing: bool = False
) -> Any:
    return ops.ObservationsWantedEvent(
        want_id=want_id,
        dataset=dataset,
        data_type="meridian.v1.Price",
        subjects=[ops.SubjectRef(entity_id=s) for s in subjects],
        kinds=[ops.PRICE_KIND_CLOSE],
        business_date=day.isoformat(),
        standing=standing,
    )


def producers(**mutations: Any) -> dict[str, Producer]:
    """Each case's producer: the plugin's own steps on its own exchange."""

    def daily(code: str, data_type: str = "", **kw: Any) -> Producer:
        async def produce(recorder: Recorder) -> None:
            dgm, store = _dgm(recorder, **kw)
            for name, mutated in mutations.items():
                setattr(dgm, name, mutated.__get__(dgm))
            subject = await held(store, dgm, code)
            await dgm.record_days([subject], venue.CLOSED, venue.CLOSED, data_type)

        return produce

    async def forming(recorder: Recorder) -> None:
        for read in (0, 1):
            dgm, store = _dgm(recorder, venue.market(forming=read))
            subject = await held(store, dgm, venue.BASE)
            await dgm.record_days([subject], venue.TODAY, venue.TODAY, "meridian.v1.Price")

    async def ambiguous(recorder: Recorder) -> None:
        # A custodian's record by its own symbol, which is the venue's code,
        # and another record already carrying the venue's: the two meet.
        dgm, store = _dgm(recorder)
        await held(store, dgm, venue.AMBIGUOUS)
        subject = store.hold("INS-HELD", meridian.Identifier(scheme="symbol",
                             value=venue.AMBIGUOUS, source=OTHER))  # fmt: skip
        await dgm.record_days([subject], venue.CLOSED, venue.CLOSED, "meridian.v1.Price")

    async def venue_not_held(recorder: Recorder) -> None:
        recorder.answer(
            "ResolveVenue",
            lambda _: ops.ResolveVenueResult(found=False, miss_reason=ops.MISS_REASON_NOT_FOUND),
        )
        await _dgm(recorder)[0].venue_id()

    def wanted(covered: bool) -> Producer:
        async def produce(recorder: Recorder) -> None:
            dgm, store = _dgm(recorder)
            subject = await held(store, dgm, venue.BASE) if covered else "LCL-NOT-COVERED"
            want = _want("WNT-1", dgm.dataset("daily"), [subject], venue.CLOSED)
            recorder.answer("ObservationsWanted", lambda _: want)
            await recorder.receive(observations_wanted=dgm.on_want)

        return produce

    async def elsewhere(recorder: Recorder) -> None:
        # Bitcoin as a custodian's book holds it, by its global identifier
        # and the custodian's symbol, never resolved by this venue: the
        # record read (W3.6), the venue's asset named from it.
        dgm, store = _dgm(recorder)
        subject = store.hold(
            "INS-HELD",
            meridian.Identifier(scheme="caip19", value=BITCOIN),
            meridian.Identifier(scheme="symbol", value="BTC", source=OTHER),
        )
        want = _want("WNT-3", dgm.dataset("daily"), [subject], venue.CLOSED)
        recorder.answer("ObservationsWanted", lambda _: want)
        await recorder.receive(observations_wanted=dgm.on_want)

    async def withdrawn(recorder: Recorder) -> None:
        dgm, store = _dgm(recorder)
        subject = await held(store, dgm, venue.BASE)
        dgm.standing["WNT-2"] = venue.Standing(
            "WNT-2", dgm.dataset("live"), "meridian.v1.Price", (subject,),
            (ops.PRICE_KIND_LAST,), venue.NOW_NS,
        )  # fmt: skip
        recorder.answer(
            "WantWithdrawn",
            lambda _: [ops.WantWithdrawnEvent(want_id="WNT-2", dataset=dgm.dataset("live"))],
        )
        await recorder.receive(want_withdrawn=dgm.on_withdrawn)
        await dgm.current(daily=True)  # one pass keeping current: nothing stands now

    made: dict[str, Producer] = {
        "a-daily-close": daily(venue.BASE, "meridian.v1.Price"),
        "the-forming-day-restated": forming,
        "an-exact-price": daily(venue.EXACT_BASE, "meridian.v1.Price"),
        "a-daily-bar": daily(venue.BAR_BASE, "meridian.v1.Bar"),
        "a-stablecoin-quote": daily(venue.STABLE_BASE, "meridian.v1.Price"),
        "an-asset-that-does-not-resolve": ambiguous,
        "a-venue-resolved": daily(venue.BASE, "meridian.v1.Price"),
        "a-venue-not-held": venue_not_held,
        "a-want-recorded-against": wanted(covered=True),
        "a-subject-another-source-resolved": elsewhere,
        "a-subject-declined": wanted(covered=False),
        "a-standing-want-withdrawn": withdrawn,
    }
    if venue.FX_BASE:
        made["an-fx-rate"] = daily(venue.FX_BASE, "meridian.v1.Price")
    return made


def test_the_suite_has_the_thirteen_cases_this_file_maps() -> None:
    assert set(names("dgm")) == set(producers()) | set(NOT_PRESENTED)


def test_every_case_the_venue_presents_passes_unchanged() -> None:
    report = run("dgm", producers(), instance_id=venue.VENUE)
    failures = dict(report.failures)
    for case, why in NOT_PRESENTED.items():
        assert failures.pop(case) == "no producer: the plugin does not map this case", why
    assert not failures, failures
    assert len(report.passed_cases) == 13 - len(NOT_PRESENTED)


def test_a_candle_read_through_a_float_fails_the_suite() -> None:
    def through_float(self: Any, mapped: Any, candle: Any) -> Any:
        from dataclasses import replace

        lossy = replace(candle, close=Decimal(float(candle.close)))
        return type(self).close(self, mapped, lossy)

    report = run("dgm", producers(close=through_float), instance_id=venue.VENUE)
    assert "an-exact-price" in report.failures


def test_a_stablecoin_price_as_its_currency_fails_the_suite() -> None:
    def as_fiat(self: Any, amount: Any, mapped: Any) -> Any:
        from meridian import Money

        return Money(amount, mapped.quote.currency_code or "USD")

    report = run("dgm", producers(_money=as_fiat), instance_id=venue.VENUE)
    assert "a-stablecoin-quote" in report.failures


def test_a_case_not_presented_is_said_with_why() -> None:
    assert all(why.strip() for why in NOT_PRESENTED.values())
    assert set(NOT_PRESENTED) <= {"an-fx-rate"}, "only a case the venue cannot present"
