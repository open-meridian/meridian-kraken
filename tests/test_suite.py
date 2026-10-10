"""The SDK's `dgm` suite, run unchanged against this plugin's own conversion.

The same file in meridian-coinbase and meridian-kraken (`make suite-same`
holds the two copies to each other): the plan's "one suite unchanged between
the two". Each of the suite's cases (meridian.suites, contracts v18 and v19)
is mapped to the plugin's own synthetic exchange for it, named in
tests/venue_cases.py, the one module that knows which venue this is; the
plugin's real client, raw records, conversion, stream and dgm run against the
SDK's `Recorder`, its typed operations recorded rather than sent, and the
runner compares what it sent with the case. The outcome is tested, never the
route. A case about trades or quotes is presented by the venue's own stream
message for it, replayed through the plugin's stream (stream.py), or by its
level-1 book.

A case about something the venue never states -- an FX rate where it lists
no product between two currencies, a trade's condition codes, a withdrawn
trade, the resting order's side where it names the taker's -- is named in
venue_cases.NOT_PRESENTED with why, and the runner holds it to being a case
about a kind of data (`about`, contract v19); every other case is mapped
(README, "What it cannot serve").
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from typing import Any

import meridian
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder, names, run, suite

import venue_cases as venue
from conftest import BITCOIN, OTHER, Instruments, held

Producer = Callable[[Recorder], Awaitable[None]]

#: Why a case has no producer, where this venue's source never presents it.
NOT_PRESENTED: dict[str, str] = venue.NOT_PRESENTED


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

    def streamed(message: str, key: str, data_type: str) -> Producer:
        # A standing want of a streamed dataset for BASE, then the venue's
        # stream stating the case's message, replayed through the plugin's
        # stream, which drops once it is said.
        async def produce(recorder: Recorder) -> None:
            dgm, store = _dgm(recorder)
            for name, mutated in mutations.items():
                setattr(dgm, name, mutated.__get__(dgm))
            subject = await held(store, dgm, venue.BASE)
            want = ops.ObservationsWantedEvent(
                want_id="WNT-S", dataset=dgm.dataset(key), data_type=data_type,
                subjects=[ops.SubjectRef(entity_id=subject)], standing=True,
            )  # fmt: skip
            recorder.answer("ObservationsWanted", lambda _: want)
            await recorder.receive(observations_wanted=dgm.on_want)

            async def replayed(_: str) -> Any:
                return venue.stream.Replayed([message], hold=False)

            dgm.stream.connect = replayed
            await dgm.stream.run(once=True)
            await dgm.flush()

        return produce

    async def one_sided(recorder: Recorder) -> None:
        dgm, store = _dgm(recorder)
        for name, mutated in mutations.items():
            setattr(dgm, name, mutated.__get__(dgm))
        subject = await held(store, dgm, venue.ONE_SIDED_BASE)
        await dgm.record_quotes([subject])

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
    trades, quotes = (("trades", "meridian.v1.Trade"), ("live", "meridian.v1.Quote"))
    for case, message in venue.STREAM_CASES.items():
        made[case] = streamed(message, *(quotes if case == "a-quote-recorded" else trades))
    made["a-one-sided-quote"] = one_sided
    return {case: produce for case, produce in made.items() if case not in NOT_PRESENTED}


def test_the_suite_has_the_twenty_one_cases_this_file_maps() -> None:
    assert len(list(names("dgm"))) == 21
    assert set(names("dgm")) == set(producers()) | set(NOT_PRESENTED)
    assert not set(producers()) & set(NOT_PRESENTED)


def test_every_case_the_venue_presents_passes_unchanged() -> None:
    report = run("dgm", producers(), not_presented=NOT_PRESENTED, instance_id=venue.VENUE)
    assert report.passed, report.failures
    assert len(report.passed_cases) == 21 - len(NOT_PRESENTED)


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
    about = {case.name: case.about for case in suite("dgm").cases}
    assert all(about[case] for case in NOT_PRESENTED), "only a case about a kind of data"
    assert set(NOT_PRESENTED) <= {
        "an-fx-rate", "a-condition-converted", "a-condition-not-converted",
        "a-trade-withdrawn", "the-makers-side-inverted",
    }, "only a case the venue cannot present"  # fmt: skip


def test_the_side_passed_through_fails_the_suite_where_it_is_the_makers() -> None:
    report = run("dgm", producers(aggressor=lambda self, seen: seen.side),
                 not_presented=NOT_PRESENTED, instance_id=venue.VENUE)  # fmt: skip
    assert ("a-trade-recorded" in report.failures) == (venue.SIDE_OF == "maker")


def test_the_side_read_the_other_way_fails_the_suite() -> None:
    def other_way(self: Any, seen: Any) -> str:
        said = type(self).aggressor(self, seen)
        return {"buy": "sell", "sell": "buy"}.get(said, "")

    report = run("dgm", producers(aggressor=other_way), not_presented=NOT_PRESENTED,
                 instance_id=venue.VENUE)  # fmt: skip
    assert "a-trade-recorded" in report.failures


def test_an_empty_side_recorded_as_zero_fails_the_suite() -> None:
    def zero(self: Any, mapped: Any, best: Any) -> Any:
        from dataclasses import replace
        from decimal import Decimal

        filled = replace(best, ask=best.ask if best.ask is not None else Decimal(0))
        return type(self).quote(self, mapped, filled)

    report = run("dgm", producers(quote=zero), not_presented=NOT_PRESENTED,
                 instance_id=venue.VENUE)  # fmt: skip
    assert "a-one-sided-quote" in report.failures
