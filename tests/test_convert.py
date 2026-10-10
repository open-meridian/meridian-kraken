"""Kraken's answers read into the venue's values: its assets, daily OHLC,
ticker and level-1 book (convert.py), on the shapes Kraken sends."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from kraken import convert
from kraken.market import json_decimal, number
from kraken.synthetic import Synthetic

PAIRS = Synthetic().held["pairs"]


def test_a_documented_fiat_code_is_its_iso_4217_cash_instrument() -> None:
    usd = convert.asset("ZUSD")
    assert (usd.kind, usd.scheme, usd.value, usd.currency_code) == (
        "currency",
        "iso4217",
        "USD",
        "USD",
    )
    assert convert.asset("CHF").currency_code == "CHF"


def test_a_z_is_no_sign_of_a_currency() -> None:
    for code in ("ZAMA", "ZBCN", "ZARS"):
        assert convert.asset(code).kind == "token", code


def test_a_native_coin_carries_its_slip44_caip19() -> None:
    assert convert.asset("XXBT").value == "bip122:000000000019d6689c085ae165831e93/slip44:0"
    assert convert.asset("XETH").value == "eip155:1/slip44:60"


def test_every_other_token_is_krakens_own_instrument() -> None:
    for code in ("USDC", "USDT", "TKN"):
        token = convert.asset(code)
        assert (token.kind, token.scheme, token.currency_code) == ("token", "", ""), code


def test_pairs_by_their_keys_a_stablecoin_never_its_currency() -> None:
    listed = {p.product: p for p in convert.listing(PAIRS)}
    assert listed["XBTUSDC"].quote.code == "USDC" and not listed["XBTUSDC"].quote.currency_code
    assert listed["ZEURZUSD"].base.currency_code == "EUR"
    assert not listed["OLDUSD"].tradable and listed["OLDUSD"].status == "cancel_only"


def test_ohlc_is_read_from_its_strings_with_vwap_and_count() -> None:
    body = json_decimal(
        '[[1791417600,"62431.3","62580.9","61750.0","0.000012345678901234","62190.7",'
        '"904.71000000",31877]]'
    )
    [candle] = convert.candles("XXBTZUSD", body, "responses/x.json")
    assert candle.close == Decimal("0.000012345678901234") and str(candle.open) == "62431.3"
    assert (candle.vwap, candle.trade_count) == (Decimal("62190.7"), 31877)
    assert candle.day == date(2026, 10, 8) and candle.raw_key == "responses/x.json#1791417600"


def test_a_day_that_traded_nothing_has_no_volume_weighted_price() -> None:
    [candle] = convert.candles(
        "QDTUSD", [[1791417600, "3.20", "3.20", "3.20", "3.20", "0.00", "0.00000000", 0]], "k"
    )
    assert (candle.vwap, candle.trade_count, candle.volume) == (None, 0, Decimal("0.00000000"))


def test_a_figure_that_does_not_read_is_kept_as_sent() -> None:
    [candle] = convert.candles("XXBTZUSD", [[1791417600, "1", "2", "1", "n/a", "1", "5", 2]], "k")
    assert candle.close is None
    assert [(u.field, u.text) for u in candle.unread] == [("ohlc.close", "n/a")]


def test_an_entry_not_on_a_utc_midnight_is_refused() -> None:
    try:
        convert.candles("XXBTZUSD", [[1791417601, "1", "1", "1", "1", "1", "1", 1]], "k")
    except ValueError as refused:
        assert "00:00 UTC" in str(refused)
    else:
        raise AssertionError("an entry off midnight was read")


def test_the_ticker_states_no_time_and_the_book_its_sides_times() -> None:
    ticker = Synthetic().held["ticker"]["XXBTZUSD"]
    tick = convert.tick("XXBTZUSD", ticker, "responses/2026-10-10/Ticker/XXBTZUSD.jsonl#4", 77)
    assert (tick.price, tick.at_ns, tick.stated, tick.ident) == (
        Decimal("61988.90000"),
        77,
        False,
        "2026-10-10#4",
    )
    depth = Synthetic().held["depth"]["XXBTZUSD"]
    top = convert.top("XXBTZUSD", depth, "k", 5)
    assert (top.bid, top.ask, top.stated) == (Decimal("61988.80000"), Decimal("61989.00000"), True)
    assert top.at_ns == 1791590399 * 1_000_000_000
    empty = convert.top("XXBTZUSD", {"bids": [], "asks": []}, "k", 7)
    assert (empty.bid, empty.ask, empty.at_ns, empty.stated) == (None, None, 7, False)


def test_number_refuses_what_is_not_the_contracts_decimal() -> None:
    assert number("62431.30000") == Decimal("62431.30000")
    for refused in (None, True, 1.5, "NaN", "abc", "0." + "1" * 19):
        assert number(refused) is None, refused
