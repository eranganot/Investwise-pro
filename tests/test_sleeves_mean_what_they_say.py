"""If the sleeves are set to 64%, they should actually BE 64%.

Eran's fourth symptom, in his own words. The Sleeves card read *"Your sleeves —
64% of the book"* while the sleeves held **15%** of it, and every attempt to fund
them abstained. Two separate defects, and neither was visible from the screen:

1. **The card printed a target in the present tense.** `sleeve_pct` is what the
   sleeves are SET to. Nothing anywhere returned what they HOLD -- the only
   place occupancy was computed was inside a funding result string -- so the
   difference could not be seen, let alone acted on.

2. **Funding was gated by the wrong constraint.** A class may give up only what
   it holds above its target, which is exactly right when the proceeds LEAVE the
   class and completely wrong when they come straight back. Selling V and SCHD
   to buy MTUM is Equities to Equities: the class weight does not move, so there
   is nothing for a class budget to protect. Equities sat 17.8 points over
   target, the sleeves needed ~13,968, the budget authorised ~5,077, and both
   sleeves abstained -- every time, silently.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services.strategy_service import _snapshot  # noqa: E402
from tests._cards import all_cards  # noqa: E402

FACTOR = "btm_factor_stack"     # MTUM / QUAL / AVUV
SOXL = "btm_trend_soxl"


def _pos(ticker, price=100.0, qty=100.0, basis=90.0, cls="Equities"):
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": cls, "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 20}


def _seed(c, cash=0.0):
    """An all-equity core of ~30k -- the shape of the real book."""
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("V"), _pos("SCHD"), _pos("MSFT")]})
    if cash:
        c.post("/api/v1/portfolio/cash", json={"amount_ils": cash})


# --------------------------------------------------------------------------- #
# 1. the card can tell target from actual
# --------------------------------------------------------------------------- #
def test_a_the_sleeves_endpoint_reports_held_as_well_as_target():
    with TestClient(m.app) as c:
        _seed(c)
        c.post(f"/api/v1/strategies/{FACTOR}/apply?sleeve_pct=64")
        s = c.get("/api/v1/plan/sleeves").json()

    assert s["allocated_pct"] == pytest.approx(64.0)
    assert "allocated_held_pct" in s, "the payload cannot distinguish target from actual"
    # Nothing has been funded, so the sleeve holds none of the book.
    assert s["allocated_held_pct"] == pytest.approx(0.0, abs=0.005)
    row = s["sleeves"][0]
    assert row["held_pct"] == pytest.approx(0.0, abs=0.005)
    assert row["gap_pct"] == pytest.approx(row["sleeve_pct"], abs=0.005)


def test_b_a_held_sleeve_reports_what_it_holds():
    """The other direction: occupancy must not always read zero.

    Deliberately built on holdings the sleeve already contains, because the
    helper must NOT be built on `_legs_for` -- that drops tickers already at
    target, so a fully funded sleeve would report as holding nothing.
    """
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("V", qty=100.0),          # 10k core
            _pos("MTUM", qty=100.0),       # 10k of the factor sleeve
            _pos("QUAL", qty=100.0)]})     # 10k of the factor sleeve
        c.post(f"/api/v1/strategies/{FACTOR}/apply?sleeve_pct=64")
        s = c.get("/api/v1/plan/sleeves").json()

    assert s["allocated_held_pct"] > 0.60, (
        f"two thirds of this book IS the sleeve; got {s['allocated_held_pct']}")
    assert s["core_held_pct"] < 0.40


def test_c_core_held_is_the_complement_of_what_the_sleeves_occupy():
    with TestClient(m.app) as c:
        _seed(c)
        c.post(f"/api/v1/strategies/{FACTOR}/apply?sleeve_pct=64")
        s = c.get("/api/v1/plan/sleeves").json()
    assert s["allocated_held_pct"] + s["core_held_pct"] == pytest.approx(1.0, abs=0.01)


def test_d_a_ticker_two_sleeves_both_want_is_counted_once_at_book_level():
    """Summing per-sleeve occupancy would double-count a shared ticker.

    The same reason `all_sleeve_targets` sums per TICKER rather than per sleeve.
    """
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("MTUM", qty=100.0), _pos("V", qty=100.0)]})
        c.post(f"/api/v1/strategies/{FACTOR}/apply?sleeve_pct=30")
        c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=20")
        s = c.get("/api/v1/plan/sleeves").json()

    per_sleeve = sum(x["held_pct"] or 0.0 for x in s["sleeves"])
    assert s["allocated_held_pct"] <= per_sleeve + 1e-9
    assert s["allocated_held_pct"] <= 1.0


# --------------------------------------------------------------------------- #
# 2. the target is now reachable
# --------------------------------------------------------------------------- #
def test_e_an_equity_sleeve_can_be_funded_out_of_equities():
    """THE FIX. Selling equities to buy equities does not move the class weight.

    Before this, the class-overweight budget gated the sale and a 64% sleeve on
    an all-equity book abstained with a shortfall -- while the card went on
    printing 64% as a fact.
    """
    with TestClient(m.app) as c:
        _seed(c, cash=0)
        c.post(f"/api/v1/strategies/{FACTOR}/apply?sleeve_pct=64")
        res = c.post("/api/v1/plan/sleeves/fund?dry_run=true").json()

    row = {x["strategy_id"]: x for x in res["sleeves"]}[FACTOR]
    assert row["status"] == "funded", (
        f"a 64% equity sleeve on an all-equity book still abstains: {row.get('reason')}")
    assert res["funding"]["shortfall_ils"] == pytest.approx(0.0, abs=1.0)


def test_f_the_swap_is_named_in_the_funding_plan():
    """A 49-point repositioning is legitimate and is not a thing to do quietly."""
    with TestClient(m.app) as c:
        _seed(c, cash=0)
        c.post(f"/api/v1/strategies/{FACTOR}/apply?sleeve_pct=64")
        res = c.post("/api/v1/plan/sleeves/fund?dry_run=true").json()

    fund = res["funding"]
    assert fund["same_class_swap_ils"] > 0, "the swap was not measured"
    assert fund["buying_class"] == "Equities"
    assert fund["swap_pct_of_nav"] > 0.3, "this turns over a third of the book; say so"
    # And the prose has to carry it, not just the payload.
    assert "does not change your Equities weight" in res["funding_summary"], \
        res["funding_summary"]


def test_g_a_cross_class_buy_is_still_gated_by_the_class_budget():
    """The guard must keep working where it was always right.

    Selling equities to buy BONDS is a rebalance, and the class budget IS the
    rebalance. Opting one path out must not open the other.
    """
    from app.services.funding_service import plan_funding

    class _P:
        def __init__(self, tk, cls, qty=100.0, price=100.0, basis=90.0):
            self.ticker, self.market, self.quantity = tk, "NASDAQ", qty
            self.current_price, self.cost_basis = price, basis
            self.meta = {"asset_class": cls}

    # An all-equity book against a Grow target of 80% Equities: 20 points over,
    # so the rebalancing budget is 6,000 of a 30,000 book. Ask for 20,000.
    # NAV comes from the app's own snapshot, not a hand-written number. These
    # prices are USD and get FX-converted to ILS, so a hand-set nav of 30,000
    # against holdings actually worth ~111,000 puts every name at 123% of the
    # book -- miles over the 40% cap, which authorises selling everything and
    # makes the two paths look identical for a reason that has nothing to do
    # with what is being tested.
    rows = [_P("V", "Equities"), _P("SCHD", "Equities"), _P("MSFT", "Equities")]
    snap = _snapshot(rows)
    nav = snap["nav"]
    ask = nav * 0.65          # more than the 20-point overweight can fund

    same = plan_funding(rows, snap, None, "Grow", 0.40, ask,
                        cash_ils=0.0, buying_class="Equities")
    cross = plan_funding(rows, snap, None, "Grow", 0.40, ask,
                         cash_ils=0.0, buying_class="Fixed Income")

    # Equities are 100% against a Grow target of 80%: the rebalancing budget is
    # 20 points of NAV and nothing else may be sold to buy a different class.
    assert cross["funded_ils"] < nav * 0.30, (
        f"buying bonds may only spend the equity overweight; got {cross['funded_ils']:.0f} "
        f"of {nav:.0f}")
    assert same["funded_ils"] > nav * 0.60, (
        f"buying equities back should reach much further; got {same['funded_ils']:.0f}")
    assert cross["same_class_swap_ils"] == 0.0


def test_h_none_preserves_the_old_behaviour_exactly():
    """Nothing that has not opted in may change."""
    from app.services.funding_service import plan_funding

    class _P:
        def __init__(self, tk, cls):
            self.ticker, self.market, self.quantity = tk, "NASDAQ", 100.0
            self.current_price, self.cost_basis = 100.0, 90.0
            self.meta = {"asset_class": cls}

    rows = [_P("V", "Equities"), _P("SCHD", "Equities"), _P("BND", "Fixed Income")]
    snap = _snapshot(rows)
    ask = snap["nav"] * 0.65

    default = plan_funding(rows, snap, None, "Grow", 0.40, ask, cash_ils=0.0)
    explicit_none = plan_funding(rows, snap, None, "Grow", 0.40, ask,
                                 cash_ils=0.0, buying_class=None)
    assert default["funded_ils"] == explicit_none["funded_ils"]
    assert default["same_class_swap_ils"] == 0.0


def test_i_a_sleeve_is_never_funded_by_selling_another_sleeve():
    """The exclusion set still binds, swap or no swap.

    This is the guard that must survive the change: two sleeves are both things
    the plan holds on purpose, and one is not a piggy bank for the other.
    """
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("V", qty=100.0), _pos("SOXL", qty=100.0), _pos("MSFT", qty=100.0)]})
        c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=20")
        c.post(f"/api/v1/strategies/{FACTOR}/apply?sleeve_pct=40")
        res = c.post("/api/v1/plan/sleeves/fund?dry_run=true").json()

    sold = {s["ticker"] for s in (res.get("funding") or {}).get("sells", [])}
    assert "SOXL" not in sold, "the factor sleeve was funded by selling the SOXL sleeve"


def test_j_the_swap_does_not_consume_the_rebalancing_budget():
    """A same-class sale spends nothing, so it must not draw the budget down.

    Otherwise two sleeve legs funded out of Equities would consume a rebalancing
    allowance that neither of them used, and a genuine cross-class rebalance
    later in the same plan would find the budget mysteriously empty.
    """
    from app.services.funding_service import plan_funding

    class _P:
        def __init__(self, tk, cls, qty=100.0):
            self.ticker, self.market, self.quantity = tk, "NASDAQ", qty
            self.current_price, self.cost_basis = 100.0, 90.0
            self.meta = {"asset_class": cls}

    rows = [_P("V", "Equities"), _P("SCHD", "Equities"), _P("MSFT", "Equities")]
    snap = _snapshot(rows)

    fund = plan_funding(rows, snap, None, "Grow", 0.40, snap["nav"] * 0.5,
                        cash_ils=0.0, buying_class="Equities")
    assert fund["same_class_swap_ils"] == pytest.approx(
        sum(s["value_ils"] for s in fund["sells"]), abs=1.0), \
        "every sale here is same-class and all of it should be counted as a swap"


# --------------------------------------------------------------------------- #
# 3. the card that cannot act must not be filed as done
# --------------------------------------------------------------------------- #
def test_k_an_empty_sleeve_card_cannot_be_buried_by_accept():
    """How the SOXL cold-start card disappeared.

    It carries no `apply` key on purpose -- starting a position is a different
    decision from correcting a drift, so it deliberately will not act in one
    tap. But Accept still filed it as completed, and a 10% sleeve holding
    nothing vanished from Today for the rest of the TTL while the Plan tab went
    on showing 10%.

    "Done" cannot be true of a card that cannot do anything. The condition is
    self-clearing instead: the card is only generated while the sleeve is empty.
    """
    with TestClient(m.app) as c:
        _seed(c)
        c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=10")

        recs = all_cards(c.get("/api/v1/recommendations").json())
        cold = [r for r in recs if (r.get("meta") or {}).get("cold_start")]
        assert cold, "no cold-start card for a sleeve that holds nothing"
        card = cold[0]
        assert card.get("persistent") is True, "the card does not declare itself persistent"
        # A normaliser stamps `{"kind": "none"}` on advisory cards, which is the
        # same statement in the schema's own vocabulary. What must not appear is
        # an executable kind.
        assert (card.get("apply") or {}).get("kind") in (None, "none"), \
            f"a cold-start card must not claim it can act: {card.get('apply')}"

        # Accept it, then look again.
        c.post(f"/api/v1/recommendations/{card['id']}/complete")
        again = all_cards(c.get("/api/v1/recommendations").json())
        still = [r for r in again if r["id"] == card["id"]]
        assert still, "accepting a card that cannot act buried a standing condition"


def test_l_holding_the_sleeve_is_what_clears_the_card():
    """The other half: persistent must not mean permanent."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("V", qty=100.0), _pos("SOXL", qty=100.0)]})
        c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=10")
        recs = c.get("/api/v1/recommendations").json()["recommendations"]

    cold = [r for r in recs if (r.get("meta") or {}).get("cold_start")]
    assert not cold, "the sleeve is held, so there is no cold start to report"


# --------------------------------------------------------------------------- #
# 4. a ship script may only commit what it names
# --------------------------------------------------------------------------- #
def test_m_the_shared_commit_helper_is_path_limited():
    """`git add -- $Files` followed by a bare `git commit` is not the same thing.

    The bare form commits the whole INDEX. Those coincide only when the index
    starts clean, and it does not always: an aborted `git stash pop` leaves its
    files staged. On 2026-08-25 that was six of Eran's in-progress T5 files
    sitting staged behind a merge conflict -- and any ship script run at that
    moment would have committed and PUSHED unfinished work under a message
    describing something else entirely.

    The repo's "never git add -A" rule exists to prevent exactly this, and the
    helper meant to enforce it had a hole underneath. Asserted here because the
    helper is PowerShell and this suite is the only gate that runs on it.
    """
    from pathlib import Path

    src = Path("scripts/deploy/_common.ps1").read_text(encoding="utf-8")
    assert "git commit -F $msgPath -- $Files" in src, (
        "New-Commit must name its paths on the commit, not just on the add")
    assert "git diff --cached --name-only -- $Files" in src, (
        "the nothing-to-do check must be path-limited too, or a dirty index masks it")
    # And it must SAY what it is leaving behind rather than silently skipping it.
    assert "Staged but NOT part of this commit" in src


def test_n_no_ship_script_stages_everything():
    """The rule the helper exists to keep. Cheap to assert, expensive to lose."""
    from pathlib import Path

    import re

    # A plain substring search matches the BAN as often as a breach -- half the
    # ship scripts carry "never `git add -A`" in their own header, and
    # phase16-repo-hygiene exists to write that rule into .gitattributes. Match
    # invocations: a line that, once its indentation is gone, IS the command.
    call = re.compile(r"^git\s+add\s+(-A\b|--all\b|\.\s*$)")
    offenders = []
    for ps1 in sorted(Path("scripts").rglob("*.ps1")):
        for n, line in enumerate(ps1.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if call.match(stripped):
                offenders.append(f"{ps1}:{n}: {stripped}")
    assert not offenders, "ship scripts staging the whole tree:\n" + "\n".join(offenders)


# --------------------------------------------------------------------------- #
# 5. the third blocker: whole-lot sizing on a fractional book
# --------------------------------------------------------------------------- #
class _Frac:
    """His actual holdings, to four decimal places."""

    def __init__(self, tk, mk, q, px, cb):
        self.ticker, self.market, self.quantity = tk, mk, q
        self.current_price, self.cost_basis = px, cb
        self.meta = {"asset_class": "Equities"}


def _real_book():
    from app.services.fx import fx_rate, price_currency

    rows = [_Frac("TQQQ", "NASDAQ", 14.72, 69.01, 72.12),
            _Frac("V", "NASDAQ", 5.607, 382.41, 340.51),
            _Frac("MSFT", "NASDAQ", 2.9358, 487.31, 462.40),
            _Frac("SCHD", "NYSE", 42.4748, 35.21, 33.36)]
    rate = fx_rate(price_currency("NASDAQ", {}))
    nav = sum(p.quantity * p.current_price * rate for p in rows) + 3088.0
    return rows, {"nav": nav, "exposure_ticker": {}}


def test_o_a_position_is_not_skipped_for_being_bigger_than_the_remainder():
    """THE THIRD BLOCKER, and it cost a whole sleeve in production.

    `shares = int(take / price_ils)` followed by `if shares <= 0: continue`.
    Funding needed 1,394.90 more; V costs 1,418.74 a share. `int()` returned 0
    and the guard skipped the position ENTIRELY -- passing over 7,954 of
    sellable V because one share was 24 shekels too big. The "take one more
    share when it closes the gap" rule sat directly underneath and was never
    reached, because the guard above it had already `continue`d.

    Live consequence: `btm_trend_soxl` reported "needs 6,571 and the book is
    1,357 short" while V sat untouched in the book.
    """
    from app.services.funding_service import plan_funding

    rows, snap = _real_book()
    f = plan_funding(rows, snap, None, "Grow", 0.40, 12658.63, cash_ils=3088.0,
                     exclude={"SOXL", "TQQQ"}, buying_class="Equities")
    assert f["shortfall_ils"] < 1.0, (
        f"still {f['shortfall_ils']:,.2f} short of a raise the book can clearly cover")
    assert f["funded_ils"] > 12000


def test_p_a_sub_one_share_position_is_still_sellable():
    """MSFT held 0.9358 shares -- worth 1,692 and 100% unsellable.

    `int(anything / price)` on a position holding less than one whole share is
    always zero, so it could never contribute a shekel however much was needed.
    A book that stores fractional quantities cannot size its sales in whole
    lots.
    """
    from app.services.funding_service import plan_funding

    rows = [_Frac("MSFT", "NASDAQ", 0.9358, 487.31, 462.40),
            _Frac("V", "NASDAQ", 5.607, 382.41, 340.51)]
    from app.services.fx import fx_rate, price_currency
    rate = fx_rate(price_currency("NASDAQ", {}))
    nav = sum(p.quantity * p.current_price * rate for p in rows)
    snap = {"nav": nav, "exposure_ticker": {}}

    f = plan_funding(rows, snap, None, "Grow", 0.40, 1200.0,
                     cash_ils=0.0, exclude={"V"}, buying_class="Equities")
    assert f["sells"], "a 1,692 position could not raise 1,200 because it holds <1 share"
    assert f["sells"][0]["ticker"] == "MSFT"
    assert 0 < f["sells"][0]["shares"] <= 0.9358


def test_q_a_sale_never_exceeds_the_position():
    """Fractional sizing must not round its way past what is actually held."""
    from app.services.funding_service import plan_funding

    rows, snap = _real_book()
    held = {p.ticker: p.quantity for p in rows}
    f = plan_funding(rows, snap, None, "Grow", 0.40, 999999.0, cash_ils=0.0,
                     exclude=set(), buying_class="Equities")
    for s in f["sells"]:
        assert s["shares"] <= held[s["ticker"]] + 1e-9, (
            f"asked to sell {s['shares']} {s['ticker']} out of {held[s['ticker']]}")


def test_r_tiny_trades_are_still_refused():
    """MIN_TRADE_ILS is the friction floor and fractional sizing must not slip under it.

    Without a whole-share step, nothing else stops a 4-shekel sale.
    """
    from app.services.funding_service import MIN_TRADE_ILS, plan_funding

    rows, snap = _real_book()
    f = plan_funding(rows, snap, None, "Grow", 0.40, 12658.63, cash_ils=3088.0,
                     exclude={"SOXL", "TQQQ"}, buying_class="Equities")
    for s in f["sells"]:
        assert s["value_ils"] >= MIN_TRADE_ILS, f"{s['ticker']} sale below the minimum"


def test_s_the_real_book_reaches_its_64_percent_target():
    """End to end, on the book and sleeve sizes he actually runs.

    Three separate defects had to be fixed before this could pass: the card
    printing a target as a fact, the class-overweight budget blocking a
    same-class swap, and whole-lot sizing on a fractional book.
    """
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "TQQQ", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
             "spot_price": 69.01, "listing_price": 69.01, "quantity": 14.72,
             "cost_basis": 72.12, "expected_return_pct": 10, "volatility_pct": 45},
            {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
             "spot_price": 382.41, "listing_price": 382.41, "quantity": 5.607,
             "cost_basis": 340.51, "expected_return_pct": 7, "volatility_pct": 20},
            {"ticker": "MSFT", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
             "spot_price": 487.31, "listing_price": 487.31, "quantity": 2.9358,
             "cost_basis": 462.40, "expected_return_pct": 8, "volatility_pct": 22},
            {"ticker": "SCHD", "market": "NYSE", "asset_class": "Equities", "depth": 1,
             "spot_price": 35.21, "listing_price": 35.21, "quantity": 42.4748,
             "cost_basis": 33.36, "expected_return_pct": 5, "volatility_pct": 12}]})
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 3088.0})
        c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High"})
        c.post(f"/api/v1/strategies/{'btm_swing_dip'}/apply?sleeve_pct=38.4")
        c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=25.6")
        res = c.post("/api/v1/plan/sleeves/fund?dry_run=true").json()

    rows = {x["strategy_id"]: x for x in res["sleeves"]}
    assert all(r["status"] == "funded" for r in rows.values()), \
        {k: (v["status"], v.get("reason")) for k, v in rows.items()}
    assert res["resulting_sleeve_pct"] == pytest.approx(64.0, abs=0.5), \
        f"intended 64%, would end at {res['resulting_sleeve_pct']}%"
    assert res["fully_funded"] is True


# --------------------------------------------------------------------------- #
# 6. the same number in two units
# --------------------------------------------------------------------------- #
def test_t_held_fractions_are_rendered_with_the_fraction_helper():
    """A units bug that looked exactly like the feature failing.

    `sleeve_pct` is 0-100; `held_pct`, `allocated_held_pct` and `core_held_pct`
    are FRACTIONS, because they are value/NAV. Both went through `_pct`, which
    only rounds -- so 0.2568 rendered as "0.3".

    The card read "Your sleeves - 0.6% held against a 64% target" on a book
    whose sleeves were sitting at 64.27%, correctly funded. That screenshot is
    indistinguishable from the funding having failed, which is what it was
    reported as. The API was right the whole time and the renderer was wrong.
    """
    from pathlib import Path

    html = Path("app/static_app/index.html").read_text(encoding="utf-8")
    assert "function _pctFrac(" in html, "no fraction-aware helper exists"

    nav = html.split("function navSummary(p){", 1)[0]
    for field in ("held_pct", "allocated_held_pct"):
        # Every read of a fraction field must go through _pctFrac, never _pct.
        assert f"_pct({field}" not in html and f"_pct(x.{field}" not in html, (
            f"{field} is a fraction and is being rendered with the 0-100 helper")
    assert "_pctFrac(x.held_pct)" in html
    assert "_pctFrac(MYSLEEVES.allocated_held_pct)" in html
    # And the 0-100 fields must NOT be multiplied.
    assert "_pct(x.sleeve_pct)" in html
    assert "_pctFrac(x.sleeve_pct)" not in html
    assert nav is not None


def test_u_the_two_helpers_disagree_by_exactly_a_hundred():
    """Pinning the contract, so a future edit cannot quietly align them."""
    import re
    from pathlib import Path

    html = Path("app/static_app/index.html").read_text(encoding="utf-8")
    frac = re.search(r"function _pctFrac\(n\)\{([^}]*)\}", html).group(1)
    assert "*1000" in frac.replace(" ", ""), (
        "_pctFrac must scale a fraction to a percentage")
    plain = re.search(r"function _pct\(n\)\{([^}]*)\}", html).group(1)
    assert "*1000" not in plain.replace(" ", ""), "_pct must NOT scale"
