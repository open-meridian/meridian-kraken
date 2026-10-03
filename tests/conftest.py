"""Kraken's synthetic exchanges, and the adapter the shared conformance suite
runs against.

tests/exchanges/ holds bodies shaped exactly as Kraken documents and sends
them (checked against its public endpoints on 2026-10-03), with invented
values: `{"error": [], "result": {...}}`, prices as strings, trade times as
JSON numbers of decimal seconds, WebSocket v2's prices as JSON numbers. TKN is
an invented token. Real recordings are never committed (the plan's Q11):
`make live` writes them under recordings/, which git ignores.

`Replay` answers the client's requests from those files, through the client's
real request, raw-record and parsing path; `Adapter` is what
tests/test_conformance.py, the same file in meridian-coinbase, asks for: the
suite's stage 3 expectations, held against this plugin's stage 1 and 2
exchanges (spec/vendor-differences-have-a-place-in-the-contract,
"Conformance and coverage").
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import pytest

from kraken import convert, rows
from kraken.raw import RawStore
from kraken.rows import Bar, DatasetDeclaration, Price, Product, Quote, RawRecordRef, Row, Trade
from kraken.sample import Session
from kraken.venue import Kraken, RateLimiter, Response

EXCHANGES = Path(__file__).parent / "exchanges"
#: The synthetic exchanges' now: 2026-10-03T12:00:00Z, the day still forming.
NOW_NS = 1_791_028_800 * 1_000_000_000

#: One clock for the whole run, so no two raw records share a key.
TICKS = itertools.count(NOW_NS, 1_000)

ROUTES = {
    "/AssetPairs": "asset-pairs.json",
    "/Assets": "assets.json",
    "/OHLC": "ohlc-daily.json",
    "/Ticker": "ticker.json",
    "/Depth": "depth.json",
    "/Trades": "trades.json",
    "/SystemStatus": "status.json",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--live", action="store_true", help="call Kraken's real endpoints")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    live = config.getoption("--live")
    for item in items:
        if ("live" in item.keywords) != live:
            item.add_marker(pytest.mark.skip(reason="make live" if not live else "not live"))


class Replay:
    """A transport answering from the synthetic exchanges, by the path's end."""

    def __init__(
        self, routes: Mapping[str, str], statuses: Mapping[str, list[int | str]] | None = None
    ):
        self.routes = dict(routes)
        self.statuses = {k: list(v) for k, v in (statuses or {}).items()}
        self.asked: list[str] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> Response:
        self.asked.append(url)
        path = urlsplit(url).path
        for end, name in self.routes.items():
            if path.endswith(end):
                pending = self.statuses.get(end)
                if pending:
                    answer = pending.pop(0)
                    if isinstance(answer, str):  # Kraken's error, under a 200
                        return Response(200, {}, (EXCHANGES / answer).read_bytes())
                    return Response(answer, {}, b'{"error":["EService:Unavailable"]}')
                body = (EXCHANGES / name).read_bytes()
                return Response(200, {"content-type": "application/json"}, body)
        return Response(404, {}, b'{"error":["EGeneral:Unknown method"]}')


class Sleeps(list[float]):
    def __call__(self, seconds: float) -> None:
        self.append(seconds)


def client(
    root: Path, routes: Mapping[str, str] | None = None, **kwargs: Any
) -> tuple[Kraken, Replay]:
    """A client on the synthetic exchanges, its clock fixed at NOW_NS."""
    replay = Replay({**ROUTES, **(routes or {})}, kwargs.pop("statuses", None))
    return (
        Kraken(
            RawStore(root),
            transport=replay,
            limiter=kwargs.pop("limiter", RateLimiter(1000.0, 1000)),
            clock_ns=lambda: next(TICKS),
            sleep=kwargs.pop("sleep", Sleeps()),
            **kwargs,
        ),
        replay,
    )


class FakeSocket:
    """Kraken's WebSocket v2, replayed from tests/exchanges/stream.jsonl."""

    def __init__(self, lines: list[str]) -> None:
        self.lines, self.sent = list(lines), []  # type: list[str], list[str]

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self.lines:
            return self.lines.pop(0)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def replayed_feed(name: str = "stream.jsonl") -> tuple[Any, list[FakeSocket]]:
    sockets: list[FakeSocket] = []

    @asynccontextmanager
    async def connector(url: str) -> AsyncIterator[FakeSocket]:
        socket = FakeSocket((EXCHANGES / name).read_text().splitlines())
        sockets.append(socket)
        yield socket

    return connector, sockets


class Adapter:
    """What the shared conformance suite asks of a plugin: its rows, from its
    own synthetic exchanges, for the suite's venue-neutral scenarios."""

    plugin = "kraken"
    rows: ModuleType = rows
    #: The suite's pairs, as this venue names them (its v1 WebSocket names).
    PAIRS = {"BTC/USD": "XBT/USD", "BTC/USDC": "XBT/USDC", "ETH/USD": "ETH/USD"}

    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = RawStore(root)

    def _session(self, routes: Mapping[str, str] | None = None) -> Session:
        kraken, _ = client(self.root, routes)
        return Session(kraken)

    def product(self, pair: str) -> Product:
        return self._session().product(self.PAIRS[pair])

    def daily(self, scenario: str) -> tuple[list[Price], list[Bar]]:
        session = self._session({"/OHLC": f"ohlc-{scenario}.json"})
        return session.daily(session.product("XBT/USD"), 3, NOW_NS)

    def last(self) -> Price:
        session = self._session()
        return session.last(session.product("XBT/USD"))

    def quote(self) -> Quote:
        session = self._session()
        return session.quote(session.product("XBT/USD"))

    def trades(self, scenario: str = "trades") -> list[Trade]:
        session = self._session({"/Trades": f"{scenario}.json"})
        return session.trades(session.product("XBT/USD"))

    def stream(self) -> list[Row]:
        session = self._session()
        connector, _ = replayed_feed()
        product = session.product("XBT/USD")
        return asyncio.run(session.stream([product], 0.2, connector=connector))

    def datasets(self) -> tuple[DatasetDeclaration, ...]:
        return convert.CATALOGUE

    def raw(self, ref: RawRecordRef) -> dict[str, Any] | None:
        return self.store.get(ref.key)


@pytest.fixture
def conformance(tmp_path: Path) -> Adapter:
    return Adapter(tmp_path / "raw")


@pytest.fixture
def exchanges() -> Iterator[Path]:
    yield EXCHANGES


def body(name: str) -> Any:
    return rows.json_decimal((EXCHANGES / name).read_bytes())


def lines(name: str) -> list[Any]:
    return [json.loads(line) for line in (EXCHANGES / name).read_text().splitlines()]
