"""The plugin's one page: its connectivity, its datasets and its entitlements.

A `dgm` shows no market data (ruled 2026-10-03: "The data unit only cares for
connectivity and data subscriptions and entitlement resolution"). So Manage
(`admin`) sees whether Kraken answers and how the plugin spends its rate
limit, the datasets it would serve, and what entitlement resolution came to
-- for Kraken's public market data, nothing to resolve -- and no price,
bar, trade or quote. There is no page under Open or View: viewing prices is
for the reading roles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import meridian

from . import convert, venue

TITLE = "Kraken"

pages = meridian.Pages(TITLE, templates=Path(__file__).parent / "templates")


@dataclass
class Connectivity:
    """What the last probe of Kraken found; the page shows it as it is."""

    state: str = "warn"
    detail: str = "Not asked yet."
    checked_at: str = ""
    products_online: int = 0
    products_listed: int = 0
    calls: int = 0
    rate_limited: int = 0
    raw_records: str = ""
    problems: list[str] = field(default_factory=list)


CONNECTIVITY = Connectivity()

DATASETS = [
    {
        "key": dataset.key,
        "types": ", ".join(dataset.data_types),
        "modes": ", ".join(dataset.modes),
        "day": f"{dataset.day.calendar}, ends {dataset.day.ends_at}" if dataset.day else "",
    }
    for dataset in convert.CATALOGUE
]


@pages.page("/", "Connectivity", levels="admin")
def connectivity(request: meridian.Request) -> str:
    identity = request.plugin.identity
    return pages.render(
        "connectivity.html",
        instance=identity.instance_id,
        roles=identity.roles,
        rest=venue.REST,
        websocket=venue.WEBSOCKET,
        rate=f"{venue.RATE_PER_SECOND:g} a second, bursts of {venue.BURST}",
        status=CONNECTIVITY,
        datasets=DATASETS,
    )
