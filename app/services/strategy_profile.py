"""Turn each strategy's basket into an honest, comparable risk/return profile.

The four Grow strategies all read as ``{"Equities": 1.0}`` by asset class and the
UI showed only a description plus a ticker list, so they looked interchangeable.
They are not: a leveraged-Nasdaq basket and a diversified-index basket have very
different volatility, drawdown and concentration. This module derives those
dimensions *from the basket itself* so the differences are visible and grounded,
never a marketing label.

Per-ticker return/vol come from a small, transparent lookup keyed by instrument
character (broad index, sector, single name, leveraged, bond, cash, commodity).
These are planning assumptions, labelled as such -- not forecasts, and never
presented as guarantees.
"""
from __future__ import annotations


# Rough annual (expected_return_pct, volatility_pct) by instrument character.
# Deliberately coarse and conservative; the point is *relative* separation
# between baskets, not a precise forecast of any one fund.
_CLASS_ASSUMPTIONS = {
    "broad_equity": (7.5, 15.0),      # VTI, VT, ITOT
    "us_large": (7.5, 16.0),          # QQQ, SPY-like
    "intl_equity": (6.5, 17.0),       # VXUS, VEA, VWO
    "sector_equity": (9.0, 24.0),     # SMH, sector ETFs
    "single_name": (9.5, 32.0),       # NVDA, MSFT, ...
    "leveraged": (12.0, 55.0),        # TQQQ and other geared funds
    "factor_equity": (7.5, 17.0),     # MTUM, QUAL, AVUV -- diversified, tilted
    "dividend_equity": (7.0, 14.0),   # SCHD, VIG, VYM
    "option_income": (6.5, 12.0),     # JEPI
    "bond": (3.5, 6.0),               # BND, AGG
    "short_bond": (3.0, 2.5),         # SHY, BIL
    "tips": (3.5, 5.0),               # TIP
    "high_yield_bond": (5.5, 10.0),   # HYG
    "commodity": (4.0, 18.0),         # DBC, IAU, GLD, DBA
    "cash": (3.0, 0.5),               # BIL as cash
    "min_vol": (6.5, 11.0),           # USMV
}

_LEVERAGED = {"TQQQ", "SQQQ", "UPRO", "SPXL", "SOXL", "TECL", "UDOW", "TNA"}
_BROAD = {"VTI", "VT", "ITOT", "VOO"}
_INTL = {"VXUS", "VEA", "VWO", "IEFA", "IEMG"}
_SECTOR = {"SMH", "SOXX", "XLK", "XLF", "XLE", "XLV", "IBB"}
_US_LARGE = {"QQQ", "SPY", "IVV", "DIA"}
# Factor ETFs. Absent from every bucket, these fell through to "single_name" --
# so the Factor Stack, three broadly diversified funds, was modelled at a single
# stock's 32% volatility and reported as "Concentrated". The app recommends
# these tickers in its own catalog; it should know what they are.
_FACTOR = {"MTUM", "QUAL", "AVUV", "VTV", "VUG", "VBR", "VB", "IWF", "IWD",
           "SIZE", "VLUE", "AVDV", "DFAC"}
_DIVIDEND = {"SCHD", "VIG", "VYM", "DVY", "NOBL"}
_OPTION_INCOME = {"JEPI", "JEPQ", "QYLD"}
_BONDS = {"BND", "AGG", "BNDX"}
_SHORT_BOND = {"SHY", "BIL", "SGOV", "SHV"}
_TIPS = {"TIP", "VTIP", "SCHP"}
_HY_BOND = {"HYG", "JNK"}
_COMMODITY = {"DBC", "IAU", "GLD", "SLV", "DBA", "PDBC", "USO", "GLDM"}
_MIN_VOL = {"USMV", "SPLV"}


def _character(ticker: str) -> str:
    t = (ticker or "").upper()
    if t in _LEVERAGED:
        return "leveraged"
    if t in _BROAD:
        return "broad_equity"
    if t in _INTL:
        return "intl_equity"
    if t in _US_LARGE:
        return "us_large"
    if t in _SECTOR:
        return "sector_equity"
    if t in _FACTOR:
        return "factor_equity"
    if t in _DIVIDEND:
        return "dividend_equity"
    if t in _OPTION_INCOME:
        return "option_income"
    if t in _BONDS:
        return "bond"
    if t in _SHORT_BOND:
        return "cash" if t in {"BIL", "SGOV", "SHV"} else "short_bond"
    if t in _TIPS:
        return "tips"
    if t in _HY_BOND:
        return "high_yield_bond"
    if t in _COMMODITY:
        return "commodity"
    if t in _MIN_VOL:
        return "min_vol"
    return "single_name"  # unrecognized tickers are treated as concentrated stock


def assumptions_for(ticker: str, asset_class: str | None = None) -> tuple[float, float]:
    """(expected_return_pct, volatility_pct) for a holding, from its character.

    Shared with the plan projection and the risk score so a holding whose intake
    metadata is missing these fields is still modelled as the kind of instrument
    it actually is. Positions added through the UI carry neither field, and the
    old fallbacks -- expected return 0.0, volatility 12/15 -- silently reported a
    normal equity book as "~0%/yr expected return, behind your target", which is
    an artefact of absent metadata rather than anything about the portfolio.

    These are coarse planning assumptions, not forecasts; the same caveat that
    applies to the strategy profiles applies here.
    """
    if asset_class and str(asset_class).lower() == "cash":
        return _CLASS_ASSUMPTIONS["cash"]
    return _CLASS_ASSUMPTIONS.get(_character(ticker), _CLASS_ASSUMPTIONS["single_name"])


def _weights(strategy: dict) -> list[tuple[str, float]]:
    basket = strategy.get("basket") or []
    total = sum(w for _, w in basket) or 1.0
    return [(tk, w / total) for tk, w in basket]


def profile(strategy: dict) -> dict:
    """Compute a grounded profile for one strategy from its basket."""
    weights = _weights(strategy)
    if not weights:
        return {}

    exp_return = 0.0
    var_terms = 0.0
    leverage = False
    single_name_weight = 0.0
    top_weight = 0.0
    # The largest weight held in a CONCENTRATED instrument. A 40% line matters
    # for concentration when it is one company or a geared fund; the same 40% in
    # a diversified factor ETF is just an allocation.
    concentrated_top = 0.0
    class_mix: dict[str, float] = {}
    for tk, w in weights:
        ch = _character(tk)
        r, v = _CLASS_ASSUMPTIONS.get(ch, _CLASS_ASSUMPTIONS["single_name"])
        exp_return += w * r
        # Assume high pairwise correlation within an equity-heavy basket: treat
        # portfolio vol as the weighted average of component vols (a conservative,
        # non-diversifying assumption) rather than sqrt of weighted variances.
        var_terms += w * v
        if ch == "leveraged":
            leverage = True
        if ch == "single_name":
            single_name_weight += w
        if ch in ("single_name", "leveraged"):
            concentrated_top = max(concentrated_top, w)
        top_weight = max(top_weight, w)
        class_mix[ch] = class_mix.get(ch, 0.0) + w

    volatility = var_terms
    # Herfindahl concentration over basket lines (1/n = perfectly even).
    hhi = sum(w * w for _, w in weights)
    n_eff = 1.0 / hhi if hhi else len(weights)
    # A rough planning estimate of a bad-year drawdown: geared to volatility, with
    # a floor/ceiling. NOT a worst case -- a genuine crash can exceed it.
    est_drawdown = min(85.0, max(8.0, volatility * 1.6 + (15.0 if leverage else 0.0)))

    horizon = ("10+ years" if volatility >= 20 else
               "7-10 years" if volatility >= 14 else
               "3-7 years" if volatility >= 8 else "1-3 years")
    # Was `top_weight >= 0.30`, which called ANY three-line basket concentrated
    # regardless of what the lines held -- so a momentum/quality/small-value stack
    # read exactly like a 100% TQQQ sleeve, and the Style chip said the same word
    # on every card. A chip that never varies is a label, not a derivation.
    if single_name_weight >= 0.5 or concentrated_top >= 0.30:
        conc = "Concentrated"
    elif n_eff >= 5:
        conc = "Broadly diversified"
    else:
        conc = "Moderately diversified"

    return {
        "expected_return_pct": round(exp_return, 1),
        "volatility_pct": round(volatility, 1),
        "est_max_drawdown_pct": round(est_drawdown, 0),
        "uses_leverage": leverage,
        "single_name_weight_pct": round(single_name_weight * 100, 0),
        "top_holding_pct": round(top_weight * 100, 0),
        "effective_holdings": round(n_eff, 1),
        "concentration": conc,
        "time_horizon": horizon,
        "holdings_count": len(weights),
    }


def with_profiles(catalog: list[dict]) -> list[dict]:
    """Return catalog entries each augmented with a computed ``profile``."""
    out = []
    for s in catalog:
        out.append({**s, "profile": profile(s)})
    return out


def diff_against_plan(strategy: dict, plan, current_mix: dict | None = None) -> dict:
    """What actually changes if you apply this strategy — objective, risk, mix.

    So the user sees a concrete before/after instead of a bare "HIGH RISK" chip.
    """
    cur_obj = getattr(plan, "objective", None) if plan is not None else None
    cur_risk = getattr(plan, "risk_tolerance", None) if plan is not None else None
    cur_strat = getattr(plan, "strategy", None) if plan is not None else None
    target = strategy.get("target_allocation", {}) or {}
    mix = current_mix or {}
    classes = sorted(set(target) | set(mix))
    mix_changes = []
    for cls in classes:
        cur = round(mix.get(cls, 0.0) * 100)
        tgt = round(target.get(cls, 0.0) * 100)
        if cur != tgt:
            mix_changes.append({"asset_class": cls, "from_pct": cur, "to_pct": tgt})
    return {
        "is_current": cur_strat == strategy.get("id"),
        "objective": {"from": cur_obj, "to": strategy.get("objective"),
                      "changes": cur_obj != strategy.get("objective")},
        "risk_tolerance": {"from": cur_risk, "to": strategy.get("risk_tolerance"),
                           "changes": cur_risk != strategy.get("risk_tolerance")},
        "mix_changes": mix_changes,
    }
