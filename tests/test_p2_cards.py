"""P2 — the card. Presentation, but two claims that must not blur.

A "Beat the Market" card reports what the rule MEASURED over real prices. Style
and leverage are structural facts about the basket, so they can sit alongside
those numbers. A *derived* expected return cannot: it is a different kind of
claim, and putting one next to a measured one is exactly the confusion this
family of strategies exists to avoid.
"""
from app.services import strategy_catalog as sc


def _card(cards, sid):
    return next(c for c in cards if c["id"] == sid)


def test_every_card_carries_style_and_horizon():
    cards = sc.as_plan_cards()
    assert cards, "the catalog must render cards even with no stored backtests"
    for c in cards:
        assert c["style"], f"{c['id']} has no style chip"
        assert c["horizon"], f"{c['id']} has no horizon chip"
        assert isinstance(c["uses_leverage"], bool)


def test_style_is_derived_from_the_basket_not_a_label():
    """A single leveraged line and a three-factor stack must not read alike.

    This test failed on its first run, and the failure was real: MTUM, QUAL and
    AVUV were in no lookup bucket, so `_character` fell through to "single_name"
    and the Factor Stack -- three broadly diversified funds -- was modelled at a
    single stock's 32% volatility and labelled "Concentrated", exactly like a
    100% TQQQ sleeve. A chip that says the same word on every card is a label,
    not a derivation.
    """
    cards = sc.as_plan_cards()
    tqqq = _card(cards, "btm_trend_tqqq")        # {TQQQ: 1.0}, geared
    stack = _card(cards, "btm_factor_stack")     # MTUM / QUAL / AVUV
    assert tqqq["style"] == "Concentrated"
    assert stack["style"] == "Moderately diversified"


def test_factor_etfs_are_not_treated_as_single_stocks():
    """The app recommends these tickers in its own catalog; it should know what
    they are. Falling through to "single_name" also mis-modelled anyone HOLDING
    one, via the assumptions_for fallback that feeds the risk score."""
    from app.services import strategy_profile as prof
    for tk in ("MTUM", "QUAL", "AVUV"):
        assert prof._character(tk) == "factor_equity", tk
        _r, vol = prof.assumptions_for(tk)
        assert vol < 25.0, f"{tk} still modelled like a single stock ({vol}%)"


def test_a_big_weight_in_a_diversified_fund_is_not_concentration():
    """40% of a factor ETF is an allocation; 40% of one company is a risk."""
    from app.services import strategy_profile as prof
    spread = prof.profile({"basket": [("MTUM", 0.40), ("QUAL", 0.35), ("AVUV", 0.25)]})
    single = prof.profile({"basket": [("NVDA", 0.40), ("MSFT", 0.35), ("AVGO", 0.25)]})
    assert spread["concentration"] != "Concentrated"
    assert single["concentration"] == "Concentrated"


def test_leverage_is_flagged_from_the_holding_itself():
    cards = sc.as_plan_cards()
    assert _card(cards, "btm_trend_tqqq")["uses_leverage"] is True
    assert _card(cards, "btm_factor_stack")["uses_leverage"] is False


def test_a_measured_card_never_carries_a_derived_return():
    """The label stays "Backtested", never "Est. return" -- so the estimate must
    not be in the payload at all. A number present in the response is a number
    something will eventually render."""
    for c in sc.as_plan_cards():
        assert "expected_return_pct" not in c
        assert "profile" not in c
        assert c["measured"] is True


def test_style_survives_a_strategy_with_no_stored_measurement():
    """Structural chips come from the basket, so they render even before the
    03:30 job has ever run -- 'not measured yet' must not blank the whole card."""
    card = _card(sc.as_plan_cards({}), "btm_trend_tqqq")
    assert card["backtest"] is None
    assert card["style"] and card["horizon"]
