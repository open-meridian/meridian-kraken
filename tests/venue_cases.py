"""Kraken's side of the dgm suite: this plugin's synthetic exchanges, named for
what each of the suite's cases asks a source to present.

tests/test_suite.py, the same file in meridian-coinbase, runs the SDK's `dgm`
suite unchanged against each plugin's own conversion; this module is the one
place it learns which venue it is holding. Each answer comes through the
plugin's real client, raw records and reading (synthetic.py's transport over
the exchanges this package carries, `src/kraken/synthetic/`), with invented
values; real recordings are never committed (Kraken asks its permission for
non-personal use; the plan's Q11).
"""

from __future__ import annotations

import copy
import json
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from kraken import page, raw, synthetic
from kraken.convert import KrakenMarket
from kraken.dgm import Dgm, Standing
from kraken.market import Market
from kraken.products import ProductMap
from kraken.raw import RawStore
from kraken.synthetic import Synthetic
from kraken.venue import Kraken, RateLimiter

__all__ = ["Dgm", "ProductMap", "Standing", "market", "page", "raw", "synthetic"]

SYNTHETIC = Path(__file__).parent.parent / "src" / "kraken" / "synthetic"

#: The venue's name as a symbol's source, and the plugin's.
VENUE = "kraken"
#: The suite's clock: 2026-10-10 12:00 UTC, the day still forming.
NOW_NS = 1_791_633_600 * 1_000_000_000
TODAY = date(2026, 10, 10)
#: A day whose candles are complete.
CLOSED = date(2026, 10, 8)
#: A base asset priced in both a currency and a stablecoin: Kraken's XXBT
#: (XXBTZUSD, and XBTUSDC on Kraken's own USDC instrument).
BASE = "XXBT"
STABLE_BASE = "XXBT"
#: A base whose close has more decimal places than a float holds (TKNUSD).
EXACT_BASE = "TKN"
#: A base whose day traded nothing (count 0), so states no volume-weighted
#: price (QDTUSD).
BAR_BASE = "QDT"
#: A currency priced in another: the euro in dollars (ZEURZUSD).
FX_BASE = "ZEUR"
#: An asset whose identifiers the suite answers as meeting two records.
AMBIGUOUS = "PLY"
#: The forming day's entry, as read twice while it fills.
FORMING_PRODUCT = "XXBTZUSD"
FORMING = [
    [1791590400, "61988.9", "62200.0", "61900.0", "62010.3", "62050.1", "120.50000000", 4100],
    [1791590400, "61988.9", "62300.0", "61850.0", "62155.8", "62100.4", "240.25000000", 8200],
]


def _instant(_: float) -> None:
    return None


def market(now_ns: int = NOW_NS, forming: int | None = None) -> Market:
    """Kraken's synthetic exchange through the plugin's own client, its answers
    kept in a fresh store; `forming` adds the forming day's entry, as read the
    first or second time."""
    directory = SYNTHETIC
    if forming is not None:
        held = Path(tempfile.mkdtemp(prefix="kraken-synthetic-"))
        for name in ("pairs", "assets", "ticker", "depth"):
            (held / f"{name}.json").write_text((SYNTHETIC / f"{name}.json").read_text())
        entries: dict[str, Any] = json.loads((SYNTHETIC / "ohlc.json").read_text())
        entries = copy.deepcopy(entries)
        entries[FORMING_PRODUCT].append(FORMING[forming])
        (held / "ohlc.json").write_text(json.dumps(entries))
        directory = held
    store = RawStore(Path(tempfile.mkdtemp(prefix="kraken-raw-")))
    client = Kraken(
        store,
        transport=Synthetic(directory),
        limiter=RateLimiter(1000.0, 1000, sleep=_instant),
        clock_ns=lambda: now_ns,
        sleep=_instant,
    )
    return KrakenMarket(client, "synthetic")


#: The last trade's price each of BASE's products states now, as its text.
LIVE_LAST = {"XXBTZUSD": "61988.90000", "XBTUSDC": "62006.75"}
#: The product quoted in a stablecoin, and that stablecoin's code.
STABLE_PRODUCT, STABLE = "XBTUSDC", "USDC"
#: A product the venue lists and no longer trades.
DELISTED = "OLDUSD"
#: The close BASE's first product states for CLOSED, as its text.
CLOSED_CLOSE = ("XXBTZUSD", "62001.40000")
#: The close EXACT_BASE states for CLOSED, as its text.
EXACT_CLOSE = "0.000012345678901234"
