"""The `dgm`: the venue's market data into the lake, converted at the edge.

The same module in meridian-coinbase and meridian-kraken: what differs
between the two venues is read in each plugin's `convert.py` into the values
of market.py, and everything from there to the lake is here, once.

**Two datasets** (declaration.py; the plan's Q21): `daily`, each day's close
and bar on the UTC day, its day ending at 00:00 UTC (`day_time_zone`
`Etc/UTC`, `day_end_minute` 0; the plan's Q5); and `live`, the last trade's
price and the best bid and offer, recorded at most once a second per subject
and kind.

**Named before it is served** (W3.6, W3.1, W3.14; products.py): each subject
the lake asks for is the deployment's record, read (a dgm's read from
contract v18 as ruled 2026-10-10) and named as one of the venue's assets by
its identifiers -- the venue's own code first, then the global identifier the
venue states, then another source's symbol that is the venue's code -- never
guessed from its description. Where the record lacks the venue's
identifiers, they are reported beside the one it matched (W3.1), so the
record carries them; where that resolve meets another record, it is reported
(W3.2) and the subject is still served. A record naming none of the venue's
assets is declined, not covered. Only the quote assets of a subject's
products are resolved by their own identifiers, a record minted only where
none resolves: no record per listed asset. The venue is resolved by its code
with the venue as source (the venue master's call: Coinbase and Kraken carry
`symbol` codes, Kraken no MIC). What does not resolve is reported (W3.2,
W3.15) and never guessed: a product quoted in an asset that did not resolve
is left out, and a venue not held is left empty on each row with its code
beside it as reported, re-resolved at the next mapping.

**A row** names the base asset's instrument as its subject, its dataset as
the instance and the key, the venue's ID, its times and the raw record it was
read from (raw.py). A daily close is keyed by the product and the candle's
start (`BTC-USD:1d:1791417600`), so a repeat is answered unchanged and the
day still forming -- its candle's end after now, valid until then, with no
`published` time yet -- is restated under the same key as it fills; its
business date is a plain date. A price is a `Money` in the quote asset: a
currency by its ISO 4217 code, which core resolves to its cash instrument;
any token -- USDT and USDC among them -- by the token's own instrument,
never a fiat code, so a USDC price is never a dollar price (decisions/030,
the plan's Q25: a stablecoin is valued through a dataset's price of it).
Every figure is the venue's own text as a Decimal; a required figure that
did not read leaves its row unrecorded and counted, and an optional one (a
volume-weighted price) is left unset with the venue's text beside it.

**Wants** (W10.7): the lake asks for a business date or a range of the
daily dataset, or the latest of either (the daily dataset's latest is the
past week's days the venue has), for subjects; each subject is
recorded against the want, or declined: `not_covered` where its record
names none of the venue's assets or no product's base is it,
`beyond_history` before the venue's reach, `source_silent` where the venue
has nothing for it. A standing want keeps its subjects
current until it is withdrawn: the live dataset polled at most once a second,
the daily dataset's forming day every five minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import meridian
from meridian import Bar, Money, ObservationMeta, Price, SourceTime
from meridian.plugin.v1 import operations_pb2 as ops

from .market import NANOS_PER_SECOND, Asset, Candle, Market, Tick, Top, Unread, utc_day
from .products import Mapped, ProductMap

log = logging.getLogger(__name__)

DAILY, LIVE = "daily", "live"
PRICE, BAR = "meridian.v1.Price", "meridian.v1.Bar"
#: The live dataset's pace: each subject and kind at most once a second.
LIVE_EVERY_SECONDS = 1.0
#: A standing want on the daily dataset: its forming day, every five minutes.
DAILY_EVERY_SECONDS = 300.0
#: The product map read again from the venue's listing.
REMAP_SECONDS = 86_400
#: A want for the daily dataset's latest: the past week's days.
LATEST_DAYS = 7
#: The lake's batch: 1 to 500 rows.
BATCH = 500
#: How many of the wants heard the Datasets page lists.
RECENT = 200

#: A record's classes that may be a coin's or a currency's: none yet, a
#: crypto asset, cash.
_NOT_A_SECURITY = {ops.ASSET_CLASS_UNSPECIFIED, ops.ASSET_CLASS_CRYPTO_ASSET, ops.ASSET_CLASS_CASH}
#: Identifiers only a security carries.
_SECURITY_SCHEMES = {"cusip", "isin", "sedol", "figi"}

_DAILY_KINDS = {ops.PRICE_KIND_CLOSE}
_LIVE_KINDS = {ops.PRICE_KIND_LAST, ops.PRICE_KIND_BID, ops.PRICE_KIND_ASK}

Plugin = Any  # meridian.Plugin, or the suite's Recorder: the SDK's typed operations


@dataclass(frozen=True)
class Standing:
    """A standing want, kept current until it is withdrawn."""

    want_id: str
    dataset: str
    data_type: str
    subjects: tuple[str, ...]
    kinds: tuple[int, ...]
    since_ns: int


@dataclass(frozen=True)
class Heard:
    """One want as the Datasets page lists it."""

    want_id: str
    dataset: str
    asked: str
    subjects: int
    recorded: int
    declined: int
    standing: bool
    at_ns: int


@dataclass
class Counts:
    """What the plugin has sent the lake since it started."""

    recorded: int = 0
    restated: int = 0
    unchanged: int = 0
    unread: int = 0
    declined: int = 0
    wants: int = 0


@dataclass
class Venue:
    """The venue's ID in the venue master, once resolved."""

    venue_id: str = ""
    missing: bool = False
    asked: bool = False


@dataclass
class Dgm:
    """The plugin's work: resolving, recording, and answering wants."""

    plugin: Plugin
    market: Market
    products: ProductMap
    clock_ns: Callable[[], int] = time.time_ns
    standing: dict[str, Standing] = field(default_factory=dict)
    counts: Counts = field(default_factory=Counts)
    venue: Venue = field(default_factory=Venue)
    heard: deque[Heard] = field(default_factory=lambda: deque(maxlen=RECENT))
    #: Whether the deployment answers this plugin's ResolveInstrument: a
    #: runtime from before the ruling of 2026-10-10 does not.
    reads_instruments: bool = True
    #: Subjects whose record named none of the venue's assets, until the
    #: listing is read again.
    unnamed: set[str] = field(default_factory=set)

    # ── Names ────────────────────────────────────────────────────────────

    def dataset(self, key: str) -> str:
        """A dataset as rows name it: the instance, a colon and the key."""
        return f"{self.plugin.identity.instance_id}:{key}"

    def _key_of(self, dataset: str) -> str:
        instance, _, key = dataset.partition(":")
        return key if instance == self.plugin.identity.instance_id else ""

    def use(self, market: Market, products: ProductMap) -> None:
        """Answer from another source (synthetic mode switched): its own map,
        the venue asked again; standing wants carry over."""
        self.market, self.products, self.venue = market, products, Venue()

    # ── Resolving ────────────────────────────────────────────────────────

    async def venue_id(self) -> str:
        """The venue master's ID for the venue, asked once per mapping by its
        code with the venue as source; a venue not held is reported once and
        left empty."""
        if self.venue.asked:
            return self.venue.venue_id
        named = [meridian.Identifier(scheme="symbol", value=self.market.venue,
                                     source=self.market.venue)]  # fmt: skip
        now = self.clock_ns()
        found = await self.plugin.resolve_venue(identifiers=named, as_of_ns=now)
        self.venue.asked = True
        if found.found and found.venue.venue_id:
            self.venue.venue_id, self.venue.missing = found.venue.venue_id, False
        else:
            self.venue.venue_id, self.venue.missing = "", True
            await self.plugin.report_missing_venue(
                source=self.market.venue,
                identifiers=named,
                as_of_ns=now,
                reason=found.miss_reason or "not_found",
                observed_at_ns=now,
            )
        return self.venue.venue_id

    def identifiers(self, asset: Asset) -> list[ops.Identifier]:
        """The identifiers the venue's own statements give an asset: its
        global one, and a token's code as a `symbol` at the venue."""
        named: list[ops.Identifier] = []
        if asset.scheme:
            named.append(meridian.Identifier(scheme=asset.scheme, value=asset.value))
        if asset.kind != "currency":
            named.append(
                meridian.Identifier(scheme="symbol", value=asset.code, source=self.market.venue)
            )
        return named

    async def _resolve(self, asset: Asset, now: int) -> None:
        cash = asset.kind == "currency"
        found = await self.plugin.resolve_identifier(
            identifiers=self.identifiers(asset),
            as_of_ns=now,
            stated_asset_class="cash" if cash else "crypto_asset",
            stated_description=asset.name,
        )
        if found.found and found.instrument_id:
            self.products.ids[asset.code] = found.instrument_id
            self.products.missing.pop(asset.code, None)
            return
        reason = ops.MissReason.Name(found.miss_reason or ops.MISS_REASON_NOT_FOUND)
        if self.products.missing.get(asset.code) != reason:
            await self.plugin.report_missing_instrument(
                source=self.market.venue,
                asset_class="cash" if cash else "crypto_asset",
                identifiers=self.identifiers(asset),
                as_of_ns=now,
                reason=found.miss_reason or "not_found",
                observed_at_ns=now,
            )
        self.products.missing[asset.code] = reason

    async def _quotes(self, code: str, now: int) -> None:
        """Resolve the quote assets of each product on `code` not resolved yet."""
        for quote in self.products.quotes(code):
            if quote.code not in self.products.ids:
                await self._resolve(quote, now)

    @staticmethod
    def _same(one: ops.Identifier, other: ops.Identifier) -> bool:
        return (one.scheme, one.value, one.source) == (other.scheme, other.value, other.source)

    def _asset_of(self, record: ops.InstrumentRecord) -> tuple[Asset, ops.Identifier] | None:
        """The venue's asset a record is, by its identifiers, and the one it
        matched: the venue's own code, then the global identifier the venue
        states, then another source's symbol that is the venue's code -- the
        last never for a record that is a security (a class other than a
        crypto asset or cash, or a security's identifier): a ticker can be
        a coin's code too (ETH is a share's)."""
        assets = self.products.assets()
        venue = self.market.venue
        held = list(record.identifiers)
        for i in held:
            if i.scheme == "symbol" and i.source == venue:
                found = next((a for a in assets if a.code == i.value), None)
                if found:
                    return found, i
        for i in held:
            found = next((a for a in assets if a.scheme and (a.scheme, a.value)
                          == (i.scheme, i.value)), None)  # fmt: skip
            if found:
                return found, i
        if record.asset_class not in _NOT_A_SECURITY or any(
            i.scheme in _SECURITY_SCHEMES for i in held
        ):
            return None
        for i in held:
            if i.scheme == "symbol" and i.source != venue:
                found = next((a for a in assets if a.code.upper() == i.value.upper()), None)
                if found:
                    return found, i
        return None

    async def _join(
        self, subject: str, asset: Asset, matched: ops.Identifier, held: Sequence[ops.Identifier]
    ) -> None:
        """Report the venue's identifiers beside the one the record matched
        (W3.1), where it lacks them: one identifier in common is one
        security, so the record gains the venue's. Meeting another record is
        a miss (W3.2), reported once; the subject is served all the same."""
        own = self.identifiers(asset)
        if all(any(self._same(o, h) for h in held) for o in own):
            return
        named = own + ([] if any(self._same(matched, o) for o in own) else [matched])
        cash = asset.kind == "currency"
        now = self.clock_ns()
        found = await self.plugin.resolve_identifier(
            identifiers=named,
            as_of_ns=now,
            stated_asset_class="cash" if cash else "crypto_asset",
            stated_description=asset.name,
        )
        if found.found and found.instrument_id == subject:
            return
        # Matched another record: two records for one asset, which a person
        # merges (W3.13).
        miss = ops.MISS_REASON_AMBIGUOUS if found.found else found.miss_reason
        reason = ops.MissReason.Name(miss or ops.MISS_REASON_AMBIGUOUS)
        if self.products.missing.get(asset.code) != reason:
            await self.plugin.report_missing_instrument(
                source=self.market.venue,
                asset_class="cash" if cash else "crypto_asset",
                identifiers=named,
                as_of_ns=now,
                reason=miss or ops.MISS_REASON_AMBIGUOUS,
                observed_at_ns=now,
            )
        self.products.missing[asset.code] = reason

    async def _named(self, subject: str) -> str:
        """The venue's code a subject is, read from its record; empty where
        the record names none of the venue's assets, or cannot be read."""
        if not self.reads_instruments:
            return ""
        try:
            found = await self.plugin.resolve_instrument(
                instrument_id=subject, as_of_ns=self.clock_ns()
            )
        except meridian.NotGranted:
            # A runtime from before the ruling of 2026-10-10 grants a dgm no
            # read of what an instrument is (W3.6). Asked once, and said on
            # the Datasets page.
            log.warning("this dgm may not read an instrument's record; nothing is named")
            self.reads_instruments = False
            return ""
        if not found.found:
            return ""
        held = list(found.instrument.identifiers)
        named = self._asset_of(found.instrument)
        if named is None:
            return ""
        asset, matched = named
        await self._join(subject, asset, matched, held)
        return asset.code

    async def name(self, subjects: Iterable[str]) -> None:
        """Name each subject not named yet as one of the venue's assets, and
        resolve the quote assets of its products; the map kept in storage."""
        now = self.clock_ns()
        changed = False
        for subject in subjects:
            if subject in self.products.subjects or subject in self.unnamed:
                continue
            code = await self._named(subject)
            if not code:
                self.unnamed.add(subject)
                continue
            self.products.subjects[subject] = code
            await self._quotes(code, now)
            changed = True
        if changed:
            self.products.save()

    async def map_products(self) -> ProductMap:
        """Read the venue's listing, and resolve the quote assets of any
        product newly serving a subject; the map kept in storage. The venue
        is asked again too, and a subject not named is read again when
        next asked."""
        listed = await asyncio.to_thread(self.market.listing)
        now = self.clock_ns()
        self.products.listed = listed
        self.products.read_at_ns = now
        self.unnamed.clear()
        for code in sorted(set(self.products.subjects.values())):
            await self._quotes(code, now)
        self.products.save()
        self.venue.asked = False
        await self.venue_id()
        return self.products

    async def ensure_mapped(self) -> None:
        if not self.products.read_at_ns:
            await self.map_products()
        elif not self.venue.asked:
            await self.venue_id()

    # ── Rows ─────────────────────────────────────────────────────────────

    def _money(self, amount: Any, mapped: Mapped) -> Money:
        """An amount in the product's quote asset: a currency by its code, any
        token by its own instrument, never a fiat code."""
        if mapped.quote.currency_code:
            return Money(amount, mapped.quote.currency_code)
        return Money(amount, instrument_id=mapped.quote_id)

    def _unconverted(self, unread: Iterable[Unread]) -> list[ops.AsReported]:
        said = [
            ops.AsReported(scheme=f"{self.market.venue}:{u.field}", code=u.text[:64],
                           text=u.text[:256])
            for u in unread
        ]  # fmt: skip
        if self.venue.missing:
            said.append(ops.AsReported(scheme=f"{self.market.venue}:venue",
                                       code=self.market.venue, text=self.market.name))  # fmt: skip
        return said

    def _meta(
        self,
        row_key: str,
        mapped: Mapped,
        key: str,
        valid_from_ns: int,
        valid_until_ns: int,
        times: Sequence[SourceTime],
        raw_key: str,
        unread: Iterable[Unread] = (),
        business_date: date | None = None,
    ) -> ObservationMeta:
        return ObservationMeta(
            row_key=row_key,
            subjects=[ops.SubjectRef(entity_id=mapped.base_id)],
            source=ops.Source(dataset=self.dataset(key), venue_id=self.venue.venue_id),
            valid_from_ns=valid_from_ns,
            valid_until_ns=valid_until_ns,
            business_date=business_date or "",
            source_times=list(times),
            raw=self.plugin.raw_record(raw_key),
            unconverted=self._unconverted(unread),
        )

    def _daily_meta(self, mapped: Mapped, candle: Candle, optional: Iterable[Unread] = ()) -> Any:
        now = self.clock_ns()
        forming = candle.end_ns > now
        times = [] if forming else [SourceTime(kind="published", at_ns=candle.end_ns)]
        return self._meta(
            f"{mapped.product}:1d:{candle.start_s}",
            mapped,
            DAILY,
            candle.start_s * NANOS_PER_SECOND,
            candle.end_ns,
            times,
            candle.raw_key,
            optional,
            candle.day,
        )

    def close(self, mapped: Mapped, candle: Candle) -> Price | None:
        """The day's close: the dataset's declared close for its business
        date, the last price of the UTC day (the plan's Q5)."""
        if candle.close is None:
            return None
        return Price(
            meta=self._daily_meta(mapped, candle),
            kind="close",
            price=self._money(candle.close, mapped),
            basis="per_unit",
        )

    def bar(self, mapped: Mapped, candle: Candle) -> Bar | None:
        """The day's bar; a volume-weighted price and a trade count only where
        the venue states them for a day that traded, never zero."""
        open_, high, low, close, volume = (
            candle.open, candle.high, candle.low, candle.close, candle.volume
        )  # fmt: skip
        if open_ is None or high is None or low is None or close is None or volume is None:
            return None
        optional = [u for u in candle.unread if u.field.endswith(("vwap", "count"))]
        return Bar(
            meta=self._daily_meta(mapped, candle, optional),
            open=self._money(open_, mapped),
            high=self._money(high, mapped),
            low=self._money(low, mapped),
            close=self._money(close, mapped),
            volume=volume,
            vwap=None if candle.vwap is None else self._money(candle.vwap, mapped),
            trade_count=candle.trade_count,
        )

    def _live(self, mapped: Mapped, kind: str, amount: Any, seen: Tick | Top) -> Price:
        times = [SourceTime(kind="event", at_ns=seen.at_ns)] if seen.stated else []
        ident = seen.ident or str(seen.at_ns)
        return Price(
            meta=self._meta(
                f"{mapped.product}:{kind}:{ident}",
                mapped,
                LIVE,
                seen.at_ns,
                0,
                times,
                seen.raw_key,
            ),  # fmt: skip
            kind=kind,
            price=self._money(amount, mapped),
            basis="per_unit",
        )

    # ── Recording ────────────────────────────────────────────────────────

    async def _send(self, prices: list[Price], bars: list[Bar], want_id: str) -> int:
        sent = 0
        for start in range(0, len(prices), BATCH):
            done = await self.plugin.record_prices(prices=prices[start : start + BATCH],
                                                   want_id=want_id)  # fmt: skip
            self._count(done)
            sent += len(prices[start : start + BATCH])
        for start in range(0, len(bars), BATCH):
            done = await self.plugin.record_bars(bars=bars[start : start + BATCH],
                                                 want_id=want_id)  # fmt: skip
            self._count(done)
            sent += len(bars[start : start + BATCH])
        return sent

    def _count(self, done: Any) -> None:
        self.counts.recorded += int(getattr(done, "recorded", 0))
        self.counts.restated += int(getattr(done, "restated", 0))
        self.counts.unchanged += int(getattr(done, "unchanged", 0))

    async def _asked(self, call: Callable[..., Any], *args: Any) -> Any:
        """One question of the venue, off the loop: what it answered, or None
        where it refused or did not answer -- said on the Connection page,
        and the subject's other products still asked."""
        try:
            return await asyncio.to_thread(call, *args)
        except Exception as failed:  # noqa: BLE001 - the venue's refusal, kept in its raw record
            log.warning("%s asked of %s, not answered: %s", args[0], self.market.name, failed)
            return None

    async def record_days(
        self,
        subjects: Iterable[str],
        first: date,
        last: date,
        data_type: str = "",
        want_id: str = "",
    ) -> dict[str, str]:
        """Each subject's closes and bars from `first` to `last` from every
        product it is the base of; what was declined, by subject and reason."""
        await self.ensure_mapped()
        subjects = list(subjects)
        await self.name(subjects)
        today = utc_day(self.clock_ns() // NANOS_PER_SECOND)
        reach = self.market.session.limits.history_days
        declined: dict[str, str] = {}
        prices: list[Price] = []
        bars: list[Bar] = []
        for subject, products in self.products.for_subjects(subjects).items():
            if not products:
                declined[subject] = "not_covered"
                continue
            if first > today:
                declined[subject] = "source_silent"
                continue
            if reach and last < today - timedelta(days=reach - 1):
                declined[subject] = "beyond_history"
                continue
            found = 0
            for mapped in products:
                read = await self._asked(
                    self.market.candles, mapped.product, first, min(last, today)
                )
                for candle in read or []:
                    if candle.unread and candle.close is None:
                        self.counts.unread += 1
                    if data_type in ("", PRICE) and (row := self.close(mapped, candle)):
                        prices.append(row)
                        found += 1
                    if data_type in ("", BAR) and (bar := self.bar(mapped, candle)):
                        bars.append(bar)
                        found += 1
            if not found:
                declined[subject] = "source_silent"
        await self._send(prices, bars, want_id)
        return declined

    async def record_live(
        self, subjects: Iterable[str], kinds: Iterable[int] = (), want_id: str = ""
    ) -> dict[str, str]:
        """Each subject's last price and best bid and offer now, from every
        product it is the base of."""
        await self.ensure_mapped()
        subjects = list(subjects)
        await self.name(subjects)
        asked = set(kinds) or set(_LIVE_KINDS)
        declined: dict[str, str] = {}
        prices: list[Price] = []
        for subject, products in self.products.for_subjects(subjects).items():
            if not products:
                declined[subject] = "not_covered"
                continue
            before = len(prices)
            for mapped in products:
                if ops.PRICE_KIND_LAST in asked:
                    tick = await self._asked(self.market.last, mapped.product)
                    if tick and tick.price is not None:
                        prices.append(self._live(mapped, "last", tick.price, tick))
                    elif tick:
                        self.counts.unread += 1
                if asked & {ops.PRICE_KIND_BID, ops.PRICE_KIND_ASK}:
                    best = await self._asked(self.market.top, mapped.product)
                    if best and ops.PRICE_KIND_BID in asked and best.bid is not None:
                        prices.append(self._live(mapped, "bid", best.bid, best))
                    if best and ops.PRICE_KIND_ASK in asked and best.ask is not None:
                        prices.append(self._live(mapped, "ask", best.ask, best))
                    self.counts.unread += len(best.unread) if best else 0
            if len(prices) == before:
                declined[subject] = "source_silent"
        await self._send(prices, [], want_id)
        return declined

    # ── Wants ────────────────────────────────────────────────────────────

    async def _decline(self, want_id: str, declined: dict[str, str]) -> None:
        by_reason: dict[str, list[str]] = {}
        for subject, reason in declined.items():
            by_reason.setdefault(reason, []).append(subject)
        for reason, subjects in sorted(by_reason.items()):
            await self.plugin.decline_want(
                want_id=want_id,
                subjects=[ops.SubjectRef(entity_id=s) for s in subjects],
                reason=reason,
            )
            self.counts.declined += len(subjects)

    async def on_want(self, heard: meridian.Heard[ops.ObservationsWantedEvent]) -> None:
        """Record what the lake wants, or decline it per subject."""
        want = heard.message
        self.counts.wants += 1
        subjects = [s.entity_id for s in want.subjects]
        key = self._key_of(want.dataset)
        kinds = tuple(want.kinds)
        before = self.counts.recorded + self.counts.restated + self.counts.unchanged
        declined: dict[str, str] = {}
        asked = want.business_date or (
            f"{want.valid_from_ns}..{want.valid_until_ns}" if want.valid_from_ns else "latest"
        )
        try:
            declined = await self._answer(want, key, subjects, kinds)
        except Exception as failed:  # noqa: BLE001 - the venue or the lake refused; said, not raised
            log.warning("want %s not answered: %s", want.want_id, failed)
            declined = {s: "source_silent" for s in subjects}
        await self._decline(want.want_id, declined)
        after = self.counts.recorded + self.counts.restated + self.counts.unchanged
        self.heard.appendleft(
            Heard(want.want_id, want.dataset, asked, len(subjects), after - before,
                  len(declined), want.standing, self.clock_ns())
        )  # fmt: skip

    async def _answer(
        self,
        want: ops.ObservationsWantedEvent,
        key: str,
        subjects: list[str],
        kinds: tuple[int, ...],
    ) -> dict[str, str]:
        served = _DAILY_KINDS if key == DAILY else _LIVE_KINDS if key == LIVE else set()
        if (
            not served
            or want.data_type not in ("", PRICE, BAR)
            or (want.data_type == BAR and key != DAILY)
            or (kinds and not set(kinds) & served)
        ):
            return {s: "not_covered" for s in subjects}
        if want.standing:
            self.standing[want.want_id] = Standing(
                want.want_id, want.dataset, want.data_type, tuple(subjects), kinds,
                self.clock_ns(),
            )  # fmt: skip
        if key == LIVE:
            return await self.record_live(subjects, kinds, want.want_id)
        if want.business_date:
            day = date.fromisoformat(want.business_date)
            return await self.record_days(subjects, day, day, want.data_type, want.want_id)
        if want.valid_from_ns:
            until = want.valid_until_ns or self.clock_ns()
            first = utc_day(want.valid_from_ns // NANOS_PER_SECOND)
            last = utc_day(max(until - 1, want.valid_from_ns) // NANOS_PER_SECOND)
            return await self.record_days(subjects, first, last, want.data_type, want.want_id)
        # The latest: the days of the past week the venue has, the forming one
        # among them, so a subject the venue still trades is never declined
        # for a day that has barely begun (a decline stands a day).
        today = utc_day(self.clock_ns() // NANOS_PER_SECOND)
        first = today - timedelta(days=LATEST_DAYS - 1)
        return await self.record_days(subjects, first, today, want.data_type, want.want_id)

    async def on_withdrawn(self, heard: meridian.Heard[ops.WantWithdrawnEvent]) -> None:
        """A standing want withdrawn: its subjects are no longer kept current."""
        self.standing.pop(heard.message.want_id, None)

    # ── Keeping current ──────────────────────────────────────────────────

    async def current(self, daily: bool = False) -> None:
        """One pass keeping each standing want's subjects current: the live
        dataset's, and with `daily` the daily dataset's forming day."""
        live = [w for w in self.standing.values() if self._key_of(w.dataset) == LIVE]
        for subjects, kinds in _grouped(live):
            await self.record_live(subjects, kinds)
        if daily:
            today = utc_day(self.clock_ns() // NANOS_PER_SECOND)
            for want in [w for w in self.standing.values() if self._key_of(w.dataset) == DAILY]:
                await self.record_days(want.subjects, today - timedelta(days=1), today,
                                       want.data_type)  # fmt: skip

    async def keep_current(self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        """Keep each standing want's subjects current, until cancelled: the live
        dataset at most once a second, the daily one's forming day every five
        minutes; the product map read again once a day."""
        last_daily = 0.0
        last_map = time.monotonic()
        while True:
            now = time.monotonic()
            try:
                if now - last_map >= REMAP_SECONDS:
                    await self.map_products()
                    last_map = now
                due = now - last_daily >= DAILY_EVERY_SECONDS
                await self.current(daily=due)
                if due:
                    last_daily = now
            except Exception as failed:  # noqa: BLE001 - said, and tried again next time
                log.warning("keeping current stopped this time: %s", failed)
            await sleep(LIVE_EVERY_SECONDS)


def _grouped(wants: Iterable[Standing]) -> list[tuple[list[str], tuple[int, ...]]]:
    """The live standing wants' subjects, once each, by the kinds asked."""
    by_kinds: dict[tuple[int, ...], set[str]] = {}
    for want in wants:
        by_kinds.setdefault(tuple(sorted(want.kinds)), set()).update(want.subjects)
    return [(sorted(subjects), kinds) for kinds, subjects in sorted(by_kinds.items())]


async def cancelled(task: asyncio.Task[Any]) -> None:
    """Cancel a task and wait for it."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
