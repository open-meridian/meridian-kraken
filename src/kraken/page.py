"""The plugin's pages, under Manage: its Connection and its Datasets.

A `dgm` shows no market data (ruled 2026-10-03: "The data unit only cares for
connectivity and data subscriptions and entitlement resolution"): no price,
bar or candle is on either page; viewing prices is for the reading roles.

- **Connection** (`/`, `read_connection`): where the plugin answers from
  (Kraken, or synthetic mode), how Kraken has answered -- its calls,
  rate limits met, failures and the last error -- what Kraken allows and
  what the plugin spends of it, and the venue master's ID it resolved.
- **Datasets** (`/datasets`, `read_datasets`): what it serves -- each
  dataset of its catalogue, its data types, modes, cadence, declared day and
  standard terms -- the product map (products listed, mapped, assets not
  resolved), what Kraken entitles it to (public market data, no key: every
  product it lists), the standing wants it keeps current, and the wants it
  heard, each with what it recorded and declined.

Each page declares the typed record it renders (`answers=`), so each is also
a read tool on the deployment's MCP surface answering the same record
(requirement 23: a page's tool answers what the page shows). Both fit one
screen at 1440x900 and 390x844: one-line rows, and the wants paged by the
kit's `om-pager`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import meridian
from meridian.v1 import sidecar_pb2

from .declaration import CATALOGUE
from .dgm import Dgm

TITLE = "Kraken"
ENTITLEMENT = "Public market data: no key, so nothing to resolve at Kraken; every pair it lists"

pages = meridian.Pages(TITLE, templates=Path(__file__).parent / "templates")


class _Held:
    dgm: Dgm | None = None


HELD = _Held()


def hold(dgm: Dgm) -> None:
    """The work the pages show."""
    HELD.dgm = dgm


def _when(at_ns: int) -> datetime | None:
    return datetime.fromtimestamp(at_ns / 1e9, tz=UTC).replace(microsecond=0) if at_ns else None


# ── Connection ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EndpointRow:
    name: str
    address: str
    what: str


@dataclass(frozen=True)
class Connection:
    """How the plugin reaches Kraken, and how Kraken has answered."""

    venue: str
    mode: str
    healthy: bool
    endpoints: list[EndpointRow] = field(default_factory=list)
    allowed: str = ""
    spent: str = ""
    calls: int = 0
    rate_limited: int = 0
    failures: int = 0
    last_answer_at: datetime | None = None
    last_error: str = ""
    venue_id: str = ""
    venue_held: bool = False


def connection_now() -> Connection:
    dgm = HELD.dgm
    if dgm is None:
        return Connection(venue=TITLE, mode="starting", healthy=False)
    session = dgm.market.session
    health = session.health
    healthy = health.last_ok_ns >= health.last_failed_ns and (
        health.last_ok_ns > 0 or health.calls == 0
    )
    return Connection(
        venue=dgm.market.name,
        mode=session.mode,
        healthy=healthy,
        endpoints=[EndpointRow(e.name, e.address, e.what) for e in session.endpoints],
        allowed=session.limits.allowed,
        spent=session.limits.spent,
        calls=health.calls,
        rate_limited=health.rate_limited,
        failures=health.failures,
        last_answer_at=_when(health.last_ok_ns),
        last_error=health.last_error,
        venue_id=dgm.venue.venue_id,
        venue_held=dgm.venue.asked and not dgm.venue.missing,
    )


@pages.page("/", "Connection", levels="admin", answers=Connection, name="read_connection")
def connection(request: meridian.Request) -> meridian.Response:
    """How the plugin reaches Kraken: the mode, the endpoint, Kraken's limits
    and what the plugin spends of them, its calls, rate limits met and
    failures, the last error, and the venue master's ID it resolved."""
    return pages.answer("connection.html", connection_now())


# ── Datasets ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DatasetRow:
    dataset: str
    data_types: str
    modes: str
    cadence_seconds: int
    day: str
    terms: str


@dataclass(frozen=True)
class WantRow:
    want_id: str
    dataset: str
    asked: str
    subjects: int
    recorded: int
    declined: int
    standing: bool
    kept_current: bool
    heard_at: datetime | None = None


@dataclass(frozen=True)
class Datasets:
    """What the plugin serves the lake, and what the lake asked of it."""

    datasets: list[DatasetRow] = field(default_factory=list)
    entitled_at_vendor: str = ENTITLEMENT
    products_listed: int = 0
    products_mapped: int = 0
    assets_not_resolved: list[str] = field(default_factory=list)
    map_read_at: datetime | None = None
    standing_wants: int = 0
    recorded: int = 0
    restated: int = 0
    unchanged: int = 0
    declined: int = 0
    not_read: int = 0
    wants: list[WantRow] = field(default_factory=list)


def _terms(licence: meridian.DatasetLicence | None) -> str:
    if licence is None:
        return ""
    said = ["kept" if licence.kept else "served, not kept"]
    if licence.retention_days:
        said.append(f"{licence.retention_days} days")
    said.append("display" if licence.display else "no display")
    if licence.personal_use:
        said.append("one person's")
    return ", ".join(said)


def datasets_now() -> Datasets:
    dgm = HELD.dgm
    instance = dgm.plugin.identity.instance_id if dgm else "kraken"
    rows = [
        DatasetRow(
            dataset=f"{instance}:{d.key}",
            data_types=", ".join(t.removeprefix("meridian.v1.") for t in d.data_types),
            modes=", ".join(_mode(m) for m in d.modes),
            cadence_seconds=d.cadence,
            day=f"{d.day_time_zone}, ends {d.day_end_minute // 60:02d}:{d.day_end_minute % 60:02d}",
            terms=_terms(d.licence_default),
        )
        for d in CATALOGUE
    ]
    if dgm is None:
        return Datasets(datasets=rows)
    standing = set(dgm.standing)
    wants = [
        WantRow(w.want_id, w.dataset, w.asked, w.subjects, w.recorded, w.declined, w.standing,
                w.want_id in standing, _when(w.at_ns))
        for w in dgm.heard
    ]  # fmt: skip
    wants.sort(key=lambda w: (not w.kept_current,))
    return Datasets(
        datasets=rows,
        products_listed=len(dgm.products.listed),
        products_mapped=len(dgm.products.mapped()),
        assets_not_resolved=sorted(dgm.products.missing),
        map_read_at=_when(dgm.products.read_at_ns),
        standing_wants=len(standing),
        recorded=dgm.counts.recorded,
        restated=dgm.counts.restated,
        unchanged=dgm.counts.unchanged,
        declined=dgm.counts.declined,
        not_read=dgm.counts.unread,
        wants=wants,
    )


def _mode(mode: object) -> str:
    if isinstance(mode, int):
        named = str(sidecar_pb2.ObservationMode.Name(mode))
        return named.removeprefix("OBSERVATION_MODE_").lower()
    return str(mode)


@pages.page("/datasets", "Datasets", levels="admin", answers=Datasets, name="read_datasets")
def datasets(request: meridian.Request) -> meridian.Response:
    """What the plugin serves the lake: each dataset of its catalogue, the
    product map, what Kraken entitles it to, the standing wants it keeps
    current, and the wants it heard with what each recorded and declined."""
    return pages.answer("datasets.html", datasets_now(), now=time.time())
