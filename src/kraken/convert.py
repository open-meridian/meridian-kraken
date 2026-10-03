"""Kraken's market data, converted into the lake's rows (rows.py).

The edge is the only place Kraken's vocabulary is read. Each converter takes
what Kraken sent, parsed with Decimals, the pair it was for (assets.py), and
the raw record it was kept as, and returns rows naming the base asset as
their subject and pricing it in the quote asset.

- **The daily close** (the plan's Q5): Kraken has no official close, so a
  dataset declares its day, the UTC day, and a daily OHLC entry (interval
  1,440 minutes, its time the bucket's start at 00:00 UTC) gives kind `close`
  for business date D, valid from D 00:00Z until D+1 00:00Z, its `published`
  source time the entry's end. Kraken's last entry is the day still forming
  (past its `last`, its end after now): it has no `published` time and keeps
  its row key, so each later reading is a restatement under the same key.
- **Bars** from the same entries, `[time, open, high, low, close, vwap,
  volume, count]`: Kraken gives a VWAP and a trade count, so both are set.
- **Trades:** Kraken's `b`/`s` is the **taker's** side, so it is the
  aggressor as sent; its `m`/`l` (the taker's order type) stays in the raw
  record. Its trade time is decimal seconds, a JSON number, converted exactly.
- **Last and quote:** the ticker's `c` is the last trade, but `Ticker`
  carries no time at all, so the last price is valid from when the plugin
  received it. `Ticker`'s bid and ask volumes are whole lots (`1.000`), so
  the REST quote comes from `Depth` (count 1), timestamped per side, whose
  volumes Kraken states to three decimals (`0.361` where WebSocket v2 said
  `0.35800347`): converted exactly as stated, and the stream's quote, to
  eight, is the better one. WebSocket v2's ticker carries a `timestamp`.
- A value that does not convert -- not a number, or past the contract's
  Decimal -- is None with the venue's text in an `AsReported`, its scheme
  naming Kraken's field (`kraken:ohlc.close`).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping
from decimal import Decimal
from typing import Any

from .rows import (
    NANOS_PER_DAY,
    NANOS_PER_SECOND,
    REGULAR,
    AsReported,
    Bar,
    DatasetDeclaration,
    DeclaredDay,
    Money,
    ObservationMeta,
    Price,
    Product,
    Quote,
    RawRecordRef,
    Row,
    Source,
    SourceTime,
    Trade,
    as_reported,
    exact,
    rfc3339_ns,
    seconds_ns,
    utc_date,
)

VENUE = "kraken"
DAY_MINUTES = 1_440

#: The catalogue (the spec's "The catalogue"; the plan's Q21): a daily dataset
#: of closes and bars, kept, its day the UTC day; and a live one of last
#: prices, quotes and trades. The keys are proposals.
DAILY = DatasetDeclaration(
    key="daily",
    vendor="Kraken",
    data_types=("price", "bar"),
    modes=("pull",),
    cadence="P1D",
    day=DeclaredDay(calendar="UTC", ends_at="00:00"),
)
LIVE = DatasetDeclaration(
    key="live",
    vendor="Kraken",
    data_types=("price", "quote", "trade"),
    modes=("pull", "stream"),
    cadence="PT1S",
)
CATALOGUE = (DAILY, LIVE)


class _Values:
    """Converts one record's values, collecting what did not convert."""

    def __init__(self, product: Product, endpoint: str) -> None:
        self.product, self.endpoint = product, endpoint
        self.unconverted: list[AsReported] = []

    def number(self, field: str, value: object) -> Decimal | None:
        converted = exact(value, f"{VENUE}:{self.endpoint}.{field}")
        if isinstance(converted, AsReported):
            self.unconverted.append(converted)
            return None
        return converted

    def money(self, field: str, value: object) -> Money | None:
        amount = self.number(field, value)
        return None if amount is None else self.product.money(amount)

    def positive(self, field: str, value: object) -> Decimal | None:
        """A quantity, which the contract has more than zero."""
        amount = self.number(field, value)
        if amount is not None and amount <= 0:
            self.unconverted.append(as_reported(f"{VENUE}:{self.endpoint}.{field}", value))
            return None
        return amount

    def count(self, field: str, value: object) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        self.unconverted.append(as_reported(f"{VENUE}:{self.endpoint}.{field}", value))
        return None


def _pid(product: Product) -> str:
    return product.product.value


def daily(
    product: Product, entries: list[Any], raw_key: str, now_ns: int
) -> tuple[list[Price], list[Bar]]:
    """Closes and bars from daily OHLC entries, oldest first."""
    closes: list[Price] = []
    bars: list[Bar] = []
    for entry in sorted(entries, key=lambda e: int(e[0])):
        start = int(entry[0])
        if start % (DAY_MINUTES * 60):
            raise ValueError(f"a daily entry at {start} does not start at 00:00 UTC")
        start_ns = start * NANOS_PER_SECOND
        end_ns = start_ns + NANOS_PER_DAY
        date = utc_date(start_ns)
        published = () if end_ns > now_ns else (SourceTime("published", end_ns),)
        values = _Values(product, "ohlc")
        open_, high, low, close, vwap = (
            values.money(field, entry[i])
            for field, i in (("open", 1), ("high", 2), ("low", 3), ("close", 4), ("vwap", 5))
        )
        volume = values.number("volume", entry[6])
        if volume is not None and volume < 0:
            values.unconverted.append(as_reported(f"{VENUE}:ohlc.volume", entry[6]))
            volume = None
        trade_count = values.count("count", entry[7])
        daily_meta = ObservationMeta(
            row_key=f"close/{_pid(product)}/{date}",
            subjects=(product.subject(),),
            source=Source(DAILY.key),
            valid_from_ns=start_ns,
            valid_until_ns=end_ns,
            business_date=date,
            source_times=published,
            raw=RawRecordRef(f"{raw_key}#{start}"),
            unconverted=tuple(u for u in values.unconverted if u.scheme.endswith(".close")),
        )
        closes.append(Price(daily_meta, "close", close))
        bar_meta = dataclasses.replace(
            daily_meta,
            row_key=f"bar/{_pid(product)}/P1D/{date}",
            unconverted=tuple(values.unconverted),
        )
        bars.append(
            Bar(
                bar_meta,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                vwap=vwap,
                trade_count=trade_count,
            )
        )
    return closes, bars


def last(product: Product, ticker: Mapping[str, Any], raw_key: str, received_at_ns: int) -> Price:
    """The ticker's last trade price (`c`), valid from when it was received:
    Kraken's `Ticker` gives no time."""
    values = _Values(product, "ticker")
    price = values.money("c", (ticker.get("c") or [None])[0])
    return Price(
        ObservationMeta(
            row_key=f"last/{_pid(product)}/{received_at_ns}",
            subjects=(product.subject(),),
            source=Source(LIVE.key),
            valid_from_ns=received_at_ns,
            raw=RawRecordRef(raw_key),
            unconverted=tuple(values.unconverted),
        ),
        "last",
        price,
    )


def quote(product: Product, depth: Mapping[str, Any], raw_key: str) -> Quote:
    """The best bid and ask from `Depth`, each `[price, volume, time]`; in force
    from the later of the two sides' times. A side with no order is unset."""
    values = _Values(product, "depth")
    bids, asks = depth.get("bids") or [], depth.get("asks") or []
    times = [seconds_ns(side[0][2]) for side in (bids, asks) if side]
    at = max(times)
    return Quote(
        ObservationMeta(
            row_key=f"quote/{_pid(product)}/{at}",
            subjects=(product.subject(),),
            source=Source(LIVE.key),
            valid_from_ns=at,
            source_times=(SourceTime("event", at),),
            raw=RawRecordRef(raw_key),
            unconverted=tuple(values.unconverted),
        ),
        bid=values.money("bids.price", bids[0][0]) if bids else None,
        ask=values.money("asks.price", asks[0][0]) if asks else None,
        bid_quantity=values.positive("bids.volume", bids[0][1]) if bids else None,
        ask_quantity=values.positive("asks.volume", asks[0][1]) if asks else None,
    )


#: Kraken's side is the taker's: the aggressor, as sent.
_AGGRESSOR = {"b": "buy", "s": "sell", "buy": "buy", "sell": "sell"}


def _trade(
    product: Product,
    endpoint: str,
    ref_key: str,
    trade_id: int,
    side: object,
    price: object,
    quantity: object,
    at: int,
) -> Trade:
    values = _Values(product, endpoint)
    aggressor = _AGGRESSOR.get(str(side), "")
    if not aggressor:
        values.unconverted.append(as_reported(f"{VENUE}:{endpoint}.side", side))
    converted_price = values.money("price", price)
    converted_quantity = values.positive("volume", quantity)
    return Trade(
        ObservationMeta(
            row_key=f"trade/{_pid(product)}/{trade_id}",
            subjects=(product.subject(),),
            source=Source(LIVE.key),
            valid_from_ns=at,
            source_times=(SourceTime("event", at),),
            raw=RawRecordRef(ref_key),
            unconverted=tuple(values.unconverted),
        ),
        price=converted_price,
        quantity=converted_quantity,
        attributes=REGULAR,
        aggressor=aggressor,  # type: ignore[arg-type]
        source_sequence=trade_id,
    )


def trades(product: Product, entries: list[Any], raw_key: str) -> list[Trade]:
    """Trades, each `[price, volume, time, side, order type, misc, trade id]`,
    oldest first as Kraken sends them."""
    return [
        _trade(
            product,
            "trades",
            f"{raw_key}#{entry[6]}",
            int(entry[6]),
            entry[3],
            entry[0],
            entry[1],
            seconds_ns(entry[2]),
        )
        for entry in sorted(entries, key=lambda e: int(e[6]))
    ]


def message(
    products: Mapping[str, Product], item: Mapping[str, Any], ref_key: str, received_at_ns: int
) -> Iterator[Row]:
    """The rows one WebSocket v2 message carries, keyed by v2 symbol: a
    `ticker` gives the last price and the quote (valid from its timestamp
    where it has one, else from when it arrived), a `trade` its trades.
    Status, heartbeats and acknowledgements give none; a refused
    subscription is raised."""
    if item.get("method") == "subscribe" and item.get("success") is False:
        raise ValueError(f"Kraken refused the subscription: {item.get('error')}")
    channel = item.get("channel")
    if channel not in ("ticker", "trade"):
        return
    for data in item.get("data") or []:
        product = products.get(str(data.get("symbol", "")))
        if product is None:
            continue
        if channel == "trade":
            yield _trade(
                product,
                "ws.trade",
                ref_key,
                int(data["trade_id"]),
                data.get("side"),
                data.get("price"),
                data.get("qty"),
                rfc3339_ns(str(data["timestamp"])),
            )
            continue
        stamped = data.get("timestamp")
        at = rfc3339_ns(str(stamped)) if stamped else received_at_ns
        values = _Values(product, "ws.ticker")
        meta = ObservationMeta(
            row_key=f"last/{_pid(product)}/{at}",
            subjects=(product.subject(),),
            source=Source(LIVE.key),
            valid_from_ns=at,
            source_times=(SourceTime("event", at),) if stamped else (),
            raw=RawRecordRef(ref_key),
        )
        price = values.money("last", data.get("last"))
        yield Price(dataclasses.replace(meta, unconverted=tuple(values.unconverted)), "last", price)
        quoted = _Values(product, "ws.ticker")
        bid = quoted.money("bid", data["bid"]) if data.get("bid") else None
        ask = quoted.money("ask", data["ask"]) if data.get("ask") else None
        bid_qty = quoted.positive("bid_qty", data["bid_qty"]) if data.get("bid_qty") else None
        ask_qty = quoted.positive("ask_qty", data["ask_qty"]) if data.get("ask_qty") else None
        yield Quote(
            dataclasses.replace(
                meta, row_key=f"quote/{_pid(product)}/{at}", unconverted=tuple(quoted.unconverted)
            ),
            bid=bid,
            ask=ask,
            bid_quantity=bid_qty,
            ask_quantity=ask_qty,
        )
