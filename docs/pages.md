# Pages

Both pages are under **Manage**, for the plugin's admin. Neither shows a
price.

## Connection

How the plugin reaches Kraken: whether it answers from Kraken or from
synthetic responses, the endpoints (REST and the WebSocket), what Kraken allows (about one call a second
from an address) and what the plugin spends (at most one a second), how many calls
it made, how many met a rate limit or failed, the last error, the venue's ID
in the platform's venue master, or that your deployment does not hold it,
and its live feed (**Stream**): whether it is open, how many pairs are on
it, how often it opened and dropped, how many messages it heard and when the
last arrived.

## Datasets

What the plugin serves: its three datasets (daily, live and trades), with
their data, modes, day and standard terms; how many of Kraken's pairs are mapped for the instruments
the lake asked about, how many of your deployment's instruments it named as
Kraken's assets, and which assets did not resolve; what Kraken entitles it
to (public data, no key: every pair it lists); and the wants the lake sent
it, those it keeps current first, each with how many rows it recorded and
subjects it declined. The search above the wants narrows them as you type,
by any word in a row.

## Tools for agents

Each page is a read tool answering what the page shows: `read_connection` and
`read_datasets`, for a person holding admin on the plugin.
