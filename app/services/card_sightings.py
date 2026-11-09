"""C34 -- when each Today card was first and last seen, and which ones no push named.

Production kept no record of which cards appeared on Today. Pushes run every 30
minutes (PR 156, D140), so a card that lived less than that could appear and go
without a push, and nobody could count how often. Every Today build -- the app's
own requests and the push job's -- now stamps the cards it produced here, and
`summary` answers the question the backlog asked: how many cards were seen and
never pushed, and how long did they live?

One row per (subject, card id): first_seen, last_seen and how many builds saw it.
No card body, no amounts. Rows not seen for RETAIN_DAYS are deleted on write.

NEVER ALLOWED TO BREAK TODAY. The writer runs in a SAVEPOINT and swallows its own
failure (logged): a bookkeeping table must not cost the user their cards.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import CardSighting, NotifiedEvent

logger = logging.getLogger(__name__)

#: Rows not seen for this long are deleted. Long enough to count a month of misses.
RETAIN_DAYS = 60
#: The push job's interval (PR 156). A card that lived less than this could be missed.
PUSH_INTERVAL_MIN = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(v: datetime) -> datetime:
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


async def record(session: AsyncSession, subject: str, cards: list[dict],
                 now: datetime | None = None) -> int:
    """Stamp every card with an id. Returns how many rows were written (0 on failure)."""
    ids = {}
    for c in cards or []:
        cid = str(c.get("id") or "")[:64]
        if cid:
            ids[cid] = c
    if not subject or not ids:
        return 0
    now = now or _now()
    try:
        async with session.begin_nested():
            have = {r.card_id: r for r in (await session.execute(
                select(CardSighting).where(CardSighting.subject == subject,
                                           CardSighting.card_id.in_(list(ids))))).scalars()}
            for cid, c in ids.items():
                kind = str(((c.get("apply") or {}).get("kind")) or c.get("dimension") or "")[:40]
                title = str(c.get("title") or "")[:200]
                row = have.get(cid)
                if row is None:
                    session.add(CardSighting(subject=subject, card_id=cid, kind=kind, title=title,
                                             first_seen=now, last_seen=now, seen_count=1))
                else:
                    row.last_seen = now
                    row.seen_count = int(row.seen_count or 0) + 1
                    row.kind, row.title = kind, title
            await session.execute(delete(CardSighting).where(
                CardSighting.subject == subject,
                CardSighting.last_seen < now - timedelta(days=RETAIN_DAYS)))
            await session.flush()
        return len(ids)
    except Exception:  # noqa: BLE001 -- bookkeeping must never cost the user Today
        logger.warning("card sightings not recorded for %s", subject, exc_info=True)
        return 0


async def summary(session: AsyncSession, subject: str, days: int = 14,
                  now: datetime | None = None) -> dict:
    """Cards seen in the last `days`, and which of them no push ever named."""
    from app.services.push_service import _ledger_key, card_signature

    now = now or _now()
    since = now - timedelta(days=max(1, int(days)))
    rows = (await session.execute(
        select(CardSighting).where(CardSighting.subject == subject,
                                   CardSighting.last_seen >= since)
        .order_by(CardSighting.first_seen.desc()))).scalars().all()
    sigs = {_ledger_key(card_signature({"id": r.card_id})) for r in rows}
    pushed = set()
    if sigs:
        pushed = set((await session.execute(
            select(NotifiedEvent.signature).where(NotifiedEvent.subject == subject,
                                                  NotifiedEvent.signature.in_(list(sigs))))).scalars())
    out, missed, short = [], 0, 0
    for r in rows:
        first, last = _aware(r.first_seen), _aware(r.last_seen)
        lived_min = round((last - first).total_seconds() / 60.0, 1)
        was_pushed = _ledger_key(card_signature({"id": r.card_id})) in pushed
        is_short = lived_min < PUSH_INTERVAL_MIN
        missed += 0 if was_pushed else 1
        short += 1 if (is_short and not was_pushed) else 0
        out.append({"card_id": r.card_id, "kind": r.kind, "title": r.title,
                    "first_seen": first.isoformat(), "last_seen": last.isoformat(),
                    "seen_count": int(r.seen_count or 0), "lived_min": lived_min,
                    "pushed": was_pushed})
    return {"days": int(days), "since": since.isoformat(), "count": len(out),
            "never_pushed": missed, "never_pushed_short_lived": short,
            "push_interval_min": PUSH_INTERVAL_MIN,
            "note": ("Seen = produced by a Today build (your app or the push job). A card "
                     "still on Today may simply not have been pushed YET; short-lived and "
                     "never pushed is the case C34 is about."),
            "cards": out}
