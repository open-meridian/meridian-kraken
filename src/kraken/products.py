"""The product map, kept in the plugin's own storage (the plan's "Identifiers
for a pair"): each product the venue lists, its base and quote assets, which
of the venue's assets each subject the lake asked for is, and the
deployment's instrument each quote asset resolved to.

The same module in meridian-coinbase and meridian-kraken.

A product names a market, not an asset, and never becomes a key: the base
asset is a row's subject, the quote asset what its price is in.

**A subject is the deployment's own record** (W3.6, a dgm's from contract
v18 as ruled 2026-10-10). The lake wants instruments by the deployment's ID,
most of them a custodian's records; the plugin reads each record and names
the venue's asset it is by its identifiers -- the venue's own code, the one
global identifier the venue states (`iso4217`, `caip19`), or another
source's symbol that is the venue's code -- and keeps which asset each
subject is (`subjects`). Nothing is resolved for an asset no subject is, so
no record is minted for every asset the venue lists.

**A quote asset** is resolved through W3.1 by the identifiers the venue's
own statements give it -- `iso4217` for a currency, `caip19` for a token
whose contract the venue states -- with its code as a `symbol` whose source
is the venue; a token with no CAIP-19 by its code alone, as the venue's own
instrument. Only a product serving a subject has its quote resolved, and a
record is minted only where none resolves. An asset that does not resolve --
its identifiers meet two of the deployment's records -- is reported through
W3.2, once, and every product quoted in it is left out until it does.

It is the plugin's to rebuild: kept as `products-<mode>.json` beside the raw
records, read at start, and the listing read again from the venue once a
day. Nothing else reads it (decisions/028).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .market import Asset, Listed


@dataclass(frozen=True)
class Mapped:
    """A product serving a subject, its quote asset resolved."""

    product: str
    base: Asset
    quote: Asset
    base_id: str
    quote_id: str


class ProductMap:
    """The venue's listing and what each asset resolved to."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.listed: list[Listed] = []
        #: The deployment's instrument for each quote asset resolved, by the
        #: venue's code.
        self.ids: dict[str, str] = {}
        #: Which of the venue's assets each subject is, by the deployment's
        #: instrument ID, read from its record.
        self.subjects: dict[str, str] = {}
        #: Each asset reported as missing, by the venue's code, and why.
        self.missing: dict[str, str] = {}
        #: When the listing was last read, in UTC nanoseconds.
        self.read_at_ns = 0

    # ── Kept ─────────────────────────────────────────────────────────────

    def load(self) -> bool:
        """Read the map kept in storage; whether there was one."""
        if self.path is None or not self.path.is_file():
            return False
        held: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        self.listed = [
            Listed(
                product=item["product"],
                base=Asset(**item["base"]),
                quote=Asset(**item["quote"]),
                tradable=item["tradable"],
                status=item["status"],
                stream=item.get("stream", ""),
            )
            for item in held.get("listed", [])
        ]
        self.ids = dict(held.get("ids", {}))
        self.subjects = dict(held.get("subjects", {}))
        self.missing = dict(held.get("missing", {}))
        self.read_at_ns = int(held.get("read_at_ns", 0))
        return True

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "listed": [asdict(item) for item in self.listed],
            "ids": self.ids,
            "subjects": self.subjects,
            "missing": self.missing,
            "read_at_ns": self.read_at_ns,
        }
        written = self.path.with_suffix(".tmp")
        written.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(written, self.path)

    # ── Read ─────────────────────────────────────────────────────────────

    def assets(self) -> list[Asset]:
        """Each asset of a product the venue trades, once, by its code."""
        seen: dict[str, Asset] = {}
        for item in self.listed:
            if item.tradable:
                seen.setdefault(item.base.code, item.base)
                seen.setdefault(item.quote.code, item.quote)
        return [seen[code] for code in sorted(seen)]

    def asset(self, code: str) -> Asset | None:
        """The asset of a product the venue trades, by its code."""
        return next((a for a in self.assets() if a.code == code), None)

    def quotes(self, code: str) -> list[Asset]:
        """The quote assets of each product the venue trades on `code`, once."""
        seen: dict[str, Asset] = {}
        for item in self.listed:
            if item.tradable and item.base.code == code:
                seen.setdefault(item.quote.code, item.quote)
        return [seen[c] for c in sorted(seen)]

    def _serving(self, subject: str) -> list[Mapped]:
        code = self.subjects.get(subject, "")
        return [
            Mapped(item.product, item.base, item.quote, subject, self.ids[item.quote.code])
            for item in self.listed
            if code and item.tradable and item.base.code == code and item.quote.code in self.ids
        ]

    def mapped(self) -> list[Mapped]:
        """Every product the venue trades serving a subject, its quote resolved;
        each once, for the first subject it serves."""
        seen: dict[str, Mapped] = {}
        for subject in sorted(self.subjects):
            for mapped in self._serving(subject):
                seen.setdefault(mapped.product, mapped)
        return [seen[p] for p in sorted(seen)]

    def for_subjects(self, subjects: Iterable[str]) -> dict[str, list[Mapped]]:
        """The products each subject is the base asset of, by subject; a
        subject the map holds none for is given an empty list."""
        return {subject: self._serving(subject) for subject in subjects}

    def stream_name(self, product: str) -> str:
        """A product's name on the venue's stream."""
        item = next((i for i in self.listed if i.product == product), None)
        return item.stream_name if item else product

    def product(self, product: str) -> Mapped | None:
        for mapped in self.mapped():
            if mapped.product == product:
                return mapped
        return None

    def code_of(self, instrument_id: str) -> str:
        """The venue's code a subject is, or a quote asset was resolved from;
        empty if none."""
        if instrument_id in self.subjects:
            return self.subjects[instrument_id]
        for code, held in self.ids.items():
            if held == instrument_id:
                return code
        return ""
