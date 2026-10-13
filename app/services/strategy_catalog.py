"""Backtestable strategy specs for the "Beat the Market" family.

These are rules, not baskets. ``services/strategies.CATALOG`` describes static
allocations whose risk/return can be derived from a lookup table; a rule cannot
be described that way, so each spec here is fed to the backtest engine and its
numbers are *measured* over real price history.

Two structural decisions worth stating, because both were wrong first:

* ``base`` is the core holding, not cash. A swing setup is live perhaps 10-15%
  of the time; parking the rest in T-bills and reporting the ten-year CAGR
  measures a savings account with a strategy attached. Measured on TQQQ
  2016-2026, the identical dip-buy rule scored 4.15%/yr against T-bills and
  15.64%/yr against a QQQ core.
* Risk overlays gate on the instrument being **held**, not on the index. Vol
  targeting a TQQQ sleeve off QQQ's volatility produces a full weight every
  single day and quietly reproduces buy-and-hold.

``sleeve_pct`` is a suggested share of the portfolio, not a rule the app
enforces. A strategy with a strong per-trade edge that is only deployed a
fraction of the time earns a larger sleeve than its headline CAGR implies.
"""
from __future__ import annotations

GOAL = "Beat the Market"

_QQQ_CORE = {"QQQ": 1.0}

CATALOG: list[dict] = [
    {
        "id": "btm_trend_tqqq",
        # PR 128 (D92): also measured over 2000-2016 with leveraged funds rebuilt.
        "long_history": True,
        "name": "Trend-Filtered Leveraged Nasdaq",
        "risk": "Very high",
        "sleeve_pct": 20,
        "horizon": "Hold for years; check weekly",
        "description": ("Holds 3x Nasdaq while QQQ is above its 200-day average, and "
                        "steps back to a plain QQQ core when it is not. Leverage's "
                        "penalty scales with volatility, and volatility clusters below "
                        "the long-term trend -- sitting out those stretches removes most "
                        "of the decay instead of paying it."),
        "weights": {"TQQQ": 1.0}, "base": _QQQ_CORE,
        "overlay": {"kind": "trend_filter", "gate_ticker": "QQQ", "ma_days": 200,
                    "confirm_days": 3,
                    "sweep_param": "ma_days", "sweep_values": [150, 175, 200, 225, 250]},
    },
    {
        "id": "btm_trend_soxl",
        # PR 128 (D92): also measured over 2000-2016 with leveraged funds rebuilt.
        "long_history": True,
        "name": "Trend-Filtered Semiconductors",
        "risk": "Very high",
        "sleeve_pct": 10,
        "horizon": "Hold for years; check weekly",
        "description": ("The same discipline applied to 3x semiconductors, gated on the "
                        "SMH sector index. Higher ceiling and a materially worse "
                        "drawdown than the Nasdaq version."),
        "weights": {"SOXL": 1.0}, "base": {"SMH": 1.0},
        "overlay": {"kind": "trend_filter", "gate_ticker": "SMH", "ma_days": 200,
                    "confirm_days": 3,
                    "sweep_param": "ma_days", "sweep_values": [150, 175, 200, 225, 250]},
    },
    {
        # PR 127. The one candidate of the eleven in the 2026-10-13 plan that
        # passed every gate test (docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md,
        # section 6). Pre-registered as Medium-high; it MEASURES High (52.7%
        # worst drop), and D88 shows the measured tier.
        "id": "btm_qld_trend",
        "name": "Trend-Filtered 2x Nasdaq",
        "risk": "Medium-high",
        "sleeve_pct": 20,
        "horizon": "Hold for years; check weekly",
        "long_history": True,
        "description": ("The Trend-Filtered Leveraged Nasdaq rule at 2x instead of 3x: QLD "
                        "while QQQ is above its 200-day average, plain QQQ when it is not. "
                        "Leverage decay grows with roughly the square of the leverage, so 2x "
                        "keeps most of the trend's benefit at well under half the decay. It is "
                        "still a Nasdaq bet, not a diversifier."),
        "weights": {"QLD": 1.0}, "base": _QQQ_CORE,
        "overlay": {"kind": "trend_filter", "gate_ticker": "QQQ", "ma_days": 200,
                    "confirm_days": 3,
                    "sweep_param": "ma_days", "sweep_values": [150, 175, 200, 225, 250]},
    },
    {
        "id": "btm_vol_target_tqqq",
        # PR 128 (D92): also measured over 2000-2016 with leveraged funds rebuilt.
        "long_history": True,
        "name": "Volatility-Targeted Leverage",
        "risk": "High",
        "sleeve_pct": 25,
        "horizon": "Hold for years; rebalances itself",
        "description": ("Scales leverage down as realized volatility rises, keeping risk "
                        "roughly constant instead of exposure constant. This attacks the "
                        "decay term directly rather than trying to time direction -- the "
                        "best-evidenced way to make leverage survivable."),
        "weights": {"TQQQ": 1.0}, "base": _QQQ_CORE,
        # PR 125. The target moved 10.6 times a year but the simulator re-trued
        # the book ~80 times a year at its 1% default, ~70 of them for price
        # drift alone (a 3x leg beside its 1x base): 162 "trades a year". At a
        # 10-point band (each leg 5 points off) the same rule measured 24.7
        # trades a year, 32.27%/yr vs 32.03%/yr, worst drop 63.1% vs 63.25%.
        # Widening `rebalance_band` instead did nothing: 0.5 traded MORE (201.7).
        "drift_band": 0.10,
        "overlay": {"kind": "vol_target", "gate_ticker": "TQQQ", "vol_days": 20,
                    "target_vol_pct": 35, "max_weight": 1.0, "rebalance_band": 0.15,
                    "sweep_param": "target_vol_pct", "sweep_values": [25, 30, 35, 40, 45]},
    },
    {
        "id": "btm_swing_dip",
        "name": "Swing: Dip-Buy in an Uptrend",
        "retired": {"on": "2026-10-13", "decision": "D87", "move_to": "btm_qqq_hold",
                    "why": ("Did not beat simply holding QQQ, its own base: 17.9%/yr "
                            "against 21.3%, with a deeper worst drop (-40.6% vs "
                            "-35.1%) and 45 trades a year.")},
        "risk": "High",
        "sleeve_pct": 15,
        "horizon": "Days to weeks per trade; check daily",
        "description": ("Buys the leveraged sleeve when it is short-term oversold, but "
                        "only while the index is above its 200-day, and exits on the "
                        "first strength. Capital sits in the core between setups. Judge "
                        "it on expectancy per trade, not on annual return -- it is only "
                        "deployed a fraction of the year."),
        "weights": {"TQQQ": 1.0}, "base": _QQQ_CORE,
        "overlay": {"kind": "rsi_pullback", "gate_ticker": "QQQ", "signal_ticker": "TQQQ",
                    "ma_days": 200, "rsi_days": 2, "entry": 15, "exit_ma": 5,
                    "sweep_param": "entry", "sweep_values": [5, 10, 15, 20, 25]},
    },
    {
        "id": "btm_swing_breakout",
        "name": "Swing: Channel Breakout",
        "retired": {"on": "2026-10-13", "decision": "D87", "move_to": "btm_qqq_hold",
                    "why": ("Matched holding QQQ (21.2%/yr against 21.3%) at nearly "
                            "twice its worst drop (-61.3% vs -35.1%), and fell from "
                            "32.0%/yr to 11.7%/yr between the two halves of its decade.")},
        "risk": "High",
        "sleeve_pct": 15,
        "horizon": "Weeks per trade; check daily",
        "description": ("Enters on a 20-day high and exits on a 10-day low -- two "
                        "parameters and a long published record, which makes it the "
                        "hardest of these setups to curve-fit."),
        "weights": {"TQQQ": 1.0}, "base": _QQQ_CORE,
        "overlay": {"kind": "donchian", "gate_ticker": "QQQ", "entry_days": 20,
                    "exit_days": 10,
                    "sweep_param": "entry_days", "sweep_values": [10, 15, 20, 30, 40]},
    },
    {
        "id": "btm_factor_stack",
        "name": "Factor Stack",
        "risk": "Medium-high",
        "sleeve_pct": 40,
        "horizon": "10+ years",
        "description": ("Momentum, quality and small-cap value, unleveraged. The "
                        "academically documented route to beating a cap-weighted index; "
                        "the premia are real but modest, so this is the patient option "
                        "rather than the exciting one."),
        "weights": {"MTUM": 0.40, "QUAL": 0.35, "AVUV": 0.25},
        "overlay": {"kind": "buy_hold"},
    },
    {
        "id": "btm_dual_momentum",
        "name": "Dual Momentum Rotation",
        "risk": "Medium",
        "sleeve_pct": 30,
        "horizon": "Months per position; check monthly",
        "description": ("Rotates into whichever of US, international or bonds has the "
                        "strongest 12-month return, and into T-bills when none of them "
                        "is rising at all. Lower ceiling, much shallower drawdowns."),
        "weights": {"QQQ": 1.0}, "risk_off": "BIL",
        "overlay": {"kind": "dual_momentum", "universe": ["QQQ", "VTI", "VXUS"],
                    "lookback_days": 252, "risk_off": "BIL",
                    "sweep_param": "lookback_days", "sweep_values": [126, 189, 252, 315]},
    },
    {
        # THE BASE, WITH NO RULE ON TOP. Added 2026-09-05 for B2.
        #
        # Every other entry here is a rule; this one deliberately is not, and it
        # exists because the catalogue had no way to express "hold the index".
        # Retiring an overlay therefore had no destination: `DELETE
        # /plan/sleeves/btm_swing_dip` retires the whole SLEEVE, collapsing its
        # 36% into the core, taking the core from 27% to ~55% and swinging the
        # book's Fixed Income target from 7.8% to ~22% -- so the app would start
        # asking to sell equities into BND. That is not what "drop the rule,
        # keep the position" means, and the absence of this entry is the only
        # reason the two were the same operation.
        #
        # WHY IT WAS NEEDED, measured on the shipped engine over 2016-09-06 to
        # 2026-09-04 (2,514 sessions, adjusted closes):
        #
        #   hold TQQQ, do nothing        40.25%/yr   vol 66.74%   maxDD 81.66%
        #   hold QQQ, do nothing         20.73%/yr   vol 22.56%   maxDD 35.12%
        #   btm_swing_dip as shipped     17.19%/yr   vol 34.35%   maxDD 40.55%
        #
        # The overlay turned a 40.25% instrument into 17.19% -- below simply
        # holding the base -- while carrying a DEEPER drawdown than the base and
        # paying ~7 points a year to 45 round-trips. Leverage decay was not the
        # cause; being out of the market during the compounding was. Full
        # working in STATUS.md B2.
        #
        # NO `base` KEY, and that is the definition rather than an omission: a
        # base is where a rule goes when it is off, and there is no rule. It is
        # also what makes the whole sleeve fundable -- `sleeve_targets` funds
        # `weights` minus `base`, so an entry that listed QQQ in both would ask
        # the app to fund nothing.
        #
        # `_target_weights` needs no new engine support: a spec with no overlay
        # already falls through to `kind = "buy_hold"` and returns constant
        # weights, so this is measured by the same code path as everything else.
        "id": "btm_qqq_hold",
        # PR 128 (D92): also measured over 2000-2016 with leveraged funds rebuilt.
        "long_history": True,
        "name": "Hold the Nasdaq index",
        "risk": "Medium",
        "sleeve_pct": 30,
        "horizon": "Years; nothing to check",
        "description": ("Holds QQQ and does nothing else. No signal, no exits, no "
                        "trades after the first one. It is here so that retiring a "
                        "rule has somewhere to land -- keeping the position while "
                        "dropping the rule that was steering it -- and so that any "
                        "overlay in this catalogue can be measured against simply "
                        "holding what it trades."),
        "weights": _QQQ_CORE,
        "overlay": {"kind": "buy_hold"},
    },
]

_BY_ID = {s["id"]: s for s in CATALOG}


# --- Retirement (PR 125, D87) -----------------------------------------------
#
# A retired strategy is NOT deleted. About ten paths (signals, funding, the
# target solver, the frontier) look a sleeve's strategy up by id through `get`;
# removing the entry while any book still runs it would make every one of those
# lookups return None and quietly stop steering that sleeve. So it stays in
# CATALOG, keeps being measured, resolves through `get` -- and is simply not
# offered: it leaves Explore's main list, cannot be added as a new sleeve or be
# swapped into, and a sleeve still on it is shown with one action: move it to
# `move_to` at the same size (the D47 swap).

def retired(strategy_id: str) -> dict | None:
    """The retirement record, or None for a strategy that is still offered."""
    e = _BY_ID.get((strategy_id or "").strip())
    return (e or {}).get("retired")


def active_ids() -> list[str]:
    return [e["id"] for e in CATALOG if not e.get("retired")]


# --- Measured tiers (PR 125) --------------------------------------------------
#
# Risk tiers used to be hand-labelled, and disagreed with the measurement:
# Channel Breakout (-61.3% worst drop) read "High" while Trend Nasdaq (-62.4%)
# read "Very high". A tier now comes from the measured worst drop, by these
# bands, whatever the strategy was designed to be. The hand label survives as
# `designed_risk` for a strategy not yet measured.
RISK_BANDS = ((36.0, "Medium"), (45.0, "Medium-high"), (60.0, "High"))


def measured_risk(max_drawdown_pct: float | None) -> str | None:
    if max_drawdown_pct is None:
        return None
    dd = abs(float(max_drawdown_pct))
    for limit, label in RISK_BANDS:
        if dd <= limit:
            return label
    return "Very high"


def risk_label(entry: dict, backtest: dict | None) -> str:
    m = ((backtest or {}).get("metrics") or {}) if isinstance(backtest, dict) else {}
    return measured_risk(m.get("max_drawdown_pct")) or entry.get("risk") or "High"


def _spec(entry: dict) -> dict:
    """The engine only wants the mechanical parts; the prose stays behind."""
    keys = ("id", "weights", "base", "risk_off", "overlay", "drift_band", "proxy", "long_history")
    return {k: entry[k] for k in keys if k in entry}


def backtestable(only: list[str] | None = None) -> list[dict]:
    wanted = set(only) if only else None
    return [_spec(e) for e in CATALOG if wanted is None or e["id"] in wanted]


def get(strategy_id: str) -> dict | None:
    return _BY_ID.get(strategy_id)


def ids() -> list[str]:
    return [e["id"] for e in CATALOG]


def steps_back_to(aggressive_ticker: str, base_ticker: str) -> bool:
    """True when some rule here holds ``aggressive_ticker`` and steps back to
    ``base_ticker`` when its signal is off.

    This is the "retire the overlay, hold the base" relationship, read straight
    off the specs rather than off a user's history: TQQQ steps back to QQQ,
    SOXL steps back to SMH. It is asymmetric on purpose -- QQQ does not step
    back to TQQQ -- because the point is that the move is a DOWNGRADE of an
    exposure the book already has, not the start of a new one.

    Used by the cold-start sleeve card to decide whether it may act in one tap.
    That card is guidance by default for a stated reason: one tap taking a book
    from nothing to a fifth in a 3x fund is not topping up a drift. Selling the
    3x fund to buy the index it was leveraging is the one funding move that
    objection does not describe -- the exposure was already there, at a higher
    multiple, under a rule that is no longer running.
    """
    a = (aggressive_ticker or "").strip().upper()
    b = (base_ticker or "").strip().upper()
    if not a or not b or a == b:
        return False
    for e in CATALOG:
        base = {str(t).upper() for t in (e.get("base") or {})}
        if not base or b not in base:
            continue
        if a in {str(t).upper() for t in (e.get("weights") or {})} - base:
            return True
    return False


# --- Presentation ----------------------------------------------------------
#
# The Plan renderer was written for static baskets: it reads `risk_tolerance`,
# `basket` as [ticker, weight] pairs and a `profile` of derived assumptions. A
# rule-based strategy has no derived profile -- it has a *measured* one -- so it
# is adapted to that shape here rather than the renderer growing a second code
# path for every field.

# Presentation risk label -> the vocabulary the existing cards already use.
_RISK_TOLERANCE = {"Very high": "High", "High": "High",
                   "Medium-high": "Medium", "Medium": "Medium"}


def as_plan_cards(measured: dict[str, dict] | None = None) -> list[dict]:
    """Catalog entries in the shape the Plan tab renders, with measured numbers.

    ``measured`` is keyed by strategy id (from ``backtest_service.get_many``).
    A strategy with no stored result still returns a card -- it simply carries
    ``backtest: None``, so the UI can say "not measured yet" instead of drawing
    a blank where a number belongs.
    """
    from app.services import strategy_profile as _prof

    measured = measured or {}
    cards = []
    for e in CATALOG:
        weights = e.get("weights") or {}
        # Style and leverage come from the basket, exactly as the static families
        # derive theirs -- they are structural facts about what is held, not
        # forecasts, so they are safe to show next to MEASURED returns. The
        # derived return/vol from the same call are deliberately NOT used here:
        # these cards report what the rule actually did, and mixing an estimate
        # in among measurements is the confusion this family exists to avoid.
        _p = _prof.profile({"basket": sorted((tk, w) for tk, w in weights.items())}) or {}
        cards.append({
            "id": e["id"],
            "goal": GOAL,
            "name": e["name"],
            "description": e["description"],
            # Applying one of these sets a growth objective; the DB column is
            # String(16) and would not hold the goal label itself.
            "objective": "Grow",
            "risk_tolerance": _RISK_TOLERANCE.get(risk_label(e, measured.get(e["id"])), "High"),
            "risk_label": risk_label(e, measured.get(e["id"])),
            "designed_risk": e.get("risk"),
            "retired": e.get("retired"),
            "sleeve_pct": e.get("sleeve_pct"),
            "sleeve_default_pct": e.get("sleeve_pct"),
            "horizon": e.get("horizon"),
            # Structural, derived from the basket -- see the comment above.
            "style": _p.get("concentration"),
            "uses_leverage": _p.get("uses_leverage", False),
            "basket": sorted((tk, w) for tk, w in weights.items()),
            "base_when_flat": sorted(e.get("base") or {}) or None,
            "rule": _rule_summary(e),
            "measured": True,
            "backtest": measured.get(e["id"]),
        })
    return cards


def _rule_summary(entry: dict) -> str:
    """One line of plain language describing what the rule actually does.

    The description sells the idea; this says the mechanism, so a card cannot
    imply a discipline it does not implement.
    """
    o = entry.get("overlay") or {}
    kind = o.get("kind", "buy_hold")
    base = ", ".join(sorted(entry.get("base") or {})) or o.get("risk_off") or entry.get("risk_off") or "cash"
    if kind == "buy_hold":
        return "Held throughout; rebalanced back to target weights."
    if kind == "trend_filter":
        return (f"Holds the sleeve while {o.get('gate_ticker')} is above its "
                f"{o.get('ma_days', 200)}-day average for {o.get('confirm_days', 1)} "
                f"session(s); otherwise {base}.")
    if kind == "rsi_pullback":
        return (f"Buys when {o.get('signal_ticker') or o.get('gate_ticker')} is short-term "
                f"oversold and {o.get('gate_ticker')} is above its {o.get('ma_days', 200)}-day; "
                f"exits on the first close above its {o.get('exit_ma', 5)}-day. Otherwise {base}.")
    if kind == "donchian":
        return (f"Enters on a {o.get('entry_days', 20)}-day high, exits on a "
                f"{o.get('exit_days', 10)}-day low. Otherwise {base}.")
    if kind == "vol_target":
        return (f"Scales the sleeve so realized volatility stays near "
                f"{o.get('target_vol_pct')}%, rebalancing when the target moves more "
                f"than {int(float(o.get('rebalance_band', 0.15)) * 100)} points. Remainder in {base}.")
    if kind == "dual_momentum":
        return (f"Each month holds whichever of {', '.join(o.get('universe') or [])} has the "
                f"strongest {int(o.get('lookback_days', 252) / 21)}-month return, or {base} "
                f"when none is rising.")
    if kind == "sector_momentum":
        return (f"Holds the top {o.get('top_n', 3)} of "
                f"{', '.join(o.get('universe') or [])} on trailing return, or {base}.")
    if kind == "ma_cross":
        return (f"Holds while the {o.get('fast')}-day average is above the "
                f"{o.get('slow')}-day; otherwise {base}.")
    return f"Rule: {kind}."


def sleeve_basket(strategy_id: str, sleeve_pct: float | None = None) -> list[list]:
    """The basket scaled to a sleeve, with the remainder in the core holding.

    A rule-based strategy is a sleeve, not a portfolio. Loading
    "trend-filtered 3x Nasdaq" at 20% should buy a fifth of the book in TQQQ and
    leave the rest in QQQ -- not put everything into a 3x fund because that is
    what the model basket says in isolation.

    Falls back to the catalog's suggested size, and to 100% only for a strategy
    that has no core to fall back to (the unleveraged families), where the whole
    allocation IS the strategy.
    """
    e = _BY_ID.get(strategy_id)
    if e is None:
        return []
    weights = e.get("weights") or {}
    base = e.get("base") or {}
    pct = sleeve_pct if sleeve_pct is not None else e.get("sleeve_pct")
    if not base or pct is None:
        return sorted([tk, w] for tk, w in weights.items())
    frac = max(0.0, min(1.0, float(pct) / 100.0))
    out: dict[str, float] = {tk: w * frac for tk, w in weights.items()}
    for tk, w in base.items():
        out[tk] = out.get(tk, 0.0) + w * (1.0 - frac)
    return sorted([tk, round(w, 4)] for tk, w in out.items() if w > 0.0005)


def as_legacy_strategy(strategy_id: str, sleeve_pct: float | None = None) -> dict | None:
    """The shape ``strategy_service`` expects, so apply/preview/load-basket work.

    Those helpers were written against ``services.strategies`` entries and read
    ``objective``, ``risk_tolerance``, ``target_allocation``, ``basket`` and
    ``preferred_depth``. Adapting here keeps one code path for applying a
    strategy rather than forking it by catalog.

    ``target_allocation`` is all-equity for every one of these: the rules move
    between an aggressive instrument and a core holding, both equity. Time spent
    in T-bills is a transient state of the rule, not a target the plan should
    hold the book to -- writing it into the target would make the allocation
    engine permanently demand a cash weight the strategy only wants sometimes.
    """
    e = _BY_ID.get(strategy_id)
    if e is None:
        return None
    return {
        "id": e["id"], "goal": GOAL, "name": e["name"],
        "description": e["description"],
        "objective": "Grow",
        "risk_tolerance": _RISK_TOLERANCE.get(e.get("risk", ""), "High"),
        "preferred_depth": 3,
        "target_allocation": {"Equities": 1.0},
        "basket": sleeve_basket(strategy_id, sleeve_pct),
        "sleeve_pct": (sleeve_pct if sleeve_pct is not None else e.get("sleeve_pct")),
    }


# Public alias: the signal service needs the same one-line mechanism text.
rule_summary = _rule_summary


# --- Discipline -------------------------------------------------------------

def discipline_rules(strategy_id: str, measured: dict | None = None) -> list[dict]:
    """Trading rules that make a strategy survivable, sized from its own backtest.

    A strategy is a rule you intend to follow, and the gap between the
    backtested number and the lived one is almost entirely the days you did not
    follow it. These are the standing orders that keep firing when you are not
    looking.

    Levels come from the strategy's MEASURED volatility and drawdown, not from a
    round number: a trailing stop that would have fired constantly during the
    backtest is not discipline, it is churn. With no stored backtest this
    returns nothing rather than guessing -- an invented stop level is worse than
    no stop, because it looks like it was calculated.

    The sleeve **cap** used to live here too, at ``sleeve_pct * 1.5``. It moved:
    applying a strategy now arms a ``max_weight`` at the sleeve size itself
    (``strategy_service._arm_sleeve_cap``), because a cap suggested at 30% when
    the slider says 20% is not the number the user chose. Emitting one here as
    well would put two ``max_weight`` rules on the same ticker at two different
    levels, which the Rules UI correctly reports as duplicates.
    """
    entry = _BY_ID.get(strategy_id)
    if entry is None:
        return []
    metrics = ((measured or {}).get("metrics") or {}) if measured else {}
    vol = metrics.get("volatility_pct")
    if not vol:
        return []

    aggressive = sorted(set(entry.get("weights") or {}) - set(entry.get("base") or {}))
    if not aggressive:
        return []

    # A trailing stop must sit outside the strategy's ordinary noise or it exits
    # on a Tuesday. Roughly a third of annual volatility approximates a large
    # move without being a crash; clamped so a very calm or very wild basket
    # still gets a usable level.
    trail = max(12.0, min(35.0, round(float(vol) / 3.0)))
    out = []
    for tk in aggressive:
        out.append({
            # STAMPED WITH THE STRATEGY THAT ASKED FOR IT. These used to go out
            # unattributed, and `apply` passes `strategy_id` straight through --
            # so the rule landed in the database with NULL and nothing could
            # ever tell which sleeve it belonged to. `_arm_sleeve_caps` retires
            # a cap for a ticker no sleeve wants any more, precisely so a
            # ceiling is not left behind a removed sleeve; the stop and the
            # take-profit had no such path, because they could not be
            # identified. PR 59.1.
            "strategy_id": strategy_id,
            "ticker": tk, "rule_type": "trailing_stop", "mode": "pct", "level": trail,
            "note": (f"{entry['name']}: {trail:.0f}% trailing stop, ~1/3 of the "
                     f"strategy's measured {float(vol):.0f}% volatility"),
        })
    # A take-profit only where the strategy's own measured drawdown says a
    # round trip is worth banking. Sized from the same volatility as the stop,
    # so the two are consistent rather than one derived and one guessed.
    dd = metrics.get("max_drawdown_pct")
    if dd and float(dd) >= 40.0:
        tp = max(20.0, min(80.0, round(float(vol) * 1.2)))
        for tk in aggressive:
            out.append({
                "strategy_id": strategy_id,
                "ticker": tk, "rule_type": "take_profit", "mode": "pct", "level": tp,
                "note": (f"{entry['name']}: bank a {tp:.0f}% gain. This basket's "
                         f"measured worst drawdown is {float(dd):.0f}%, so a round "
                         f"trip is worth taking off the table."),
            })

    # No max_weight here -- applying the strategy arms the sleeve cap at the size
    # the user actually chose. See the docstring.
    return out


def signal_rules(strategy_id: str, measured: dict | None = None) -> list[dict]:
    """Entry and exit rules that ARE the strategy's signal, not a guess at it.

    Every level in `discipline_rules` is a protective band around a position.
    These are different: they are the strategy's own entry and exit, surfaced as
    armable rules so they get an audit trail, an alert, and an on/off switch.

    They carry the strategy's MEASURED per-trade statistics -- win rate, average
    hold, expectancy -- because an armed rule that cannot say how it performed is
    an opinion wearing a rule's clothes. With no stored backtest this returns
    nothing rather than describing trades nobody counted.
    """
    entry = _BY_ID.get(strategy_id)
    if entry is None:
        return []
    m = ((measured or {}).get("metrics") or {}) if measured else {}
    if not m.get("trades_per_year"):
        return []
    aggressive = sorted(set(entry.get("weights") or {}) - set(entry.get("base") or {}))
    if not aggressive:
        return []

    stats = {k: m.get(k) for k in ("win_rate_pct", "expectancy_pct_per_trade",
                                   "avg_holding_days", "profit_factor",
                                   "trades_per_year")
             if m.get(k) is not None}
    if not stats.get("win_rate_pct"):
        return []                       # nothing measured worth quoting

    mechanism = _rule_summary(entry)
    out = []
    for tk in aggressive:
        for side, verb in (("entry", "Buy"), ("exit", "Exit")):
            out.append({
                "ticker": tk, "rule_type": "strategy_signal", "mode": side,
                "level": 0.0, "strategy_id": strategy_id,
                "note": f"{entry['name']}: {verb.lower()} when the strategy says so.",
                "mechanism": mechanism,
                "stats": stats,
                "why": (f"{verb} on the rule you already chose, rather than on the "
                        f"day you happen to look. Measured over the backtest: "
                        f"{stats['win_rate_pct']:.0f}% of round trips profitable"
                        + (f", average hold {stats['avg_holding_days']:.0f} days"
                           if stats.get("avg_holding_days") else "")
                        + (f", expectancy {stats['expectancy_pct_per_trade']:+.2f}% per trade"
                           if stats.get("expectancy_pct_per_trade") is not None else "")
                        + "."),
            })
    return out
