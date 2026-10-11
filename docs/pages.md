# Pages

The data role's two pages, under **Manage**, for the plugin's admin, laid
out as every data plugin's are: the same sections, rows and words in the
same order. Neither shows a price.

## Connection

- **Key**: the mode (Live, or Sample data on a development deployment),
  "No key needed" -- Kraken's market data is public -- what Kraken
  entitles it to (every pair it lists), and the venue's ID in the
  platform's venue master, or that your deployment does not hold it.
- **Limits and use**: what Kraken allows and what the plugin spends of
  it, how many calls it made and how many met a rate limit and waited it
  out, the last call and the last error.
- **Stream**: whether its live feed is open, how many pairs are on it,
  how often it reconnected, how many messages it heard and when the last
  arrived.

## Datasets

- **Served**: its three datasets (daily, live and trades), with their data,
  how rows arrive, their day and default terms, whether a deployment admin
  has licensed each ("Licensed" or "Not licensed yet"), and what each
  recorded and declined, and when it last recorded.
- **Wants**: the wants the lake sent it, those it keeps current first, each
  with how many rows it recorded and subjects it declined; the search above
  them narrows them as you type, and the pager pages them.
- **Subjects**: whether your deployment lets it read an instrument's record.
- **Kraken**: how many of Kraken's pairs are mapped for the instruments
  the lake asked about, and which assets did not resolve.

Before anything has been asked of it, Datasets says the next step: "No
wants yet", until a deployment admin licenses its datasets and entitles a
plugin on **Data sources**.

## Tools for agents

Each page is a read tool answering what the page shows, the data role's
fields under the same names as every data plugin's: `read_connection` and
`read_datasets`, for a person holding admin on the plugin.
