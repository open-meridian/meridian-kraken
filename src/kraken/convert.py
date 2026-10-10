"""Kraken's answers, read into the venue's values (market.py): the one place
Kraken's vocabulary is read.

Kraken names everything its own way: assets by legacy codes (`XXBT` for
bitcoin, whose altname is `XBT`, never `BTC`; `ZUSD` for the dollar), pairs by
keys built from them (`XXBTZUSD`) and by altnames (`XBTUSD`). A pair names a
market, not an asset: its base is a row's subject, priced in its quote.

**Assets** (the plan's "Identifiers for a pair"; the product owner's ruling of
2026-10-03 on a venue's balance in a multi-network token):

- **A currency** is a cash instrument, by its ISO 4217 code, for the fiat
  asset codes Kraken documents (FIAT, below). Kraken's `Assets` does not say
  which assets are fiat (every one is `aclass: currency`), and a `Z` is no
  sign of one (`ZAMA` and `ZBCN` are tokens), so the table is the plugin's,
  keyed by Kraken's codes; a code not in it resolves by its code until a
  person adds it.
- **A native coin** carries its CAIP-19 by SLIP-44 coin type (`XXBT`, `XETH`).
- **Every other token** is Kraken's own instrument: Kraken's public API states
  no network and no contract for any asset, so none carries a contract's
  CAIP-19; each is known by its code with source `kraken` alone. Never
  guessed from a ticker.
- **A stablecoin is a token,** never its currency (decisions/030): a pair
  quoted in `USDC` is quoted in Kraken's USDC instrument, and Kraken's own
  `USDCUSD` is the dataset's price for valuing it (the plan's Q25).

**OHLC** entries are `[time, open, high, low, close, vwap, volume, count]`,
every figure a string, `time` the day's start at 00:00 UTC; the last entry is
the day still forming. A day with no trades (`count` 0) has no
volume-weighted price, whatever Kraken writes in its place.

**The ticker's** `c` is the last trade's price; Kraken states no time and no
trade identity for it, so it is in force from when its answer arrived and is
known by that answer's place in the raw record. **The level-1 book's** best
bid and ask are `Depth`'s first `bids` and `asks`, `[price, volume, time]`, in
force from the later side's time.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from .market import (
    NANOS_PER_SECOND,
    SECONDS_PER_DAY,
    Asset,
    Candle,
    Endpoint,
    Limits,
    Listed,
    Session,
    Tick,
    Top,
    Unread,
    day_start,
    number,
)
from .venue import BURST, MOST_CANDLES, RATE_PER_SECOND, REST, Kraken, result_for

VENUE = "kraken"
NAME = "Kraken"

#: Native coins by SLIP-44 coin type, keyed by Kraken's asset code.
NATIVE: dict[str, str] = {
    "XXBT": "bip122:000000000019d6689c085ae165831e93/slip44:0",
    "XETH": "eip155:1/slip44:60",
}

#: Kraken's fiat asset codes, as its documentation lists them, to ISO 4217.
FIAT: dict[str, str] = {
    "ZUSD": "USD",
    "ZEUR": "EUR",
    "ZGBP": "GBP",
    "ZCAD": "CAD",
    "ZJPY": "JPY",
    "ZAUD": "AUD",
    "CHF": "CHF",
}

LIMITS = Limits(
    allowed=(
        "Public REST limited by IP, and OHLC and Trades by pair; about one call a second "
        "stays inside (Kraken's documentation)"
    ),
    spent=f"At most {RATE_PER_SECOND:g} a second, bursts of {BURST}; a rate limit waited out",
    most_candles=MOST_CANDLES,
    history_days=MOST_CANDLES,
)


def asset(code: str) -> Asset:
    """One Kraken asset, by its code."""
    if code in FIAT:
        return Asset(code, "currency", "iso4217", FIAT[code])
    if code in NATIVE:
        return Asset(code, "token", "caip19", NATIVE[code])
    return Asset(code, "token")


def listing(pairs: Mapping[str, Any]) -> list[Listed]:
    """Every pair Kraken lists, by its key, each side read as an asset. A pair
    not `online` is kept with its status: its history still converts."""
    listed: list[Listed] = []
    for key, item in pairs.items():
        status = str(item.get("status") or "")
        listed.append(
            Listed(
                product=str(key),
                base=asset(str(item["base"])),
                quote=asset(str(item["quote"])),
                tradable=status == "online",
                status=status,
            )
        )
    return sorted(listed, key=lambda p: p.product)


def candles(pair: str, entries: list[Any], raw_key: str) -> list[Candle]:
    """Daily OHLC entries, oldest first, each naming its place in the raw record."""
    read: list[Candle] = []
    for entry in sorted(entries, key=lambda e: int(e[0])):
        start = int(entry[0])
        if start % SECONDS_PER_DAY:
            raise ValueError(f"a daily entry at {start} does not start at 00:00 UTC")
        unread: list[Unread] = []

        def figure(field: str, value: object, unread: list[Unread] = unread) -> Any:
            amount = number(value)
            if amount is None:
                unread.append(Unread(f"ohlc.{field}", str(value)))
            return amount

        open_, high, low, close = (
            figure(field, entry[i]) for field, i in (("open", 1), ("high", 2), ("low", 3),
                                                      ("close", 4))
        )  # fmt: skip
        volume = figure("volume", entry[6])
        count = entry[7] if isinstance(entry[7], int) and not isinstance(entry[7], bool) else None
        if count is None:
            unread.append(Unread("ohlc.count", str(entry[7])))
        vwap = figure("vwap", entry[5]) if count else None
        read.append(
            Candle(
                product=pair,
                start_s=start,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                vwap=vwap,
                trade_count=count,
                raw_key=f"{raw_key}#{start}",
                unread=tuple(unread),
            )
        )
    return read


def tick(pair: str, body: Mapping[str, Any], raw_key: str, first_seen_ns: int) -> Tick:
    """The ticker's last trade (`c`), in force from when Kraken's answer first
    said it: Kraken's `Ticker` states no time."""
    stated = (body.get("c") or [None])[0]
    price = number(stated)
    return Tick(
        product=pair,
        price=price,
        at_ns=first_seen_ns,
        stated=False,
        ident=f"{raw_key.split('/')[1]}#{raw_key.rsplit('#', 1)[-1]}",
        raw_key=raw_key,
        unread=() if price is not None else (Unread("ticker.c", str(stated)),),
    )


def top(pair: str, body: Mapping[str, Any], raw_key: str, received_ns: int) -> Top:
    """`Depth`'s best bid and ask, in force from the later side's time; a side
    with no order is None."""
    bids, asks = body.get("bids") or [], body.get("asks") or []
    unread: list[Unread] = []
    bid = number(bids[0][0]) if bids else None
    ask = number(asks[0][0]) if asks else None
    if bids and bid is None:
        unread.append(Unread("depth.bids.price", str(bids[0][0])))
    if asks and ask is None:
        unread.append(Unread("depth.asks.price", str(asks[0][0])))
    times = [int(side[0][2]) for side in (bids, asks) if side]
    at = max(times) * NANOS_PER_SECOND if times else received_ns
    return Top(
        product=pair,
        bid=bid,
        ask=ask,
        at_ns=at,
        stated=bool(times),
        ident=str(max(times)) if times else str(received_ns),
        raw_key=raw_key,
        unread=tuple(unread),
    )


class KrakenMarket:
    """Kraken's market data, as the shared dgm asks for it."""

    venue = VENUE
    name = NAME

    def __init__(self, client: Kraken, mode: str = "live") -> None:
        self.client = client
        address = REST if mode == "live" else f"{mode}: no call leaves the deployment"
        self.session = Session(
            mode=mode,
            endpoints=(Endpoint("REST", address, "asset pairs, daily OHLC, ticker, depth"),),
            limits=LIMITS,
            health=client.health,
        )

    def listing(self) -> list[Listed]:
        return listing(self.client.asset_pairs().value)

    def candles(self, product: str, first: date, last: date) -> list[Candle]:
        start, end = day_start(first), day_start(last)
        fetched = self.client.ohlc(product, start - 1)
        entries = result_for(fetched.value, product)
        return [c for c in candles(product, entries, fetched.raw_key) if start <= c.start_s <= end]

    def last(self, product: str) -> Tick:
        fetched = self.client.ticker(product)
        kept = self.client.store.get(fetched.raw_key) or {}
        first_seen = int(kept.get("received_at_ns", fetched.received_at_ns))
        return tick(product, result_for(fetched.value, product), fetched.raw_key, first_seen)

    def top(self, product: str) -> Top:
        fetched = self.client.depth(product)
        return top(product, result_for(fetched.value, product), fetched.raw_key,
                   fetched.received_at_ns)  # fmt: skip
