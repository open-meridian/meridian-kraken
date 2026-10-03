"""The `dgm` conformance suite for the crypto pair: one file, unchanged.

This file is identical, byte for byte, in meridian-coinbase and
meridian-kraken (`make conformance-same` diffs it against the sibling). It
states the lake's rows in contract terms alone -- the token, the quote
currency, the kind, the business date, the valid times, the exact decimals
-- and asks each plugin, through its `conformance` fixture (tests/conftest.py),
to convert its own venue's synthetic exchange of the same market facts:
Coinbase's candles as JSON numbers, low before high, its maker's side;
Kraken's strings, its XXBT and ZUSD, its taker's side, its trade times as
decimal seconds. Both must produce these rows, so the suite holds unchanged
between them, the plan's "one suite unchanged between the two".

When v14 and the SDK's `dgm` suite exist, these cases become that suite's
1a and 1b cases (plans/the-lake-prices-the-book, "The `dgm` suite").
"""

from __future__ import annotations

import dataclasses
import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any

#: The digest of rows.py, the seam, which is the same module in both plugins.
ROWS_SHA256 = "623eb48ee58cabb3ec532e196d7d2166dcb91a214f3100c8dd1c3d8a34ab0104"

BITCOIN = ("caip19", "bip122:000000000019d6689c085ae165831e93/slip44:0", "")
ETHER = ("caip19", "eip155:1/slip44:60", "")
USD = ("iso4217", "USD", "")

S = 1_000_000_000
DAY = 86_400 * S
OCT_1 = 1_790_812_800 * S
OCT_2, OCT_3 = OCT_1 + DAY, OCT_1 + 2 * DAY
#: The synthetic exchanges' now: noon on 2026-10-03, that day still forming.
NOW = OCT_3 + 12 * 3600 * S

#: The market facts both synthetic exchanges state, each in its venue's dialect.
#: (business date, valid from, open, high, low, close, volume, vwap, trade count)
DAYS = [
    (
        "2026-10-01",
        OCT_1,
        "61000.5",
        "62500.25",
        "60500.75",
        "62000.12345678",
        "1234.56789012",
        "61800.1",
        45678,
    ),
    (
        "2026-10-02",
        OCT_2,
        "62000.12345678",
        "63000",
        "61500",
        "62750.5",
        "987.65432109",
        "62300.2",
        34567,
    ),
    ("2026-10-03", OCT_3, "62750.5", "62900", "62600", "62800", "100.5", "62780.3", 1234),
]
LATER = ("2026-10-03", OCT_3, "62750.5", "62950", "62600", "62850.25", "150.75", "62800.4", 2345)
#: (source sequence, price, quantity, aggressor, valid from)
TRADES = [
    (1001, "62800.01", "0.015", "buy", NOW - 2 * S + 123_400_000),  # 11:59:58.1234Z
    (1002, "62799.5", "0.25", "sell", NOW - S // 2),  # 11:59:59.5Z
]


def ident(identifier: Any) -> tuple[str, str, str]:
    return (identifier.scheme, identifier.value, identifier.source)


def subject(row: Any) -> tuple[str, str, str]:
    [only] = row.meta.subjects
    return ident(only.resolve_by)


def money(value: Any) -> tuple[Decimal, tuple[str, str, str]] | None:
    return None if value is None else (value.amount, ident(value.currency))


def usd(text: str) -> tuple[Decimal, tuple[str, str, str]]:
    return (Decimal(text), USD)


def walk(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for f in dataclasses.fields(value):
            yield from walk(getattr(value, f.name))
    elif isinstance(value, tuple | list):
        for item in value:
            yield from walk(item)
    else:
        yield value


# --- the seam -----------------------------------------------------------------


def test_the_seam_is_one_module_in_both_plugins(conformance: Any) -> None:
    source = Path(conformance.rows.__file__).read_bytes()
    assert hashlib.sha256(source).hexdigest() == ROWS_SHA256


# --- identifiers ----------------------------------------------------------------


def test_btc_usd_is_the_bitcoin_token_priced_in_the_dollars_cash_instrument(
    conformance: Any,
) -> None:
    product = conformance.product("BTC/USD")
    assert product.base.kind == "token" and ident(product.base.identifier) == BITCOIN
    assert product.quote.kind == "currency" and ident(product.quote.identifier) == USD
    assert ident(product.subject().resolve_by) == BITCOIN
    # The venue's own codes stay source-scoped, never a key.
    for code in (product.product, product.base.venue_code, product.quote.venue_code):
        assert code.scheme == "symbol" and code.source == conformance.plugin


def test_ether_is_its_native_coin_by_slip44(conformance: Any) -> None:
    assert ident(conformance.product("ETH/USD").base.identifier) == ETHER


def test_a_stablecoin_quote_is_the_token_never_the_dollar(conformance: Any) -> None:
    product = conformance.product("BTC/USDC")
    assert product.quote.kind == "token"
    assert product.quote.resolve_by().scheme != "iso4217"
    assert ident(product.money(Decimal("1")).currency) != USD


# --- the daily close, on the declared day ------------------------------------------


def test_the_daily_dataset_declares_its_day_as_the_utc_day(conformance: Any) -> None:
    daily, live = conformance.datasets()
    assert (daily.key, daily.data_types) == ("daily", ("price", "bar"))
    assert (daily.day.calendar, daily.day.ends_at) == ("UTC", "00:00")
    assert (live.key, set(live.data_types)) == ("live", {"price", "quote", "trade"})
    assert live.day is None


def test_a_daily_close_is_the_declared_days_last_price(conformance: Any) -> None:
    closes, _ = conformance.daily("daily")
    got = [
        (
            p.kind,
            p.basis,
            subject(p),
            money(p.price),
            p.meta.business_date,
            p.meta.valid_from_ns,
            p.meta.valid_until_ns,
            [(t.kind, t.at_ns) for t in p.meta.source_times],
            p.meta.source.dataset,
            p.meta.source.venue,
            p.meta.unconverted,
        )
        for p in closes
    ]
    assert got == [
        (
            "close",
            "per_unit",
            BITCOIN,
            usd(close),
            date,
            start,
            start + DAY,
            # A complete day was published at its end; the day still forming not yet.
            [("published", start + DAY)] if start + DAY <= NOW else [],
            "daily",
            "",
            (),
        )
        for date, start, _o, _h, _l, close, _v, _w, _n in DAYS
    ]


def test_daily_bars_carry_what_the_venue_gave_and_nothing_it_did_not(conformance: Any) -> None:
    _, bars = conformance.daily("daily")
    assert len(bars) == len(DAYS)
    for bar, (date, start, o, h, low, c, v, vwap, count) in zip(bars, DAYS, strict=True):
        assert (bar.meta.business_date, bar.meta.valid_from_ns, bar.meta.valid_until_ns) == (
            date,
            start,
            start + DAY,
        )
        assert subject(bar) == BITCOIN
        assert [money(x) for x in (bar.open, bar.high, bar.low, bar.close)] == [
            usd(o),
            usd(h),
            usd(low),
            usd(c),
        ]
        assert bar.volume == Decimal(v)
        # Unset where the venue gives none, never zero.
        assert bar.vwap is None or money(bar.vwap) == usd(vwap)
        assert bar.trade_count is None or bar.trade_count == count


def test_the_forming_day_is_restated_under_its_row_key(conformance: Any) -> None:
    first, _ = conformance.daily("daily")
    later, later_bars = conformance.daily("daily-later")
    assert [p.meta.row_key for p in first] == [p.meta.row_key for p in later]
    assert len({p.meta.row_key for p in first}) == 3
    # The complete days are an identical repeat: nothing changes but the raw record.
    for a, b in zip(first[:2], later[:2], strict=True):
        assert dataclasses.replace(a, meta=dataclasses.replace(a.meta, raw=b.meta.raw)) == b
    # The day still forming: the same key, its new value.
    date, start, o, h, low, c, v, vwap, count = LATER
    assert money(later[2].price) == usd(c) and money(first[2].price) == usd("62800")
    assert later[2].meta.source_times == ()
    assert [money(x) for x in (later_bars[2].high, later_bars[2].close)] == [usd(h), usd(c)]
    assert later_bars[2].volume == Decimal(v)


def test_a_price_past_what_a_float_holds_is_exact(conformance: Any) -> None:
    closes, _ = conformance.daily("precise")
    [close] = closes
    assert close.price.amount == Decimal("62000.123456789012345")
    assert str(close.price.amount) == "62000.123456789012345"
    assert Decimal(float(close.price.amount)) != close.price.amount


def test_a_value_past_the_contracts_decimal_crosses_as_reported(conformance: Any) -> None:
    closes, bars = conformance.daily("unconvertible")
    [close], [bar] = closes, bars
    assert close.price is None and bar.close is None
    [reported] = close.meta.unconverted
    assert reported.scheme.startswith(conformance.plugin + ":")
    assert reported.code == "1.0000000000000000001"
    assert reported in bar.meta.unconverted
    # Everything that did convert still crosses.
    assert money(bar.open) == usd("61000.5") and bar.volume == Decimal("1234.56789012")


# --- live prices, quotes and trades ---------------------------------------------------


def test_the_last_price(conformance: Any) -> None:
    price = conformance.last()
    assert (price.kind, subject(price), money(price.price)) == ("last", BITCOIN, usd("62800.01"))
    assert (price.meta.business_date, price.meta.valid_until_ns) == ("", 0)
    assert price.meta.source.dataset == "live"


def test_the_quote(conformance: Any) -> None:
    quote = conformance.quote()
    assert subject(quote) == BITCOIN
    assert (money(quote.bid), quote.bid_quantity) == (usd("62800.00"), Decimal("1.5"))
    assert (money(quote.ask), quote.ask_quantity) == (usd("62800.02"), Decimal("0.75"))
    assert quote.characteristics == () and quote.meta.source.dataset == "live"


def test_a_trades_aggressor_from_a_makers_side_or_a_takers(conformance: Any) -> None:
    trades = conformance.trades()
    got = [
        (t.source_sequence, money(t.price), t.quantity, t.aggressor, t.meta.valid_from_ns)
        for t in trades
    ]
    assert got == [(n, usd(p), Decimal(q), a, at) for n, p, q, a, at in TRADES]
    for trade in trades:
        assert subject(trade) == BITCOIN and not trade.cancelled
        # No SIP and no condition codes: a regular trade, eligible for everything.
        for view in (trade.attributes.consolidated, trade.attributes.market_centre):
            assert (view.high_low, view.open, view.close, view.volume) == ("eligible",) * 4
        assert trade.attributes.characteristics == ()


def test_a_side_the_venue_never_sends_is_not_known_and_reported(conformance: Any) -> None:
    [trade] = conformance.trades("trades-unknown-side")
    assert trade.aggressor == ""
    [reported] = trade.meta.unconverted
    assert reported.scheme.startswith(conformance.plugin + ":") and "side" in reported.scheme


def test_the_stream_converts_to_the_same_rows(conformance: Any) -> None:
    rows = conformance.stream()
    kinds = {type(r).__name__: r for r in rows}
    assert set(kinds) == {"Price", "Quote", "Trade"}
    assert money(kinds["Price"].price) == usd("62800.01") and kinds["Price"].kind == "last"
    assert (money(kinds["Quote"].bid), kinds["Quote"].bid_quantity) == (
        usd("62800.00"),
        Decimal("1.5"),
    )
    assert (money(kinds["Quote"].ask), kinds["Quote"].ask_quantity) == (
        usd("62800.02"),
        Decimal("0.75"),
    )
    trade = kinds["Trade"]
    assert (trade.source_sequence, money(trade.price), trade.quantity, trade.aggressor) == (
        1002,
        usd("62799.5"),
        Decimal("0.25"),
        "sell",
    )
    assert trade.meta.valid_from_ns == TRADES[1][4]


# --- every row --------------------------------------------------------------------------


def _every_row(conformance: Any) -> list[Any]:
    closes, bars = conformance.daily("daily")
    return [
        *closes,
        *bars,
        conformance.last(),
        conformance.quote(),
        *conformance.trades(),
        *conformance.stream(),
    ]


def test_every_row_references_a_raw_record_that_resolves(conformance: Any) -> None:
    for row in _every_row(conformance):
        assert row.meta.raw.instance_id == ""  # the sidecar's to fill
        assert conformance.raw(row.meta.raw) is not None, row.meta.raw


def test_no_float_anywhere_in_any_row(conformance: Any) -> None:
    for row in _every_row(conformance):
        assert not any(isinstance(value, float) for value in walk(row))


#: Each row type's shape: one contract between the two plugins.
SHAPES: dict[str, dict[str, Any]] = {
    "Price": {"kind": "str", "price": "Money", "basis": "str"},
    "Bar": {
        "open": "Money",
        "high": "Money",
        "low": "Money",
        "close": "Money",
        "volume": "Decimal",
        "vwap": ("Money", None),
        "trade_count": ("int", None),
    },
    "Quote": {
        "bid": "Money",
        "ask": "Money",
        "bid_quantity": "Decimal",
        "ask_quantity": "Decimal",
        "characteristics": "tuple",
    },
    "Trade": {
        "price": "Money",
        "quantity": "Decimal",
        "attributes": "TradeAttributes",
        "aggressor": "str",
        "source_sequence": "int",
        "cancelled": "bool",
    },
}
META = {
    "row_key": "str",
    "subjects": "tuple",
    "source": "Source",
    "valid_from_ns": "int",
    "raw": "RawRecordRef",
    "valid_until_ns": "int",
    "business_date": "str",
    "source_times": "tuple",
    "unconverted": "tuple",
}


def test_every_row_has_the_contracts_shape(conformance: Any) -> None:
    for row in _every_row(conformance):
        name = type(row).__name__
        fields = {f.name: getattr(row, f.name) for f in dataclasses.fields(row)}
        meta = fields.pop("meta")
        assert {
            f.name: type(getattr(meta, f.name)).__name__ for f in dataclasses.fields(meta)
        } == META
        assert set(fields) == set(SHAPES[name]), name
        for field, value in fields.items():
            wanted = SHAPES[name][field]
            allowed = wanted if isinstance(wanted, tuple) else (wanted,)
            got = None if value is None else type(value).__name__
            assert got in allowed, (name, field, got)
        if hasattr(row, "price") and row.price is not None:
            assert type(row.price.amount).__name__ == "Decimal"
