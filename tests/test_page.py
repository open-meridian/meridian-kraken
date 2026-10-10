"""The pages, Connection and Datasets, and their read tools.

The same file in meridian-coinbase and meridian-kraken (`make suite-same`).
Asked in-process as the sidecar would ask them (`meridian.testing`), after
the plugin has answered wants on its synthetic exchange: each page is served
under Manage alone, shows no price, fits its rows on one line each, and its
tool answers what the page shows.
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


def test_the_tools_answer_what_the_pages_show(worked: tuple[Any, Lake]) -> None:
    dgm, _ = worked
    client = PageClient(venue.page.pages)
    assert {t.name for t in venue.page.pages.tools} >= set(TOOLS)
    connection = client.call_tool("read_connection", level="admin")
    assert connection.outcome == "unchanged", connection.json
    data = connection.data
    assert (data["venue"], data["mode"], data["venue_id"]) == (
        dgm.market.name,
        "synthetic",
        dgm.venue.venue_id,
    )
    assert data["calls"] > 0 and data["healthy"] is True
    page = client.get("/", "admin").text
    assert dgm.venue.venue_id in page and data["allowed"] in page.replace("&#39;", "'")

    datasets = client.call_tool("read_datasets", level="admin").data
    assert [d["dataset"] for d in datasets["datasets"]] == [
        f"{venue.VENUE}:daily",
        f"{venue.VENUE}:live",
    ]
    assert all(d["day"] == "Etc/UTC, ends 00:00" for d in datasets["datasets"])
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


def test_the_wants_are_searched(worked: tuple[Any, Lake]) -> None:
    client = PageClient(venue.page.pages)
    text = client.get("/datasets", "admin", q="kept current").text
    assert 'type="search"' in text and "WNT-2" in text and "WNT-1" not in text
    assert "1 of 2" in text
    both = client.get("/datasets", "admin", q=f"{venue.VENUE}:").text
    assert "WNT-1" in both and "WNT-2" in both and "2 of 2" in both
    assert "No want matches" in client.get("/datasets", "admin", q="nothing-like-it").text
    assert len(client.call_tool("read_datasets", level="admin").data["wants"]) == 2


def test_a_tool_is_refused_below_manage(worked: tuple[Any, Lake]) -> None:
    client = PageClient(venue.page.pages)
    for name in TOOLS:
        assert client.call_tool(name, level="read").status == 403
