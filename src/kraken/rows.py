"""The lake's slice 1 rows, as plain Python values: the one seam.

Contract v14 (the lake's slice 1a, meridian-design's
plans/the-lake-prices-the-book) does not exist yet, and neither do the SDK's
generated types for it. Every value this plugin converts lands in the
dataclasses below, which mirror spec/the-lake.md's proposed messages
(`ObservationMeta`, `SubjectRef`, `Source`, `SourceTime`, `Price`, `Bar`,
`Trade` with `TradeAttributes` and `Eligibility`, `Quote`) and step 6's
`AsReported` and `RawRecordRef`, field for field where the spec names one.
**When v14 lands, this module is replaced by the SDK's generated types**, and
nothing else in the plugin should change but its imports: the converters
build these, the tests compare these, the CLI prints these.

The module is identical, byte for byte, in meridian-coinbase and
meridian-kraken; tests/test_conformance.py, also identical in both, holds its
digest, so the two plugins cannot drift apart on the shape of a row.

Exact decimals only. Every number is a `decimal.Decimal` made from the text
the venue sent (`json_decimal` parses a body with no float ever existing);
a float handed to any constructor here is a TypeError, never a value. The
contract's bounds (v11's `Decimal`: scale 0 to 18, under 10^38) are checked by
`exact`, and a value past them, or not a number at all, comes back as the
venue's text in an `AsReported`, the field beside it left not known (None).

Where this departs from the spec, each departure is a question for v14:

- `SubjectRef.resolve_by` is the identifier the plugin would resolve through
  W3.1 (a token's `caip19`, a currency's `iso4217`, otherwise its
  venue-scoped `symbol`, which W3.2 reports as missing); the spec's
  `entity_id` needs an entity store this spike does not have.
- `Money.currency` is an `Identifier`, not v11's `Money.currency_code`: ISO
  4217 alone cannot name a stablecoin, and a price quoted in USDC is quoted
  in the venue's USDC instrument, by its venue code: never in dollars
  (decisions/030; the plan's Q25), and never in one chain's USDC contract
  (the ruling under `Asset`).
- A required value that fails conversion is None, with its `AsReported` in
  `meta.unconverted`; the spec's dictionary says `Price.price` is never empty,
  which the lake would refuse. And an `AsReported` in `meta.unconverted` does
  not say which field it stands beside, so the scheme carries the vendor's
  field (`coinbase:candles.close`) until the contract gives it a path.
- `Source` carries `dataset` and `venue` only: `instance` and
  `plugin_version` are stamped by the sidecar; `sent_at_ns`,
  `recorded_at_ns`, `version`, `sequence` and `previous_sequence` are the
  sidecar's and the lake's, never the plugin's.
- `DatasetDeclaration` and `DeclaredDay` mirror the catalogue as the plan's
  Q5 proposes it (a dataset declares the calendar its business date is in and
  when its day ends). Dataset keys, like every name here not in the contract,
  are proposals, not reserved vocabulary.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from meridian.bounds import (
    AS_REPORTED_CODE_LENGTH,
    AS_REPORTED_SCHEME_LENGTH,
    AS_REPORTED_TEXT_LENGTH,
    RAW_RECORD_REF_KEY_LENGTH,
)

NANOS_PER_SECOND = 1_000_000_000
NANOS_PER_DAY = 86_400 * NANOS_PER_SECOND

# v11's Decimal: an integer below 10^38 and a scale of 0 to 18.
MAX_SCALE = 18
MAX_DIGITS = 38

# Identifier schemes the contract already has (v11's instrument record).
ISO4217 = "iso4217"
CAIP19 = "caip19"
SYMBOL = "symbol"
_GLOBAL_SCHEMES = frozenset({ISO4217, CAIP19})


def _length(text: str, least: int, most: int, name: str) -> str:
    if not least <= len(text) <= most:
        raise ValueError(f"{name} is {len(text)} characters; {least} to {most}")
    return text


def _decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} is {type(value).__name__}; only Decimal, never a float")
    return value


def _optional_decimal(value: object, name: str) -> Decimal | None:
    return None if value is None else _decimal(value, name)


# --- identity -------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Identifier:
    """v11's `Identifier`: a global scheme (`iso4217`, `caip19`) with no
    source, or a source-scoped one (`symbol`) only meaningful beside its
    source (`coinbase`, `kraken`)."""

    scheme: str
    value: str
    source: str = ""

    def __post_init__(self) -> None:
        if not self.scheme or not self.value:
            raise ValueError("an identifier has a scheme and a value")
        if self.scheme in _GLOBAL_SCHEMES and self.source:
            raise ValueError(f"{self.scheme} is a global scheme; it has no source")
        if self.scheme not in _GLOBAL_SCHEMES and not self.source:
            raise ValueError(f"{self.scheme} is source-scoped; it needs its source")


def currency(code: str) -> Identifier:
    """A currency's cash instrument, by its ISO 4217 code."""
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError(f"{code!r} is not an ISO 4217 code")
    return Identifier(ISO4217, code)


def caip19(value: str) -> Identifier:
    """A token, by its CAIP-19 asset ID (`chain/namespace:reference`)."""
    if not re.fullmatch(
        r"[-a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}/[-a-z0-9]{3,8}:[-.%a-zA-Z0-9]{1,128}", value
    ):
        raise ValueError(f"{value!r} is not a CAIP-19 asset ID")
    return Identifier(CAIP19, value)


def symbol(value: str, source: str) -> Identifier:
    """A venue's own code, meaningful only beside the venue that uses it."""
    return Identifier(SYMBOL, value, source)


AssetKind = Literal["currency", "token"]


@dataclasses.dataclass(frozen=True)
class Asset:
    """One side of a venue's market: a currency's cash instrument or a token.
    `identifier` is the global one W3.1 resolves by (`iso4217` for a
    currency, `caip19` for a token), None where the plugin does not know it:
    never guessed from a ticker.

    The product owner's ruling (plans/the-lake-prices-the-book, "a venue's
    balance in a multi-network token is the venue's own instrument"): each
    issuer contract on each chain is its own instrument, with its CAIP-19; a
    token whose network the venue does not specify -- Coinbase's `USDC`,
    listed on many networks -- is the venue's promise that it converts into
    one of those contracts, a separate instrument with the venue's code alone
    and never a contract's CAIP-19. A token on the one network the venue
    states, with its contract, carries that contract's CAIP-19; a native coin
    its SLIP-44 one.

    `venue_instrument` and `networks` are this spike's own placeholders, not
    contract vocabulary: `venue_instrument` marks a token as the venue's own
    instrument, and `networks` keeps, as plain data, the networks the venue
    lists it on (empty where it states none), for the "convertible into"
    relationship whose shape and name are the entity store's spec's."""

    venue_code: Identifier
    kind: AssetKind
    identifier: Identifier | None = None
    name: str = ""
    venue_instrument: bool = False
    networks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.venue_code.scheme != SYMBOL:
            raise ValueError("an asset's venue code is a source-scoped symbol")
        if self.identifier is not None:
            wanted = ISO4217 if self.kind == "currency" else CAIP19
            if self.identifier.scheme != wanted:
                raise ValueError(f"a {self.kind} is identified by {wanted}, never otherwise")
        if self.venue_instrument and (self.kind != "token" or self.identifier is not None):
            raise ValueError("the venue's own instrument is a token known by its venue code alone")
        if self.networks and not self.venue_instrument:
            raise ValueError("networks are kept only for the venue's own instrument")

    def resolve_by(self) -> Identifier:
        """What W3.1 is asked for: the global identifier, else the venue's code
        (which resolves to nothing and is reported missing through W3.2)."""
        return self.identifier if self.identifier is not None else self.venue_code


@dataclasses.dataclass(frozen=True)
class Product:
    """A venue's market, mapped to its two assets: what the plugin keeps in
    its own storage (the plan's "Identifiers for a pair"). A product names a
    market, not an asset, and is never a row's subject: the base asset is."""

    product: Identifier
    base: Asset
    quote: Asset
    status: str
    tradable: bool

    def subject(self) -> SubjectRef:
        return SubjectRef(self.base.resolve_by())

    def money(self, amount: Decimal) -> Money:
        return Money(amount, self.quote.resolve_by())


# --- the envelope ------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Money:
    """An exact amount in a currency or a token (see the module's note)."""

    amount: Decimal
    currency: Identifier

    def __post_init__(self) -> None:
        within_bounds(_decimal(self.amount, "Money.amount"))


@dataclasses.dataclass(frozen=True)
class AsReported:
    """Step 6's `AsReported`: a vendor value that did not convert, beside the
    field's not-known value. Bounded as v11 bounds it."""

    scheme: str
    code: str
    text: str

    def __post_init__(self) -> None:
        for name, value, bound in (
            ("scheme", self.scheme, AS_REPORTED_SCHEME_LENGTH),
            ("code", self.code, AS_REPORTED_CODE_LENGTH),
            ("text", self.text, AS_REPORTED_TEXT_LENGTH),
        ):
            _length(value, bound.least, bound.most, f"AsReported.{name}")


def as_reported(scheme: str, value: object) -> AsReported:
    """The venue's value as it sent it, cut to the contract's bound."""
    if isinstance(value, str):
        text = value
    elif isinstance(value, Decimal | int) and not isinstance(value, bool):
        text = str(value)
    else:
        text = json.dumps(value, default=str)
    code = text[: AS_REPORTED_CODE_LENGTH.most] or "(empty)"
    return AsReported(scheme, code, text[: AS_REPORTED_TEXT_LENGTH.most] or "(empty)")


@dataclasses.dataclass(frozen=True)
class RawRecordRef:
    """Step 6's `RawRecordRef`: the plugin's own key for the raw record a row
    was converted from. The instance is the sidecar's to fill."""

    key: str
    instance_id: str = ""

    def __post_init__(self) -> None:
        bound = RAW_RECORD_REF_KEY_LENGTH
        _length(self.key, bound.least, bound.most, "RawRecordRef.key")


@dataclasses.dataclass(frozen=True)
class SubjectRef:
    """What a row is about (see the module's note on `resolve_by`)."""

    resolve_by: Identifier


@dataclasses.dataclass(frozen=True)
class Source:
    """Where a row came from: the dataset in the instance's catalogue, and the
    venue's MIC where it has one ("" where ISO 10383 has none; the plan's Q24)."""

    dataset: str
    venue: str = ""

    def __post_init__(self) -> None:
        _length(self.dataset, 1, 96, "Source.dataset")
        if self.venue and not re.fullmatch(r"[A-Z0-9]{4}", self.venue):
            raise ValueError(f"{self.venue!r} is not a MIC")


SourceTimeKind = Literal["event", "consolidated", "reported", "vendor_received", "published"]
_SOURCE_TIME_KINDS = ("event", "consolidated", "reported", "vendor_received", "published")


@dataclasses.dataclass(frozen=True)
class SourceTime:
    """One of the source's own clocks, in UTC nanoseconds."""

    kind: SourceTimeKind
    at_ns: int

    def __post_init__(self) -> None:
        if self.kind not in _SOURCE_TIME_KINDS:
            raise ValueError(f"{self.kind!r} is not a source time kind")
        if self.at_ns <= 0:
            raise ValueError("a source time is never 0")


@dataclasses.dataclass(frozen=True)
class ObservationMeta:
    """The envelope every row carries, less what the sidecar and the lake
    decide (see the module's note)."""

    row_key: str
    subjects: tuple[SubjectRef, ...]
    source: Source
    valid_from_ns: int
    raw: RawRecordRef
    valid_until_ns: int = 0
    business_date: str = ""
    source_times: tuple[SourceTime, ...] = ()
    unconverted: tuple[AsReported, ...] = ()

    def __post_init__(self) -> None:
        _length(self.row_key, 1, 128, "row_key")
        if not 1 <= len(self.subjects) <= 8:
            raise ValueError("a row has 1 to 8 subjects")
        if self.valid_from_ns <= 0:
            raise ValueError("valid_from_ns is never 0")
        if self.valid_until_ns and self.valid_until_ns <= self.valid_from_ns:
            raise ValueError("valid_until_ns is after valid_from_ns, or 0")
        if self.business_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.business_date):
            raise ValueError(f"business_date {self.business_date!r} is not YYYY-MM-DD")
        kinds = [time.kind for time in self.source_times]
        if len(kinds) > 6 or len(set(kinds)) != len(kinds):
            raise ValueError("at most one source time of each kind")
        if len(self.unconverted) > 16:
            raise ValueError("at most 16 values as reported")


# --- slice 1's data types ------------------------------------------------------

PriceKind = Literal["close", "last", "nav", "settlement", "bid", "ask", "mid"]
PriceBasis = Literal["per_unit"]
Eligible = Literal["eligible", "not_eligible", ""]
Aggressor = Literal["buy", "sell", ""]
TradeCharacteristic = Literal[
    "opening_auction",
    "closing_auction",
    "other_auction",
    "odd_lot",
    "extended_hours",
    "out_of_sequence",
    "late_report",
    "average_price",
    "non_regular_settlement",
    "intermarket_sweep",
    "derivatively_priced",
    "prior_reference_price",
    "contingent",
    "cross",
]
QuoteCharacteristic = Literal["indicative", "halted"]


def _not_known_beside_as_reported(meta: ObservationMeta, missing: Iterable[str]) -> None:
    missing = list(missing)
    if missing and not meta.unconverted:
        raise ValueError(f"{', '.join(missing)} not known, and nothing as reported beside it")


def _one_currency(*amounts: Money | None) -> None:
    currencies = {money.currency for money in amounts if money is not None}
    if len(currencies) > 1:
        raise ValueError("one currency for all of a row's prices")


@dataclasses.dataclass(frozen=True)
class Price:
    """A price of one kind for one subject."""

    meta: ObservationMeta
    kind: PriceKind
    price: Money | None
    basis: PriceBasis = "per_unit"

    def __post_init__(self) -> None:
        _not_known_beside_as_reported(self.meta, ["price"] if self.price is None else [])


@dataclasses.dataclass(frozen=True)
class Bar:
    """Open, high, low and close over the envelope's valid time."""

    meta: ObservationMeta
    open: Money | None
    high: Money | None
    low: Money | None
    close: Money | None
    volume: Decimal | None
    vwap: Money | None = None
    trade_count: int | None = None

    def __post_init__(self) -> None:
        _one_currency(self.open, self.high, self.low, self.close, self.vwap)
        volume = _optional_decimal(self.volume, "Bar.volume")
        if volume is not None and volume < 0:
            raise ValueError("volume is never negative")
        if self.trade_count is not None and (
            isinstance(self.trade_count, bool) or self.trade_count < 0
        ):
            raise ValueError("trade_count is a count")
        fields = (
            ("open", self.open),
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
            ("volume", volume),
        )
        _not_known_beside_as_reported(self.meta, [k for k, v in fields if v is None])


@dataclasses.dataclass(frozen=True)
class Eligibility:
    """Whether a trade may set each of a bar's statistics; "" is not known."""

    high_low: Eligible = ""
    open: Eligible = ""
    close: Eligible = ""
    volume: Eligible = ""


#: A regular trade: eligible for every statistic.
ELIGIBLE = Eligibility("eligible", "eligible", "eligible", "eligible")


@dataclasses.dataclass(frozen=True)
class TradeAttributes:
    """What a trade may count for, in both views, and its characteristics."""

    consolidated: Eligibility
    market_centre: Eligibility
    characteristics: tuple[TradeCharacteristic, ...] = ()


#: A crypto venue's trade: no SIP and no condition codes, so a regular trade
#: is eligible for everything in its venue's view and a reader's consolidated
#: view (the plan's "Trade attributes for a crypto trade").
REGULAR = TradeAttributes(consolidated=ELIGIBLE, market_centre=ELIGIBLE)


@dataclasses.dataclass(frozen=True)
class Trade:
    """One trade as its source reported it."""

    meta: ObservationMeta
    price: Money | None
    quantity: Decimal | None
    attributes: TradeAttributes
    aggressor: Aggressor = ""
    source_sequence: int = 0
    cancelled: bool = False

    def __post_init__(self) -> None:
        quantity = _optional_decimal(self.quantity, "Trade.quantity")
        if quantity is not None and quantity <= 0:
            raise ValueError("a trade's quantity is more than zero")
        if self.aggressor not in ("buy", "sell", ""):
            raise ValueError(f"{self.aggressor!r} is not an aggressor")
        fields = (("price", self.price), ("quantity", quantity))
        _not_known_beside_as_reported(self.meta, [k for k, v in fields if v is None])


@dataclasses.dataclass(frozen=True)
class Quote:
    """The best bid and offer; a side unset is a side empty."""

    meta: ObservationMeta
    bid: Money | None = None
    ask: Money | None = None
    bid_quantity: Decimal | None = None
    ask_quantity: Decimal | None = None
    characteristics: tuple[QuoteCharacteristic, ...] = ()

    def __post_init__(self) -> None:
        _one_currency(self.bid, self.ask)
        for name in ("bid_quantity", "ask_quantity"):
            quantity = _optional_decimal(getattr(self, name), f"Quote.{name}")
            if quantity is not None and quantity <= 0:
                raise ValueError(f"{name} is more than zero")


Row = Price | Bar | Trade | Quote


# --- the catalogue --------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class DeclaredDay:
    """The day a dataset's business dates are in (the plan's Q5): the
    calendar, and the moment, in it, the day ends."""

    calendar: str
    ends_at: str


@dataclasses.dataclass(frozen=True)
class DatasetDeclaration:
    """One dataset in a `dgm`'s catalogue, as the spec's "The catalogue" has it."""

    key: str
    vendor: str
    data_types: tuple[Literal["price", "bar", "trade", "quote"], ...]
    modes: tuple[Literal["pull", "push", "stream"], ...]
    cadence: str
    day: DeclaredDay | None = None
    aggregator: str = ""


# --- converting at the edge -------------------------------------------------------


def within_bounds(value: Decimal) -> Decimal:
    """The value, if v11's Decimal can carry it exactly; ValueError if not."""
    if not value.is_finite():
        raise ValueError(f"{value} is not a finite number")
    sign, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    scale = -exponent if exponent < 0 else 0
    width = len(digits) + (exponent if exponent > 0 else 0)
    if scale > MAX_SCALE or width > MAX_DIGITS:
        raise ValueError(f"{value} is past scale {MAX_SCALE} or {MAX_DIGITS} digits")
    return value


def exact(value: object, scheme: str) -> Decimal | AsReported:
    """The venue's number as an exact Decimal, from its own text; or, where it
    is not a number the contract can carry, the value as reported under
    `scheme`. A float is a bug in the parser, never a vendor value."""
    if isinstance(value, float):
        raise TypeError("a float reached conversion; parse the body with json_decimal")
    if isinstance(value, bool):
        return as_reported(scheme, value)
    if isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, Decimal):
        number = value
    elif isinstance(value, str):
        try:
            number = Decimal(value.strip()) if value.strip() else Decimal("NaN")
        except InvalidOperation:
            return as_reported(scheme, value)
    else:
        return as_reported(scheme, value)
    try:
        return within_bounds(number)
    except ValueError:
        return as_reported(scheme, value)


def json_decimal(body: str | bytes) -> Any:
    """A JSON body with every non-integer number a Decimal from its own text:
    no float ever exists (one float() loses a satoshi)."""
    return json.loads(body, parse_float=Decimal)


_RFC3339 = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})"
)


def rfc3339_ns(text: str) -> int:
    """An RFC 3339 time, to the nanosecond (Coinbase sends nine digits, which
    datetime cannot hold)."""
    match = _RFC3339.fullmatch(text)
    if match is None:
        raise ValueError(f"{text!r} is not an RFC 3339 time")
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    offset = _dt.timedelta()
    if zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        offset = sign * _dt.timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6]))
    moment = _dt.datetime(
        int(year),
        int(month),
        int(day),
        int(hour),
        int(minute),
        int(second),
        tzinfo=_dt.timezone(offset),
    )
    nanos = int((fraction or "0").ljust(9, "0"))
    return int(moment.timestamp()) * NANOS_PER_SECOND + nanos


def seconds_ns(value: object) -> int:
    """Epoch seconds as the venue sent them (an integer, or Kraken's decimal
    seconds), exactly, in nanoseconds."""
    if isinstance(value, float):
        raise TypeError("a float reached conversion; parse the body with json_decimal")
    number = Decimal(value) if isinstance(value, int | str) else value
    if not isinstance(number, Decimal):
        raise TypeError(f"{value!r} is not a time")
    return int(number * NANOS_PER_SECOND)


def utc_date(at_ns: int) -> str:
    """The UTC calendar date a moment falls on."""
    return _dt.datetime.fromtimestamp(at_ns // NANOS_PER_SECOND, _dt.UTC).date().isoformat()


def utc_day_start_ns(date: str) -> int:
    moment = _dt.datetime.fromisoformat(date).replace(tzinfo=_dt.UTC)
    return int(moment.timestamp()) * NANOS_PER_SECOND


# --- reading rows -------------------------------------------------------------------


def as_dict(row: object) -> Any:
    """A row as plain JSON-ready values, every Decimal as its exact text, at
    the scale the venue stated it."""
    if dataclasses.is_dataclass(row) and not isinstance(row, type):
        return {f.name: as_dict(getattr(row, f.name)) for f in dataclasses.fields(row)}
    if isinstance(row, tuple | list):
        return [as_dict(item) for item in row]
    if isinstance(row, Decimal):
        return format(row, "f")  # 0.00000004, never 4E-8
    return row


def shape(row: object) -> Any:
    """A row's shape: each field's name and the type of its value, recursively;
    None where a field is unset. Two plugins whose rows have one shape have
    one contract between them."""
    if dataclasses.is_dataclass(row) and not isinstance(row, type):
        return {
            "type": type(row).__name__,
            **{f.name: shape(getattr(row, f.name)) for f in dataclasses.fields(row)},
        }
    if isinstance(row, tuple | list):
        return sorted({json.dumps(shape(item), sort_keys=True) for item in row})
    return None if row is None else type(row).__name__
