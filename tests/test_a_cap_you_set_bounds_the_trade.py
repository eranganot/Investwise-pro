"""PR 65 — V5's and V6's "PR C: the veto and the cap that nothing reads".

Two claims were carried on that row since 2026-09-02. **One of them is wrong**,
and finding that out is most of this PR.

## Claim A — "nothing on the live path stops a card buying into an overweight class"

**False.** `AllocationAgent.review_buy` really is reachable only from
`pipeline.py:102` / `POST /decision-feed/generate` — but the live card path does
not need it, because `propose_funded_buy` enforces a *stricter* rule of its own.
Measured 2026-09-06 on a book 100% in Equities against a Grow target:

    buy into Equities      -> REFUSED (None)
    buy into Fixed Income  -> card for 1,500

`review_buy` asks one question: is this class overweight. `propose_funded_buy`
asks three — does the trade move the class toward its target, is it a within-class
swap that must be labelled as one, and does total book drift get worse. The
weaker check sits on the quieter path. Wiring it into Today would add a second,
laxer implementation of a rule already enforced, which is the shape this codebase
has spent PRs removing.

So this file pins the guarantee instead, so it cannot regress into being true.

## Claim B — "a hand-set cap plays no part in what gets trimmed"

**True, and it is worse than trimming.** It played no part in BUYING either, and
the buy is what creates the breach. Measured on a book with NVDA at 10%, a
hand-set 12% cap, and Equities under its Grow target so the purchase was
legitimate:

    sized buy: 4,500  ->  NVDA lands at 40.0% of the book

Bounded only by the plan-wide `concentration_cap`, twenty-eight points past the
limit the user set — and the `max_weight` rule then fires a card telling him the
cap has been breached. **A cap that alerts after the trade it could have
prevented is not a cap.**

On the sell side the same position at 20% against its 12% cap ranked BELOW an
overweight BND, carrying the reason *"corrects no drift"*, because `name_over`
was measured against the plan-wide 40% too.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import funding_service as fs  # noqa: E402


class _P:
    def __init__(self, tk, ils, cls="Equities", gain=0.2):
        self.ticker, self.market, self.quantity = tk, "TASE", 100.0
        self.current_price = ils / 100.0
        self.cost_basis = self.current_price / (1 + gain)
        self.meta = {"asset_class": cls}


class _Plan:
    objective, risk_tolerance, cash_target_pct = "Grow", "High", 7.5


def _under_target():
    """VTI 40%, NVDA 10%, BND 50% — Equities is UNDER its Grow target."""
    rows = [_P("VTI", 6000.0), _P("NVDA", 1500.0), _P("BND", 7500.0, cls="Fixed Income")]
    snap = {"nav": 15000.0, "exposure_ticker": {"VTI": 0.4, "NVDA": 0.1, "BND": 0.5}}
    return rows, snap, _Plan()


# --------------------------------------------------------------------------- #
# Claim A — pin the guarantee the live path already gives
# --------------------------------------------------------------------------- #
def test_the_live_path_refuses_a_buy_into_an_over_target_class():
    """The veto V5 and V6 said was missing. It is not, and this keeps it so."""
    rows = [_P("VTI", 9000.0), _P("SCHD", 6000.0)]
    snap = {"nav": 15000.0, "exposure_ticker": {"VTI": 0.6, "SCHD": 0.4}}
    plan = _Plan()

    over = fs.propose_funded_buy(rows=rows, snap=snap, plan=plan, objective="Grow",
                                 cap=0.40, ticker="MTUM", buying_class="Equities",
                                 cash_ils=3000.0)
    assert over is None, "a card was offered buying further into a 100% equity book"

    under = fs.propose_funded_buy(rows=rows, snap=snap, plan=plan, objective="Grow",
                                  cap=0.40, ticker="BND", buying_class="Fixed Income",
                                  cash_ils=3000.0)
    assert under is not None, "the under-target class was refused too; the guard is too broad"


def test_a_within_class_swap_still_needs_the_caller_to_opt_in():
    """The one way past it, and it is not a hole.

    A swap is only labelled a swap when the simulated post-trade class weight
    does not move — `propose_funded_buy` classifies from the simulation, not from
    the caller's intent, so opting in buys the trade and never the label.
    """
    rows = [_P("VTI", 9000.0), _P("SCHD", 6000.0)]
    snap = {"nav": 15000.0, "exposure_ticker": {"VTI": 0.6, "SCHD": 0.4}}
    plan = _Plan()
    assert fs.propose_funded_buy(rows=rows, snap=snap, plan=plan, objective="Grow",
                                 cap=0.40, ticker="MTUM", buying_class="Equities",
                                 cash_ils=3000.0) is None
    swap = fs.propose_funded_buy(rows=rows, snap=snap, plan=plan, objective="Grow",
                                 cap=0.40, ticker="MTUM", buying_class="Equities",
                                 cash_ils=3000.0, allow_within_class_swap=True)
    assert swap is not None
    assert swap.kind == "within_class_swap", swap.kind
    assert "Does not move your Equities weight" in swap.impact


def test_a_hand_set_cap_bounds_the_buy():
    """The 4,500 that became 300."""
    rows, snap, plan = _under_target()

    loose = fs.propose_funded_buy(rows=rows, snap=snap, plan=plan, objective="Grow",
                                  cap=0.40, ticker="NVDA", buying_class="Equities",
                                  cash_ils=2000.0)
    assert loose is not None
    assert (1500.0 + loose.amount_ils) / 15000.0 == pytest.approx(0.40, abs=0.01), (
        "without a rule the plan-wide cap still binds, as it always did")

    capped = fs.propose_funded_buy(rows=rows, snap=snap, plan=plan, objective="Grow",
                                   cap=0.40, ticker="NVDA", buying_class="Equities",
                                   cash_ils=2000.0, name_caps={"NVDA": 0.12})
    assert capped is not None, "the cap silenced the card instead of sizing it"
    landed = (1500.0 + capped.amount_ils) / 15000.0
    assert landed == pytest.approx(0.12, abs=0.005), (
        f"the buy took NVDA to {landed:.1%} against its own 12% cap")


def test_a_hand_set_cap_ranks_the_trim_first_and_says_whose_cap_it_is():
    """20% against a 12% cap ranked below an overweight BND, saying "corrects no drift"."""
    rows = [_P("VTI", 6000.0), _P("NVDA", 3000.0), _P("BND", 6000.0, cls="Fixed Income")]
    snap = {"nav": 15000.0, "exposure_ticker": {"VTI": 0.4, "NVDA": 0.2, "BND": 0.4}}

    before = fs.rank_trim_candidates(rows, snap, "Grow", 0.40)
    assert before[0]["ticker"] != "NVDA", "the fixture no longer reproduces the defect"

    after = fs.rank_trim_candidates(rows, snap, "Grow", 0.40, name_caps={"NVDA": 0.12})
    assert after[0]["ticker"] == "NVDA", [c["ticker"] for c in after]
    assert "your own cap on it of 12%" in after[0]["reason"], after[0]["reason"]


def test_the_tighter_cap_wins_in_both_directions():
    """Neither cap may relax the other."""
    assert fs.effective_name_cap(0.40, "NVDA", {"NVDA": 0.12}) == 0.12
    assert fs.effective_name_cap(0.10, "NVDA", {"NVDA": 0.12}) == 0.10
    assert fs.effective_name_cap(0.40, "NVDA", {}) == 0.40
    assert fs.effective_name_cap(0.40, "NVDA", None) == 0.40
    assert fs.effective_name_cap(0.40, "nvda", {"NVDA": 0.12}) == 0.12


def test_a_book_with_no_rules_behaves_exactly_as_before():
    """Nothing that has not opted in changes."""
    rows, snap, plan = _under_target()
    a = fs.propose_funded_buy(rows=rows, snap=snap, plan=plan, objective="Grow",
                              cap=0.40, ticker="NVDA", buying_class="Equities",
                              cash_ils=2000.0)
    b = fs.propose_funded_buy(rows=rows, snap=snap, plan=plan, objective="Grow",
                              cap=0.40, ticker="NVDA", buying_class="Equities",
                              cash_ils=2000.0, name_caps={})
    assert a is not None and b is not None
    assert a.amount_ils == b.amount_ils


# --------------------------------------------------------------------------- #
# the loader
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_only_active_caps_are_loaded_and_the_units_are_fractions(own_session):
    """`trading_rules.level` is a PERCENTAGE for this rule type. The /100 is here.

    And a disabled cap is a cap the user switched off: a constraint that outlives
    its own switch is worse than no constraint.
    """
    from app.models.tables import TradingRule, User
    from app.services.rules_service import name_caps

    async with own_session() as s:
        u = User(email="caps@x.y", name="C", role="SuperAdmin")
        s.add(u)
        await s.flush()
        s.add(TradingRule(subject="caps@x.y", ticker="NVDA", rule_type="max_weight",
                          level=12.0, active=True))
        s.add(TradingRule(subject="caps@x.y", ticker="TQQQ", rule_type="max_weight",
                          level=20.0, active=False))
        s.add(TradingRule(subject="caps@x.y", ticker="VTI", rule_type="stop_loss",
                          level=90.0, active=True))
        await s.commit()

        got = await name_caps(s, u)

    assert got == {"NVDA": 0.12}, got
