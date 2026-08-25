"""Bond options - real, live-priced, retail-investable instruments.

WHY THIS FILE DID NOT EXIST UNTIL NOW, AND WHAT THAT COST.

`universe.full_universe()` returned 83 candidates: 64 Equities, 19 Commodities,
and **zero Fixed Income**. Not one bond ETF, on an app whose objective presets
have wanted 10-60% bonds since the beginning:

    Grow      80 / 10 / 10        Preserve  30 / 60 / -
    Balanced  60 / 30 / 10        Income    40 / 50 / 10

So every book the app has ever managed had a Fixed Income target it could not
place. `_redeploy_cash_recs` looked for a candidate whose class was "Fixed
Income", never found one, and dropped the money into `unplaced` with the reason
*"you hold no Fixed Income and no candidate was available"* -- accurate, and
reported every single scan, forever.

It became urgent on 2026-08-25. The core is `bal_all_weather`, 40% Fixed Income
of a 36% core = **14.4% of the book, about 3,054 ILS**, and PR 9 had just fixed
the rebalance card so it asks for the right number. The app now says "Buy Fixed
Income ~3,054" correctly and still cannot name a single thing to buy.

WHAT A BOND CATALOGUE HAS TO CARRY THAT AN EQUITY ONE DOES NOT.

**Duration**, because it is the entire risk. Two "bond funds" can behave nothing
alike: SGOV moves ~0.1% when rates move 1%, TLT moves ~16%. Ranking them
together on price alone is meaningless.

**Credit**, separately from duration, because credit risk is EQUITY risk wearing
a bond's name. High yield fell with stocks in 2008, 2020 and 2022. A book buying
bonds as ballast against a 64% leveraged-equity sleeve and getting handed HYG has
bought more of what it was trying to hedge.

**A currency caveat.** Every fund here is USD-denominated. For an ILS-based book
the "safe" asset carries FX risk, which is a real cost and not this file's to
solve -- but it must be said rather than discovered.

**A tax caveat.** MUB and VTEB are exempt from US federal income tax. That is
worth nothing to a non-US investor and the exemption is priced INTO the yield,
so a non-US holder takes the lower yield and gets no relief. A catalogue that
lists them without saying so is quietly recommending a worse bond.

LIVENESS. Every ticker below was checked against the live Yahoo provider on
2026-08-25: all returned 251 daily bars ending on the previous close. That is
the check `commodities.py` promised and did not perform, which is how four
delisted iPath ETNs sat in the buy catalogue for months. The general gate -- no
card may name a ticker without a recent close -- is still a backlog item; until
it exists, this list is hand-maintained and `DELISTED` is the record.
"""
from __future__ import annotations

# ticker, display name, category, expense ratio %, approximate effective
# duration in years, credit exposure, optional caveat.
#
# `duration_yrs` is CATALOGUE METADATA, not a measurement. It comes from the
# fund's published profile and drifts as the portfolio rolls. It is used to
# BUCKET candidates (cash-like / short / intermediate / long), never quoted to
# the user as a computed figure -- `screen_bonds` derives a measured volatility
# from real history for anything that has to be ranked, exactly as the app keeps
# estimated and measured drawdown in separate fields elsewhere.
CATALOG: list[dict] = [
    # ---- Aggregate: the default for a core's bond allocation -------------- #
    {"ticker": "BND", "name": "Total US bond market", "category": "Aggregate",
     "expense_ratio_pct": 0.03, "duration_yrs": 6.0, "credit": "investment_grade"},
    {"ticker": "AGG", "name": "US aggregate bond", "category": "Aggregate",
     "expense_ratio_pct": 0.03, "duration_yrs": 6.0, "credit": "investment_grade"},
    {"ticker": "BNDW", "name": "Total world bond", "category": "Aggregate",
     "expense_ratio_pct": 0.05, "duration_yrs": 6.5, "credit": "investment_grade"},
    {"ticker": "BNDX", "name": "International bond (USD-hedged)", "category": "Aggregate",
     "expense_ratio_pct": 0.07, "duration_yrs": 7.0, "credit": "investment_grade",
     "caveat": "Hedged back to USD, not to ILS."},

    # ---- Government, by maturity: ballast with no credit risk ------------- #
    {"ticker": "GOVT", "name": "US Treasuries, all maturities", "category": "Government",
     "expense_ratio_pct": 0.05, "duration_yrs": 5.8, "credit": "government"},
    {"ticker": "VGSH", "name": "Short-term Treasury (1-3y)", "category": "Government",
     "expense_ratio_pct": 0.03, "duration_yrs": 1.9, "credit": "government"},
    {"ticker": "VGIT", "name": "Intermediate Treasury (3-10y)", "category": "Government",
     "expense_ratio_pct": 0.03, "duration_yrs": 5.2, "credit": "government"},
    {"ticker": "VGLT", "name": "Long-term Treasury (10y+)", "category": "Government",
     "expense_ratio_pct": 0.03, "duration_yrs": 15.5, "credit": "government"},
    {"ticker": "TLT", "name": "20+ year Treasury", "category": "Government",
     "expense_ratio_pct": 0.15, "duration_yrs": 16.5, "credit": "government",
     "caveat": "A 1% rate move swings this ~16%. It is a rates BET, not ballast."},
    {"ticker": "IEF", "name": "7-10 year Treasury", "category": "Government",
     "expense_ratio_pct": 0.15, "duration_yrs": 7.3, "credit": "government"},

    # ---- Cash-like: near-zero duration ------------------------------------ #
    {"ticker": "SGOV", "name": "0-3 month T-bills", "category": "Cash-like",
     "expense_ratio_pct": 0.09, "duration_yrs": 0.1, "credit": "government"},
    {"ticker": "BIL", "name": "1-3 month T-bills", "category": "Cash-like",
     "expense_ratio_pct": 0.14, "duration_yrs": 0.1, "credit": "government"},
    {"ticker": "USFR", "name": "Floating-rate Treasury", "category": "Cash-like",
     "expense_ratio_pct": 0.15, "duration_yrs": 0.0, "credit": "government"},

    # ---- Inflation-linked -------------------------------------------------- #
    {"ticker": "SCHP", "name": "TIPS (inflation-protected)", "category": "Inflation-linked",
     "expense_ratio_pct": 0.03, "duration_yrs": 6.7, "credit": "government"},
    {"ticker": "VTIP", "name": "Short-term TIPS", "category": "Inflation-linked",
     "expense_ratio_pct": 0.03, "duration_yrs": 2.6, "credit": "government"},
    {"ticker": "TIP", "name": "TIPS (TIP)", "category": "Inflation-linked",
     "expense_ratio_pct": 0.18, "duration_yrs": 6.7, "credit": "government"},

    # ---- Investment-grade corporate --------------------------------------- #
    {"ticker": "VCSH", "name": "Short-term corporate", "category": "Corporate",
     "expense_ratio_pct": 0.03, "duration_yrs": 2.6, "credit": "investment_grade"},
    {"ticker": "VCIT", "name": "Intermediate corporate", "category": "Corporate",
     "expense_ratio_pct": 0.03, "duration_yrs": 6.2, "credit": "investment_grade"},
    {"ticker": "LQD", "name": "Investment-grade corporate", "category": "Corporate",
     "expense_ratio_pct": 0.14, "duration_yrs": 8.4, "credit": "investment_grade"},

    # ---- High yield: CREDIT RISK, i.e. equity risk in a bond wrapper ------- #
    {"ticker": "USHY", "name": "US high yield (low-fee)", "category": "High yield",
     "expense_ratio_pct": 0.08, "duration_yrs": 3.3, "credit": "high_yield",
     "caveat": "High yield falls WITH equities. It is not ballast."},
    {"ticker": "HYG", "name": "US high yield (HYG)", "category": "High yield",
     "expense_ratio_pct": 0.49, "duration_yrs": 3.2, "credit": "high_yield",
     "caveat": "High yield falls WITH equities. It is not ballast."},

    # ---- Emerging-market and municipal: real instruments, real caveats ----- #
    {"ticker": "EMB", "name": "Emerging-market USD bonds", "category": "Emerging market",
     "expense_ratio_pct": 0.39, "duration_yrs": 7.0, "credit": "emerging",
     "caveat": "Sovereign credit risk plus EM equity correlation."},
    {"ticker": "VTEB", "name": "US municipal bonds (low-fee)", "category": "Municipal",
     "expense_ratio_pct": 0.03, "duration_yrs": 6.5, "credit": "municipal",
     "caveat": "The US-tax exemption is priced into the yield. A non-US investor "
               "takes the lower yield and gets no relief."},
    {"ticker": "MUB", "name": "US municipal bonds", "category": "Municipal",
     "expense_ratio_pct": 0.05, "duration_yrs": 6.4, "credit": "municipal",
     "caveat": "The US-tax exemption is priced into the yield. A non-US investor "
               "takes the lower yield and gets no relief."},
]

# Nothing here yet, and that is the point of having it: `commodities.DELISTED`
# was created AFTER four dead ETNs had been recommended for months. This exists
# from day one so the first bond fund to close has somewhere to be recorded
# rather than being quietly deleted by whoever notices.
DELISTED: dict[str, str] = {}

# Every fund above is USD-denominated. Said once, here, so a card can say it
# without each caller inventing its own wording.
CURRENCY_NOTE = ("These are USD-denominated funds. On an ILS book the bond "
                 "sleeve carries currency risk as well as rate risk.")

_BY_TICKER = {c["ticker"]: c for c in CATALOG}

# Credit that behaves like equity when it matters. A book buying bonds as
# ballast must be able to exclude these as a class, not one ticker at a time.
EQUITY_LIKE_CREDIT = frozenset({"high_yield", "emerging"})


def get(ticker: str) -> dict | None:
    return _BY_TICKER.get((ticker or "").upper())


def tickers() -> list[str]:
    return [c["ticker"] for c in CATALOG]


def duration_bucket(years: float | None) -> str:
    """The only axis on which two bond funds are meaningfully comparable.

    SGOV and TLT are both "bond ETFs" and they are not the same kind of object:
    a 1% rate move is worth ~0.1% to one and ~16% to the other.
    """
    d = float(years or 0.0)
    if d < 1.0:
        return "cash-like"
    if d < 4.0:
        return "short"
    if d < 8.0:
        return "intermediate"
    return "long"
