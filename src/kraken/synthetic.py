"""Synthetic mode: Kraken's answers from files, never from Kraken.

A developer's setting (`synthetic`, shown only on a development deployment)
turns it on. The client asks exactly as it asks Kraken, keeps each answer as a
raw record and reads it the same way; only the transport differs:

- `Synthetic`: the synthetic exchanges this package carries (`synthetic/`),
  shaped exactly as Kraken documents and sends its answers -- every figure a
  string, refusals under HTTP 200 in an `error` list -- with invented values,
  and an invented token (TKN). They are what CI and `make e2e` replay.
- `Recorded`: a developer's own recorded exchanges -- the raw records a plugin
  kept of Kraken's real answers, recorded under personal use -- replayed by
  `make e2e-recorded` from a directory that is never committed (Kraken's
  terms ask its permission for non-personal use; the plan's Q11).
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any

from .venue import Response

_HEADERS = {"cache-control": "no-store"}


def _answer(result: Any = None, error: list[str] | None = None) -> Response:
    body = {"error": error or [], **({"result": result} if result is not None else {})}
    return Response(200, dict(_HEADERS), json.dumps(body, default=str).encode("utf-8"))


def _unknown() -> Response:
    return _answer(error=["EQuery:Unknown asset pair"])


def _route(url: str) -> tuple[str, dict[str, str]]:
    split = urllib.parse.urlsplit(url)
    return split.path.rstrip("/").rsplit("/", 1)[-1], dict(urllib.parse.parse_qsl(split.query))


def _ohlc(pair: str, entries: list[list[Any]], since: int) -> Response:
    after = sorted((e for e in entries if int(e[0]) > since), key=lambda e: int(e[0]))
    last = int(after[-1][0]) if after else since
    return _answer({pair: after, "last": last})


class Synthetic:
    """Answers from a directory of synthetic exchanges: `pairs.json` and
    `assets.json` as Kraken's results, and `ohlc.json`, `ticker.json` and
    `depth.json` each a result per pair."""

    def __init__(self, directory: Path | None = None) -> None:
        root = directory or Path(str(resources.files("kraken") / "synthetic"))
        self.held = {
            name: json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
            for name in ("pairs", "assets", "ohlc", "ticker", "depth")
        }

    def __call__(self, url: str, headers: Mapping[str, str]) -> Response:
        call, query = _route(url)
        pair = query.get("pair", "")
        if call == "AssetPairs":
            return _answer(self.held["pairs"])
        if call == "Assets":
            return _answer(self.held["assets"])
        if call == "OHLC":
            entries = self.held["ohlc"].get(pair)
            return _unknown() if entries is None else _ohlc(pair, entries, int(query["since"]))
        if call in ("Ticker", "Depth"):
            held = self.held[call.lower()].get(pair)
            return _unknown() if held is None else _answer({pair: held})
        return _answer(error=[f"EGeneral:Unknown method {call}"])


class Recorded:
    """Answers from a developer's recorded raw records (raw.py's files under a
    `responses/` directory): the latest good answer kept for each call and
    pair, and for OHLC every entry kept for the pair, after the `since`
    asked."""

    def __init__(self, directory: Path) -> None:
        self.latest: dict[tuple[str, str], Any] = {}
        self.entries: dict[str, dict[int, list[Any]]] = {}
        for path in sorted(directory.rglob("*")):
            if path.suffix == ".json":
                records = [json.loads(path.read_text(encoding="utf-8"))]
            elif path.suffix == ".jsonl":
                lines = path.read_text(encoding="utf-8").splitlines()
                records = [json.loads(line) for line in lines]
            else:
                continue
            for record in records:
                try:
                    body = json.loads(str(record["body"]), parse_float=Decimal)
                except (KeyError, ValueError):
                    continue
                if record.get("status") != 200 or body.get("error"):
                    continue
                call, query = _route(str(record["request"]["url"]))
                pair = query.get("pair", "")
                result = body.get("result") or {}
                if call == "OHLC":
                    for key, entries in result.items():
                        if key != "last":
                            for entry in entries:
                                self.entries.setdefault(pair, {})[int(entry[0])] = entry
                elif call in ("Ticker", "Depth"):
                    for value in (v for k, v in result.items() if k != "last"):
                        self.latest[(call, pair)] = value
                else:
                    self.latest[(call, "")] = result

    def __call__(self, url: str, headers: Mapping[str, str]) -> Response:
        call, query = _route(url)
        pair = query.get("pair", "")
        if call == "OHLC":
            held = self.entries.get(pair)
            return _unknown() if held is None else _ohlc(pair, list(held.values()),
                                                         int(query["since"]))  # fmt: skip
        if call in ("Ticker", "Depth"):
            value = self.latest.get((call, pair))
            return _unknown() if value is None else _answer({pair: value})
        value = self.latest.get((call, ""))
        return _answer(value) if value is not None else _answer(error=["EGeneral:Not recorded"])
