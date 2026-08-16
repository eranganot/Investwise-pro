"""Phase N - what the money actually did, recorded daily.

Everything historical this app showed before now was a BACKFILL: today's
holdings priced back through their own past. That answers "what would this book
have done", which is a real question, but not "what did my money do".

**Past NAV cannot be recovered, only started.** `grep` for `Transaction(` across
`app/` outside the models returns zero hits -- the trade ledger has never been
written -- and `whs_snapshots` stores health scores, not value, and is never
written either. So there is no record of what the book was worth on any past
day, and no way to reconstruct one. Every day without a snapshot is a day of
real history that will never exist.

**The correctness issue that dominates this module: deposits are not
performance.** Put 5,000 into a 20,000 book and a naive endpoint-to-endpoint
percentage reads +25% -- the chart announcing a great day when there was a bank
transfer. Neutralising that is what `time_weighted` does, using the dated
`contributions` ledger. Without it this feature is WORSE than the backfill it
replaces: same wrongness, more authority, and it errs in the flattering
direction.

The pure functions here take plain data and are importable without the app, so
the return arithmetic is testable without a database.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = "n1"

# Below this, a percentage line is noise rather than information. The card says
# how many days it has instead of drawing two points and calling it a trend.
MIN_POINTS_FOR_SERIES = 3

RANGE_DAYS = {"1W": 7, "1M": 31, "1Q": 93, "1Y": 366, "MAX": 36500}


# --------------------------------------------------------------------------
# the return arithmetic -- pure, no I/O, no ORM
# --------------------------------------------------------------------------

def net_flow_between(flows: list[dict], after: str, upto: str) -> float:
    """Signed external cash moved in the window (after, upto].

    `flows` are contribution-ledger rows: {"occurred_at": ISO, "amount_ils": +/-}.
    Withdrawals are already stored negative, so this sums directly.

    Half-open on purpose: a deposit dated exactly on the earlier snapshot's day
    is already inside that snapshot's NAV, and counting it again would subtract
    money that was never added during this period.
    """
    total = 0.0
    for f in flows or []:
        d = str(f.get("occurred_at") or "")[:10]
        if not d:
            continue
        if after < d <= upto:
            total += float(f.get("amount_ils") or 0.0)
    return round(total, 2)


def time_weighted(points: list[dict], flows: list[dict] | None = None) -> dict:
    """Chain sub-period returns across every cash-flow boundary.

    For consecutive snapshots V0 (at t0) and V1 (at t1), with net external flow
    F arriving during (t0, t1]:

        r = (V1 - F) / V0 - 1

    and the series compounds Prod(1 + r) - 1.

    F is assumed to arrive at the END of its period. With daily snapshots the
    error from that is a single day of market movement on the deposit, which is
    the standard simplification -- stated here rather than hidden, because the
    alternative (Modified Dietz or true unit-pricing) needs the flow's timestamp
    within the day and the ledger only records a date.

    Returns the cumulative percentage series aligned to `points`, so index 0 is
    always 0.0 -- the first snapshot is the baseline, not a return.
    """
    flows = flows or []
    out = {"dates": [], "pct": [], "nav_ils": [], "flows_ils": [],
           "total_pct": 0.0, "flow_total_ils": 0.0, "points": len(points or [])}
    if not points:
        return out

    cum = 1.0
    prev = None
    for p in points:
        d = str(p.get("as_of"))
        nav = float(p.get("nav_ils") or 0.0)
        if prev is None:
            out["dates"].append(d); out["pct"].append(0.0)
            out["nav_ils"].append(round(nav, 2)); out["flows_ils"].append(0.0)
            prev = (d, nav)
            continue
        f = net_flow_between(flows, prev[0], d)
        # A zero or negative starting value cannot produce a return; carry the
        # level rather than inventing one or dividing by zero.
        r = ((nav - f) / prev[1] - 1.0) if prev[1] > 0 else 0.0
        cum *= (1.0 + r)
        out["dates"].append(d)
        out["pct"].append(round((cum - 1.0) * 100.0, 4))
        out["nav_ils"].append(round(nav, 2))
        out["flows_ils"].append(f)
        out["flow_total_ils"] = round(out["flow_total_ils"] + f, 2)
        prev = (d, nav)

    out["total_pct"] = out["pct"][-1] if out["pct"] else 0.0
    return out


def simple_change_pct(points: list[dict]) -> float:
    """Endpoint-to-endpoint value change, INCLUDING deposits.

    Reported next to the time-weighted figure so the two can be compared, never
    instead of it. This is the number a deposit inflates, and showing both is
    how the card explains why they differ.
    """
    if not points or len(points) < 2:
        return 0.0
    a = float(points[0].get("nav_ils") or 0.0)
    b = float(points[-1].get("nav_ils") or 0.0)
    return round((b / a - 1.0) * 100.0, 2) if a > 0 else 0.0


def gaps(points: list[dict], max_gap_days: int = 4) -> list[dict]:
    """Stretches where the job did not run.

    Reported, never interpolated. A straight line drawn across a missing week
    is a claim that nothing happened, which is exactly the kind of invented
    number this codebase has been removing everywhere else.
    """
    out = []
    for a, b in zip(points or [], (points or [])[1:]):
        try:
            d0 = date.fromisoformat(str(a["as_of"]))
            d1 = date.fromisoformat(str(b["as_of"]))
        except (ValueError, KeyError, TypeError):
            continue
        n = (d1 - d0).days
        if n > max_gap_days:
            out.append({"from": a["as_of"], "to": b["as_of"], "days": n})
    return out


# --------------------------------------------------------------------------
# the session-bound half
# --------------------------------------------------------------------------

async def record_for(session, user, *, source: str = "job") -> dict:
    """Upsert today's snapshot for one user. Idempotent within a day."""
    from sqlalchemy import select

    from app.models.tables import NavSnapshot
    from app.services.intake_service import list_positions
    from app.services.strategy_service import _snapshot

    rows = await list_positions(session, user)
    if not rows:
        return {"ok": False, "reason": "no holdings"}
    # NAV from the app's own snapshot -- the same single source the sleeve path
    # and the solver use. A second NAV implementation is two numbers that can
    # disagree on one screen.
    snap = _snapshot(rows)
    nav = float(snap.get("nav") or 0.0)
    if nav <= 0:
        return {"ok": False, "reason": "nav is zero"}

    cash = 0.0
    try:
        from app.services.intake_service import get_cash
        cash = float(await get_cash(session, user) or 0.0)
    except Exception:  # noqa: BLE001
        logger.warning("nav snapshot: cash unavailable", exc_info=False)

    invested = 0.0
    try:
        from app.services.intake_service import list_contributions
        invested = round(sum(float(c["amount_ils"]) for c in
                             await list_contributions(session, user)), 2)
    except Exception:  # noqa: BLE001
        logger.warning("nav snapshot: contributions unavailable", exc_info=False)

    today = datetime.now(timezone.utc).date().isoformat()
    row = (await session.execute(
        select(NavSnapshot).where(NavSnapshot.subject == user.email,
                                  NavSnapshot.as_of == today))).scalar_one_or_none()
    if row is None:
        row = NavSnapshot(subject=user.email, as_of=today)
        session.add(row)
    row.nav_ils = nav
    row.cash_ils = cash
    row.invested_ils = invested
    row.positions = len(rows)
    row.source = source[:16]
    row.engine_version = SNAPSHOT_VERSION
    return {"ok": True, "as_of": today, "nav_ils": round(nav, 2),
            "invested_ils": invested, "positions": len(rows)}


async def record_health_for(session, user) -> dict:
    """Also snapshot the health score, because the table already exists for it.

    `whs_snapshots` has score/risk/tax/alloc/liq and a `taken_at`, and nothing
    has ever written it -- which is why "my score was 92 last week, what
    changed?" has no answer. One job, two rows, and the question becomes
    answerable from next week rather than never.
    """
    from app.models.tables import WhsSnapshot
    from app.services.intake_service import list_positions
    from app.services.plan_service import effective_caps, get_plan
    from app.services.portfolio_analytics import compute_snapshot, health_scores

    rows = await list_positions(session, user)
    if not rows:
        return {"ok": False, "reason": "no holdings"}
    snap = compute_snapshot([{
        "ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
        "cost_basis": float(p.cost_basis or 0), "current_price": float(p.current_price or 0),
        "meta": dict(p.meta or {}),
    } for p in rows])
    if not snap.get("nav"):
        return {"ok": False, "reason": "nav is zero"}
    caps = effective_caps(await get_plan(session, user))
    sc = health_scores(snap, caps["concentration_cap"], caps.get("volatility_cap"))
    ent = getattr(rows[0], "account", None)
    entity_id = getattr(getattr(ent, "entity_id", None), "hex", None) or getattr(ent, "entity_id", None)
    if entity_id is None:
        return {"ok": False, "reason": "no entity to attach the score to"}
    session.add(WhsSnapshot(
        entity_id=entity_id,
        score=float(sc["wealth_health_score"]),
        risk=float(sc["risk_score"]), tax=float(sc["tax_efficiency_score"]),
        alloc=float(sc["diversification_score"]), liq=float(sc["liquidity_score"]),
        thematic=0.0,
        # The CAPS travel with the score. Without them a later reader cannot
        # tell a book that got worse from a yardstick that got stricter -- and
        # those need opposite responses.
        detail={"concentration_cap": caps["concentration_cap"],
                "volatility_cap": caps.get("volatility_cap"),
                "max_weight": round(snap.get("max_weight") or 0.0, 4),
                "avg_volatility_pct": round(snap.get("avg_volatility_pct") or 0.0, 2),
                "version": SNAPSHOT_VERSION},
    ))
    return {"ok": True, "score": sc["wealth_health_score"]}


async def series_for(session, user, *, range_key: str = "1M") -> dict:
    """The recorded history, as a time-weighted percentage series."""
    from sqlalchemy import select

    from app.models.tables import NavSnapshot
    from app.services.intake_service import list_contributions

    days = RANGE_DAYS.get((range_key or "1M").upper())
    if days is None:
        return {"ok": False, "reason": "unknown range",
                "detail": f"range must be one of {', '.join(RANGE_DAYS)}"}

    rows = (await session.execute(
        select(NavSnapshot).where(NavSnapshot.subject == user.email)
        .order_by(NavSnapshot.as_of))).scalars().all()
    points = [{"as_of": r.as_of, "nav_ils": float(r.nav_ils or 0.0),
               "invested_ils": float(r.invested_ils or 0.0)} for r in rows]
    recorded_since = points[0]["as_of"] if points else None

    if days < 36500 and points:
        cutoff = (date.today().toordinal() - days)
        points = [p for p in points
                  if date.fromisoformat(p["as_of"]).toordinal() >= cutoff]

    if len(points) < MIN_POINTS_FOR_SERIES:
        # Honest empty state. Never seeded from the backfill: a seeded curve is
        # the reconstruction wearing the real thing's label, which is the
        # confusion this phase exists to end.
        return {"ok": False, "reason": "not enough history",
                "recorded_since": recorded_since, "points": len(points),
                "needs": MIN_POINTS_FOR_SERIES,
                "detail": ("recording started " + recorded_since
                           if recorded_since else "no snapshots recorded yet")}

    flows = await list_contributions(session, user)
    twr = time_weighted(points, flows)
    return {
        "ok": True, "kind": "nav_snapshots", "range": (range_key or "1M").upper(),
        "recorded_since": recorded_since,
        "dates": twr["dates"], "pct": twr["pct"], "nav_ils": twr["nav_ils"],
        "total_pct": twr["total_pct"],
        # Both numbers, always. The gap between them IS the deposits, and
        # showing only one invites the reader to mistake a transfer for a gain.
        "simple_change_pct": simple_change_pct(points),
        "flow_total_ils": twr["flow_total_ils"],
        "gaps": gaps(points),
        "engine_version": SNAPSHOT_VERSION,
        "window": {"start": twr["dates"][0], "end": twr["dates"][-1],
                   "sessions": len(twr["dates"]), "kind": "nav_snapshots"},
    }


# --------------------------------------------------------------------------
# the scheduled job
# --------------------------------------------------------------------------

def run_nav_snapshot_blocking() -> dict:
    """Daily: one NAV row and one health row per user with holdings."""
    import asyncio

    async def _run() -> dict:
        from sqlalchemy import select

        from app.core.database import async_session
        from app.models.tables import User
        done, failed, health = 0, 0, 0
        async with async_session() as session:
            users = (await session.execute(select(User))).scalars().all()
            for u in users:
                try:
                    r = await record_for(session, u)
                    if r.get("ok"):
                        done += 1
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.warning("nav snapshot failed for %s: %s", u.email, exc)
                try:
                    # Non-fatal on purpose: losing the score row must not cost
                    # the NAV row, which is the one that cannot be recovered.
                    h = await record_health_for(session, u)
                    if h.get("ok"):
                        health += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("health snapshot failed for %s: %s", u.email, exc)
            await session.commit()
        return {"nav_rows": done, "health_rows": health, "failed": failed}

    return asyncio.run(_run())
