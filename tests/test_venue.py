"""Kraken's own differences, which the shared suite does not state: its rate
limit and its error array, its raw records, its many names for one pair, its
fiat codes, its JSON-number trade times, its WebSocket v2, and the CLI. Each
against the synthetic exchanges."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import pytest

from kraken import assets, convert, sample
from kraken.raw import RawStore
from kraken.rows import NANOS_PER_SECOND, Price, Quote, Trade
from kraken.sample import Session
from kraken.venue import RateLimiter, VenueError, result_for

from conftest import EXCHANGES, NOW_NS, FakeSocket, Sleeps, body, client, replayed_feed


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_the_rate_limiter_spends_one_call_a_second() -> None:
    clock = Clock()
    limiter = RateLimiter(1.0, 1, clock=clock, sleep=clock.sleep)
    for _ in range(6):
        limiter.acquire()
    assert clock.now == pytest.approx(5.0) and limiter.waited == pytest.approx(5.0)


def test_krakens_too_many_requests_under_a_200_is_waited_out(tmp_path: Path) -> None:
    sleeps = Sleeps()
    limited = ["rate-limited.json", "rate-limited.json"]
    kraken, replay = client(tmp_path, statuses={"/Ticker": limited}, sleep=sleeps)
    fetched = kraken.ticker("XXBTZUSD")
    assert result_for(fetched.value, "XXBTZUSD")["c"][0] == "62800.01000"
    assert sleeps == [2.0, 4.0] and kraken.rate_limited == 2 and len(replay.asked) == 3


def test_a_429_is_waited_out_too_and_persisting_is_an_error(tmp_path: Path) -> None:
    kraken, _ = client(tmp_path, statuses={"/Ticker": [429] * 4})
    with pytest.raises(VenueError) as refused:
        kraken.ticker("XXBTZUSD")
    assert refused.value.status == 429 and kraken.rate_limited == 3
    assert kraken.store.get(refused.value.raw_key) is not None


def test_an_error_in_krakens_array_is_raised_with_its_words(tmp_path: Path) -> None:
    kraken, _ = client(tmp_path, {"/Ticker": "unknown-pair.json"})
    with pytest.raises(VenueError) as refused:
        kraken.ticker("NOPE")
    # Under a 200: Kraken's status says nothing, its error array everything.
    assert refused.value.status == 200 and refused.value.detail == "EQuery:Unknown asset pair"


def test_every_response_is_kept_exactly_as_it_arrived(tmp_path: Path) -> None:
    kraken, _ = client(tmp_path)
    fetched = kraken.ohlc("XXBTZUSD", 1440, 1_790_812_800)
    record = kraken.store.get(fetched.raw_key)
    assert record is not None
    assert record["body"] == (EXCHANGES / "ohlc-daily.json").read_text()
    assert record["call"] == "OHLC" and "interval=1440" in record["request"]["url"]


def test_a_trade_time_is_decimal_seconds_read_exactly(tmp_path: Path) -> None:
    kraken, _ = client(tmp_path)
    entries = result_for(kraken.trades("XXBTZUSD").value, "XXBTZUSD")
    assert entries[0][2] == Decimal("1791028798.1234") and isinstance(entries[0][2], Decimal)
    product = assets.Pairs(body("asset-pairs.json")["result"], body("assets.json")["result"])
    [first, _] = convert.trades(product.find("XXBTZUSD"), entries, "k")
    assert first.meta.valid_from_ns == 1_791_028_798 * NANOS_PER_SECOND + 123_400_000


def test_one_pair_is_found_by_every_name_kraken_gives_it() -> None:
    pairs = assets.Pairs(body("asset-pairs.json")["result"], body("assets.json")["result"])
    found = {pairs.find(n).product.value for n in ("XXBTZUSD", "XBTUSD", "XBT/USD", "BTC/USD")}
    assert found == {"XXBTZUSD"}
    assert pairs.websocket["XXBTZUSD"] == "BTC/USD"
    with pytest.raises(KeyError):
        pairs.find("BTC-USD")


def test_krakens_codes_map_to_currencies_and_tokens_never_by_guessing() -> None:
    pairs = assets.Pairs(body("asset-pairs.json")["result"], body("assets.json")["result"])
    usd = pairs.find("XBT/USD").quote
    assert (usd.kind, usd.identifier and usd.identifier.value, usd.venue_code.value) == (
        "currency",
        "USD",
        "ZUSD",
    )
    # Kraken states no network or contract for any token: no CAIP-19.
    usdc = pairs.find("USDC/USD").base
    assert usdc.kind == "token" and usdc.identifier is None
    assert (usdc.resolve_by().scheme, usdc.resolve_by().source) == ("symbol", "kraken")
    # A pair that is not online is kept, with Kraken's word for its state.
    tkn = pairs.find("TKN/USD")
    assert (tkn.status, tkn.tradable) == ("cancel_only", False)


def test_krakens_side_is_the_takers() -> None:
    pairs = assets.Pairs(body("asset-pairs.json")["result"], body("assets.json")["result"])
    product = pairs.find("XXBTZUSD")
    entry = ["1.0", "1.0", 1791028800, "b", "m", "", 7]
    assert convert.trades(product, [entry], "k")[0].aggressor == "buy"
    assert convert.trades(product, [[*entry[:3], "s", *entry[4:]]], "k")[0].aggressor == "sell"


def test_the_last_price_is_valid_from_when_it_arrived(tmp_path: Path) -> None:
    kraken, _ = client(tmp_path)
    session = Session(kraken)
    price = session.last(session.product("XBT/USD"))
    record = kraken.store.get(price.meta.raw.key)
    assert record is not None and price.meta.valid_from_ns == record["received_at_ns"]
    assert price.meta.source_times == ()


async def test_websocket_v2_is_subscribed_and_each_message_kept(tmp_path: Path) -> None:
    kraken, _ = client(tmp_path)
    connector, sockets = replayed_feed()
    session = Session(kraken)
    rows = await session.stream([session.product("XBT/USD")], 0.2, connector=connector)
    [socket] = sockets
    assert [json.loads(m) for m in socket.sent] == [
        {"method": "subscribe", "params": {"channel": "ticker", "symbol": ["BTC/USD"]}},
        {"method": "subscribe", "params": {"channel": "trade", "symbol": ["BTC/USD"]}},
    ]
    assert [type(r) for r in rows] == [Price, Quote, Trade]
    assert [r.meta.raw.key.rsplit("#", 1)[1] for r in rows] == ["2", "2", "3"]


async def test_a_dropped_socket_is_opened_again_after_a_pause(tmp_path: Path) -> None:
    kraken, _ = client(tmp_path)
    opened: list[int] = []
    pauses: list[float] = []

    @asynccontextmanager
    async def flaky(url: str) -> AsyncIterator[FakeSocket]:
        opened.append(1)
        if len(opened) == 1:
            raise ConnectionResetError("dropped")
        yield FakeSocket((EXCHANGES / "stream.jsonl").read_text().splitlines())

    async def pause(seconds: float) -> None:
        pauses.append(seconds)

    got = [m async for m, _, _ in kraken.stream(["BTC/USD"], 0.2, connector=flaky, pause=pause)]
    assert len(opened) == 2 and pauses == [1.0] and kraken.reconnected == 1
    assert len(got) == 5


def test_a_refused_subscription_is_raised() -> None:
    refused = {"method": "subscribe", "success": False, "error": "Currency pair not supported"}
    with pytest.raises(ValueError, match="not supported"):
        list(convert.message({}, refused, "k", NOW_NS))


def test_the_cli_prints_each_row_as_exact_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sample, "Kraken", lambda store: client(store.root)[0])
    monkeypatch.setattr("time.time_ns", lambda: NOW_NS)
    assert sample.main(["--product", "XBT/USD", "--raw-dir", str(tmp_path)]) == 0
    printed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    kinds = [(p["row"], p.get("kind")) for p in printed]
    assert kinds[:3] == [("Price", "close")] * 3 and ("Quote", None) in kinds
    assert printed[0]["price"]["amount"] == "62000.12345678"
    assert printed[3]["vwap"]["amount"] == "61800.1" and printed[3]["trade_count"] == 45678
    assert any(RawStore(tmp_path).records())
