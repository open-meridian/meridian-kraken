# kraken

Kraken's public market data as an Open Meridian `dgm`: a
connectivity spike, made by `meridian plugin new` (meridian 0.1.28) on
open-meridian 0.16.0. It proves Kraken's public API and the conversion into
the lake's rows before the lake's contract (v14) exists; it records nothing,
because there is nothing to record into yet. The plan is meridian-design's
`plans/the-lake-prices-the-book.md`.

- `src/kraken/venue.py`: the client. REST (`AssetPairs`, `Assets`, `OHLC`,
  `Ticker`, `Depth`, `Trades`) and WebSocket v2 (`ticker`, `trade`), no key,
  about one call a second, Kraken's "too many requests" waited out. Every response is kept first as a raw record (`raw.py`), in the
  storage the deployment grants (`meridian.edge.storage_dir()`), else
  `recordings/`, which git ignores.
- `src/kraken/rows.py`: **the seam.** The lake's slice 1 rows (prices,
  bars, trades, quotes, the envelope, as reported) as plain Python values,
  to be replaced by the SDK's generated types when v14 lands. Identical, byte
  for byte, in meridian-coinbase.
- `src/kraken/assets.py`: pairs to assets, found by any of Kraken's names
  (`XXBTZUSD`, `XBTUSD`, `XBT/USD`, `BTC/USD`). A pair is a market, not an
  asset: the base token is a row's subject (CAIP-19 for native coins by
  SLIP-44; Kraken states no token's network or contract, so no other token
  has one), priced in the quote asset (a currency's ISO 4217 cash instrument,
  by Kraken's fiat codes, or a stablecoin's own token, never the dollar).
- `src/kraken/convert.py`: Kraken's values to rows, exact decimals only (its
  trade times and WebSocket v2's prices are JSON numbers, read as Decimals
  from their text), the daily close on the declared UTC day with the forming
  day restated, the taker's side as the aggressor.
- The Connectivity page, under Manage alone: whether Kraken answers, the
  datasets it would serve and its entitlements. A `dgm` shows no market data.

Look at real rows, and check the gates:

    make sample PRODUCT=XBT/USD   # converted rows from the real endpoints, one JSON line each
    make ci-local                 # lint, tests on synthetic exchanges, plugin check, image, suite unchanged
    make live                     # the live smoke test on the real endpoints; never in CI

`tests/test_conformance.py` is the crypto pair's suite, the same file in
meridian-coinbase: both plugins convert their own venue's synthetic exchange of
the same market facts into the same rows. `make conformance-same` holds the
two copies, and rows.py, to each other.

`AGENTS.md` teaches any coding agent the kit and the live loop; `CLAUDE.md`
and the `develop-live` skill under `.claude/` lead Claude Code to it.
