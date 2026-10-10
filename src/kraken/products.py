"""The product map, kept in the plugin's own storage (the plan's "Identifiers
for a pair"): each product the venue lists, its base and quote assets, and the
deployment's instrument each asset resolved to.

The same module in meridian-coinbase and meridian-kraken.

A product names a market, not an asset, and never becomes a key: the base
asset is a row's subject, the quote asset what its price is in. Each asset is
resolved through W3.1 by the one global identifier the venue's own statements
give it -- `iso4217` for a currency, `caip19` for a token whose contract the
venue states -- with its code as a `symbol` whose source is the venue; a
token with no CAIP-19 by its code alone, as the venue's own instrument. An
asset that does not resolve -- its identifiers meet two of the deployment's
records -- is reported through W3.2, once, and every product on it is left
out until it does.

Wants name the deployment's instruments, never the venue's codes, so the
map is what turns a want's subject back into the products to ask the venue
for. It is the plugin's to rebuild: kept as `products-<mode>.json` beside the
raw records, read at start, and made again from the venue's listing once a
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
    """A product both of whose assets resolved."""

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
        #: The deployment's instrument for each asset, by the venue's code.
        self.ids: dict[str, str] = {}
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
            )
            for item in held.get("listed", [])
        ]
        self.ids = dict(held.get("ids", {}))
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

    def mapped(self) -> list[Mapped]:
        """Every product the venue trades whose two assets resolved."""
        return [
            Mapped(item.product, item.base, item.quote, self.ids[item.base.code],
                   self.ids[item.quote.code])
            for item in self.listed
            if item.tradable and item.base.code in self.ids and item.quote.code in self.ids
        ]  # fmt: skip

    def for_subjects(self, subjects: Iterable[str]) -> dict[str, list[Mapped]]:
        """The products each subject is the base asset of, by subject; a
        subject the map holds none for is given an empty list."""
        wanted = list(subjects)
        found: dict[str, list[Mapped]] = {subject: [] for subject in wanted}
        for mapped in self.mapped():
            if mapped.base_id in found:
                found[mapped.base_id].append(mapped)
        return found

    def product(self, product: str) -> Mapped | None:
        for mapped in self.mapped():
            if mapped.product == product:
                return mapped
        return None

    def code_of(self, instrument_id: str) -> str:
        """The venue's code an instrument was resolved from; empty if none."""
        for code, held in self.ids.items():
            if held == instrument_id:
                return code
        return ""
