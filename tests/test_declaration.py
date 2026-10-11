"""The declaration: the catalogue declared from code, as registration and
`meridian plugin upload` read it."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from kraken.declaration import CATALOGUE, DECLARATION, SAMPLE_DATA


def test_three_datasets_daily_live_and_trades_on_the_utc_day() -> None:
    assert [d.key for d in CATALOGUE] == ["daily", "live", "trades"]
    for dataset in CATALOGUE:
        assert (dataset.vendor, dataset.day_time_zone, dataset.day_end_minute) == (
            "Kraken",
            "Etc/UTC",
            0,
        )
        assert dataset.venue_id == "", "the venue's ID is resolved at run time, never invented"
    daily, live, trades = CATALOGUE
    assert list(daily.data_types) == [
        "meridian.v1.Price",
        "meridian.v1.Bar",
        "meridian.v1.Bar.vwap",
        "meridian.v1.Bar.trade_count",
    ]
    assert daily.history == 720, "OHLC's 720 most recent days"
    assert list(live.data_types) == ["meridian.v1.Price", "meridian.v1.Quote"]
    assert list(trades.data_types) == ["meridian.v1.Trade"], "trades apart from live prices"
    assert daily.licence_default and daily.licence_default.kept
    for kept_two_days in (live, trades):
        licence = kept_two_days.licence_default
        assert licence and licence.kept and licence.retention_days == 2
    assert all(d.licence_default and d.licence_default.personal_use for d in CATALOGUE), (
        "one person's, without Kraken's permission for non-personal use"
    )


def test_no_key_needed_and_sample_data_a_developers_setting() -> None:
    # The data role's settings (spec/plugin-pages-follow-their-role): one
    # environment and no credential, so sample data alone, labelled as the
    # SDK's helper labels it.
    assert isinstance(DECLARATION.settings, tuple)
    [setting] = DECLARATION.settings
    assert (setting.name, setting.developer, setting.secret) == (SAMPLE_DATA, True, False)
    assert (setting.label, setting.default) == ("Sample data", False)
    assert DECLARATION.storage is not None


def test_upload_reads_the_same_declaration() -> None:
    printed = subprocess.run(
        [str(Path(sys.executable).parent / "meridian-declaration"),
         "kraken.declaration:DECLARATION"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    datasets = json.loads(printed.stdout)["catalogue"]["datasets"]
    assert [d["key"] for d in datasets] == ["daily", "live", "trades"]
    assert [d["modes"] for d in datasets] == [["pull", "push"], ["pull", "stream"],
                                              ["pull", "stream"]]  # fmt: skip
