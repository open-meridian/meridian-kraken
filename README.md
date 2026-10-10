# meridian-kraken

Kraken's public market data in an Open Meridian deployment's lake: a `dgm`
plugin. It records each pair's daily close and bar, and its last trade price
and best bid and offer, for the instruments a deployment's readers ask about,
every figure exactly as Kraken stated it and every code converted at the
edge. No key: Kraken's market data is public.

It is the second half of the lake's crypto pair, with
[meridian-coinbase](https://github.com/open-meridian/meridian-coinbase): the two
pass one `dgm` suite unchanged between them (meridian-design
`plans/the-lake-prices-the-book.md`, row 6).

**Status.** 0.1.0, built on open-meridian 0.22.0 (contract v18), which is on
meridian-python's `main` and not yet on PyPI. Until it is, CI's `check`, `ci`
and `e2e` jobs fail at installing the SDK or building on its base image; the
same gates pass locally, where the Makefile builds both from the sibling
meridian-python checkout (`make ci-local`). Not released, uploaded or
launched anywhere.

## What it does

Two datasets, declared from code in its catalogue (`declaration.py`):

| Dataset | Data | Modes | Day | Kraken's standard terms |
|---|---|---|---|---|
| `<instance>:daily` | a close and a bar per pair per day, with its volume-weighted price and trade count; 720 days back | pull, push | `Etc/UTC`, ending at 00:00 | kept, one person's |
| `<instance>:live` | the last trade's price; the best bid and offer | pull, stream | `Etc/UTC`, ending at 00:00 | kept two days, one person's |

- **What the lake wants, recorded.** A reader reads a price nothing has
  recorded; the lake asks this plugin (a want), which records it against the
  want or declines each subject with why: `not_covered` (no pair's base is
  it), `beyond_history` (older than the 720 days Kraken's `OHLC` gives),
  `source_silent` (Kraken has nothing for it). A
  standing want keeps its subjects current until the lake withdraws it: the
  live dataset at most once a second per subject and kind, the daily
  dataset's forming day every five minutes.
- **The daily close** is the last price of the UTC day: Kraken has no
  official close, so the catalogue declares the day (`day_time_zone`
  `Etc/UTC`, `day_end_minute` 0) and a daily candle gives kind `close` for
  its business date, a plain date. The day still forming is recorded valid
  until its end, with no published time, and restated under the same row key
  as it fills; a completed day carries its published time, the candle's
  end. Bars carry Kraken's volume-weighted price and trade count, and a day
  that traded nothing (count 0) carries no volume-weighted price, whatever
  Kraken writes in its place.
- **Every subject is the deployment's own record.** The lake asks for
  instruments by the deployment's ID, most of them a custodian's records
  that Kraken never resolved. The plugin reads each record (W3.6, a `dgm`'s
  read from contract v18 as ruled 2026-10-10) and names Kraken's asset it is
  by its identifiers: Kraken's code (`symbol` at `kraken`), else the global
  identifier Kraken's asset carries (a fiat code's ISO 4217, a native coin's
  CAIP-19), else another source's symbol that is Kraken's code (never for
  a record that is a security, by its class or a CUSIP or ISIN). Where the
  record lacks Kraken's identifiers they are reported beside the one it
  matched, so the record carries them; where that meets another record it is
  reported, and the subject is served all the same. A record naming none of
  Kraken's assets is declined, not covered: a custodian's `BTC` alone is not
  Kraken's `XXBT`, so bitcoin is named by its CAIP-19. Nothing is resolved
  for an asset no subject is: no record is minted for each asset Kraken
  lists.
- **The pair map**, kept in the plugin's storage (`products.py`): a pair
  names a market, not an asset, so its base asset is a row's subject and its
  quote asset what the price is in. Kraken names assets by its own legacy
  codes (`XXBT` for bitcoin, `ZUSD` for the dollar). Which asset each
  subject is is kept there, and each quote asset of a subject's pairs is
  resolved through the deployment's instrument store, with its code as a
  `symbol` whose source is `kraken`, a record minted only where none
  resolves:
  - a fiat code Kraken documents (`ZUSD`, `ZEUR`, `ZGBP`, `ZCAD`, `ZJPY`,
    `ZAUD`, `CHF`) by its ISO 4217 code -- a `Z` is no sign of a currency;
  - a native coin (`XXBT`, `XETH`) by its SLIP-44 CAIP-19;
  - every other token by its code alone: Kraken states no network or
    contract for any asset, so each is Kraken's own instrument, its promise
    that it converts into one of the issuer's contracts (the product owner's
    ruling of 2026-10-03), never a contract's CAIP-19.
  A quote asset that does not resolve (its identifiers meet two records) is
  reported, once, and every pair quoted in it left out.
- **Money names an instrument.** A price in a currency names it by its ISO
  4217 code, which core resolves to the currency's cash instrument. A price
  in a token -- an XBT/USDT or XBT/USDC price -- names the token's own
  instrument, exactly as stated, and never a fiat code: a USDC price is never
  a dollar price. A reader values it through a dataset's price of the
  stablecoin (Kraken's USDC/USD, recorded like any pair), never by assuming 1.
- **FX** from Kraken's fiat pairs: EUR/USD is the price of the euro's cash
  instrument in dollars, as Kraken's trades made it -- a venue's price, not a
  fixing.
- **The venue** is resolved in the platform's venue master by its code
  (`symbol` `kraken`, source `kraken`: Kraken has no MIC, curation's call at
  the venue master's seed), and each row names its `VEN-` ID. A deployment
  that does not hold it gets each row with the venue left empty and the code
  beside it as reported, and the miss reported, re-resolved at the next
  mapping.
- **Exact.** Every figure is a `Decimal` from Kraken's own text, never
  through a float; Kraken's refusals under HTTP 200 are read as refusals. A value
  with more places than the contract's Decimal holds is not rounded.

### What it cannot serve

- **More than 720 days back.** Kraken's `OHLC` gives the 720 most recent
  days; an older day is declined as beyond its history.
- **The venue in its catalogue.** The catalogue is declared from code, and
  the venue master's ID for Kraken is minted by the platform, so
  `DatasetDeclaration.venue_id` is empty; each row names the venue by the ID
  it resolves at run time.
- **A required figure that does not read** (a close past the contract's
  Decimal): the row is left unrecorded and counted on the Datasets page, its
  raw record kept. The SDK's `Price` carries no price left not known.

## Kraken's terms

A summary, not legal advice; read the terms themselves before relying on
them. Kraken asks its prior permission (marketdata@kraken.com) for "any
non-personal commercial use of data from publicly accessible endpoints".
Without it, a deployment keeps Kraken's data for one person's own use. The
catalogue's standard terms say so (kept, one person's), and a deployment
admin confirms or replaces them in the deployment's licence on the Data
sources page, which warns where more than one person reads a plugin entitled
to it. Nothing here says that a deployment meets Kraken's terms.

So **no real Kraken response is committed** to this public repository: the
tests and synthetic mode use synthetic exchanges (`src/kraken/synthetic/`),
shaped exactly as Kraken documents and sends its answers, with invented
values and an invented token. A developer's recordings of Kraken's real
answers stay outside the repository and are replayed locally
(`make e2e-recorded`).

## Settings

| Setting | What it does |
|---|---|
| Synthetic mode (`synthetic`) | A developer's setting, shown only on a development deployment: answer from the synthetic exchanges this plugin carries instead of calling Kraken, or from a developer's recorded exchanges where its storage holds them (`recorded/`). |
| Raw responses' window (`responses_window_days`, `responses_past_window`) | The SDK's, for the plugin's one kind of raw record: how long Kraken's answers stay in its storage (30 days by default), and whether a day past it is archived, kept or deleted. |

No secret: Kraken's public market data needs no key.

## Its pages, under Manage

A `dgm` shows no market data: viewing prices is for the reading roles.

- **Connection**: where it answers from (Kraken, or synthetic mode), the
  endpoint, what Kraken allows (public REST by IP, about one call a
  second) and what the plugin spends of it (at most one a second, a rate
  limit waited out), its calls, rate limits met and failures, the last error, and
  the venue master's ID it resolved.
- **Datasets**: each dataset of its catalogue, the pair map (pairs listed,
  and mapped for the subjects the lake asked for; assets not resolved), what
  Kraken entitles it to (public data, no key: every pair it lists), and the
  wants it heard, standing ones first, each with what it recorded and
  declined, under a search that narrows them.

Both fit one screen at 1440x900 and 390x844, rows one line each, the wants
paged by the kit's `om-pager`.

### Offered to agents

Each page is a read tool on the deployment's MCP surface, answering the record
the page shows: `read_connection` and `read_datasets`, at the plugin's admin
level. The plugin has no route that changes anything.

## Raw responses

Every answer Kraken sends, an error as much as a candle, is kept in the
storage the deployment grants the plugin before anything is read from it,
with the request that asked for it, its status and freshness headers, and
when it arrived (`raw.py`); a live answer is a line of its day's file, added
only when it changed. Each row names its raw record (`RawRecordRef`) and
where in it the row came from. Kept in units of a UTC day under
`responses/`, moved past the window through the SDK.

## Proven against a released runtime

    make e2e

runs the plugin in synthetic mode on core's plugin harness, the runtime and
harness of core e4a14fc pinned by digest, beside the e2e's reader
(`e2e/reader.py`, a `reporting` plugin run from the plugin's image, holding
`custody` to resolve a custodian's records): the catalogue listed, the
listing read with no record minted for any asset, both datasets licensed and
the reader entitled, a custodian's bitcoin (by its CAIP-19 and symbol) read
and named, and a day's closes wanted and recorded against the want (XXBTZUSD's in
dollars, XBTUSDC's on USDC's own instrument, exactly), the dollar declined
and the euro's rate in dollars recorded, bitcoin's live prices kept current until withdrawn, both read tools answering
what the pages show, and `store lake` numbering each row with no holes
(`e2e/run.py` says each step).

    make e2e-recorded RECORDED=<dir> DAY=<a day it holds>

runs the same replaying a developer's recorded exchanges, reading the day
named and holding each row to its shape rather than to the synthetic values:
a directory of this plugin's raw records of Kraken's real answers, recorded under personal use,
never committed.

## Working on it

    make ci-local        # every gate: lint, tests, plugin check, the image, e2e, the pair the same
    make test            # the tests, the SDK's dgm suite among them
    make preview         # preview/: each page on synthetic data, linking the kit
    make install-hooks   # git push runs ci-local first

`AGENTS.md` is for any coding agent working on it; `CLAUDE.md` leads Claude
Code there. The documentation for a deployment admin is in `docs/`.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`. Kraken is a trademark of its owner;
this project is not affiliated with or endorsed by Kraken.
