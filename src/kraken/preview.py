"""The pages as a deployment admin would see them, on synthetic data, each
printed as one HTML file: for looking at them without a deployment.

    python -m kraken.preview connection > preview/connection.html
    python -m kraken.preview datasets > preview/datasets.html

The page links the kit where the dashboard serves it, /.meridian/ui/<version>/,
so serve it beside the kit to see it styled (meridian-ui's `make serve`);
opened on its own it is the page without the kit, unstyled, which works too.

There is no sidecar here: the SDK's `Recorder` answers as one would, the
deployment holding two custodian's records (bitcoin and ether, by their
CAIP-19s and the custodian's symbols), the venue answers from the synthetic
exchanges this package carries, and the lake asks for a run of days and
keeps two subjects current, as a reader would make it, so the Datasets page
has wants to page through and search (the kit's search, in the browser);
their products are streamed from the synthetic venue's first connection,
which drops, so Connection says how the stream has gone.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder
from meridian.testing import PageClient

from .convert import KrakenMarket
from .dgm import Dgm
from .page import hold, pages
from .products import ProductMap
from .raw import RawStore
from .stream import Replayed, Socket
from .synthetic import Synthetic
from .venue import Kraken

#: The preview's clock: 2026-10-10 12:00 UTC.
NOW_NS = 1_791_633_600 * 1_000_000_000


#: The deployment's records the lake asks about: a custodian's, by each coin's
#: CAIP-19 and the custodian's symbol.
RECORDS = {
    "LCL-custodian-btc": [
        ops.Identifier(scheme="caip19", value="bip122:000000000019d6689c085ae165831e93/slip44:0"),
        ops.Identifier(scheme="symbol", value="BTC", source="custodian"),
    ],
    "LCL-custodian-eth": [
        ops.Identifier(scheme="caip19", value="eip155:1/slip44:60"),
        ops.Identifier(scheme="symbol", value="ETH", source="custodian"),
    ],
}


def _record(params: Any) -> Any:
    held = RECORDS.get(params.instrument_id)
    record = ops.InstrumentRecord(instrument_id=params.instrument_id, identifiers=held or [])
    return ops.ResolveInstrumentResult(found=held is not None, instrument=record)


def _resolved(params: Any) -> Any:
    if any(i.value == "PLY" for i in params.identifiers):
        return ops.ResolveIdentifierResult(found=False, miss_reason=ops.MISS_REASON_AMBIGUOUS)
    for instrument_id, held in RECORDS.items():
        if any(i in held for i in params.identifiers):
            return ops.ResolveIdentifierResult(found=True, instrument_id=instrument_id)
    return ops.ResolveIdentifierResult(
        found=True, instrument_id=f"LCL-{params.identifiers[0].value[-12:]}"
    )


def _delivering(want: ops.ObservationsWantedEvent) -> Callable[[Any], Any]:
    return lambda _: want


async def _worked() -> Dgm:
    lake = Recorder("kraken")
    lake.answer("ResolveIdentifier", _resolved)
    lake.answer("ResolveInstrument", _record)
    store = RawStore(Path(tempfile.mkdtemp(prefix="kraken-preview-")))
    synthetic = Synthetic()
    client = Kraken(store, transport=synthetic, clock_ns=lambda: NOW_NS)
    dgm = Dgm(lake, KrakenMarket(client, "synthetic"), ProductMap(), clock_ns=lambda: NOW_NS)
    await dgm.map_products()
    btc, eth = RECORDS
    for n in range(30):
        day = date(2026, 10, 9) - timedelta(days=n % 4)
        want = ops.ObservationsWantedEvent(
            want_id=f"WNT-{n:04d}",
            dataset="kraken:daily",
            data_type="meridian.v1.Price",
            subjects=[ops.SubjectRef(entity_id=btc if n % 2 else eth)],
            business_date=day.isoformat(),
        )
        lake.answer("ObservationsWanted", _delivering(want))
        await lake.receive(observations_wanted=dgm.on_want)
    for n, subject in enumerate((btc, eth)):
        want = ops.ObservationsWantedEvent(
            want_id=f"WNT-L{n}",
            dataset="kraken:live",
            data_type="meridian.v1.Price",
            subjects=[ops.SubjectRef(entity_id=subject)],
            standing=True,
        )
        lake.answer("ObservationsWanted", _delivering(want))
        await lake.receive(observations_wanted=dgm.on_want)
    first = synthetic.stream_sessions()[0]

    async def replayed(_: str) -> Socket:
        return Replayed(first, hold=False)

    dgm.stream.connect = replayed
    await dgm.stream.run(once=True)
    await dgm.flush()
    return dgm


def main(argv: list[str]) -> int:
    page = argv[0] if argv else "connection"
    paths = {"connection": "/", "datasets": "/datasets"}
    if page not in paths:
        print(f"no page {page!r}: connection or datasets", file=sys.stderr)
        return 2
    hold(asyncio.run(_worked()))
    answered = PageClient(pages).get(paths[page], "admin")
    sys.stdout.write(answered.text)
    return 0 if answered.status == 200 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
