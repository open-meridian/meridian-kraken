"""The pages, Connection and Datasets, and their read tools.

The same file in meridian-coinbase and meridian-kraken (`make suite-same`).
Asked in-process as the sidecar would ask them (`meridian.testing`), after
the plugin has answered wants on its synthetic exchange: each page is served
under Manage alone, shows no price, fits its rows on one line each, holds
the data role's sections in the role's order, and its tool answers what the
page shows under the role's names; the wants, which grow, carry the kit's
search and pager; Connection says how the stream has gone; Datasets says
which datasets a deployment admin has licensed (contract v20); and the
role's figures come in the role's order.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from meridian.operations import as_decimal
from meridian.testing import PageClient

import venue_cases as venue
from conftest import Lake, dgm_on, hear, wanted

TOOLS = {"read_connection": "/", "read_datasets": "/datasets"}


@pytest.fixture
async def worked() -> tuple[Any, Lake]:
    lake = Lake()
    dgm = dgm_on(lake)
    btc = await lake.held(dgm, venue.BASE)
    for want_id, dataset, standing in (("WNT-1", "daily", False), ("WNT-2", "live", True)):
        day = None if standing else venue.CLOSED
        await hear(lake, dgm, wanted(dgm, want_id, dataset, [btc], day=day, standing=standing))
    # The stream's first connection, as the synthetic venue answers it: it
    # opens, says its messages and drops.
    first = dgm.market.client.transport.stream_sessions()[0]

    async def replayed(_: str) -> Any:
        return venue.stream.Replayed(first, hold=False)

    dgm.stream.connect = replayed
    await dgm.stream.run(once=True)
    venue.page.hold(dgm)
    return dgm, lake


def _prices(lake: Lake) -> set[str]:
    return {str(as_decimal(r.price.amount)) for r in lake.rows()}


def test_each_page_is_served_under_manage_alone(worked: tuple[Any, Lake]) -> None:
    client = PageClient(venue.page.pages)
    served = {(r.page.path, r.level): r.response.status for r in client.every_page()}
    assert {path for path, _ in served} == {"/", "/datasets"}
    for (_, level), status in served.items():
        assert status == (200 if level == "admin" else 403), (level, status)


def test_no_page_shows_a_price(worked: tuple[Any, Lake]) -> None:
    _, lake = worked
    prices = _prices(lake)
    assert prices, "the plugin recorded prices for the pages to leave out"
    for path in TOOLS.values():
        text = PageClient(venue.page.pages).get(path, "admin").text
        shown = [p for p in prices if re.search(rf"(?<![\d.]){re.escape(p)}(?![\d.])", text)]
        assert not shown, (path, shown)


def test_every_table_is_one_line_rows(worked: tuple[Any, Lake]) -> None:
    for path in TOOLS.values():
        text = PageClient(venue.page.pages).get(path, "admin").text
        tables = re.findall(r"<table[^>]*>", text)
        assert tables and all('class="one-line"' in t for t in tables), (path, tables)


def _sections(text: str) -> list[str]:
    return re.findall(r"<h2>([^<]*)</h2>", text)


def test_each_page_holds_the_roles_sections_in_its_order(worked: tuple[Any, Lake]) -> None:
    client = PageClient(venue.page.pages)
    assert _sections(client.get("/", "admin").text) == ["Key", "Limits and use", "Stream"]
    assert _sections(client.get("/datasets", "admin").text) == [
        "Served", "Wants", "Subjects", venue.page.TITLE,
    ]  # fmt: skip


def test_the_tools_answer_what_the_pages_show(worked: tuple[Any, Lake]) -> None:
    dgm, _ = worked
    client = PageClient(venue.page.pages)
    assert {t.name for t in venue.page.pages.tools} >= set(TOOLS)
    connection = client.call_tool("read_connection", level="admin")
    assert connection.outcome == "unchanged", connection.json
    data = connection.data
    # The role's common fields, under the role's names.
    assert (data["mode"], data["credentials"], data["entitled_at_vendor"]) == (
        "Sample data", [], venue.page.ENTITLED,
    )  # fmt: skip
    assert data["calls"] > 0 and data["last_call_at"] and data["sessions"] == 0
    # The venue's, beside them.
    assert (data["venue"], data["venue_id"], data["healthy"]) == (
        dgm.market.name, dgm.venue.venue_id, True,
    )  # fmt: skip
    page = client.get("/", "admin").text.replace("&#39;", "'")
    assert "No key needed" in page and venue.page.ENTITLED in page
    assert "Sample data: the plugin answers from recorded responses" in page
    assert dgm.venue.venue_id in page and data["allowed"] in page
    assert (data["stream_state"], data["streaming"], data["stream_opened"],
            data["stream_dropped"]) == ("waiting", len(venue.STREAM_NAMES), 1, 1)  # fmt: skip
    assert data["stream_messages"] > 0 and data["last_message_at"]
    assert f"{data['streaming']} products on it;" in page
    assert "WebSocket" in [e["name"] for e in data["endpoints"]]

    datasets = client.call_tool("read_datasets", level="admin").data
    assert [d["dataset"] for d in datasets["served"]] == [
        f"{venue.VENUE}:daily",
        f"{venue.VENUE}:live",
        f"{venue.VENUE}:trades",
    ]
    assert all(d["day"] == "Etc/UTC, ends 00:00" for d in datasets["served"])
    daily, live, _ = datasets["served"]
    assert daily["recorded"] > 0 and daily["last_recorded"] and live["recorded"] > 0
    assert datasets["standing_wants"] == 1
    assert [(w["want_id"], w["kept_current"]) for w in datasets["wants"]] == [
        ("WNT-2", True),
        ("WNT-1", False),
    ]
    shown = client.get("/datasets", "admin").text
    assert (
        f"{datasets['products_mapped']} of {datasets['products_listed']} products mapped" in shown
    )
    assert datasets["subjects_named"] == 1 and datasets["reads_instruments"] is True
    assert "for 1 subject the lake asked for" in shown
    assert "Kept current" in shown


def test_datasets_says_which_a_deployment_admin_licensed(worked: tuple[Any, Lake]) -> None:
    dgm, _ = worked
    client = PageClient(venue.page.pages)
    served = client.call_tool("read_datasets", level="admin").data["served"]
    assert not any(d["licensed"] for d in served)
    assert client.get("/datasets", "admin").text.count("Not licensed yet") == 6  # row, detail
    dgm.licensed = frozenset({dgm.dataset("daily")})
    served = client.call_tool("read_datasets", level="admin").data["served"]
    assert [d["licensed"] for d in served] == [True, False, False]
    assert client.get("/datasets", "admin").text.count("Not licensed yet") == 4


def test_the_wants_carry_the_kits_search_beside_its_pager(worked: tuple[Any, Lake]) -> None:
    client = PageClient(venue.page.pages)
    text = client.get("/datasets", "admin").text
    assert re.findall(r"<om-pager[^>]*>", text) == ['<om-pager search="Search wants">']
    wants = text[text.index("<h2>Wants</h2>") : text.index("<h2>Subjects</h2>")]
    assert '<om-pager search="Search wants"><table class="one-line">' in wants
    assert "</table></om-pager>" in wants
    assert len(client.call_tool("read_datasets", level="admin").data["wants"]) == 2


def test_with_no_wants_datasets_says_the_next_step(lake: Lake) -> None:
    venue.page.hold(dgm_on(lake))
    asked = PageClient(venue.page.pages).get("/datasets", "admin").text
    assert "No wants yet" in asked and "Ask a deployment admin to license them." in asked
    assert "<om-pager" not in asked
    admin = PageClient(venue.page.pages, deployment_admin=True).get("/datasets", "admin").text
    assert 'data-om-opens="data-sources"' in admin


def test_the_roles_figures_in_its_order(worked: tuple[Any, Lake]) -> None:
    figures = venue.page.figures_now()
    assert [f.label for f in figures] == [
        "Connection", "Entitled at the vendor", "Stream", "Last recorded",
    ]  # fmt: skip
    assert figures[0].value == "Sample data" and figures[1].value == "3 of 3 datasets"
    assert figures[2].value == f"{len(venue.STREAM_NAMES)} listings" and figures[2].state == "warn"


def test_a_tool_is_refused_below_manage(worked: tuple[Any, Lake]) -> None:
    client = PageClient(venue.page.pages)
    for name in TOOLS:
        assert client.call_tool(name, level="read").status == 403
