"""Kraken's asset pairs, mapped to the assets they trade (the plan's
"Identifiers for a pair").

Kraken names everything its own way: assets by legacy codes (`XXBT` for
bitcoin, whose altname is `XBT`, never `BTC`; `ZUSD` for the dollar), pairs
by keys built from them (`XXBTZUSD`), by an altname (`XBTUSD`), by a v1
WebSocket name (`XBT/USD`), and its WebSocket v2 by yet another (`BTC/USD`).
A pair names a market, not an asset, so it is never a row's subject: the base
asset is, priced in the quote asset. Each side resolves through W3.1 by its
global identifier, derived from Kraken's own codes and nothing else:

- **A currency** is a cash instrument, by its ISO 4217 code, for the fiat
  asset codes Kraken documents (`ZUSD`, `ZEUR`, ...). Kraken's `Assets` does
  not say which assets are fiat (every one is `aclass: currency`), and a `Z`
  is no sign of one (`ZAMA` and `ZBCN` are tokens; `AED` has no `Z`), so the
  table below is the plugin's, keyed by Kraken's codes. A code not in it --
  `ZARS` and `AED` among them, seen 2026-10-03 -- resolves by its venue code
  until a person adds it.
- **A native coin** carries its CAIP-19 by SLIP-44 coin type (`XXBT`,
  `XETH`).
- **A token** follows the product owner's ruling (the plan's "a venue's
  balance in a multi-network token is the venue's own instrument"): each
  issuer contract on each chain is its own instrument, and a venue's balance
  in a token is one of them only when the venue states the one network and
  its contract. Kraken's public API states no network and no contract
  address for any asset, so every token but a native coin is Kraken's own
  instrument, its promise that it converts into one of the issuer's
  contracts: known by `symbol@kraken` alone, never by a contract's CAIP-19,
  with no networks to keep (`Asset.networks` stays empty). W3.2 reports it
  as missing for a person to map. Never guessed from a ticker. The same
  holds, until a person adds them to FIAT, for the fiat codes above that
  Kraken does not flag.
- **A stablecoin is a token,** never its fiat currency (decisions/030): a
  pair quoted in `USDC` is quoted in Kraken's USDC instrument, and Kraken's own
  `USDCUSD` pair is the dataset's price for valuing it (the plan's Q25).

Kraken's codes stay source-scoped `symbol`s (source `kraken`); the pair's key
is the product's ID, and its other names are aliases for finding it.
"""

from __future__ import annotations

from typing import Any

from .rows import Asset, Identifier, Product, caip19, currency, symbol

VENUE = "kraken"

#: Native coins by SLIP-44 coin type, keyed by Kraken's asset code.
NATIVE: dict[str, Identifier] = {
    "XXBT": caip19("bip122:000000000019d6689c085ae165831e93/slip44:0"),
    "XETH": caip19("eip155:1/slip44:60"),
}

#: Kraken's fiat asset codes, as its documentation lists them, to ISO 4217.
FIAT: dict[str, str] = {
    "ZUSD": "USD",
    "ZEUR": "EUR",
    "ZGBP": "GBP",
    "ZCAD": "CAD",
    "ZJPY": "JPY",
    "ZAUD": "AUD",
    "CHF": "CHF",
}

#: WebSocket v2 spells two of Kraken's legacy altnames the common way.
V2_NAMES = {"XBT": "BTC", "XDG": "DOGE"}


def asset(code: str, details: dict[str, Any] | None) -> Asset:
    """One Kraken asset, from its `Assets` record (None where Kraken did not
    list it)."""
    venue_code = symbol(code, VENUE)
    name = str((details or {}).get("altname") or "")
    if code in FIAT:
        return Asset(venue_code, "currency", currency(FIAT[code]), name)
    if code in NATIVE:
        return Asset(venue_code, "token", NATIVE[code], name)
    return Asset(venue_code, "token", None, name, venue_instrument=True)


def v2_symbol(wsname: str) -> str:
    """The pair's WebSocket v2 symbol, from its v1 name (`XBT/USD` to `BTC/USD`)."""
    return "/".join(V2_NAMES.get(part, part) for part in wsname.split("/"))


class Pairs:
    """Every pair Kraken lists, by its key, each side resolved as above, and
    every name Kraken gives a pair found back to its key."""

    def __init__(self, pairs: dict[str, Any], assets: dict[str, Any]) -> None:
        self.products: dict[str, Product] = {}
        self.aliases: dict[str, str] = {}
        self.websocket: dict[str, str] = {}
        for key, item in pairs.items():
            base, quote = str(item["base"]), str(item["quote"])
            status = str(item.get("status") or "")
            self.products[key] = Product(
                product=symbol(key, VENUE),
                base=asset(base, assets.get(base)),
                quote=asset(quote, assets.get(quote)),
                status=status,
                tradable=status == "online",
            )
            names = [key, str(item.get("altname") or "")]
            if item.get("wsname"):
                names += [str(item["wsname"]), v2_symbol(str(item["wsname"]))]
                self.websocket[key] = v2_symbol(str(item["wsname"]))
            for name in names:
                if name:
                    self.aliases.setdefault(name, key)

    def find(self, name: str) -> Product:
        """A pair by any name Kraken gives it: `XXBTZUSD`, `XBTUSD`, `XBT/USD`
        or `BTC/USD`."""
        if name not in self.aliases:
            raise KeyError(f"Kraken lists no pair {name!r}")
        return self.products[self.aliases[name]]
