# Overview

The Kraken plugin puts Kraken's public market data into your
deployment's lake, where the plugins that value and report on your book read
it. It needs no key: Kraken's market data is public.

## Its role

It holds one role, `dgm`: it records prices and bars into the lake, with
Kraken's codes converted to your deployment's instruments, and keeps
Kraken's answers in its own storage. It shows no prices itself: reading
them is for the reporting, portfolio, compliance and signal plugins.

## What it records

- **Daily**: each pair's close and bar, with its volume-weighted price and
  trade count, for a UTC day. Kraken has no
  official close, so the close is the last price of the day ending at 00:00
  UTC, for the 720 most recent days; the day still forming is recorded and corrected as it fills.
- **Live**: the last trade's price and the best bid and offer, at most once
  a second for each instrument and kind, while a reader keeps asking.

It records what readers ask for, not the whole exchange: when a reader asks
for a price nothing has recorded, the lake asks the plugin, which records it
or says why it cannot.

## How it names what it prices

A Kraken pair such as XXBTZUSD (bitcoin in dollars) names a market, not an
asset. The plugin records bitcoin's price in dollars: bitcoin is the
instrument, and the dollar what the price is in. It finds each in your deployment's instruments by a global
identifier where one is certain (the dollar's ISO 4217 code, bitcoin's
CAIP-19), and otherwise by Kraken's own code (`XXBT`, `ZUSD`).

A price in a stablecoin is recorded in that stablecoin, never in the
currency it tracks: an XBT/USDC price is in Kraken's USDC, not in dollars.
Kraken's USDC is its own instrument, a promise to convert into one of
Circle's contracts, so it carries no contract's identifier.

Kraken's fiat pairs give FX: its EUR/USD is the euro's price in dollars, as
Kraken's trades made it.

## What it never makes up

- A figure is recorded exactly as Kraken stated it, never rounded and never
  through a float.
- A value Kraken did not send is left out, never zero: a day that traded
  nothing has no volume-weighted price.

## What it keeps

Kraken's answers, as received, in the storage your deployment gives the
plugin, for 30 days by default, each price naming the answer it came from.
Past that window, a day's answers are archived, kept or deleted as you set
it.
