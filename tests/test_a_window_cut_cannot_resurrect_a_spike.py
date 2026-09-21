"""A bad snapshot on the FIRST day of a window must still be left out.

Reported 2026-09-21 ("are you sure that I'm so behind the SPY?"): the 1M
account-history chart read **-21.9641%** against SPY -0.5263%. MAX, on the same
rows, read +4.0613%. The whole difference is the 08-22 row -- NAV 28,496.54, the
FX-bug spike PR 28 taught `_spike_indices` to exclude.

`_spike_indices` judges a row by its neighbours on both sides. `series_for`
cut the window FIRST, and 30 days before 2026-09-21 is 2026-08-22 -- so the bad
row became the window's first point, had no left neighbour, could never be
flagged, and anchored the next step at 21,369.63 / 28,496.54 - 1 = -25.01%.
Tomorrow the window would have moved past it and the chart would have "healed"
by itself, which is exactly how a defect like this survives.

WHY SILENT: PR 28's tests call `time_weighted` on the full series, and the MAX
range -- the one the fix was checked against -- has 08-16/08-17 in front of it.
Rows verbatim from /api/v1/portfolio/nav-history?range=MAX, 2026-09-21.

Not financial advice.
"""
from __future__ import annotations

import datetime as _dt
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.services.nav_history as nh  # noqa: E402
from app.main import app  # noqa: E402

LIVE = [("2026-08-16", 21168.33, 10), ("2026-08-17", 21168.33, 10),
        ("2026-08-22", 28496.54, 5),          # <- the FX-bug spike
        ("2026-08-24", 21369.63, 5), ("2026-08-25", 21534.31, 5),
        ("2026-08-26", 21540.83, 5), ("2026-08-27", 22114.59, 5),
        ("2026-08-28", 21397.00, 5), ("2026-08-29", 21333.59, 4),
        ("2026-08-30", 21333.59, 4), ("2026-08-31", 21474.35, 6),
        ("2026-09-01", 20777.45, 5), ("2026-09-02", 20950.86, 5),
        ("2026-09-03", 21207.78, 5), ("2026-09-04", 21883.94, 5),
        ("2026-09-08", 22276.85, 6), ("2026-09-11", 22378.87, 6),
        ("2026-09-14", 20725.72, 6), ("2026-09-17", 21686.62, 7),
        ("2026-09-18", 22237.52, 8)]


class _Sep21(_dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 21)


@pytest.fixture()
def client(monkeypatch, own_session):
    import asyncio
    from decimal import Decimal

    from app.models.tables import NavSnapshot
    from app.services.feed_service import SUPERADMIN_EMAIL

    monkeypatch.setattr(nh, "date", _Sep21)
    with TestClient(app) as c:
        async def _seed():
            async with own_session() as s:
                for as_of, nav, n in LIVE:
                    s.add(NavSnapshot(subject=SUPERADMIN_EMAIL, as_of=as_of,
                                      nav_ils=Decimal(str(nav)), cash_ils=Decimal("0"),
                                      invested_ils=Decimal("20000"), positions=n))
                await s.commit()
        asyncio.run(_seed())
        yield c


def test_the_1m_window_that_starts_on_the_spike_leaves_it_out(client):
    r = client.get("/api/v1/portfolio/nav-history?range=1M").json()
    assert r["ok"], r
    assert "2026-08-22" not in r["dates"], "the bad row is still plotted"
    assert r["dates"][0] == "2026-08-24"
    assert r["total_pct"] > -5.0, f"still anchored on the spike: {r['total_pct']}%"
    assert [x["as_of"] for x in r["suspect_snapshots"]] == ["2026-08-22"]


def test_the_1m_and_max_ranges_agree_about_the_same_days(client):
    """MAX leaves the 08-17 -> 08-24 step unmeasured (a hole, as on production:
    `pct` is null there), so from 08-24 on both ranges compound the same steps
    and must report the same return. Before the fix they differed by 26 points
    (-21.96 vs +4.06)."""
    one = client.get("/api/v1/portfolio/nav-history?range=1M").json()
    mx = client.get("/api/v1/portfolio/nav-history?range=MAX").json()
    assert mx["pct"][mx["dates"].index("2026-08-24")] is None
    assert one["total_pct"] == pytest.approx(mx["total_pct"], abs=0.05)
    assert "2026-08-22" not in mx["dates"]
