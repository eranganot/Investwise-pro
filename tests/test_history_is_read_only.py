"""PR 72 -- looking at what you already did must not let you do it again.

Eran, 2026-09-28, item 5: *"when I click on the show hidden/marked done
recommendation cards --> I can see them (which is great) but I don't have any
indication that they are old/approved/rejected/hidden."*

It was worse than missing labels. "show them" called the BULK restores, which
emptied the done and ignored maps and put every card back as a live card with a
working Accept. The audit log has `/recommendations/restore` at 2026-09-22
18:43 followed by accepts. The server already sent `hidden_cards` with
`how` and `hidden_at`; the screen threw both away.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.services import recommendations as rr
from app.services.feed_service import ensure_user

HTML = (Path(__file__).resolve().parent.parent / "app" / "static_app" / "index.html").read_text(
    encoding="utf-8")


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


@pytest.mark.asyncio
async def test_restoring_one_card_touches_no_other_mark():
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await ensure_user(s, "restore_one_probe@example.com")
            await rr.dismiss_recommendation(s, user, "rec_aaaaaa")
            await rr.dismiss_recommendation(s, user, "rec_bbbbbb")
            await rr.complete_recommendation(s, user, "rec_cccccc")
            await rr.complete_recommendation(s, user, "rec_dddddd")
            out = await rr.restore_one(s, user, "rec_cccccc")
            ignored = await rr.load_dismissed(s, user)
            done = await rr.load_completed(s, user)
    finally:
        await eng.dispose()
    assert out == {"id": "rec_cccccc", "removed": ["done"]}
    assert ignored == {"rec_aaaaaa", "rec_bbbbbb"}
    assert done == {"rec_dddddd"}


def test_the_screen_no_longer_calls_the_bulk_restores():
    assert "/api/v1/recommendations/restore\"" not in HTML
    assert "restore-completed" not in HTML, (
        "a bulk restore from the Today screen resurrects every accepted card")


def test_the_history_list_badges_every_card_and_has_no_accept():
    start = HTML.index("function showHistory(")
    body = HTML[start:HTML.index("async function restoreOne(", start)]
    for badge in ("✅ Done", "🙈 Ignored", "⚖️ Blocked"):
        assert badge in HTML
    assert "acceptRec(" not in body and "decideRule(" not in body, (
        "the history list is read-only")
    assert "hidden_at" in body, "when it was hidden or accepted is shown"
    assert re.search(r"restoreOne\('\$\{c\.id\}'", body), "restore is per card"


def test_a_decision_card_renders_both_choices():
    # PR 75: the two answers are option tiles (`decisionBlock`), and the one
    # the sleeve's signal recommends carries the tag and the filled button.
    start = HTML.index("function decisionBlock(")
    body = HTML[start:HTML.index("async function acceptRec(", start)]
    assert "rule_vs_sleeve" in body
    assert "decideRule('${o.id}','sell')" in body and "decideRule('${o.id}','keep')" in body
    assert "lvl_${o.id}" in body, "the keep level is editable"
    assert "Recommended" in body and "ap.recommended" in body
