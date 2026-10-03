"""`python -m kraken sample --product XBT/USD`: converted rows, for a person to
eyeball.

It asks Kraken's public endpoints once each, about a second apart (asset
pairs and assets for the pair map, the daily OHLC, the ticker, the depth's
best bid and ask, recent trades; with `--stream N`, N seconds of WebSocket
v2), keeps every response as a raw record under `--raw-dir` (default
`recordings/`, which git ignores: real recordings stay local), and prints
each row it converted as one JSON line, every number its exact decimal text.
A pair is found by any of Kraken's names for it: `XXBTZUSD`, `XBTUSD`,
`XBT/USD` or `BTC/USD`. Nothing is recorded anywhere else: there is no lake
to record into yet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from . import assets, convert
from .raw import RawStore, storage_root
from .rows import NANOS_PER_DAY, NANOS_PER_SECOND, Bar, Price, Product, Quote, Row, Trade, as_dict
from .venue import Kraken, result_for


class Session:
    """Fetch and convert, one call per step: what the CLI, the live smoke
    test and the conformance adapter share."""

    def __init__(self, client: Kraken) -> None:
        self.client = client
        self._pairs: assets.Pairs | None = None

    def pairs(self) -> assets.Pairs:
        if self._pairs is None:
            listed = self.client.asset_pairs().value
            known = self.client.assets().value
            self._pairs = assets.Pairs(listed, known)
        return self._pairs

    def product(self, name: str) -> Product:
        return self.pairs().find(name)

    def daily(self, product: Product, days: int, now_ns: int) -> tuple[list[Price], list[Bar]]:
        today = now_ns - now_ns % NANOS_PER_DAY
        since = (today - (days - 1) * NANOS_PER_DAY) // NANOS_PER_SECOND
        pair = product.product.value
        fetched = self.client.ohlc(pair, convert.DAY_MINUTES, since)
        entries = [e for e in result_for(fetched.value, pair) if int(e[0]) >= since]
        return convert.daily(product, entries, fetched.raw_key, now_ns)

    def last(self, product: Product) -> Price:
        pair = product.product.value
        fetched = self.client.ticker(pair)
        ticker = result_for(fetched.value, pair)
        return convert.last(product, ticker, fetched.raw_key, fetched.received_at_ns)

    def quote(self, product: Product) -> Quote:
        pair = product.product.value
        fetched = self.client.depth(pair)
        return convert.quote(product, result_for(fetched.value, pair), fetched.raw_key)

    def trades(self, product: Product, count: int = 100) -> list[Trade]:
        pair = product.product.value
        fetched = self.client.trades(pair, count)
        return convert.trades(product, result_for(fetched.value, pair), fetched.raw_key)

    async def stream(
        self, products: Sequence[Product], seconds: float, **kwargs: object
    ) -> list[Row]:
        websocket = self.pairs().websocket
        by_symbol = {websocket[p.product.value]: p for p in products}
        rows: list[Row] = []
        async for item, ref, received in self.client.stream(list(by_symbol), seconds, **kwargs):  # type: ignore[arg-type]
            rows.extend(convert.message(by_symbol, item, ref, received))
        return rows


def _print(rows: Sequence[Row]) -> None:
    for row in rows:
        print(json.dumps({"row": type(row).__name__, **as_dict(row)}, sort_keys=False))


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m kraken sample")
    parser.add_argument("--product", default="XBT/USD", help="any of Kraken's names for a pair")
    parser.add_argument("--days", type=int, default=3, help="daily entries, today included")
    parser.add_argument("--trades", type=int, default=5, help="recent trades")
    parser.add_argument("--stream", type=float, default=0, help="seconds of WebSocket v2")
    parser.add_argument("--raw-dir", type=Path, default=Path("recordings"))
    args = parser.parse_args(argv)

    store = RawStore(storage_root(args.raw_dir))
    session = Session(Kraken(store))
    product = session.product(args.product)
    print(f"# {args.product}: {as_dict(product)}", file=sys.stderr)
    closes, bars = session.daily(product, args.days, time.time_ns())
    _print(closes)
    _print(bars)
    _print([session.last(product), session.quote(product)])
    _print(session.trades(product, args.trades))
    if args.stream:
        _print(asyncio.run(session.stream([product], args.stream)))
    print(
        f"# {session.client.calls} calls, {session.client.rate_limited} rate-limited, "
        f"raw records in {store.root}",
        file=sys.stderr,
    )
    return 0
