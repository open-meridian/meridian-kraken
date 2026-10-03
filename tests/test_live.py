"""The live smoke test: Kraken's real public endpoints, converted.

Opt in only: `make live` runs `pytest --live`, and CI never does. It asks
each endpoint once, a second apart (Kraken's public rate limit), checks the
conversion holds on real data -- every row converts with nothing as reported,
every number exact, the daily closes on consecutive UTC days with only the
forming day unpublished, bid below ask, every trade's aggressor known -- and
writes the real responses under recordings/, which git ignores (the plan's
Q11). What it saw is printed, and kept as recordings/<run>/summary.json.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from kraken import assets
from kraken.raw import RawStore
from kraken.rows import NANOS_PER_DAY, Bar, Price, Quote, Row, Trade, utc_date
from kraken.sample import Session
from kraken.venue import Kraken

pytestmark = pytest.mark.live

RECORDINGS = Path(__file__).resolve().parent.parent / "recordings"
PRODUCT = "XBT/USD"
BITCOIN = "bip122:000000000019d6689c085ae165831e93/slip44:0"


def scale(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int)
    return max(0, -exponent)


@pytest.fixture(scope="module")
def run() -> Iterator[tuple[Session, dict[str, Any]]]:
    root = RECORDINGS / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    session = Session(Kraken(RawStore(root)))
    summary: dict[str, Any] = {"venue": "kraken", "recordings": str(root)}
    yield session, summary
    summary["calls"] = session.client.calls
    summary["rate_limited"] = session.client.rate_limited
    summary["reconnected"] = session.client.reconnected
    summary["limiter_waited_s"] = round(session.client.limiter.waited, 3)
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print("\nLIVE SUMMARY " + json.dumps(summary, indent=1, default=str))


def _clean(rows: list[Any]) -> None:
    for row in rows:
        assert row.meta.unconverted == (), (row.meta.row_key, row.meta.unconverted)


def test_pairs_map_to_their_assets(run: tuple[Session, dict[str, Any]]) -> None:
    session, summary = run
    pairs = session.pairs()
    products = pairs.products
    btc = session.product(PRODUCT)
    assert btc.product.value == "XXBTZUSD"
    assert btc.base.identifier is not None and btc.base.identifier.value == BITCOIN
    assert btc.quote.identifier is not None and btc.quote.identifier.value == "USD"
    assert pairs.websocket["XXBTZUSD"] == "BTC/USD"
    known = session.client.assets().value
    statuses = Counter(p.status for p in products.values())
    quotes = Counter(p.quote.venue_code.value for p in products.values())
    pair_raw = session.client.asset_pairs().value
    summary["pairs"] = {
        "listed": len(products),
        "statuses": dict(statuses),
        "tradable": sum(p.tradable for p in products.values()),
        "quote_assets": dict(quotes.most_common(10)),
        "assets": len(known),
        "fiat_codes_present": sorted(c for c in assets.FIAT if c in known),
        "fiat_codes_absent": sorted(c for c in assets.FIAT if c not in known),
        "asset_classes": dict(Counter(str(a.get("aclass")) for a in known.values())),
        "pair_base_classes": dict(Counter(str(p.get("aclass_base")) for p in pair_raw.values())),
        "execution_venues": dict(Counter(str(p.get("execution_venue")) for p in pair_raw.values())),
        "x_or_z_prefixed_assets": sorted(c for c in known if len(c) == 4 and c[0] in "XZ")[:20],
        "usdc_quoted": sorted(k for k, v in products.items() if v.quote.venue_code.value == "USDC")[
            :10
        ],
    }


def test_daily_closes_and_bars(run: tuple[Session, dict[str, Any]]) -> None:
    session, summary = run
    product = session.product(PRODUCT)
    now = time.time_ns()
    closes, bars = session.daily(product, 5, now)
    _clean(closes + bars)
    assert len(closes) == len(bars) == 5
    dates = [c.meta.business_date for c in closes]
    assert dates[-1] == utc_date(now)
    for earlier, later in zip(closes, closes[1:], strict=False):
        assert later.meta.valid_from_ns - earlier.meta.valid_from_ns == NANOS_PER_DAY
    assert [bool(c.meta.source_times) for c in closes] == [True] * 4 + [False]
    assert all(b.vwap is not None and b.trade_count is not None for b in bars)
    summary["daily"] = {
        "dates": dates,
        "closes": [str(c.price.amount) for c in closes if c.price],
        "close_scales": sorted({scale(c.price.amount) for c in closes if c.price}),
        "volume_scales": sorted({scale(b.volume) for b in bars if b.volume is not None}),
    }


def test_last_quote_and_trades(run: tuple[Session, dict[str, Any]]) -> None:
    session, summary = run
    product = session.product(PRODUCT)
    last, quote, trades = (
        session.last(product),
        session.quote(product),
        session.trades(product, 100),
    )
    _clean([last, quote, *trades])
    assert last.price is not None and last.price.amount > 0
    assert quote.bid is not None and quote.ask is not None and quote.bid.amount < quote.ask.amount
    assert trades and all(t.aggressor in ("buy", "sell") for t in trades)
    times = [t.meta.valid_from_ns for t in trades]
    assert times == sorted(times)
    summary["live"] = {
        "last": str(last.price.amount),
        "bid": [str(quote.bid.amount), str(quote.bid_quantity)],
        "ask": [str(quote.ask.amount), str(quote.ask_quantity)],
        "trades": len(trades),
        "aggressors": dict(Counter(t.aggressor for t in trades)),
        "price_scales": sorted({scale(t.price.amount) for t in trades if t.price}),
        "size_scales": sorted({scale(t.quantity) for t in trades if t.quantity}),
    }


def test_the_feed(run: tuple[Session, dict[str, Any]]) -> None:
    session, summary = run
    rows: list[Row] = asyncio.run(session.stream([session.product(PRODUCT)], 10))
    _clean(rows)
    counts = Counter(type(r).__name__ for r in rows)
    assert counts["Price"] >= 1 and counts["Quote"] >= 1
    summary["stream"] = {"seconds": 10, "rows": dict(counts)}
    assert not [r for r in rows if not isinstance(r, Price | Quote | Trade | Bar)]
