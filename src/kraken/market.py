"""What a venue's market data comes to before it becomes the lake's rows: the
venue's own words read, nothing of the contract's yet.

The same module in meridian-coinbase and meridian-kraken. Each plugin's
`convert.py` reads its venue's responses into these values -- every number a
`Decimal` from the venue's own text, never a float; a value that would not
read kept as the venue sent it -- and `dgm.py`, the same in both, turns them
into the lake's prices and bars. So the two plugins differ only where their
venues differ (the plan's "one suite unchanged between the two").

- `Asset`: one of the venue's assets, by its own code, with the one global
  identifier the venue's own statements give it: a currency's ISO 4217 code,
  or a token's CAIP-19 where the venue says which contract it is (a native
  coin by its SLIP-44 coin type). A token the venue states no single network
  for is the venue's own instrument, known by its code alone (the product
  owner's ruling of 2026-10-03). A stablecoin is a token, never its currency.
- `Listed`: a product (a market), its base and quote assets. A product names
  a market, not an asset: its base is a row's subject, priced in its quote.
- `Candle`: one day's open, high, low, close and volume, its start a UTC
  midnight, with a volume-weighted price and a trade count where the venue
  gives them.
- `Tick`: the last trade's price; `Top`: the best bid and offer (the level-1
  book).
- `Unread`: a value that did not read as a number, kept as sent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

NANOS_PER_SECOND = 1_000_000_000
SECONDS_PER_DAY = 86_400
NANOS_PER_DAY = SECONDS_PER_DAY * NANOS_PER_SECOND

#: The contract's Decimal: at most 18 places and 38 digits (meridian.Money).
MOST_PLACES = 18
MOST_DIGITS = 38


@dataclass(frozen=True)
class Unread:
    """A venue's value that did not read as the contract's number: its field
    in the venue's response, and its text as sent."""

    field: str
    text: str


def json_decimal(body: str | bytes) -> Any:
    """A JSON body with every number that is not a whole one a `Decimal` from
    its own text: no float ever exists (one float() loses a satoshi)."""
    return json.loads(body, parse_float=Decimal)


def number(value: object) -> Decimal | None:
    """A venue's figure as the contract's number, from its text: a string or a
    JSON number read by `json_decimal`. None where it is not a finite number,
    or holds more places or digits than the contract's Decimal does; never
    rounded."""
    if isinstance(value, bool) or value is None or isinstance(value, float):
        return None
    try:
        amount = Decimal(value) if isinstance(value, int | Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    sign, digits, exponent = amount.as_tuple()
    places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    if places > MOST_PLACES or len(digits) > MOST_DIGITS:
        return None
    return amount


def utc_day(start_s: int) -> date:
    """The UTC date a day starting at `start_s` (a UTC midnight) is."""
    return date.fromordinal(date(1970, 1, 1).toordinal() + start_s // SECONDS_PER_DAY)


def day_start(day: date) -> int:
    """The UTC midnight `day` starts at, in seconds."""
    return (day.toordinal() - date(1970, 1, 1).toordinal()) * SECONDS_PER_DAY


@dataclass(frozen=True)
class Asset:
    """One of the venue's assets."""

    #: The venue's own code (Coinbase's `BTC`, Kraken's `XXBT`).
    code: str
    #: "currency" or "token".
    kind: str
    #: Its global identifier's scheme and value -- ("iso4217", "USD"),
    #: ("caip19", "bip122:.../slip44:0") -- or None where the venue states
    #: none (a token on several networks, or on none it says).
    scheme: str = ""
    value: str = ""
    #: The venue's own name for it, where it gives one, offered as stated.
    name: str = ""

    @property
    def currency_code(self) -> str:
        """Its ISO 4217 code, for a currency; empty for anything else."""
        return self.value if self.scheme == "iso4217" else ""


@dataclass(frozen=True)
class Listed:
    """One product the venue lists: a market, its base asset priced in its
    quote asset."""

    product: str
    base: Asset
    quote: Asset
    #: Whether the venue trades it now; a product it does not is still kept
    #: with its status, since its history still converts.
    tradable: bool = True
    status: str = ""


@dataclass(frozen=True)
class Candle:
    """One UTC day of a product's trading, as the venue stated it."""

    product: str
    start_s: int
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None
    #: The day's volume-weighted price and trade count, where the venue
    #: states them for a day that traded; None otherwise, never zero.
    vwap: Decimal | None = None
    trade_count: int | None = None
    #: The raw record it was read from, and where in it.
    raw_key: str = ""
    unread: tuple[Unread, ...] = ()

    @property
    def day(self) -> date:
        return utc_day(self.start_s)

    @property
    def end_ns(self) -> int:
        return (self.start_s + SECONDS_PER_DAY) * NANOS_PER_SECOND


@dataclass(frozen=True)
class Tick:
    """The last trade's price."""

    product: str
    price: Decimal | None
    #: When it traded, as the venue stated it; else when the response
    #: arrived, `stated` False.
    at_ns: int
    stated: bool
    #: The venue's own identity for it -- its trade ID -- where it gives one.
    ident: str = ""
    raw_key: str = ""
    unread: tuple[Unread, ...] = ()


@dataclass(frozen=True)
class Top:
    """The best bid and offer, the level-1 book; a side with no order is None."""

    product: str
    bid: Decimal | None
    ask: Decimal | None
    at_ns: int
    stated: bool
    ident: str = ""
    raw_key: str = ""
    unread: tuple[Unread, ...] = ()


@dataclass
class Health:
    """How the venue has answered since the plugin started."""

    calls: int = 0
    rate_limited: int = 0
    failures: int = 0
    last_ok_ns: int = 0
    last_failed_ns: int = 0
    last_error: str = ""
    waited_seconds: float = 0.0


@dataclass(frozen=True)
class Limits:
    """What the venue allows, as its documentation says, and what the plugin
    spends of it."""

    allowed: str
    spent: str
    #: The most daily candles one request returns, and how far back the
    #: venue's daily history reaches in days (0: it states no bound).
    most_candles: int
    history_days: int


@dataclass(frozen=True)
class Endpoint:
    """One way the plugin reaches the venue."""

    name: str
    address: str
    what: str


@dataclass
class Session:
    """What the plugin connects to, and how it is going."""

    mode: str
    endpoints: tuple[Endpoint, ...]
    limits: Limits
    health: Health = field(default_factory=Health)


class Market(Protocol):
    """A venue's public market data, read into the values above; every
    response kept as a raw record before anything is read from it."""

    #: The venue's code as a `symbol`'s source (`coinbase`, `kraken`).
    venue: str
    #: The venue's name, as people know it.
    name: str
    session: Session

    def listing(self) -> list[Listed]:
        """Every product the venue lists, each side read as an asset."""
        ...

    def candles(self, product: str, first: date, last: date) -> list[Candle]:
        """The product's daily candles from `first` to `last`, inclusive, oldest
        first; a day the venue has no candle for is missing, never made up."""
        ...

    def last(self, product: str) -> Tick: ...

    def top(self, product: str) -> Top: ...
