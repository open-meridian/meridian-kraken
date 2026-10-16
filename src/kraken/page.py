"""The plugin's pages, under Manage: the data role's Connection and Datasets
(spec/plugin-pages-follow-their-role), built on `meridian.roles.data`.

The same module in meridian-coinbase and meridian-kraken (`make
suite-same`), the venue's names read from convert.py.

A data plugin shows no market data (ruled 2026-10-03: "The data unit only
cares for connectivity and data subscriptions and entitlement resolution"):
no price, bar or candle is on either page; viewing prices is for the
reading roles. Both pages are the role's, in its order, their sections,
rows, labels and "not set up yet" states the role template's; the venue's
nuance is added only at the template's blocks:

- **Connection** (`/`, `read_connection`): "Key" -- the mode (Live, or
  Sample data from the package's or a developer's recorded responses), "No
  key needed", what the venue entitles it to; then the venue master's ID it
  resolved (Venue, the role's row for a source of one venue) and the
  endpoints, the venue's rows -- "Limits and use" -- what the venue allows
  and what the plugin spends of it, its calls and the rate-limited ones
  waited out, the last call and the last error -- and "Stream", the venue's
  session (contract v19): its state and the products on it, reconnects, the
  last message and its error.
- **Datasets** (`/datasets`, `read_datasets`): "Served" -- each dataset of
  its catalogue, its data, how its rows arrive, its day, its default terms
  and whether a deployment admin has licensed it (contract v20), what the
  venue entitles it to, and what it recorded and declined and when it last
  recorded -- "Wants", each want it heard and each standing want it keeps
  current, searched and paged by the kit's `om-pager`, since they grow --
  "Subjects", whether the deployment lets it read an instrument's record --
  then a section under the venue's name: the product map (products listed,
  those serving the subjects the lake asked for, assets not resolved) and
  the rows restated, unchanged and not read.

Each page declares the record it renders (`answers=`), the role's record
extended with the venue's fields, so each is also a read tool on the
deployment's MCP surface answering the same record under the role's names.
Both fit one screen at 1440x900 and 390x844.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import meridian
from meridian import Figure
from meridian.roles import data
from meridian.v1 import sidecar_pb2

from .convert import ENTITLED as ENTITLED
from .convert import NAME, VENUE
from .declared import CATALOGUE
from .dgm import DAILY, LIVE, TRADES, Dgm

TITLE = NAME
#: The kit the pages are built against: 0.12.0's next step (`data-om-opens`).
KIT = "0.12.0"

pages = meridian.Pages(TITLE, templates=Path(__file__).parent / "templates", kit=KIT)


class _Held:
    dgm: Dgm | None = None


HELD = _Held()


def hold(dgm: Dgm) -> None:
    """The work the pages show."""
    HELD.dgm = dgm


def _when(at_ns: int) -> str:
    """A time as the role's records carry it: ISO 8601, to the second, UTC."""
    if not at_ns:
        return ""
    return datetime.fromtimestamp(at_ns / 1e9, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mode(session_mode: str) -> str:
    """The role's mode: Live, or Sample data for the package's synthetic
    responses and a developer's recorded ones alike."""
    return "Live" if session_mode == "live" else "Sample data"


# ── Connection ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EndpointRow:
    name: str
    address: str
    what: str


@dataclass(frozen=True)
class Connection(data.ConnectionRead):
    """The role's Connection, and the venue's: its name, the venue master's
    ID it resolved, the endpoints, what the venue allows and what the plugin
    spends of it, the failed calls, and the stream's session."""

    venue: str = NAME
    venue_id: str = ""
    venue_held: bool = False
    healthy: bool = False
    endpoints: list[EndpointRow] = field(default_factory=list)
    allowed: str = ""
    spent: str = ""
    failures: int = 0
    #: The stream (contract v19): "off" while nothing stands to stream,
    #: "connecting", "open", or "waiting" to open again after a drop; the
    #: products on it, how often it opened and dropped, its messages.
    stream_state: str = "off"
    streaming: int = 0
    stream_opened: int = 0
    stream_dropped: int = 0
    stream_messages: int = 0
    last_message_at: str = ""
    stream_error: str = ""


def connection_now() -> Connection:
    dgm = HELD.dgm
    if dgm is None:
        return Connection(mode="Live", entitled_at_vendor=ENTITLED, sessions=0)
    session = dgm.market.session
    health = session.health
    stream = dgm.stream.health
    healthy = health.last_ok_ns >= health.last_failed_ns and (
        health.last_ok_ns > 0 or health.calls == 0
    )
    return Connection(
        mode=_mode(session.mode),
        credentials=[],
        entitled_at_vendor=ENTITLED,
        limit=f"{session.limits.allowed}. This plugin: {session.limits.spent}",
        calls=health.calls,
        rate_limited=health.rate_limited,
        last_call_at=_when(max(health.last_ok_ns, health.last_failed_ns)),
        last_error=health.last_error,
        sessions=1 if stream.state == "open" else 0,
        venue=dgm.market.name,
        venue_id=dgm.venue.venue_id,
        venue_held=dgm.venue.asked and not dgm.venue.missing,
        healthy=healthy,
        endpoints=[EndpointRow(e.name, e.address, e.what) for e in session.endpoints],
        allowed=session.limits.allowed,
        spent=session.limits.spent,
        failures=health.failures,
        stream_state=stream.state,
        streaming=len(stream.subscribed),
        stream_opened=stream.opened,
        stream_dropped=stream.dropped,
        stream_messages=stream.messages,
        last_message_at=_when(stream.last_message_ns),
        stream_error=stream.last_error,
    )


@pages.page("/", "Connection", levels="admin", answers=Connection, name="read_connection")
def connection(request: meridian.Request) -> meridian.Response:
    """How the plugin reaches the venue: the mode, no key needed, what the
    venue entitles it to, the venue master's ID it resolved and the
    endpoints; the venue's limits and what the plugin spends of them, its
    calls, the last call and the last error; and the stream's session."""
    return pages.answer("connection.html", connection_now())


# ── Datasets ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Want(data.Want):
    """The role's want, with its ID and whether it is kept current."""

    want_id: str = ""
    kept_current: bool = False


@dataclass(frozen=True)
class Datasets(data.DatasetsRead):
    """The role's Datasets, and the venue's product map and counts."""

    standing_wants: int = 0
    products_listed: int = 0
    products_mapped: int = 0
    subjects_named: int = 0
    assets_not_resolved: list[str] = field(default_factory=list)
    map_read_at: str = ""
    restated: int = 0
    unchanged: int = 0
    not_read: int = 0


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


def _arrives(mode: object) -> str:
    if isinstance(mode, int):
        named = str(sidecar_pb2.ObservationMode.Name(mode))
        return named.removeprefix("OBSERVATION_MODE_").lower()
    return str(mode)


def datasets_now() -> Datasets:
    dgm = HELD.dgm
    instance = dgm.plugin.identity.instance_id if dgm else VENUE
    served = []
    for declared in CATALOGUE:
        name = f"{instance}:{declared.key}"
        sent = dgm.sent_to(declared.key) if dgm else None
        served.append(
            data.Served(
                dataset=name,
                data=", ".join(t.removeprefix("meridian.v1.") for t in declared.data_types),
                arrives=", ".join(_arrives(m) for m in declared.modes),
                day=f"{declared.day_time_zone}, ends {declared.day_end_minute // 60:02d}:"
                f"{declared.day_end_minute % 60:02d}",
                default_terms=_terms(declared.licence_default),
                entitled_at_vendor=ENTITLED,
                recorded=sent.recorded if sent else 0,
                declined=sent.declined if sent else 0,
                last_recorded=_when(sent.last_ns) if sent else "",
                licensed=dgm is not None and name in dgm.licensed,
            )
        )
    if dgm is None:
        return Datasets(served=served)
    standing = set(dgm.standing)
    wants: dict[str, Want] = {}
    for heard in dgm.heard:  # newest first: a want heard again is one row, as last heard
        if heard.want_id in wants:
            continue
        kept = heard.want_id in standing
        wants[heard.want_id] = Want(
            dataset=heard.dataset,
            asked=heard.asked,
            subjects=heard.subjects,
            recorded=heard.recorded,
            declined=heard.declined,
            state=_state(kept, heard.standing),
            want_id=heard.want_id,
            kept_current=kept,
        )
    kept_first: list[data.Want] = sorted(wants.values(), key=lambda w: w.state != "Kept current")
    return Datasets(
        served=served,
        wants=kept_first,
        reads_instruments=dgm.reads_instruments,
        standing_wants=len(standing),
        products_listed=len(dgm.products.listed),
        products_mapped=len(dgm.products.mapped()),
        subjects_named=len(dgm.products.subjects),
        assets_not_resolved=sorted(dgm.products.missing),
        map_read_at=_when(dgm.products.read_at_ns),
        restated=dgm.counts.restated,
        unchanged=dgm.counts.unchanged,
        not_read=dgm.counts.unread,
    )


def _state(kept_current: bool, standing: bool) -> str:
    return "Kept current" if kept_current else "Withdrawn" if standing else "Answered"


@pages.page("/datasets", "Datasets", levels="admin", answers=Datasets, name="read_datasets")
def datasets(request: meridian.Request) -> meridian.Response:
    """What the plugin serves the lake: each dataset of its catalogue and
    whether it is licensed, what the venue entitles it to, what it recorded
    and declined; the wants it heard and the standing wants it keeps
    current; whether it may read an instrument's record; and the product
    map."""
    return pages.answer("datasets.html", datasets_now(), venue=NAME)


# ── Figures ─────────────────────────────────────────────────────────────────


def figures_now() -> list[Figure]:
    """The data role's figures on the Summary, in its order: Connection,
    Entitled at the vendor, Stream and Last recorded. The venue states no
    running rate to measure against, so Rate use is left out."""
    dgm = HELD.dgm
    if dgm is None:
        return data.figures(connection="Answering")
    said = connection_now()
    if said.mode == "Sample data":
        answering: str | Figure = "Sample data"
    elif said.healthy:
        answering = "Answering"
    else:
        answering = Figure("", "Not answering", state="error", why=said.last_error[:200] or None)
    stream: str | Figure = "Off"
    if said.stream_state != "off":
        listings = f"{said.streaming} {'listing' if said.streaming == 1 else 'listings'}"
        stream = Figure(
            "",
            listings,
            state=None if said.stream_state == "open" else "warn",
            why=None if said.stream_state == "open" else f"The stream is {said.stream_state}",
        )
    last = max((dgm.sent_to(key).last_ns for key in (DAILY, LIVE, TRADES)), default=0)
    return data.figures(
        connection=answering,
        entitled_at_vendor=f"{len(CATALOGUE)} of {len(CATALOGUE)} datasets",
        stream=stream,
        last_recorded=datetime.fromtimestamp(last / 1e9, tz=UTC) if last else None,
    )
