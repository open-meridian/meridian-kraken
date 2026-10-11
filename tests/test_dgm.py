"""The dgm's behaviour past the suite's cases: restatement, wants, the live
dataset, the venue, the product map, and each subject named from its record.

The same file in meridian-coinbase and meridian-kraken (`make suite-same`),
run against each plugin's synthetic exchange (tests/venue_cases.py) and a
recorder answering batches as the lake does (conftest.Lake).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import meridian
import pytest
from meridian.operations import as_decimal, as_money
from meridian.plugin.v1 import operations_pb2 as ops

import venue_cases as venue
from conftest import BITCOIN, OTHER, Lake, dgm_on, hear, wanted


async def test_the_forming_day_read_again_is_restated_and_a_repeat_unchanged(lake: Lake) -> None:
    first = dgm_on(lake, forming=0)
    btc = await lake.held(first, venue.BASE)
    await first.record_days([btc], venue.TODAY, venue.TODAY, "meridian.v1.Price")
    again = dgm_on(lake, forming=1)
    await again.record_days([btc], venue.TODAY, venue.TODAY, "meridian.v1.Price")
    await again.record_days([btc], venue.TODAY, venue.TODAY, "meridian.v1.Price")
    key = ("price", f"{venue.FORMING_PRODUCT}:1d:{venue.FORMING[0][0]}")
    assert lake.versions[key][0] == 2
    assert again.counts.restated == 1
    assert again.counts.unchanged >= 1
    forming = [r for r in lake.rows() if r.meta.row_key == key[1]]
    assert all(r.meta.valid_until_ns > venue.NOW_NS for r in forming)
    assert all(not r.meta.source_times for r in forming), (
        "a day still forming has no published time"
    )


async def test_a_completed_day_is_a_plain_date_with_its_published_time(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    await dgm.record_days([btc], venue.CLOSED, venue.CLOSED, "meridian.v1.Price")
    product, text = venue.CLOSED_CLOSE
    [row] = [r for r in lake.rows() if r.meta.row_key.startswith(f"{product}:1d:")]
    assert row.meta.business_date == venue.CLOSED.isoformat()
    assert [t.kind for t in row.meta.source_times] == [ops.SOURCE_TIME_KIND_PUBLISHED]
    assert row.meta.source_times[0].at_ns == row.meta.valid_until_ns
    assert str(as_decimal(row.price.amount)) == text
    assert row.meta.source.dataset == f"{venue.VENUE}:daily"


async def test_a_price_past_a_float_is_recorded_exactly(lake: Lake) -> None:
    dgm = dgm_on(lake)
    subject = await lake.held(dgm, venue.EXACT_BASE)
    await dgm.record_days([subject], venue.CLOSED, venue.CLOSED, "meridian.v1.Price")
    amounts = {str(as_decimal(r.price.amount)) for r in lake.rows()}
    assert venue.EXACT_CLOSE in amounts


async def test_a_stablecoin_quote_names_the_tokens_instrument_never_a_currency(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.STABLE_BASE)
    await dgm.record_days([btc], venue.CLOSED, venue.CLOSED, "meridian.v1.Price")
    stable = dgm.products.ids[venue.STABLE]
    [row] = [r for r in lake.rows() if r.meta.row_key.startswith(f"{venue.STABLE_PRODUCT}:")]
    money = as_money(row.price)
    assert (money.instrument_id, money.currency_code) == (stable, "")
    others = [as_money(r.price) for r in lake.rows() if r is not row]
    assert all(m.currency_code and not m.instrument_id for m in others), "fiat by its code"


async def test_wants_are_recorded_against_or_declined_with_why(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    future = venue.TODAY + timedelta(days=3)
    for want_id, day, subjects in (
        ("WNT-1", venue.CLOSED, [btc, "LCL-NOT-COVERED"]),
        ("WNT-2", future, [btc]),
    ):
        await hear(lake, dgm, wanted(dgm, want_id, "daily", subjects, day=day))
    recorded = [p for p in lake.on("RecordPrices") if p.want_id == "WNT-1"]
    assert recorded and all(r.meta.subjects[0].entity_id == btc for r in recorded[0].prices)
    declined = {
        (d.want_id, s.entity_id, d.reason) for d in lake.on("DeclineWant") for s in d.subjects
    }
    assert declined == {
        ("WNT-1", "LCL-NOT-COVERED", ops.UNANSWERED_REASON_NOT_COVERED),
        ("WNT-2", btc, ops.UNANSWERED_REASON_SOURCE_SILENT),
    }
    assert [h.want_id for h in dgm.heard] == ["WNT-2", "WNT-1"]


async def test_the_daily_datasets_latest_is_the_past_weeks_days(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    await hear(lake, dgm, wanted(dgm, "WNT-7", "daily", [btc]))
    days = {r.meta.business_date for r in lake.rows()}
    assert venue.CLOSED.isoformat() in days and not lake.on("DeclineWant"), days
    assert all(r.meta.business_date >= (venue.TODAY - timedelta(days=6)).isoformat()
               for r in lake.rows())  # fmt: skip


@pytest.mark.skipif(not venue.market().session.limits.history_days, reason="no reach stated")
async def test_a_day_before_the_venues_reach_is_declined_beyond_history(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    reach = dgm.market.session.limits.history_days
    old = venue.TODAY - timedelta(days=reach + 5)
    declined = await dgm.record_days([btc], old, old)
    assert declined == {btc: "beyond_history"}


async def test_the_live_dataset_records_last_bid_and_ask_once_per_change(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    await dgm.record_live([btc])
    await dgm.record_live([btc])
    rows = lake.rows()
    kinds = {(r.meta.row_key.split(":")[0], r.kind) for r in rows}
    for product in venue.LIVE_LAST:
        assert {
            (product, k) for k in (ops.PRICE_KIND_LAST, ops.PRICE_KIND_BID, ops.PRICE_KIND_ASK)
        } <= kinds
    last = {r.meta.row_key.split(":")[0]: str(as_decimal(r.price.amount)) for r in rows
            if r.kind == ops.PRICE_KIND_LAST}  # fmt: skip
    assert last == venue.LIVE_LAST
    assert all(not r.meta.business_date and r.meta.valid_until_ns == 0 for r in rows)
    assert all(r.meta.source.dataset == f"{venue.VENUE}:live" for r in rows)
    assert dgm.counts.unchanged == dgm.counts.recorded, "the same answer again changes nothing"


async def test_a_standing_live_want_is_kept_current_until_withdrawn(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    event = wanted(dgm, "WNT-9", "live", [btc], kinds=(ops.PRICE_KIND_LAST,), standing=True)
    await hear(lake, dgm, event)
    assert set(dgm.standing) == {"WNT-9"}
    sent = len(lake.on("RecordPrices"))
    await dgm.current()
    assert len(lake.on("RecordPrices")) == sent + 1
    assert {r.kind for r in lake.rows()} == {ops.PRICE_KIND_LAST}
    lake.answer("WantWithdrawn", lambda _: ops.WantWithdrawnEvent(want_id="WNT-9"))
    await lake.receive(want_withdrawn=dgm.on_withdrawn)
    await dgm.current()
    assert not dgm.standing and len(lake.on("RecordPrices")) == sent + 1


async def test_what_a_dataset_does_not_serve_is_not_covered(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    for dataset, data_type, kinds in (
        ("live", "meridian.v1.Bar", []),
        ("daily", "meridian.v1.Price", [ops.PRICE_KIND_NAV]),
        ("elsewhere", "meridian.v1.Price", []),
    ):
        event = wanted(dgm, f"W-{dataset}", dataset, [btc], data_type=data_type,
                       kinds=tuple(kinds), day=venue.CLOSED)  # fmt: skip
        await hear(lake, dgm, event)
    assert not lake.on("RecordPrices") and not lake.on("RecordBars")
    assert {d.reason for d in lake.on("DeclineWant")} == {ops.UNANSWERED_REASON_NOT_COVERED}


async def test_a_venue_not_held_is_reported_once_and_named_as_reported_on_each_row() -> None:
    lake = Lake()
    lake.answer("ResolveVenue", lambda _: ops.ResolveVenueResult(
        found=False, miss_reason=ops.MISS_REASON_NOT_FOUND))  # fmt: skip
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    await dgm.record_days([btc], venue.CLOSED, venue.CLOSED)
    await dgm.record_live([btc])
    assert len(lake.on("ReportMissingVenue")) == 1
    for row in lake.rows() + lake.rows("RecordBars"):
        assert row.meta.source.venue_id == ""
        assert [(u.scheme, u.code) for u in row.meta.unconverted] == [
            (f"{venue.VENUE}:venue", venue.VENUE)
        ]


async def test_a_venue_held_is_named_by_its_id_on_every_row(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    await dgm.record_days([btc], venue.CLOSED, venue.CLOSED)
    [asked] = lake.on("ResolveVenue")
    assert (
        [(i.scheme, i.value, i.source) for i in asked.identifiers]
        == [("symbol", venue.VENUE, venue.VENUE)]
        == list(dgm.market.venue_identifiers)
    ), "the venue's own code, as its convert.py declares"
    assert {r.meta.source.venue_id for r in lake.rows()} == {dgm.venue.venue_id}
    assert dgm.venue.venue_id.startswith("VEN-")


async def test_the_product_map_is_kept_and_read_back_without_resolving_again(
    tmp_path: Path,
) -> None:
    lake = Lake()
    map_ = venue.ProductMap(tmp_path / "products-synthetic.json")
    dgm = venue.Dgm(lake, venue.market(), map_, clock_ns=lambda: venue.NOW_NS)
    btc = await lake.held(dgm, venue.BASE)
    await dgm.record_days([btc], venue.CLOSED, venue.CLOSED)
    assert lake.on("ResolveInstrument") and lake.on("ResolveIdentifier")
    assert map_.path is not None and map_.path.is_file()
    again = venue.ProductMap(map_.path)
    assert again.load() and again.ids == map_.ids and again.subjects == {btc: venue.BASE}
    second = Lake()
    restarted = venue.Dgm(second, venue.market(), again, clock_ns=lambda: venue.NOW_NS)
    await restarted.map_products()
    await restarted.record_days([btc], venue.CLOSED, venue.CLOSED)
    assert not second.on("ResolveIdentifier"), "an asset resolved once is not asked again"
    assert not second.on("ResolveInstrument"), "a subject named once is not read again"
    assert venue.DELISTED not in {m.product for m in again.mapped()}


async def test_reading_the_listing_mints_no_record_for_each_asset(lake: Lake) -> None:
    # The finding of the first build: every listed asset resolved, a record
    # minted for each the deployment lacked. Now nothing is resolved until a
    # subject asked for is named, and then only its products' quotes.
    dgm = dgm_on(lake)
    await dgm.map_products()
    assert dgm.products.listed and not lake.on("ResolveIdentifier")
    assert not dgm.products.mapped()
    btc = await lake.held(dgm, venue.BASE)
    await dgm.record_days([btc], venue.CLOSED, venue.CLOSED)
    quotes = {q.code for q in dgm.products.quotes(venue.BASE)}
    assert set(dgm.products.ids) == quotes, "only the quotes of the subject's products"
    assert len(lake.on("ResolveIdentifier")) == len(quotes)
    assert {m.base_id for m in dgm.products.mapped()} == {btc}


async def test_a_custodians_record_is_named_by_its_global_identifier_and_joined(
    lake: Lake,
) -> None:
    # Bitcoin as a custodian's book holds it -- its CAIP-19 and the
    # custodian's symbol -- never resolved by this venue: the record read
    # (W3.6), the venue's asset found by the CAIP-19, the venue's code
    # joined to the record (W3.1), and its close recorded for the subject.
    held = lake.instruments.hold(
        "INS-CUSTODIAN",
        meridian.Identifier(scheme="caip19", value=BITCOIN),
        meridian.Identifier(scheme="symbol", value="BTC", source=OTHER),
    )
    dgm = dgm_on(lake)
    await hear(lake, dgm, wanted(dgm, "WNT-1", "daily", [held], day=venue.CLOSED))
    (read,) = lake.on("ResolveInstrument")
    assert read.instrument_id == held and read.as_of_ns == venue.NOW_NS
    joined = lake.on("ResolveIdentifier")[0]
    assert ("symbol", venue.BASE, venue.VENUE) in {(i.scheme, i.value, i.source)
                                                   for i in joined.identifiers}  # fmt: skip
    assert not lake.on("ReportMissingInstrument") and not lake.on("DeclineWant")
    assert dgm.products.subjects == {held: venue.BASE}
    sent = [p for p in lake.on("RecordPrices") if p.want_id == "WNT-1"]
    assert sent and {r.meta.subjects[0].entity_id for p in sent for r in p.prices} == {held}
    product, text = venue.CLOSED_CLOSE
    assert text in {str(as_decimal(r.price.amount)) for p in sent for r in p.prices
                    if r.meta.row_key.startswith(f"{product}:1d:")}  # fmt: skip


async def test_another_sources_symbol_that_is_the_venues_code_names_the_asset(
    lake: Lake,
) -> None:
    # A token with no global identifier, known to the deployment only by a
    # custodian's symbol, which is the venue's code: named, and the venue's
    # symbol reported beside the custodian's.
    held = lake.instruments.hold(
        "INS-CUSTODIAN", meridian.Identifier(scheme="symbol", value=venue.EXACT_BASE, source=OTHER)
    )
    dgm = dgm_on(lake)
    declined = await dgm.record_days([held], venue.CLOSED, venue.CLOSED, "meridian.v1.Price")
    assert not declined and dgm.products.subjects == {held: venue.EXACT_BASE}
    joined = [(i.scheme, i.value, i.source) for i in lake.on("ResolveIdentifier")[0].identifiers]
    assert ("symbol", venue.EXACT_BASE, venue.VENUE) in joined
    assert joined[-1] == ("symbol", venue.EXACT_BASE, OTHER), "beside the one it matched"
    assert venue.EXACT_CLOSE in {str(as_decimal(r.price.amount)) for r in lake.rows()}


async def test_a_record_naming_none_of_the_venues_assets_is_not_covered(lake: Lake) -> None:
    held = lake.instruments.hold(
        "INS-EQUITY",
        meridian.Identifier(scheme="cusip", value="74347X831"),
        meridian.Identifier(scheme="symbol", value="TQQQ", source=OTHER),
    )
    dgm = dgm_on(lake)
    for _ in range(2):
        assert await dgm.record_days([held], venue.CLOSED, venue.CLOSED) == {held: "not_covered"}
    assert len(lake.on("ResolveInstrument")) == 1, "read once until the listing is read again"
    assert not lake.on("ResolveIdentifier") and not lake.on("ReportMissingInstrument")
    await dgm.map_products()
    await dgm.record_days([held], venue.CLOSED, venue.CLOSED)
    assert len(lake.on("ResolveInstrument")) == 2


async def test_a_securitys_ticker_that_is_a_coins_code_names_no_asset(lake: Lake) -> None:
    # A share whose ticker is the venue's code for a token (ETH is a
    # share's): by its CUSIP, or by its class, it is no coin, and nothing is
    # joined to its record.
    ticker = meridian.Identifier(scheme="symbol", value=venue.EXACT_BASE, source=OTHER)
    by_cusip = lake.instruments.hold(
        "INS-SHARE", meridian.Identifier(scheme="cusip", value="297602104"), ticker
    )
    dgm = dgm_on(lake)
    by_class = "INS-SHARE-CLASSED"

    def read(params: Any) -> Any:
        if params.instrument_id != by_class:
            return lake.instruments.read(params)
        record = ops.InstrumentRecord(instrument_id=by_class, identifiers=[ticker],
                                      asset_class=ops.ASSET_CLASS_EQUITY)  # fmt: skip
        return ops.ResolveInstrumentResult(found=True, instrument=record)

    lake.answer("ResolveInstrument", read)
    for subject in (by_cusip, by_class):
        declined = await dgm.record_days([subject], venue.CLOSED, venue.CLOSED)
        assert declined == {subject: "not_covered"}
    assert not lake.on("ResolveIdentifier") and not dgm.products.subjects


async def test_with_no_read_of_an_instrument_nothing_is_named(lake: Lake) -> None:
    # On a runtime from before the ruling of 2026-10-10, which grants a data plugin
    # no read of an instrument's record.
    def refused(_: Any) -> Any:
        raise meridian.NotGranted(
            "platform.reference.query.resolve-instrument", "this plugin holds dgm"
        )

    lake.answer("ResolveInstrument", refused)
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    assert await dgm.record_days([btc], venue.CLOSED, venue.CLOSED) == {btc: "not_covered"}
    await dgm.record_days([btc], venue.CLOSED, venue.CLOSED)
    assert len(lake.on("ResolveInstrument")) == 1 and not dgm.reads_instruments


async def test_an_asset_that_does_not_resolve_is_reported_once_and_left_out() -> None:
    lake = Lake()
    dgm = dgm_on(lake)
    # The quote's identifiers meet two of the deployment's records.
    await dgm.ensure_mapped()
    usd = next(q for q in dgm.products.quotes(venue.BASE) if q.currency_code)
    for n in (1, 2):
        lake.instruments.hold(f"INS-{usd.code}-{n}", *dgm.identifiers(usd))
    btc = await lake.held(dgm, venue.BASE)
    await dgm.record_days([btc], venue.CLOSED, venue.CLOSED)
    await dgm.map_products()
    await dgm.record_days([btc], venue.CLOSED, venue.CLOSED)
    assert len(lake.on("ReportMissingInstrument")) == 1
    assert dgm.products.missing == {usd.code: "MISS_REASON_AMBIGUOUS"}
    assert all(usd.code != m.quote.code for m in dgm.products.mapped())
    assert lake.rows(), "its products quoted in other assets still recorded"


async def test_a_join_meeting_another_record_is_reported_and_the_subject_served(
    lake: Lake,
) -> None:
    dgm = dgm_on(lake)
    await lake.held(dgm, venue.BASE)
    held = lake.instruments.hold(
        "INS-CUSTODIAN", meridian.Identifier(scheme="symbol", value="BTC", source=OTHER),
        meridian.Identifier(scheme="caip19", value=BITCOIN),
    )  # fmt: skip
    declined = await dgm.record_days([held], venue.CLOSED, venue.CLOSED, "meridian.v1.Price")
    (miss,) = lake.on("ReportMissingInstrument")
    assert miss.reason == ops.MISS_REASON_AMBIGUOUS
    assert not declined and dgm.products.subjects == {held: venue.BASE}
    assert {r.meta.subjects[0].entity_id for r in lake.rows()} == {held}


async def test_every_row_names_a_raw_record_its_storage_holds(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    await dgm.record_days([btc], venue.CLOSED - timedelta(days=1), venue.CLOSED)
    await dgm.record_live([btc])
    store = dgm.market.client.store
    for row in lake.rows() + lake.rows("RecordBars"):
        assert row.meta.raw.key and store.get(row.meta.raw.key) is not None, row.meta.raw.key


def test_no_float_reaches_a_row() -> None:
    import ast

    package = Path(venue.raw.__file__).parent
    for module in ("dgm.py", "convert.py", "market.py", "synthetic.py"):
        tree = ast.parse((package / module).read_text())
        floats = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
                  and getattr(n.func, "id", "") == "float"]  # fmt: skip
        assert not floats, f"{module} calls float() at {floats}"


async def test_a_product_the_venue_refuses_leaves_the_subjects_others_recorded(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    asked = dgm.market.candles

    def refusing(product: str, *days: Any) -> Any:
        if product == venue.STABLE_PRODUCT:
            raise OSError("refused")
        return asked(product, *days)

    dgm.market.candles = refusing
    declined = await dgm.record_days([btc], venue.CLOSED, venue.CLOSED, "meridian.v1.Price")
    assert not declined
    assert {r.meta.row_key.split(":")[0] for r in lake.rows()} == {venue.CLOSED_CLOSE[0]}


# ── Trades and quotes (contract v19) ─────────────────────────────────────────


async def _streaming(lake: Lake, key: str = "trades", data_type: str = "meridian.v1.Trade") -> Any:
    """The dgm with a standing want of `key` for BASE, its stream not run yet."""
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    await hear(lake, dgm, wanted(dgm, "WNT-S", key, [btc], data_type=data_type, standing=True))
    return dgm, btc


async def _drive(dgm: Any) -> None:
    """Each of the synthetic venue's connections in turn, as the stream opens
    them, each dropping once it has said everything; then what is held sent."""
    for session in dgm.market.client.transport.stream_sessions():

        async def replayed(_: str, said: list[str] = session) -> Any:
            return venue.stream.Replayed(said, hold=False)

        dgm.stream.connect = replayed
        await dgm.stream.run(once=True)
    await dgm.flush()


async def test_a_standing_want_puts_its_subjects_products_on_the_stream_until_withdrawn(
    lake: Lake,
) -> None:
    dgm, _ = await _streaming(lake)
    assert sorted(dgm.streams) == venue.STREAM_NAMES
    assert dgm.stream._wanted == tuple(sorted(venue.STREAM_NAMES))
    lake.answer("WantWithdrawn", lambda _: [ops.WantWithdrawnEvent(want_id="WNT-S")])
    await lake.receive(want_withdrawn=dgm.on_withdrawn)
    assert dgm.streams == {} and dgm.stream._wanted == ()


async def test_the_stream_records_every_trade_and_reads_back_the_gap_a_drop_leaves(
    lake: Lake,
) -> None:
    dgm, btc = await _streaming(lake)
    await _drive(dgm)
    trades = lake.rows("RecordTrades")
    by_product: dict[str, list[str]] = {}
    for row in trades:
        product, kind, ident = row.meta.row_key.split(":")
        assert kind == "t"
        by_product.setdefault(product, []).append(ident)
    assert {p: sorted(ids, key=int) for p, ids in by_product.items()} == venue.STREAMED
    assert all(len(ids) == len(set(ids)) for ids in by_product.values()), "each trade once"
    assert dgm.stream.health.opened == 2 and dgm.stream.health.dropped == 2
    for row in trades:
        ident = row.meta.row_key.rsplit(":", 1)[1]
        assert ops.Aggressor.Name(row.aggressor) == venue.AGGRESSORS[ident], ident
        assert row.meta.source.dataset == f"{venue.VENUE}:trades"
        assert [s.entity_id for s in row.meta.subjects] == [btc]
        assert not row.meta.business_date and row.meta.valid_from_ns
        assert [t.kind for t in row.meta.source_times] == [ops.SOURCE_TIME_KIND_EVENT]
        assert row.source_sequence == int(ident)
        yes = ops.ELIGIBLE_ELIGIBLE
        every = ops.Eligibility(high_low=yes, open=yes, close=yes, volume=yes)
        assert row.attributes.consolidated == every and row.attributes.market_centre == every
        assert not row.attributes.characteristics
        gap = ident in venue.GAP
        assert ("/trades/" in row.meta.raw.key.lower()) == gap, (ident, row.meta.raw.key)
        assert ("/stream/" in row.meta.raw.key) != gap, (ident, row.meta.raw.key)
    (product, ident), (price, quantity) = venue.TRADE_PRICE, venue.TRADE_QUANTITY
    [stated] = [r for r in trades if r.meta.row_key == f"{product}:t:{ident}"]
    assert (str(as_decimal(stated.price.amount)), str(as_decimal(stated.quantity))) == (
        price,
        quantity,
    )


async def test_the_streams_quotes_are_each_products_latest_with_their_sizes(lake: Lake) -> None:
    dgm, _ = await _streaming(lake, "live", "meridian.v1.Quote")
    await _drive(dgm)
    quotes = lake.rows("RecordQuotes")
    latest: dict[str, Any] = {}
    for row in quotes:
        product, kind, at = row.meta.row_key.split(":")
        assert kind == "q" and int(at) == row.meta.valid_from_ns
        assert row.meta.source.dataset == f"{venue.VENUE}:live" and not row.meta.business_date
        latest[product] = row
    for params in lake.on("RecordQuotes"):
        products = [q.meta.row_key.split(":")[0] for q in params.quotes]
        assert len(products) == len(set(products)), "at most one a product each time sent"
    for product, (bid, bid_size, ask, ask_size) in venue.LAST_QUOTES.items():
        row = latest[product]
        said = (as_decimal(row.bid.amount), as_decimal(row.bid_quantity),
                as_decimal(row.ask.amount), as_decimal(row.ask_quantity))  # fmt: skip
        assert said == tuple(Decimal(v) for v in (bid, bid_size, ask, ask_size)), product


async def test_a_quote_now_is_read_from_the_book_and_an_empty_side_left_unset(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    one_sided = await lake.held(dgm, venue.ONE_SIDED_BASE)
    await hear(lake, dgm, wanted(dgm, "WNT-Q", "live", [btc, one_sided],
                                 data_type="meridian.v1.Quote"))  # fmt: skip
    quotes = lake.rows("RecordQuotes")
    assert {r.meta.subjects[0].entity_id for r in quotes} == {btc, one_sided}
    for row in quotes:
        if row.meta.subjects[0].entity_id == one_sided:
            assert row.HasField("bid") and row.HasField("bid_quantity")
            assert not row.HasField("ask") and not row.HasField("ask_quantity")
        else:
            assert row.HasField("ask") and row.HasField("ask_quantity")
    assert all(params.want_id == "WNT-Q" for params in lake.on("RecordQuotes"))
    assert dgm.stream._wanted == (), "a quote wanted now puts nothing on the stream"


async def test_a_range_of_trades_is_read_from_the_history_and_one_before_it_declined(
    lake: Lake,
) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    start = venue.NOW_NS - 60 * 1_000_000_000
    event = wanted(dgm, "WNT-R", "trades", [btc], data_type="meridian.v1.Trade")
    event.valid_from_ns, event.valid_until_ns = start, venue.NOW_NS
    await hear(lake, dgm, event)
    trades = lake.rows("RecordTrades")
    assert trades and all(start <= r.meta.valid_from_ns < venue.NOW_NS for r in trades)
    assert all(params.want_id == "WNT-R" for params in lake.on("RecordTrades"))
    before = wanted(dgm, "WNT-B", "trades", [btc], data_type="meridian.v1.Trade")
    before.valid_from_ns = venue.NOW_NS - 10 * 86_400 * 1_000_000_000
    before.valid_until_ns = before.valid_from_ns + 3_600 * 1_000_000_000
    await hear(lake, dgm, before)
    [declined] = [d for d in lake.on("DeclineWant") if d.want_id == "WNT-B"]
    assert declined.reason == ops.UNANSWERED_REASON_BEYOND_HISTORY


async def test_a_trade_heard_again_is_not_sent_again(lake: Lake) -> None:
    dgm, _ = await _streaming(lake)
    await _drive(dgm)
    sent = len(lake.rows("RecordTrades"))
    await _drive(dgm)
    assert len(lake.rows("RecordTrades")) == sent


async def test_a_trade_or_quote_the_dataset_does_not_hold_is_not_covered(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    for want_id, key, data_type in (("WNT-1", "daily", "meridian.v1.Trade"),
                                    ("WNT-2", "trades", "meridian.v1.Quote"),
                                    ("WNT-3", "daily", "meridian.v1.Quote")):  # fmt: skip
        await hear(lake, dgm, wanted(dgm, want_id, key, [btc], data_type=data_type))
    declined = {d.want_id: d.reason for d in lake.on("DeclineWant")}
    assert declined == dict.fromkeys(("WNT-1", "WNT-2", "WNT-3"),
                                     ops.UNANSWERED_REASON_NOT_COVERED)  # fmt: skip


async def test_a_batch_the_lake_refuses_is_said_and_the_stream_goes_on(lake: Lake) -> None:
    dgm, _ = await _streaming(lake)

    def refused(_: Any) -> Any:
        raise meridian.MeridianError("refused: trades[0].quantity")

    lake.answer("RecordTrades", refused)
    await _drive(dgm)
    assert dgm.stream.health.opened == 2, "both connections opened, the refusal said"
    assert dgm.pending_trades == [], "a refused batch is not sent again and again"


async def test_a_restarts_gap_refused_is_said_and_the_stream_goes_on(lake: Lake) -> None:
    # A runtime that does not answer ListTrades for its own rows (before
    # contract v20): the gap is not read back, and the subject still streams.
    def refused(_: Any) -> Any:
        raise meridian.NotGranted("platform.lake.query.trades", "not granted")

    lake.answer("ListTrades", refused)
    dgm, _ = await _streaming(lake)
    assert dgm.backfilled and dgm.streams, "the subject went on the stream all the same"
    assert not lake.rows("RecordTrades")


async def test_licences_not_answered_leave_each_not_licensed_yet(lake: Lake) -> None:
    def refused(_: Any) -> Any:
        raise meridian.NotGranted("platform.lake.query.datasets", "not granted")

    lake.answer("ListDatasets", refused)
    dgm = dgm_on(lake)
    assert await dgm.read_licences() == frozenset() and dgm.reads_licences is False
    assert await dgm.read_licences() == frozenset(), "asked once, then said"
    assert len(lake.on("ListDatasets")) <= 1


async def test_each_dataset_counts_what_it_recorded(lake: Lake) -> None:
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    await hear(lake, dgm, wanted(dgm, "WNT-1", "daily", [btc], day=venue.CLOSED))
    daily = dgm.sent_to("daily")
    assert daily.recorded > 0 and daily.last_ns == venue.NOW_NS
    assert dgm.sent_to("live").recorded == 0 and dgm.sent_to("trades").last_ns == 0
