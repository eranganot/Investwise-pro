"""Actionable recommendations endpoint (Today view)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.models.tables import User
from app.services.recommendations import (
    apply_recommendation, build_recommendations, complete_recommendation,
    dismiss_recommendation, restore_completed, restore_dismissed, restore_one,
)

router = APIRouter(prefix="/api/v1", tags=["recommendations"])


class CardState(BaseModel):
    """What the card SAID at the moment the user acted on it.

    Optional, and the caller that had the card on screen is the one that knows
    it. Rebuilding the whole Today list to look the card up would put a
    multi-second sweep on a button press -- `/recommendations` is the slowest
    path in the app.

    Trusting the client here is bounded on purpose: `recommendations.moved_on`
    only ever uses this to bring a card BACK early. A wrong value cannot extend
    a mute, hide anything, or change a single number in the book.
    """
    amount: float | None = None
    severity: str | None = None


@router.get("/recommendations")
async def recommendations(simulate_cash: float | None = Query(None, ge=0.0, le=10_000_000.0),
                          session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    """`?simulate_cash=3000` (PR 75): a read-only preview of the cash card as if
    you held that much cash. Returns only that card, marked simulated."""
    built = await build_recommendations(session, user, simulate_cash_ils=simulate_cash)
    if simulate_cash is None:
        # C34: stamp what Today showed. The same list the push job judges
        # (`recommendations`, not the collapsed overflow). Never raises.
        from app.services import card_sightings
        if await card_sightings.record(session, user.email, built.get("recommendations", [])):
            try:
                await session.commit()
            except Exception:  # noqa: BLE001 -- bookkeeping must not cost the user Today
                await session.rollback()
    return built


@router.get("/cards/sightings")
async def card_sightings_summary(days: int = Query(14, ge=1, le=60),
                                 session: AsyncSession = Depends(get_session),
                                 user: User = Depends(acting_user)) -> dict:
    """C34: the cards Today showed in the last `days`, and which no push named."""
    from app.services import card_sightings
    return await card_sightings.summary(session, user.email, days=days)


def refusal_text(result: dict) -> str:
    """C17: the whole refusal, as the Plan tab shows it -- the short error, the reason
    (what would work instead) and the legs it would have traded -- not just the error."""
    parts = [str(result.get("error") or "Not applied.").rstrip(".") + "."]
    reason = result.get("reason")
    if isinstance(reason, str) and reason.strip() and reason.strip() not in parts[0]:
        parts.append(reason.strip())
    if isinstance(result.get("funding_summary"), str) and result["funding_summary"].strip():
        parts.append(result["funding_summary"].strip())
    legs = [b for b in (result.get("buys") or []) if isinstance(b, dict) and b.get("ticker")]
    if legs:
        parts.append("It would have bought: " + ", ".join(
            f"{b['ticker']} \u20aa{float(b.get('buy_ils') or b.get('amount_ils') or 0):,.0f}" for b in legs[:6])
            + (f" and {len(legs) - 6} more" if len(legs) > 6 else "") + ".")
    return " ".join(parts)


@router.post("/recommendations/{rec_id}/accept", dependencies=[Depends(require_role(Role.ANALYST))])
async def accept_recommendation(rec_id: str, state: CardState | None = None,
                                choice: str | None = Query(None, pattern="^(sell|keep|buy|redeploy|trim|raise|o[1-9])$"),
                                level: float | None = Query(None, gt=0),
                                alloc: str | None = Query(None, max_length=600),
                                session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    """Apply a recommendation to the portfolio/plan immediately.

    `choice` (and, for "keep", an optional `level`) answer a decision card --
    a trading rule that fights a sleeve target. Other cards ignore both.
    """
    from app.services import buyback_hold as _bh
    try:
        result = await apply_recommendation(session, user, rec_id, choice=choice, level=level,
                                            alloc=alloc)
    except _bh.HeldBack as exc:
        # PR 139: a buy of a ticker under a stop hold. Every executor checks
        # before its first write (`assert_buyable`), so nothing was written;
        # the rollback discards only what the session had pending. 400, and
        # the card is NOT marked done -- same as any other refusal.
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Recommendation not found (it may have changed).")
    # A decision that was not taken is not done. Marking the card complete here
    # would hide a question the user has not answered yet.
    if result.get("ok") is False:
        raise HTTPException(status_code=400, detail=refusal_text(result))
    # Accepting/completing is NOT the same as ignoring: it goes in the completed
    # bucket so the card is gone rather than parked in the ignored list.
    await complete_recommendation(session, user, rec_id,
                                  state.model_dump() if state else None)
    return {"ok": True, **result}


@router.post("/recommendations/{rec_id}/dismiss", dependencies=[Depends(require_role(Role.ANALYST))])
async def dismiss_recommendation_route(rec_id: str, state: CardState | None = None,
                                       session: AsyncSession = Depends(get_session),
                                       user: User = Depends(acting_user)) -> dict:
    """Mark a recommendation as ignored so it stops showing AND stops notifying."""
    await dismiss_recommendation(session, user, rec_id,
                                 state.model_dump() if state else None)
    return {"ok": True}


@router.post("/recommendations/headsups/{hid}/dismiss", dependencies=[Depends(require_role(Role.ANALYST))])
async def dismiss_headsup_route(hid: str, session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    """PR 102: a heads-up dismissed is gone for good -- it never re-fires."""
    from app.services import headsup
    await headsup.dismiss(session, user, hid[:40])
    return {"ok": True}


@router.post("/recommendations/restore", dependencies=[Depends(require_role(Role.ANALYST))])
async def restore_recommendations(session: AsyncSession = Depends(get_session),
                                  user: User = Depends(acting_user)) -> dict:
    """Un-ignore every dismissed recommendation so they reappear on Today."""
    return {"ok": True, "restored": await restore_dismissed(session, user)}


@router.post("/recommendations/restore-completed", dependencies=[Depends(require_role(Role.ANALYST))])
async def restore_completed_recommendations(session: AsyncSession = Depends(get_session),
                                            user: User = Depends(acting_user)) -> dict:
    """Bring back cards previously marked as done."""
    return {"ok": True, "restored": await restore_completed(session, user)}


@router.post("/recommendations/{rec_id}/restore", dependencies=[Depends(require_role(Role.ANALYST))])
async def restore_one_recommendation(rec_id: str, session: AsyncSession = Depends(get_session),
                                     user: User = Depends(acting_user)) -> dict:
    """Bring back ONE ignored or done card, and nothing else.

    PR 72: "show them" used to call the bulk restores above, which erased every
    mark and put every card back live with a working Accept -- so looking at
    what you had done made it possible to do it twice. Looking is now read-only
    (the history list), and bringing a card back is this, one card at a time.
    """
    return {"ok": True, **(await restore_one(session, user, rec_id))}


@router.post("/holding-review/refresh", dependencies=[Depends(require_role(Role.ANALYST))])
async def refresh_holding_review(session: AsyncSession = Depends(get_session),
                                 user: User = Depends(acting_user)) -> dict:
    """PR 77: measure your holdings now instead of at 04:40. A MEASUREMENT --
    it backtests each holding's two sell signals and stores the result; nothing
    in the book changes. Takes a while: ~10 years of prices per holding."""
    from app.services import holding_review as hr
    from app.services.intake_service import is_cash_position, list_positions
    rows = await list_positions(session, user)
    tks = {p.ticker.upper() for p in rows
           if not is_cash_position(p.ticker, p.meta if isinstance(p.meta, dict) else None)}
    return {"measured": await hr.measure_for(session, tks)}
