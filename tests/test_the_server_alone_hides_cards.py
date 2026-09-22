"""PR 69 - a card the server shows is shown on every device.

OBSERVED 2026-09-22: the web Today showed "QQQ is closing on your take-profit"
(re-fired: "it asked for about 664 ILS when you filed it and asks for about
1,064 ILS now") and the SOXL 42% rule card; Eran's Android home-screen app
showed neither.

CAUSE: Ignore, Accept and Mark-as-done each also wrote a per-device hide
(`localStorage["iw_snoozed_v2"]`, 7 days, keyed by card id) and Today filtered
the server's list through it. The server brings a card back when its amount
moves >=25% or its rule fires again (PR 34); the device that pressed the button
kept hiding it. Reproduced in Chromium against the shipped `index.html` with the
server response mocked: with a saved hide the re-fired card is missing, without
one it shows, and with this change it shows either way.

WHY SILENT: the "N suggestions are hidden" line counted and named only what the
SERVER hid. A device-hidden card appeared nowhere.

These tests hold the contract at its source: nothing on the device may decide
which cards Today shows.
"""
from __future__ import annotations

import re
from pathlib import Path

SHELL = Path(__file__).resolve().parents[1] / "app" / "static_app" / "index.html"


def _src() -> str:
    return SHELL.read_text(encoding="utf-8")


def test_today_is_not_filtered_by_anything_stored_on_the_device():
    s = _src()
    for gone in ("snoozeAdd(", "activeSnoozed(", 'localStorage.getItem("iw_snoozed',
                 "localStorage.setItem(\"iw_snoozed"):
        assert gone not in s, f"a per-device card hide is back: {gone}"


def test_the_old_device_hides_are_cleared_once():
    s = _src()
    assert 'localStorage.removeItem("iw_snoozed_v2")' in s


def test_ignore_waits_for_the_server_before_reloading():
    s = _src()
    body = re.search(r"async function snooze\(id\)\{(.*?)\n\s*loadToday\(\);\}", s, re.S)
    assert body, "snooze() must be async and end by reloading Today"
    assert "await fetch(`/api/v1/recommendations/${id}/dismiss`" in body.group(1)


def test_the_hidden_count_is_the_servers_count():
    s = _src()
    assert "const hidden=r.dismissed_count||0;" in s
