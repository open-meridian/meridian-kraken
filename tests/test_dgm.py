"""The dgm's behaviour past the suite's cases: restatement, wants, the live
dataset, the venue, the product map, and each subject named from its record.

The same file in meridian-coinbase and meridian-kraken (`make suite-same`),
run against each plugin's synthetic exchange (tests/venue_cases.py) and a
recorder answering batches as the lake does (conftest.Lake).
"""

from __future__ import annotations

from datetime import timedelta
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
    assert [(i.scheme, i.value, i.source) for i in asked.identifiers] == [
        ("symbol", venue.VENUE, venue.VENUE)
    ]
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
    # On a runtime from before the ruling of 2026-10-10, which grants a dgm
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
