"""Kraken's public market data: the one module that speaks to it.

REST at api.kraken.com/0/public/ (`Time`, `SystemStatus`, `Assets`,
`AssetPairs`, `OHLC`, `Ticker`, `Depth`, `Trades`) and WebSocket v2 at
ws.kraken.com/v2 (`ticker` and `trade`, public). No key: nothing here holds a
credential.

**Rate limits, honoured.** Kraken limits public REST by IP, and `OHLC` and
`Trades` by pair too; about one call a second stays inside, which is what
this client's token bucket spends. Kraken says it is being asked too often in
its `error` array (`EGeneral:Too many requests`, `EAPI:Rate limit exceeded`),
under a 200, or with a 429: either is waited out (2, 4, 8 seconds) and asked
again at most three times, each one counted. A WebSocket that drops is
reconnected after a growing pause, a few times at most, well inside Kraken's
150 connections per 10 minutes per IP.

**Every response is a raw record first.** Each body is kept exactly as its
bytes arrived, with its status and the request that asked for it, before
anything is converted (raw.py). A body is parsed with `json_decimal`: Kraken
sends prices as strings, but its trade times and WebSocket v2's prices are
JSON numbers, which become Decimals from their own text and never a float.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from .raw import RawStore
from .rows import json_decimal

REST = "https://api.kraken.com/0/public"
WEBSOCKET = "wss://ws.kraken.com/v2"
USER_AGENT = "meridian-kraken/0.1.0 (+https://open-meridian.dev)"

#: About one call a second stays inside Kraken's public limits.
RATE_PER_SECOND = 1.0
BURST = 1
#: Kraken's most OHLC entries: the 720 most recent, whatever `since` says.
MAX_OHLC = 720
#: Kraken's words for being asked too often.
RATE_LIMITED = ("EGeneral:Too many requests", "EAPI:Rate limit exceeded")
KEPT_HEADERS = ("date", "cache-control", "age", "cf-cache-status", "retry-after", "content-type")


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[str, Mapping[str, str]], Response]


def urllib_transport(url: str, headers: Mapping[str, str]) -> Response:
    """One GET over HTTPS, with the standard library."""
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=15) as answer:
            return Response(
                answer.status, {k.lower(): v for k, v in answer.headers.items()}, answer.read()
            )
    except urllib.error.HTTPError as answer:
        return Response(
            answer.code, {k.lower(): v for k, v in answer.headers.items()}, answer.read()
        )


class RateLimiter:
    """A token bucket: `rate` calls a second, `burst` at once."""

    def __init__(
        self,
        rate: float,
        burst: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate, self.burst = rate, burst
        self._clock, self._sleep = clock, sleep
        self._tokens = float(burst)
        self._at = clock()
        self.waited = 0.0

    def acquire(self) -> None:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._at) * self.rate)
        self._at = now
        if self._tokens < 1:
            wait = (1 - self._tokens) / self.rate
            self._sleep(wait)
            self.waited += wait
            self._at = self._clock()
            self._tokens = 1.0
        self._tokens -= 1


class VenueError(Exception):
    """Kraken answered with an error, or with something other than asked for."""

    def __init__(self, call: str, status: int, detail: str, raw_key: str) -> None:
        super().__init__(f"{call}: {status} {detail} (raw record {raw_key})")
        self.call, self.status, self.detail, self.raw_key = call, status, detail, raw_key


@dataclass(frozen=True)
class Fetched:
    """A parsed `result`, the key of the raw record it was kept as, and when
    it arrived."""

    value: Any
    raw_key: str
    received_at_ns: int


class WebSocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...


Connector = Callable[[str], AbstractAsyncContextManager[WebSocket]]


def websockets_connector(url: str) -> AbstractAsyncContextManager[WebSocket]:
    from websockets.asyncio.client import connect

    return connect(url, open_timeout=10, max_size=2**22, user_agent_header=USER_AGENT)


class Kraken:
    """Kraken's public market data, every response kept."""

    def __init__(
        self,
        store: RawStore,
        transport: Transport = urllib_transport,
        limiter: RateLimiter | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
    ) -> None:
        self.store, self.transport = store, transport
        self.limiter = limiter or RateLimiter(RATE_PER_SECOND, BURST)
        self.clock_ns, self.sleep, self.retries = clock_ns, sleep, retries
        self.calls = 0
        self.rate_limited = 0
        self.reconnected = 0
        self._kept = 0

    def _keep(self, key: str, record: dict[str, Any]) -> str:
        self._kept += 1
        return self.store.keep(f"{key}/{record['received_at_ns']}-{self._kept}", record)

    def _get(self, call: str, key: str, params: Mapping[str, str] | None = None) -> Fetched:
        url = f"{REST}/{call}" + (("?" + urllib.parse.urlencode(params)) if params else "")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        for attempt in range(self.retries + 1):
            self.limiter.acquire()
            self.calls += 1
            answer = self.transport(url, headers)
            received = self.clock_ns()
            text = answer.body.decode("utf-8", errors="replace")
            raw_key = self._keep(
                key,
                {
                    "venue": "kraken",
                    "call": call,
                    "request": {"method": "GET", "url": url},
                    "received_at_ns": received,
                    "status": answer.status,
                    "headers": {h: answer.headers[h] for h in KEPT_HEADERS if h in answer.headers},
                    "body": text,
                },
            )
            try:
                parsed = json_decimal(text)
            except json.JSONDecodeError:
                parsed = None
            errors = (
                [str(e) for e in (parsed or {}).get("error", [])]
                if isinstance(parsed, dict)
                else []
            )
            limited = answer.status == 429 or any(e.startswith(RATE_LIMITED) for e in errors)
            if limited and attempt < self.retries:
                self.rate_limited += 1
                self.sleep(2.0 ** (attempt + 1))
                continue
            if answer.status != 200 or errors or not isinstance(parsed, dict):
                detail = "; ".join(errors) or text[:200]
                raise VenueError(call, answer.status, detail, raw_key)
            return Fetched(parsed.get("result"), raw_key, received)
        raise AssertionError("unreachable")

    def time(self) -> Fetched:
        return self._get("Time", "time")

    def system_status(self) -> Fetched:
        return self._get("SystemStatus", "status")

    def assets(self) -> Fetched:
        return self._get("Assets", "assets")

    def asset_pairs(self) -> Fetched:
        return self._get("AssetPairs", "pairs")

    def ohlc(self, pair: str, interval: int, since_s: int) -> Fetched:
        """OHLC entries from `since_s`, `interval` minutes each: Kraken gives
        the 720 most recent at most, the last one the interval still forming."""
        return self._get(
            "OHLC",
            f"ohlc/{pair}/{interval}",
            {"pair": pair, "interval": str(interval), "since": str(since_s)},
        )

    def ticker(self, pair: str) -> Fetched:
        return self._get("Ticker", f"ticker/{pair}", {"pair": pair})

    def depth(self, pair: str, count: int = 1) -> Fetched:
        """The best `count` bids and asks, each with its exact volume and time."""
        return self._get("Depth", f"depth/{pair}", {"pair": pair, "count": str(count)})

    def trades(self, pair: str, count: int = 100) -> Fetched:
        return self._get("Trades", f"trades/{pair}", {"pair": pair, "count": str(count)})

    async def stream(
        self,
        symbols: Iterable[str],
        seconds: float,
        channels: Iterable[str] = ("ticker", "trade"),
        connector: Connector = websockets_connector,
        reconnects: int = 3,
        pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> AsyncIterator[tuple[Any, str, int]]:
        """WebSocket v2's messages for `seconds`: each parsed, kept as the next
        line of this stream's raw record, and yielded with its reference key
        and when it arrived. A dropped connection is opened again, after 1, 2,
        4 seconds, at most `reconnects` times."""
        wanted = list(symbols)
        subscribe = [
            json.dumps({"method": "subscribe", "params": {"channel": channel, "symbol": wanted}})
            for channel in channels
        ]
        key = f"stream/{self.clock_ns()}"
        deadline = time.monotonic() + seconds
        attempt = 0
        while time.monotonic() < deadline:
            try:
                async with connector(WEBSOCKET) as socket:
                    for message in subscribe:
                        await socket.send(message)
                    while (left := deadline - time.monotonic()) > 0:
                        try:
                            text = await asyncio.wait_for(socket.recv(), timeout=left)
                        except TimeoutError:
                            return
                        received = self.clock_ns()
                        if isinstance(text, bytes):
                            text = text.decode("utf-8")
                        index = self.store.append(key, {"received_at_ns": received, "text": text})
                        yield json_decimal(text), f"{key}#{index}", received
                    return
            except Exception as dropped:
                if not _closed(dropped) or attempt >= reconnects:
                    raise
            await pause(2.0**attempt)
            attempt += 1
            self.reconnected += 1


def _closed(error: BaseException) -> bool:
    """A connection that dropped, which is worth opening again."""
    closed = type(error).__name__ in {"ConnectionClosed", "ConnectionClosedError"}
    return closed or isinstance(error, OSError | ConnectionError)


def result_for(result: Mapping[str, Any], pair: str) -> Any:
    """The one pair's entry in a `result`: Kraken keys it by the pair's own
    key (`XXBTZUSD`) even when asked by its altname (`XBTUSD`), beside `last`."""
    entries = {k: v for k, v in result.items() if k != "last"}
    if pair in entries:
        return entries[pair]
    if len(entries) == 1:
        return next(iter(entries.values()))
    raise KeyError(f"Kraken's result names {sorted(entries)}, not {pair!r}")
