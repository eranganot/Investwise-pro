"""PR 167 (D150) -- a name the plan asks for may be held up to its plan weight,
and the cash card sizes every name against that plan.

MEASURED ON PRODUCTION, 2026-11-16 (877d39f; `/recommendations`, `/rules`,
`/plan/sleeves`, `/portfolio/ledger`, read-only). The Trend-Filtered Leveraged
Nasdaq sleeve (35%) was parked in QQQ, and the Diversified Global Growth core
(29.23% of the book) holds 25% QQQ: the plan wanted 42.3% of QQQ, as the QQQ
band rule itself said ("35.0% vs plan 42.3% +-5"). Two limits said 35%: the
plan-wide single-name cap and the cap armed with the sleeve.

    18:27:47  core realign          Buy  QQQ 1,317.18
    18:27:59  rec_449403 (Trim QQQ) Sell QQQ 1,299.59      <- _rid("trim", "QQQ")

Then the cash card (`rec_d5eb9d`), with QQQ 7 ILS under its cap, put that cash
into the core at targets it made up: "VTI holds your core Equities: 11.7% vs
15.3% of the book" against a plan of 11.7%, VXUS "6.2% vs 8.2%" against 5.8%,
and an SMH top-up swapped for XLE, which the realign sells. Accepting it takes
VTI to 15.2%, over the 14.7% top of its band -- the VTI band had already sold
838.54 of VTI at 17:31 the same day.

Owner, 2026-11-16: "plan wins". Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import funding_service as fs  # noqa: E402
from app.services import recommendations as R  # noqa: E402
from app.services import redeploy_research as rr  # noqa: E402

# /api/v1/portfolio at 2026-11-16 20:18 UTC, ILS.
BOOK = [("BIL", 6835.35, "Fixed Income"), ("QQQ", 6769.65, "Equities"),
        ("VTI", 2259.55, "Equities"), ("VXUS", 1202.10, "Equities"),
        ("SMH", 843.70, "Equities"), ("XLF", 2.19, "Equities")]
CASH = 1449.59
NAV = sum(v for _, v, _ in BOOK) + CASH                      # 19,362.13
TARGET = {"Fixed Income": 0.35, "Equities": 0.6423, "Cash": 0.0077}
SLEEVES = {"BIL": 0.35, "QQQ": 0.35}
CORE_PCT = 0.2923
CORE = {"VTI": 0.40 * CORE_PCT, "QQQ": 0.25 * CORE_PCT,
        "VXUS": 0.20 * CORE_PCT, "SMH": 0.15 * CORE_PCT}
PLAN = {**CORE, "BIL": 0.35, "QQQ": 0.35 + CORE["QQQ"]}       # QQQ 42.3%
BANDS = {"VTI": 3.0, "VXUS": 3.0, "SMH": 1.0, "QQQ": 5.0, "BIL": 3.0}   # /rules
CAP = 0.35                                                    # thresholds.concentration_cap_pct
# The screener's scores on the live card (`meta.research`).
SCORES = {"VTI": 48.2, "VXUS": 54.5, "SMH": 42.2, "XLE": 58.1, "VNQ": 51.5, "VYM": 50.5}


class _Row:
    def __init__(self, ticker, value_ils, asset_class="Equities"):
        self.ticker = ticker
        self.market = "TASE"
        self.quantity = float(value_ils)
        self.current_price = 1.0
        self.cost_basis = 1.0
        self.meta = {"asset_class": asset_class, "price_currency": "ILS"}


class _Plan:
    cash_target_pct = None
    cash_target_ils = 150.0
    cash_redeploy_at_ils = 1000.0
    currency = "ILS"
    objective = "Grow"
    risk_tolerance = "Medium"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(R, "_buy_ideas", lambda *a, **k: [])
    monkeypatch.setattr(rr, "live_scores", lambda *a, **k: {
        t: {"score": s, "screen": "Equities", "asset_class": "Equities", "data_gaps": [],
            "reasons": [], "name": None, "kind": "fund"} for t, s in SCORES.items()})
    monkeypatch.setattr(rr, "live_trend", lambda tk: {"state": "mixed"})


def _rows(book=BOOK, cash=CASH):
    return [_Row(t, v, c) for t, v, c in book] + [_Row("CASH", cash, "Cash")]


def _card(*, name_caps, core_targets, book=BOOK, cash=CASH):
    rows = _rows(book, cash)
    nav = sum(r.quantity for r in rows)
    out = R._redeploy_cash_recs(rows, {"nav": nav, "exposure_ticker": {}}, _Plan(), "Grow",
                                CAP, cash, TARGET, name_caps, SLEEVES, None, 0.0, [],
                                core_id="grow_diversified", sleeve_slots=SLEEVES,
                                core_targets=core_targets)
    return out[0] if out else None


def _legs(card):
    return {x["ticker"]: x["amount_ils"] for x in card["apply"]["legs"]}


# --------------------------------------------------------------------------- #
# The limit
# --------------------------------------------------------------------------- #
def test_a_plain_dict_of_caps_lifts_nothing():
    """No plan known (a test, an old caller): exactly the behaviour before."""
    assert fs.effective_name_cap(0.35, "QQQ", None) == 0.35
    assert fs.effective_name_cap(0.35, "QQQ", {"QQQ": 0.30}) == 0.30
    assert fs.effective_name_cap(0.35, "QQQ", {}) == 0.35


def test_the_plan_wide_cap_is_never_below_what_the_plan_asks_for():
    caps = fs.NameCaps({}, PLAN)
    assert fs.effective_name_cap(CAP, "QQQ", caps) == pytest.approx(0.423075)
    assert fs.effective_name_cap(CAP, "qqq", caps) == pytest.approx(0.423075)
    # A planned name UNDER the cap, and a name outside the plan, keep the cap.
    assert fs.effective_name_cap(CAP, "VTI", caps) == CAP
    assert fs.effective_name_cap(CAP, "NVDA", caps) == CAP


def test_a_cap_set_by_hand_on_the_name_still_binds():
    """Owner: the exception is for the plan-wide cap, not for a limit he chose."""
    caps = fs.NameCaps({"QQQ": 0.30}, PLAN)
    assert fs.effective_name_cap(CAP, "QQQ", caps) == 0.30


def test_room_under_the_limit_is_the_distance_to_the_plan_weight():
    caps = fs.NameCaps({}, PLAN)
    w = 6769.65 / NAV
    room = fs.name_room_ils(NAV, w, fs.effective_name_cap(CAP, "QQQ", caps))
    assert room == pytest.approx((0.423075 - w) * NAV, abs=1.0)        # ~1,422
    assert fs.name_room_ils(NAV, w, fs.effective_name_cap(CAP, "QQQ", None)) < 10.0   # the live 7


# --------------------------------------------------------------------------- #
# The cash card on the live book
# --------------------------------------------------------------------------- #
def test_the_live_card_is_reproduced_without_the_plan():
    """No core targets and bare caps -- what production built. Pinned so the
    numbers in the STATUS entry are a reproduction, not a description."""
    card = _card(name_caps={"QQQ": 0.35, "BIL": 0.35}, core_targets=None)
    legs = _legs(card)
    assert "QQQ" not in legs
    assert legs["VTI"] == pytest.approx(678.15, abs=1.0)
    assert legs["VXUS"] == pytest.approx(360.78, abs=1.0)
    assert sum(legs.values()) == pytest.approx(1292.15, abs=2.0)
    assert (2259.55 + legs["VTI"]) / NAV > (PLAN["VTI"] * 100 + BANDS["VTI"]) / 100   # over its band


def test_the_cash_goes_to_the_name_the_plan_is_short_of():
    card = _card(name_caps=fs.NameCaps({"QQQ": PLAN["QQQ"], "BIL": 0.35}, PLAN),
                 core_targets=CORE)
    legs = _legs(card)
    assert set(legs) == {"QQQ"}, legs
    assert legs["QQQ"] == pytest.approx(1299.59, abs=15.0)
    assert (6769.65 + legs["QQQ"]) / NAV <= PLAN["QQQ"] + 1e-6
    why = card["apply"]["legs"][0]["reason"]
    assert "sleeve +" in why and "core)" in why, why
    assert "42.3%" in why, why


def test_accepting_it_leaves_every_name_inside_its_band():
    """What nothing measured: what an accept does to the OTHER rules."""
    card = _card(name_caps=fs.NameCaps({"QQQ": PLAN["QQQ"], "BIL": 0.35}, PLAN),
                 core_targets=CORE)
    after = {t: v for t, v, _ in BOOK}
    for tk, amt in _legs(card).items():
        after[tk] = after.get(tk, 0.0) + amt
    for tk, band in BANDS.items():
        w = after[tk] / NAV * 100.0
        assert w <= PLAN[tk] * 100.0 + band + 1e-6, (tk, w, PLAN[tk] * 100.0, band)


def test_a_held_name_outside_the_plan_gets_no_top_up():
    """XLF is held and in no part of the plan: target 0, never a leg."""
    book = [(t, (900.0 if t == "XLF" else v), c) for t, v, c in BOOK]
    card = _card(name_caps=fs.NameCaps({"QQQ": PLAN["QQQ"]}, PLAN), core_targets=CORE, book=book)
    assert "XLF" not in _legs(card)


def test_research_cannot_swap_a_core_top_up_for_a_name_the_realign_sells():
    """SMH short, XLE 16 points ahead on the screen (the live swap): XLE is not
    in the basket, so it is not a candidate."""
    book = [(t, (6769.65 + 1300.0 if t == "QQQ" else 400.0 if t == "SMH" else v), c)
            for t, v, c in BOOK]
    card = _card(name_caps=fs.NameCaps({"QQQ": PLAN["QQQ"]}, PLAN), core_targets=CORE, book=book)
    assert card is not None
    legs = _legs(card)
    assert "XLE" not in legs and "VNQ" not in legs and "VYM" not in legs, legs
    assert "SMH" in legs, legs
    assert set(legs) <= set(PLAN), legs


def test_the_basket_filter_is_what_keeps_xle_out():
    """The same book with the filter off swaps to XLE -- so the test above is
    evidence, not an absence."""
    keep = R._core_basket_only({"XLE": {}, "SMH": {}, "VTI": {}}, "grow_diversified")
    assert set(keep) == {"SMH", "VTI"}
    assert set(R._core_basket_only({"XLE": {}, "SMH": {}}, None)) == {"XLE", "SMH"}


def test_a_name_kept_out_by_a_limit_is_named_on_the_card():
    """A hand-set 35% cap on QQQ: the money goes elsewhere, and the card says
    why. On main the leg vanished and the card read as a complete answer."""
    card = _card(name_caps=fs.NameCaps({"QQQ": 0.35}, PLAN), core_targets=CORE)
    assert card is not None
    assert "QQQ" not in _legs(card)
    line = next((h for h in card["how"] if h.startswith("⚠ QQQ is")), None)
    assert line and "35% limit" in line and "short of its plan" in line, card["how"]


def test_without_a_core_basket_the_card_sizes_as_before():
    """No core claim known: sleeve targets + the class remainder split by value."""
    a = R._redeploy_name_targets({"Equities": _rows()[1:6]}, {t: v / NAV for t, v, _ in BOOK},
                                 TARGET, SLEEVES, None)
    assert a["QQQ"] == 0.35
    assert a["VTI"] == pytest.approx(0.1533, abs=5e-4)          # the live "15.3%"
    b = R._redeploy_name_targets({"Equities": _rows()[1:6]}, {t: v / NAV for t, v, _ in BOOK},
                                 TARGET, SLEEVES, CORE)
    assert b["QQQ"] == pytest.approx(0.423075)
    assert b["VTI"] == pytest.approx(0.11692)
    assert b["XLF"] == 0.0


# --------------------------------------------------------------------------- #
# The note about a card that is not on the screen
# --------------------------------------------------------------------------- #
def test_a_take_them_together_note_about_a_dropped_card_is_removed():
    from app.services.contradictions import drop_notes_about
    recs = [{"id": "rec_buy", "notes": ["Another card sells Equities. Take them together or neither.",
                                        "something else"]}]
    found = [{"kind": "buys_and_sells_one_class", "card": "rec_buy", "other": "rec_tax",
              "asset_class": "Equities"}]
    drop_notes_about(recs, found, ["rec_tax"])
    assert recs[0]["notes"] == ["something else"]
    assert found[0]["partner_not_shown"] is True
    # Both on the screen: the note stays.
    recs2 = [{"id": "rec_buy", "notes": ["Another card sells Equities. Take them together or neither."]}]
    drop_notes_about(recs2, [dict(found[0])], ["rec_other"])
    assert len(recs2[0]["notes"]) == 1


# --------------------------------------------------------------------------- #
# Every caller reaches the plan through the caps it already passes
# --------------------------------------------------------------------------- #
def test_every_cap_lookup_passes_the_caps_and_nobody_strips_the_plan():
    """The plan rides on `NameCaps`, so a call that omits the caps -- or copies
    them into a plain dict -- silently goes back to the bare cap. An AST walk,
    not a grep (PR 43: a grep through `head` missed two of six call sites)."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    calls, strips = [], []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == "effective_name_cap":
                has = len(node.args) >= 3 or any(k.arg == "name_caps" for k in node.keywords)
                calls.append((path.name, node.lineno, has))
            # dict(name_caps) / dict(_name_caps): a copy that drops `.plan`.
            if name == "dict" and node.args and isinstance(node.args[0], ast.Name) \
                    and node.args[0].id in ("name_caps", "_name_caps"):
                strips.append((path.name, node.lineno))
    assert len(calls) >= 9, calls                        # the walker sees the call sites
    assert [c for c in calls if not c[2]] == [], calls
    assert strips == [], strips
    # And the walker flags the shape it exists to catch.
    bad = ast.parse("effective_name_cap(cap, tk)").body[0].value
    assert len(bad.args) < 3 and not bad.keywords
