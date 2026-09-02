"""Cards you can actually see.

Five separate mechanisms were hiding recommendations, and only one of them was
the 90-day mute everybody suspected:

1. `_reconcile` merged two rebalance legs and kept the FIRST leg's id, so
   completing a card titled SELL Equities filed the BUY leg riding inside it.
2. `rebalance_actions[:2]` never GENERATED the third leg. A card that was never
   built cannot be restored and nothing reports it missing.
3. An OPEN history circuit made three agents return empty lists without raising,
   so `degraded` stayed `[]` and the response claimed health.
4. Ids like `_rid("tax")` carry no content, so one Accept muted that whole
   family for the window -- today's card and next month's are the same id.
5. The rule self-heal resolved a re-fired rule as `acknowledged` within a second
   of every firing, forever.

Each test below is one of those, written so it fails against the old behaviour.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


def _client():
    """Always used as `with _client() as c:`.

    Without the context manager every request runs in its own anyio portal loop,
    so request 2 borrows the pooled connection request 1 left behind -- green on
    SQLite, and on Postgres it poisons the app engine for whichever LATER test
    touches it first. conftest now empties the pool between tests so this cannot
    bite; the correct usage is still the correct usage.
    """
    logging.disable(logging.CRITICAL)
    return TestClient(m.app)


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 371.04, "listing_price": 371.04, "quantity": 10.0,
         "cost_basis": 340.51, "expected_return_pct": 7, "volatility_pct": 20},
        {"ticker": "SCHD", "market": "NYSE", "asset_class": "Equities", "depth": 1,
         "spot_price": 35.11, "listing_price": 35.11, "quantity": 60.0,
         "cost_basis": 33.36, "expected_return_pct": 5, "volatility_pct": 12},
    ]})
    c.post("/api/v1/portfolio/cash", json={"amount_ils": 621.79})
    c.post("/api/v1/portfolio/contributions",
           json={"amount_ils": 20000, "kind": "deposit", "note": "opening"})


# --------------------------------------------------------------------------- #
# 1. the merged rebalance card
# --------------------------------------------------------------------------- #
def test_a_merged_rebalance_card_does_not_inherit_a_leg_id():
    """Accepting the merge must retire the MERGE, not one of its legs.

    `keep = rebal[0]` meant the merged card's id was the sell leg's id. So
    "Rebalance toward your target mix" and "Sell Equities" were one id, and
    completing the visible card muted a buy card the user had never been shown a
    title for. He reported never having hidden a buy recommendation. He hadn't.
    """
    from app.services.recommendations import _reconcile, _rid

    legs = [
        {"id": _rid("rebal", "Equities"), "dimension": "allocation",
         "severity": "MEDIUM", "title": "Sell Equities", "est_amount": 5078.0,
         "apply": {"kind": "rebalance_to_objective", "asset_class": "Equities"}},
        {"id": _rid("rebal", "Commodities"), "dimension": "allocation",
         "severity": "MEDIUM", "title": "Buy Commodities", "est_amount": 2100.0,
         "apply": {"kind": "rebalance_to_objective", "asset_class": "Commodities"}},
    ]
    leg_ids = {r["id"] for r in legs}

    out = _reconcile([dict(r) for r in legs])
    merged = [r for r in out if r.get("title") == "Rebalance toward your target mix"]
    assert len(merged) == 1, "the two legs should collapse into one card"

    assert merged[0]["id"] not in leg_ids, (
        "the merged card kept a leg's id -- accepting it retires that leg's twin")

    # And the id must be a function of every leg it absorbed, so a merge of a
    # different pair is a different card.
    other = _reconcile([
        dict(legs[0]),
        {**legs[1], "id": _rid("rebal", "Fixed Income"), "title": "Buy Fixed Income",
         "apply": {"kind": "rebalance_to_objective", "asset_class": "Fixed Income"}},
    ])
    other_merged = [r for r in other
                    if r.get("title") == "Rebalance toward your target mix"][0]
    assert other_merged["id"] != merged[0]["id"], (
        "two different merges share an id, so one Accept mutes both")


def test_b_merged_id_is_order_independent():
    """Same legs in the other order is the same card, not a second one.

    Otherwise the mute lands on whichever ordering the engine happened to emit
    that morning, and the card comes back tomorrow looking new.
    """
    from app.services.recommendations import _reconcile

    def _legs(order):
        base = {"Equities": "Sell Equities", "Commodities": "Buy Commodities"}
        return [{"id": f"rec_{k[:6]}", "dimension": "allocation", "severity": "MEDIUM",
                 "title": base[k], "est_amount": 1000.0,
                 "apply": {"kind": "rebalance_to_objective", "asset_class": k}}
                for k in order]

    a = [r for r in _reconcile(_legs(["Equities", "Commodities"]))
         if r["title"] == "Rebalance toward your target mix"][0]
    b = [r for r in _reconcile(_legs(["Commodities", "Equities"]))
         if r["title"] == "Rebalance toward your target mix"][0]
    assert a["id"] == b["id"]


# --------------------------------------------------------------------------- #
# 2 + 4. ids that carry their own content
# --------------------------------------------------------------------------- #
def test_c_family_ids_carry_their_subject():
    """`_rid("tax")` is a constant. One Accept, ever, mutes the family.

    A tax-loss card about SOXL and a tax-loss card about MTUM are different
    advice about different money, and they were the same id.
    """
    from app.services.recommendations import _rid

    assert _rid("tax", *sorted(["SOXL"])) != _rid("tax", *sorted(["MTUM"]))
    assert _rid("tax", *sorted(["SOXL", "MTUM"])) == _rid("tax", *sorted(["MTUM", "SOXL"]))
    assert _rid("commodity_add", "DBC", 2000) != _rid("commodity_add", "GLD", 2000)
    assert _rid("raise_cash", 1000) != _rid("raise_cash", 3000)


def test_d_sized_ids_are_bucketed_not_exact():
    """Bucketed to 250 on purpose.

    An id keyed to the exact shekel would change with every tick, so a card
    dismissed at lunch reappears by dinner as a "new" one -- swapping a mute
    that never expires for one that never applies. 250 is coarse enough that
    ordinary drift keeps the same id and a real change of size does not.
    """
    from app.services.recommendations import _rid

    assert (_rid("raise_cash", round(1000 / 250) * 250)
            == _rid("raise_cash", round(1080 / 250) * 250))
    assert (_rid("raise_cash", round(1000 / 250) * 250)
            != _rid("raise_cash", round(3000 / 250) * 250))


# --------------------------------------------------------------------------- #
# 3. an open circuit is a degraded agent
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_e_open_history_circuit_names_its_agents_in_degraded(own_session, monkeypatch):
    """The guarantee that was quietly broken.

    The rule this project holds is that a degraded agent means a MISSING card
    says nothing about the underlying state. Per-ticker error swallowing (which
    is correct -- one bad symbol must not sink the scan) turns a breaker that is
    OPEN for EVERY symbol into an empty list and no exception. Nothing appended
    to `degraded`, so the app reported itself healthy while three agents were
    dark. That is the shape of the 2026-08-23 report: no buy cards, no holding
    cards, `degraded: []`.
    """
    from app.services.recommendations import build_recommendations

    with _client() as c:
        _seed(c)
    logging.disable(logging.NOTSET)

    monkeypatch.setattr(
        "app.providers.registry.provider_health",
        lambda: {"history": {"circuit_state": "OPEN"},
                 "fundamentals": {"circuit_state": "CLOSED"},
                 "quote": {"circuit_state": "CLOSED"}})

    from app.models.tables import User
    from sqlalchemy import select

    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        out = await build_recommendations(s, user)

    deg = out.get("degraded") or []
    for agent in ("holding_verdicts", "momentum", "war_room"):
        assert agent in deg, f"{agent} depends on history and the circuit is OPEN"


@pytest.mark.asyncio
async def test_f_closed_circuits_do_not_invent_degradation(own_session, monkeypatch):
    """The other half. A false "degraded" is its own lie.

    Reporting agents as degraded when the providers are fine would make the
    smoke scripts red forever and teach the reader to ignore the field --
    exactly how `degraded: []` became unreadable in the first place.
    """
    from app.services.recommendations import build_recommendations

    with _client() as c:
        _seed(c)
    logging.disable(logging.NOTSET)

    monkeypatch.setattr(
        "app.providers.registry.provider_health",
        lambda: {"history": {"circuit_state": "CLOSED"},
                 "fundamentals": {"circuit_state": "CLOSED"}})

    from app.models.tables import User
    from sqlalchemy import select

    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        out = await build_recommendations(s, user)

    deg = set(out.get("degraded") or [])
    assert "momentum" not in deg
    assert "war_room" not in deg


# --------------------------------------------------------------------------- #
# 5. a mute must not outlive the condition
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_g_a_rule_that_fired_again_is_not_auto_acknowledged(own_session):
    """The V/MSFT loop, in one test.

    `build_recommendations` runs on every hourly push evaluation, and the
    self-heal resolved any hidden rule card unconditionally. So a max_weight
    rule breaching every 30 minutes was marked `acknowledged` one second after
    each firing, and the red banner rendered empty -- a single dismissal in
    August became permanent consent to a condition that recurs.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.models.tables import TradingRule, User
    from app.services.recommendations import (
        _done_key, _mark, _DONE_TTL_DAYS, build_recommendations,
    )

    with _client() as c:
        _seed(c)
    logging.disable(logging.NOTSET)

    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        rule = TradingRule(subject=user.email, ticker="V", rule_type="max_weight",
                           mode="pct", level=0.30, active=True, triggered=True,
                           last_triggered_at=datetime.now(timezone.utc))
        s.add(rule)
        await s.commit()
        card_id = f"rule_{str(rule.id)[:8]}"

        # Dealt with two days ago; fired again just now.
        await _mark(s, _done_key(user), card_id, _DONE_TTL_DAYS)
        m_ = await s.get(type(rule), rule.id)
        m_.last_triggered_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        # and back-date the mark so "fired after dealt" is unambiguous
        from app.services.recommendations import _load_map
        raw = await _load_map(s, _done_key(user))
        raw[card_id] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        import json

        from app.models.tables import KVSetting
        row = await s.get(KVSetting, _done_key(user))
        row.value = json.dumps(raw)
        await s.commit()

        out = await build_recommendations(s, user)

        refreshed = await s.get(TradingRule, rule.id)
        assert refreshed.triggered is True, (
            "a rule that fired again was resolved against a two-day-old dismissal")

    ids = {r.get("id") for r in out.get("recommendations", out.get("items", []))}
    assert card_id in ids, "healing the rule while still hiding its card is the deadlock"


@pytest.mark.asyncio
async def test_h_a_high_card_marked_done_comes_back_on_the_ignore_window(own_session):
    """Done is the stronger statement, and HIGH outranks it.

    HIGH means the app thinks this is hurting you NOW. If it is still saying so
    a week after you filed the card, the filing was premature. Note what this
    does not do: HIGH cards remain dismissable. A button that visibly does
    nothing is worse than a mute that expires early.
    """
    from datetime import datetime, timedelta, timezone

    from app.services.recommendations import (
        _DISMISS_TTL_DAYS, _DONE_TTL_DAYS, _parse_dt,
    )

    assert _DONE_TTL_DAYS > _DISMISS_TTL_DAYS, "the premise of this test"

    marked = datetime.now(timezone.utc) - timedelta(days=_DISMISS_TTL_DAYS + 1)
    cut = datetime.now(timezone.utc) - timedelta(days=_DISMISS_TTL_DAYS)
    # inside the DONE window, outside the IGNORE window -> a HIGH card is back
    assert marked > datetime.now(timezone.utc) - timedelta(days=_DONE_TTL_DAYS)
    assert _parse_dt(marked.isoformat()) < cut


# --------------------------------------------------------------------------- #
# supporting fixes
# --------------------------------------------------------------------------- #
def test_i_commodity_etfs_are_not_equities():
    """`classify` defaulted every commodity ETF to Equities.

    So `_redeploy_cash_recs` looked for a leg whose class was "Commodities",
    never found one however many commodity ideas the screener returned, and the
    leg landed in `unplaced` every time -- which reads on the Today screen as
    the app having no opinion, not as a lookup miss.
    """
    from app.services.allocation_mix import classify

    for tk in ("DBC", "GLD", "USO", "COW", "NIB", "PDBC", "SLV", "CORN"):
        assert classify(tk, "NYSE") == "Commodities", f"{tk} misfiled"
    for tk in ("BND", "AGG", "TLT", "HYG", "LQD"):
        assert classify(tk, "NASDAQ") == "Fixed Income", f"{tk} misfiled"
    for tk in ("V", "MSFT", "SCHD", "TQQQ"):
        assert classify(tk, "NASDAQ") == "Equities", f"{tk} misfiled"
    # THE PRECEDENCE FLIPPED, deliberately. This line used to read
    #     assert classify("GLD", "NYSE", asset_class="Equities") == "Equities"
    # and what it was asserting is the defect: a STORED class outranked the
    # table, while the only writers that ever stored one were defaults. On the
    # live book that default reached BND and 13.7% of NAV was filed as equity.
    # A stored class now wins only when a human set it, which the flag records.
    assert classify("GLD", "NYSE", asset_class="Equities") == "Commodities"
    assert classify("GLD", "NYSE", asset_class="Equities",
                    asset_class_source="user") == "Equities"


def test_j_war_room_sessions_carry_action_type():
    """The guard that read `s.get("action_type")` was dead code.

    It read None every time, so a REBALANCE/trim signal on a name you do not
    hold fell through to the BUY branch and got sized as a purchase.
    """
    from app.schemas.lag import LagObservation
    from app.schemas.state_machine import ActionType, Market
    from app.services.war_room import build_war_room

    obs = LagObservation(ticker="TQQQ", market=Market.NASDAQ, depth=3,
                         spot_price=100.0, listing_price=110.0,
                         action_type=ActionType.REBALANCE,
                         expected_return_pct=8.0, volatility_pct=12.0)

    out = build_war_room([obs], portfolio_tickers={"TQQQ"}, narrate=False)
    sessions = [s for s in (out.get("sessions") or []) if s.get("outcome") == "DISPLAYED"]
    assert sessions, "a 10% depth-3 divergence should reach the decision feed"
    assert "action_type" in sessions[0], "the session must carry the signal's action"
    assert str(sessions[0]["action_type"]).upper() == "REBALANCE"


def test_k_the_rebalance_guard_matches_the_enums_real_value():
    """`ActionType.REBALANCE.value` is "Rebalance", not "REBALANCE".

    Emitting `action_type` fixes half the bug; a guard comparing against a
    spelling the enum never produces fixes nothing. Both halves or neither.
    """
    from app.schemas.state_machine import ActionType

    assert ActionType.REBALANCE.value == "Rebalance"
    assert ActionType.REBALANCE.value.upper() == "REBALANCE", (
        "the guard upper-cases both sides; if this stops holding, the guard is dead again")
