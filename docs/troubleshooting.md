# Troubleshooting

## Kraken is not answering

The Connection page's status says so, with the last error. Kraken's public
endpoints sit behind a cache and sometimes refuse; the plugin waits out a rate
limit and tries again. Check the deployment can reach api.kraken.com.

## Venue master: not held

Your deployment has not pulled Kraken from the platform's venue
master. Each price is recorded with its venue left empty and Kraken named
as reported, and the plugin asks again at its next mapping, once a day.

## An asset not resolved

The Datasets page lists assets whose identifiers matched two of your
deployment's instruments. A pair quoted in one is left out until a person
resolves the conflict on the Instruments page; an instrument asked about
whose record met another is still priced.

## A subject declined

A reader asked for an instrument the plugin cannot price: its record names
none of Kraken's assets -- no Kraken code, no ISO 4217 code or CAIP-19 a
Kraken asset carries, no other source's symbol that is Kraken's code (a
custodian's `BTC` is not Kraken's `XXBT`: add bitcoin's CAIP-19 to the
record on the Instruments page, and the plugin reads it again within a day)
-- or no Kraken pair has it as its base (`not covered`), the day is older than the 720 days Kraken
gives (`beyond history`), or Kraken has nothing for that day (`source
silent`).

## A price in USDC, not dollars

As it should be: a price quoted in a stablecoin is recorded in that
stablecoin. A reader values it through a price of the stablecoin, such as
Kraken's USDC/USD.

## Kraken is rate-limiting

Kraken limits its public endpoints by address, and some by pair. The plugin
asks at most once a second and waits out a limit; the Connection page counts
each one met.

## Still stuck

Open the plugin's Connection page and copy its last error into a ticket with
**Report a problem**.
