"""The Connectivity page, against a stand-in for the sidecar.

`meridian plugin check --run-tests` runs these. A `dgm` shows no market data,
so its one page is at Manage alone: it is refused under Open and View, and
shows the probe's findings, its datasets and its entitlements, never a price.
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import meridian
from meridian.operations import Operations
from meridian.testing import PageClient

from kraken import __main__ as plugin
from kraken.page import CONNECTIVITY, Connectivity, pages

from conftest import client


class Sidecar(Operations):
    """Who the plugin was launched as; it sends no operation from its page."""

    identity = meridian.Identity("kraken-1", roles=("dgm",))
    grants = meridian.Grants()

    async def _operate(self, method: Any, params: Any) -> Any:
        raise AssertionError(f"the page sent {method}, which a dgm's page never sends")


def page_client() -> PageClient:
    return PageClient(pages, Sidecar())


def test_the_page_is_served_under_manage_alone() -> None:
    served = {(r.page.path, r.level): r.response.status for r in page_client().every_page()}
    assert served == {("/", "admin"): 200, ("/", "write"): 403, ("/", "read"): 403}


def test_it_shows_connectivity_datasets_and_entitlements_and_no_market_data(
    tmp_path: Path,
) -> None:
    kraken, _ = client(tmp_path)
    plugin.probe(kraken)
    page = page_client().get("/", "admin").text
    assert "Kraken answers; its system is online." in page
    assert "4 online of 5 listed" in page
    assert "<code>daily</code>" in page and "UTC, ends 00:00" in page
    assert "nothing to resolve" in page and "<code>kraken-1</code>" in page
    # No price from any synthetic exchange reaches the page.
    for price in ("62800", "62000.12345678", "61000.5"):
        assert price not in page


def test_a_venue_that_does_not_answer_is_said_on_the_page(tmp_path: Path) -> None:
    kraken, _ = client(tmp_path, statuses={"/AssetPairs": [503]})
    plugin.probe(kraken)
    page = page_client().get("/", "admin").text
    assert "Kraken did not answer" in page and 'state="bad"' in page
    CONNECTIVITY.__dict__.update(Connectivity().__dict__)


def _status(address: str) -> int:
    try:
        with urllib.request.urlopen(address, timeout=10) as answer:
            return int(answer.status)
    except urllib.error.HTTPError as answer:
        return answer.code


async def test_a_request_the_sidecar_did_not_forward_is_turned_away() -> None:
    server = pages.serve(Sidecar(), 0)  # type: ignore[arg-type]
    try:
        address = f"http://127.0.0.1:{server.server_address[1]}/"
        assert await asyncio.to_thread(_status, address) == 401
    finally:
        server.shutdown()
        server.server_close()
