"""The SDK's data suite, run unchanged against this plugin's own conversion.

The same file in meridian-coinbase and meridian-kraken (`make suite-same`
holds the two copies to each other): the plan's "one suite unchanged between
the two". Each of the suite's cases (meridian.suites, contracts v18 to v20)
is mapped to the plugin's own synthetic exchange for it, named in
tests/venue_cases.py, the one module that knows which venue this is; the
plugin's real client, raw records, conversion, stream and data plugin's work run against the
SDK's `Recorder`, its typed operations recorded rather than sent, and the
runner compares what it sent with the case. The outcome is tested, never the
route. A case about trades or quotes is presented by the venue's own stream
message for it, replayed through the plugin's stream (stream.py), or by its
level-1 book. From contract v20 a restart's gap is read back from the
venue's history after the last trade the lake holds of a streamed subject,
and the plugin reads its own datasets' licences.

A case about something the venue never states -- an FX rate where it lists
no product between two currencies, a trade's condition codes, a withdrawn
trade, the side it does not name, the resting order's or the taker's -- is
named in venue_cases.NOT_PRESENTED with why, and the runner holds it to
being a case about a kind of data (`about`, contract v19) or a field
(`about_field`, contract v20); every other case is mapped (README, "What it
cannot serve").
"""

from __future__ import annotations

import asyncio
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
#: The case about the side the venue names (`about_field` aggressor, contract
#: v20): the taker's stated, or the resting order's inverted.
SIDE_CASE = next(
    case for case in ("the-takers-side-stated", "the-makers-side-inverted")
    if case not in NOT_PRESENTED
)  # fmt: skip


def _dgm(
    recorder: Recorder, market: Any = None, now_ns: int = venue.NOW_NS
) -> tuple[Any, Instruments]:
    """The plugin's work (Dgm) on the recorder, which resolves from an instrument
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

    async def restarted(recorder: Recorder) -> None:
        # The plugin started again: the lake holds the last trade this
        # instance recorded of BASE's dollar product before it stopped, and
        # the venue's history states two since. The standing want of the
        # trades dataset, heard again, reads the gap back before streaming.
        dgm, store = _dgm(recorder)
        subject = await held(store, dgm, venue.BASE)
        product, ident, at_ns = venue.RESTARTED
        last = ops.Trade(meta=ops.ObservationMeta(
            row_key=f"{product}:t:{ident}", subjects=[ops.SubjectRef(entity_id=subject)],
            source=ops.Source(dataset=dgm.dataset("trades")), valid_from_ns=at_ns,
        ))  # fmt: skip
        recorder.answer("ListTrades", lambda _: ops.ListTradesResult(trades=[last]))
        want = ops.ObservationsWantedEvent(
            want_id="WNT-S", dataset=dgm.dataset("trades"), data_type="meridian.v1.Trade",
            subjects=[ops.SubjectRef(entity_id=subject)], standing=True,
        )  # fmt: skip
        recorder.answer("ObservationsWanted", lambda _: want)
        await recorder.receive(observations_wanted=dgm.on_want)

    async def licences(recorder: Recorder) -> None:
        # A deployment admin has licensed the daily dataset and not the others.
        dgm, _ = _dgm(recorder)
        recorder.answer("ListDatasets", lambda _: ops.ListDatasetsResult(
            licences=[ops.DatasetLicence(dataset=dgm.dataset("daily"), kept=True)]))  # fmt: skip
        await dgm.read_licences()

    made["a-restarts-gap-backfilled"] = restarted
    made["reads-its-datasets-licences"] = licences
    return {case: produce for case, produce in made.items() if case not in NOT_PRESENTED}


def test_the_suite_has_the_twenty_four_cases_this_file_maps() -> None:
    assert len(list(names("data"))) == 24
    assert set(names("data")) == set(producers()) | set(NOT_PRESENTED)
    assert not set(producers()) & set(NOT_PRESENTED)


def test_every_case_the_venue_presents_passes_unchanged() -> None:
    report = run("data", producers(), not_presented=NOT_PRESENTED, instance_id=venue.VENUE)
    assert report.passed, report.failures
    assert len(report.passed_cases) == 24 - len(NOT_PRESENTED)


def test_a_candle_read_through_a_float_fails_the_suite() -> None:
    def through_float(self: Any, mapped: Any, candle: Any) -> Any:
        from dataclasses import replace

        lossy = replace(candle, close=Decimal(float(candle.close)))
        return type(self).close(self, mapped, lossy)

    report = run("data", producers(close=through_float), instance_id=venue.VENUE)
    assert "an-exact-price" in report.failures


def test_a_stablecoin_price_as_its_currency_fails_the_suite() -> None:
    def as_fiat(self: Any, amount: Any, mapped: Any) -> Any:
        from meridian import Money

        return Money(amount, mapped.quote.currency_code or "USD")

    report = run("data", producers(_money=as_fiat), instance_id=venue.VENUE)
    assert "a-stablecoin-quote" in report.failures


def test_a_case_not_presented_is_said_with_why() -> None:
    assert all(why.strip() for why in NOT_PRESENTED.values())
    about = {case.name: case.about or case.about_field for case in suite("data").cases}
    assert all(about[case] for case in NOT_PRESENTED), "only a case about data or a field"
    assert set(NOT_PRESENTED) <= {
        "an-fx-rate", "a-condition-converted", "a-condition-not-converted",
        "a-trade-withdrawn", "the-makers-side-inverted", "the-takers-side-stated",
    }, "only a case the venue cannot present"  # fmt: skip
    sides = {"the-makers-side-inverted", "the-takers-side-stated"}
    assert len(sides & set(NOT_PRESENTED)) == 1, "the venue names one side or the other"


def test_the_side_passed_through_fails_the_suite_where_it_is_the_makers() -> None:
    report = run("data", producers(aggressor=lambda self, seen: seen.side),
                 not_presented=NOT_PRESENTED, instance_id=venue.VENUE)  # fmt: skip
    assert (SIDE_CASE in report.failures) == (venue.SIDE_OF == "maker")


def test_the_side_read_the_other_way_fails_the_suite() -> None:
    def other_way(self: Any, seen: Any) -> str:
        said = type(self).aggressor(self, seen)
        return {"buy": "sell", "sell": "buy"}.get(said, "")

    report = run("data", producers(aggressor=other_way), not_presented=NOT_PRESENTED,
                 instance_id=venue.VENUE)  # fmt: skip
    assert SIDE_CASE in report.failures


def test_an_empty_side_recorded_as_zero_fails_the_suite() -> None:
    def zero(self: Any, mapped: Any, best: Any) -> Any:
        from dataclasses import replace
        from decimal import Decimal

        filled = replace(best, ask=best.ask if best.ask is not None else Decimal(0))
        return type(self).quote(self, mapped, filled)

    report = run("data", producers(quote=zero), not_presented=NOT_PRESENTED,
                 instance_id=venue.VENUE)  # fmt: skip
    assert "a-one-sided-quote" in report.failures


async def _drive(produce: Producer, recorder: Recorder) -> None:
    await produce(recorder)


def _delivering(want: Any) -> Callable[[Any], Any]:
    return lambda _: want


def test_a_restarts_gap_is_the_trades_after_the_last_one_recorded() -> None:
    recorder = Recorder(venue.VENUE)
    asyncio.run(_drive(producers()["a-restarts-gap-backfilled"], recorder))
    [asked] = recorder.on("ListTrades")
    assert asked.latest_recorded and [s.entity_id for s in asked.subjects]
    sent = [t.meta.row_key for r in recorder.on("RecordTrades") for t in r.trades]
    product, _, _ = venue.RESTARTED
    assert sent == [f"{product}:t:{ident}" for ident in venue.BACKFILLED]


def test_a_restarts_gap_is_read_once_per_subject() -> None:
    recorder = Recorder(venue.VENUE)
    asyncio.run(_drive(producers()["a-restarts-gap-backfilled"], recorder))
    # The same process hearing the standing want again, as the lake delivers
    # it each minute: the gap is not read back a second time.
    dgm, store = _dgm(recorder)
    product, ident, at_ns = venue.RESTARTED

    async def twice() -> None:
        subject = await held(store, dgm, venue.BASE)
        for want_id in ("WNT-S", "WNT-S"):
            want = ops.ObservationsWantedEvent(
                want_id=want_id, dataset=dgm.dataset("trades"), data_type="meridian.v1.Trade",
                subjects=[ops.SubjectRef(entity_id=subject)], standing=True,
            )  # fmt: skip
            recorder.answer("ObservationsWanted", _delivering(want))
            await recorder.receive(observations_wanted=dgm.on_want)

    asyncio.run(twice())
    assert len(recorder.on("ListTrades")) == 2, "once in the producer's process, once in this"
    assert dgm.backfilled


def test_the_licensed_datasets_are_those_a_deployment_admin_licensed() -> None:
    recorder = Recorder(venue.VENUE)
    dgm, _ = _dgm(recorder)
    recorder.answer("ListDatasets", lambda _: ops.ListDatasetsResult(licences=[
        ops.DatasetLicence(dataset=dgm.dataset("daily"), kept=True),
        ops.DatasetLicence(dataset="another-1:daily", kept=True),
    ]))  # fmt: skip
    assert asyncio.run(dgm.read_licences()) == frozenset({dgm.dataset("daily")})
