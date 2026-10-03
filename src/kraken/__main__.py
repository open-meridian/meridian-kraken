"""Kraken's public market data as a `dgm`, a connectivity spike.

`python -m kraken sample --product XBT/USD` prints converted rows for a
person to eyeball (sample.py). With no arguments it runs as a plugin: it
connects to its sidecar declaring its one page and its storage, asks Kraken
once whether it answers (its status and pairs, kept as raw records in the storage
the deployment grants), reports what it found as its health, and serves the
Connectivity page until stopped.

It records nothing: the lake's contract (v14) does not exist yet, so there is
no operation to record a price with. rows.py is the seam where v14's types
replace the spike's.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

import meridian

from . import assets, sample
from .declaration import DECLARATION
from .page import CONNECTIVITY, TITLE, pages
from .raw import RawStore, storage_root
from .venue import Kraken, VenueError

log = logging.getLogger("kraken")


def probe(client: Kraken) -> None:
    """Ask Kraken for its status, pairs and assets once, and say what it found."""
    CONNECTIVITY.checked_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    CONNECTIVITY.raw_records = str(client.store.root)
    try:
        status = str(client.system_status().value.get("status", ""))
        mapped = assets.Pairs(client.asset_pairs().value, client.assets().value).products
    except (VenueError, OSError) as failed:
        CONNECTIVITY.state, CONNECTIVITY.detail = "bad", f"Kraken did not answer: {failed}"
        CONNECTIVITY.problems = [str(failed)]
    else:
        online = sum(1 for p in mapped.values() if p.tradable)
        healthy = status == "online"
        CONNECTIVITY.state = "ok" if healthy else "warn"
        CONNECTIVITY.detail = f"Kraken answers; its system is {status or 'not saying'}."
        CONNECTIVITY.products_listed, CONNECTIVITY.products_online = len(mapped), online
        CONNECTIVITY.problems = []
    CONNECTIVITY.calls, CONNECTIVITY.rate_limited = client.calls, client.rate_limited


async def run() -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop, stopped.set)

    port = int(os.environ.get("REFERENCE_PAGE_PORT", "8000"))
    async with await meridian.connect(
        interface=meridian.Interface(port=port, title=TITLE, pages=pages),
        declaration=DECLARATION,
    ) as plugin:
        log.info("registered as %s, roles %s", plugin.identity.instance_id, plugin.identity.roles)
        served = pages.serve(plugin, port)
        store = RawStore(storage_root(Path(tempfile.mkdtemp(prefix="kraken-raw-"))))
        await asyncio.to_thread(probe, Kraken(store))
        await plugin.report(healthy=CONNECTIVITY.state == "ok", detail=CONNECTIVITY.detail)
        await stopped.wait()
        log.info("stopping")
        served.shutdown()


def main() -> None:
    if sys.argv[1:2] == ["sample"]:
        sys.exit(sample.main(sys.argv[2:]))
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
