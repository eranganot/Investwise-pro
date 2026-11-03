"""PR 157 (D141) -- the protection card says only what Accept does; cash-like funds get no stop.

Live 2026-11-03 07:45 (Israel), `ffb6d3d`: "One exit per holding: remove 0 rules, set 1 trailing
stop". Accept's spec: delete [], edit [], create [BIL trailing stop 5%]. The why began "Today 0
exit rules sit on your holdings, several on one fund" (a fixed sentence from the 10-24 clean-up),
and the how said "5% (2x its monthly volatility, 0.3%)" -- 2 x 0.3 is 0.6; the 5% is MIN_TRAIL_PCT.
Owner, 2026-11-03: fix the wording; "BIL don't need to get stop".

Not financial advice.
"""
from app.services import protection_manager as pm


def test_a_t_bill_fund_is_cash_like_and_gets_no_exit_even_as_a_sleeve_leg():
    for tk in ("BIL", "SGOV", "SHV", "bil"):
        assert pm.is_cash_like(tk), tk
        assert pm.role_of(tk, {"BIL", "SGOV", "SHV"}, False) == "cash", tk
    for tk in ("SHY", "BND", "UGL", "VTI"):
        assert not pm.is_cash_like(tk), tk
    assert pm.role_of("UGL", {"UGL"}, True) == "sleeve"


def test_the_live_book_has_nothing_to_change():
    """The 2026-11-03 book: BIL held as the 2x gold sleeve's leg, no exit on it, vol 0.3%."""
    rules = [{"id": "b1", "ticker": "BIL", "rule_type": "rebalance_band", "mode": "pct", "level": 3.0,
              "active": True}]
    roles = {"BIL": pm.role_of("BIL", {"BIL"}, False), "VTI": "core"}
    for k in pm.K_OPTIONS:
        assert pm.is_empty(pm.plan_changes(rules, roles, {"BIL": 0.3}, k)), k


def test_an_exit_already_on_a_cash_like_fund_is_removed():
    rules = [{"id": "s1", "ticker": "BIL", "rule_type": "trailing_stop", "mode": "pct", "level": 5.0,
              "active": True}]
    ch = pm.plan_changes(rules, {"BIL": "cash"}, {}, 2.0)
    assert [d["rule_id"] for d in ch["delete"]] == ["s1"]
    assert ch["delete"][0]["why"] == "cash-like fund: a stop on it never fires"
    assert ch["create"] == [] and ch["edit"] == []


def test_the_title_names_only_what_accept_does():
    assert pm._title_parts(0, 1) == "set 1 trailing stop"
    assert pm._title_parts(3, 0) == "remove 3 rules"
    assert pm._title_parts(1, 2) == "remove 1 rule, set 2 trailing stops"
    assert "0" not in pm._title_parts(0, 2) + pm._title_parts(2, 0)


def test_the_first_sentence_is_counted_not_fixed():
    assert pm._today([]) == "Today none of your holdings has an exit rule."
    band = {"ticker": "VTI", "rule_type": "rebalance_band"}
    assert pm._today([band]) == "Today none of your holdings has an exit rule.", "a band is not an exit"
    one = [{"ticker": "TQQQ", "rule_type": "trailing_stop"}]
    assert pm._today(one) == "Today 1 exit rule sits on your holdings."
    stacked = one + [{"ticker": "TQQQ", "rule_type": "take_profit"}, {"ticker": "UGL", "rule_type": "stop_loss"}]
    assert pm._today(stacked) == "Today 3 exit rules sit on your holdings, more than one on TQQQ."
    assert "several on one fund" not in pm._today(one)


def test_a_width_moved_by_the_bound_says_so():
    assert pm._sized(5.0, 2.0, 0.3) == "the 5% minimum; 2x its monthly volatility, 0.3%, is 0.6%"
    assert pm._sized(60.0, 4.0, 18.0) == "the 60% maximum; 4x its monthly volatility, 18%, is 72%"
    assert pm._sized(37.0, 2.0, 18.5) == "2x its monthly volatility, 18.5%"


async def test_a_plan_stored_before_this_fix_shows_no_card(own_session, monkeypatch):
    """Production's stored plan (refreshed 05:40 UTC) has roles {BIL: sleeve} and a BIL stop.
    It must not reach Today after the deploy; the nightly refresh replaces it."""
    from app.services.feed_service import ensure_user
    stored = {"ok": True, "roles": {"BIL": "sleeve"}, "fingerprint": [], "as_of": "2026-11-03T05:40:00",
              "changes": {"delete": [], "edit": [], "create": [
                  {"ticker": "BIL", "rule_type": "trailing_stop", "mode": "pct", "level": 5.0,
                   "vol_pct": 0.3, "k": 2.0}]}}

    async def load(session, user):
        return stored
    monkeypatch.setattr(pm, "load", load)
    async with own_session() as s:
        u = await ensure_user(s, "pr157@example.com")
        assert await pm.card(s, u) == []
        # A plan stored after the fix, on a sleeve that does need a stop, still shows.
        stored["roles"] = {"BIL": "cash", "TQQQ": "sleeve"}
        stored["changes"]["create"][0]["ticker"] = "TQQQ"
        got = await pm.card(s, u)
        assert len(got) == 1 and got[0]["title"] == "One exit per holding: set 1 trailing stop", got
        assert got[0]["why"].startswith("Today none of your holdings has an exit rule. ")
        assert "Add: TQQQ trailing stop 5% (the 5% minimum; 2x its monthly volatility, 0.3%, is 0.6%)" \
            in got[0]["how"]
