"""PR 74 -- ONE goal, measured on the money you put in.

Before this there were four goals (Eran's item 4, 2026-09-28):

  * `/plan` derived the target LIVE from NAV: ``NAV x (1+roi)^years``. Progress
    was therefore ``NAV / (NAV x 1.3^6) = 0.2072`` -- a constant. No market move,
    gain or loss, could ever change it.
  * the "You're behind" card read the STORED ``plan.target_amount`` (a different
    number, written at the last save), counted 5 years from the deadline where
    the plan screen counted 6 from the horizon, and projected at a static 8.5%
    where the plan screen used the measured ~25%.
  * two cards ("Set a target you'll actually hit", "Give it more time") rewrote
    the target or the horizon to whatever the projection said -- the goalposts
    moved to meet the ball.

Now:

  **target** = sum over every contribution *i* of
  ``amount_i x (1 + annual_roi) ^ (years from its date to the deadline)``.

It reads the ``contributions`` ledger, which only a deposit or a withdrawal
changes. So the target moves only when money goes in or out, or when you edit
the ROI or the deadline -- never because the market moved. Progress is
``NAV / target``, so gains now move it.

**on_track_value** is the same sum compounded only to TODAY: where the book
should be right now if it had earned exactly your ROI. NAV against it is the
honest ahead/behind.

**The deadline governs** (Eran, Q1): 31 Dec of the plan's deadline year. With no
deadline it is the first contribution's date plus the horizon -- still a fixed
date, so the goal cannot drift with the calendar.

Every consumer calls ``compute_goal``: /plan, /plan/projection, the behind-goal
cards, the risk service, and (through the projection) the digest and health.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Contribution, User

PERIODS_PER_YEAR = {"monthly": 12, "quarterly": 4, "yearly": 1}
_DAYS_PER_YEAR = 365.25
_PER_WORD = {"monthly": "month", "quarterly": "quarter", "yearly": "yr"}


def annual_roi(plan) -> float | None:
    """The plan's ROI as a yearly rate (0.30 for 30%/yr), or None when unset.

    A per-period ROI compounds per period, exactly as the old `_auto_target`
    did: 2%/month is ``1.02^12 - 1`` a year, not 24%."""
    if plan is None or getattr(plan, "target_roi_pct", None) is None:
        return None
    r = float(plan.target_roi_pct) / 100.0
    n = PERIODS_PER_YEAR.get(getattr(plan, "target_roi_period", None) or "yearly", 1)
    return (1.0 + r) ** n - 1.0


def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def deadline_of(plan, first_deposit: date | None) -> tuple[date | None, str | None]:
    """(deadline, source). The plan's deadline year governs -> 31 Dec of it.
    A full ISO date is honoured as given. Without one: first deposit + horizon,
    or -- no deposits logged -- the plan's creation date + horizon. Both are
    FIXED dates; "today + horizon" would slide with the calendar forever."""
    if plan is None:
        return None, None
    raw = str(getattr(plan, "target_date", "") or "").strip()
    if raw:
        if len(raw) >= 10:
            try:
                return date.fromisoformat(raw[:10]), "deadline"
            except ValueError:
                pass            # "2031/06/30": the YEAR still governs, below
        try:
            return date(int(raw[:4]), 12, 31), "deadline"
        except (ValueError, TypeError):
            pass
    h = getattr(plan, "horizon_years", None)
    anchor, src = first_deposit, "horizon_from_first_deposit"
    if anchor is None:
        anchor, src = _as_date(getattr(plan, "created_at", None)), "horizon_from_plan_created"
    if h and anchor is not None:
        try:
            return anchor.replace(year=anchor.year + int(h)), src
        except ValueError:          # 29 Feb
            return anchor.replace(year=anchor.year + int(h), day=28), src
    return None, None


def years_between(a: date, b: date) -> float:
    return (b - a).days / _DAYS_PER_YEAR


def compound(flows: list[tuple[date, float]], roi: float, to: date) -> float:
    """Each flow grown at `roi` a year from its date to `to`. A flow dated after
    `to` counts at face value (it has not had time to grow) -- never shrunk."""
    total = 0.0
    for at, amt in flows:
        t = max(0.0, years_between(at, to))
        total += amt * (1.0 + roi) ** t
    return total


def goal_from(plan, flows: list[tuple[date, float]], nav: float | None,
              *, today: date | None = None) -> dict:
    """Pure: the whole goal from the plan, the dated ledger and NAV."""
    today = today or datetime.now(timezone.utc).date()
    out: dict = {"configured": plan is not None, "basis": None, "target": None,
                 "deadline": None, "deadline_source": None, "years_left": None,
                 "annual_roi_pct": None, "put_in": None, "on_track_value": None,
                 "ahead_by": None, "progress": None, "breakdown": None,
                 "contributions": [{"at": at.isoformat(), "amount_ils": round(a, 2)}
                                   for at, a in flows]}
    if plan is None:
        return out
    roi = annual_roi(plan)
    first = min((at for at, _ in flows), default=None)
    deadline, src = deadline_of(plan, first)
    out.update(deadline=deadline.isoformat() if deadline else None, deadline_source=src,
               years_left=(round(max(0.0, years_between(today, deadline)), 3)
                           if deadline else None),
               annual_roi_pct=round(roi * 100, 4) if roi is not None else None,
               put_in=round(sum(a for _, a in flows), 2) if flows else None)

    if roi is not None and flows and deadline is None:
        # Deposits and an ROI but no date to grow them to. The stored column
        # is a cache of an older formula; quoting it would be a stale number.
        out.update(basis="no_deadline",
                   breakdown="Set a deadline year and the target is derived from what you put in.")
    elif roi is not None and flows:
        target = compound(flows, roi, deadline)
        on_track = compound(flows, roi, min(today, deadline))
        if target <= 0:
            # Withdrawals at or above the deposits. A target of 0 or less is not
            # a goal: every probability against it reads 100%.
            out.update(basis="nothing_put_in",
                       breakdown=(f"Withdrawals equal or exceed deposits (net "
                                  f"₪{out['put_in']:,.0f}), so there is no money-in goal to track."))
            return out
        out.update(basis="contributions", target=round(target, 2),
                   on_track_value=round(on_track, 2))
        per = getattr(plan, "target_roi_period", None) or "yearly"
        out["breakdown"] = (
            f"₪{out['put_in']:,.0f} put in, compounding {float(plan.target_roi_pct):g}%/"
            f"{_PER_WORD.get(per, 'yr')} from each deposit's date "
            f"to {deadline.strftime('%d %b %Y')} → ₪{target:,.0f}. It changes only when "
            f"you add or withdraw money, or edit the ROI or the deadline -- not when "
            f"the market moves.")
    elif getattr(plan, "target_amount", None) is not None:
        # A fixed amount the user typed (no ROI), or no ledger yet: the saved
        # number is already frozen, it just is not derived.
        out.update(basis="saved_amount", target=round(float(plan.target_amount), 2))
        if roi is not None and not flows:
            out["breakdown"] = ("No deposits are logged yet, so the goal is the saved "
                                "amount. Log what you put in and it is derived from that.")
    if out["target"] and nav is not None:
        out["progress"] = round(min(1.0, max(0.0, float(nav) / out["target"])), 4)
    if out["on_track_value"] is not None and nav is not None:
        out["ahead_by"] = round(float(nav) - out["on_track_value"], 2)
    return out


async def ledger(session: AsyncSession, user: User) -> list[tuple[date, float]]:
    res = await session.execute(
        select(Contribution.occurred_at, Contribution.amount_ils)
        .where(Contribution.subject == user.email)
        .order_by(Contribution.occurred_at.asc()))
    flows = []
    for at, amt in res.all():
        d = _as_date(at)
        if d is not None and amt is not None:
            flows.append((d, float(amt)))
    return flows


async def compute_goal(session: AsyncSession, user: User, plan, nav: float | None,
                       *, today: date | None = None) -> dict:
    return goal_from(plan, await ledger(session, user), nav, today=today)
