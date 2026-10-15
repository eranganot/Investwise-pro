# Wave 3 — different strategies: candidates and their gate, for owner review (2026-10-15)

Status: **proposed. Nothing below is measured until the owner approves the list and the gate** (owner, 2026-10-15:
"i want to review the new plans you are suggesting"). Pre-registered: once approved, a strategy is measured exactly as
written here, and a result is never "fixed" by changing the spec afterwards — a changed spec is a new candidate.
Part of [the sleeve plan](SLEEVE_OVERLAP_PLAN_2026-10-14.md), wave 3.

## Why these

Production correlations (PR 132 smoke, 2026-10-15): every strategy in today's catalogue moves with the owner's
Volatility-Targeted Leverage sleeve at 0.59-0.99. Even Factor Stack, which shares no ticker with it, reads 0.86. Under
D97 nothing in the catalogue can be added to this book. The candidates below are chosen to be a different ENGINE —
other assets, or US stocks only while they trend — and come from the research in the sleeve plan (Faber GTAA,
managed futures, gold, Antonacci GEM) plus the two "profit from falls" ideas the owner asked about.

## The diversifier gate (fixed before measuring)

The Beat-the-Market gate asks a strategy to beat the Nasdaq on its own; a diversifier is built not to. These six
tests replace it for this family. All recorded in STATUS, pass or fail:

| # | Test | Pass when |
|---|---|---|
| G1 | **Different** | daily-return correlation with QQQ <= 0.50 over the measured window (also shown against each held sleeve) |
| G2 | **Improves your book** | the book-effect rule (D93): added at its suggested size with the core making room, return per unit of risk +0.02 or more and worst drop no more than 2 points deeper (these two limits become settings in this wave) |
| G3 | **Earns its place** | after-tax return above T-bills (BIL) over the window, with a worst drop shallower than QQQ's on the same window |
| G4 | **Holds up** | the same as G3 over the longest history the funds allow (2000-2016 where they exist); a fund younger than ~10 years is marked **short history** and cannot pass G4 — see question Q2 |
| G5 | **Robust** | the parameter sweep stays within 5 points of return (same as Beat-the-Market test 4) |
| G6 | **Not a near-duplicate** | two candidates correlated above 0.90 -> only the one with the better G2 result ships |

Passing strategies join the catalogue (Explore). Failures are kept as specs and reported with their numbers, as in
PR 127-130. Measured on production prices (local prices are synthetic), on the engine every sleeve already uses.

## The candidates

Sizes are the suggested sleeve size used for G2. "Off" is what the rule holds when its condition fails. Every rule
re-checks daily unless a cadence is given; the 200-day test uses 3 confirming closes unless noted.

### Family A — other assets, held only while they trend

| Code | Name | Rule | Engine spec | Size | Sweep | History |
|---|---|---|---|---|---|---|
| **D1** | Global Tactical Allocation (Faber GTAA-5) | Five equal 20% slots: US stocks (SPY), non-US developed (EFA), 7-10y Treasuries (IEF), commodities (DBC), real estate (VNQ). Each slot holds its fund only while it is above its 200-day average; otherwise that slot sits in T-bills. Re-checked monthly. | `rank_select` universe [SPY, EFA, IEF, DBC, VNQ], score `ret`, `top_n` 5, `trend_veto_days` 200, equal weights, `cadence_days` 21, base {BIL: 1} | 20% | `trend_veto_days` 150/175/200/225/250 | DBC from 2006-02 |
| **D2** | Trend Risk Parity (all-weather, unleveraged) | US stocks (SPY), long Treasuries (TLT), gold (GLD), commodities (DBC), each sized inverse to its 60-day volatility, each held only while above its own 200-day average, its share in T-bills when not. Re-weighted monthly. | `risk_parity` legs {SPY: SPY, TLT: TLT, GLD: GLD, DBC: DBC}, `vol_days` 60, `trend_days` 200, `confirm_days` 1, `cadence_days` 21, cash BIL | 20% | `vol_days` 20/40/60/90/120 | DBC from 2006-02 |
| **D3** | Gold Trend | Gold (GLD) while above its 200-day average; T-bills otherwise. | `trend_filter` weights {GLD: 1}, base {BIL: 1}, gate GLD, `ma_days` 200, `confirm_days` 3 | 10% | `ma_days` 150/175/200/225/250 | GLD from 2004-11 |
| **D4** | Treasury Trend | 7-10y Treasuries (IEF) while above their 200-day average; T-bills otherwise. | `trend_filter` weights {IEF: 1}, base {BIL: 1}, gate IEF, `ma_days` 200, `confirm_days` 3 | 15% | `ma_days` 150/175/200/225/250 | IEF from 2002-07 |
| **D5** | Non-US Stocks Trend | Developed non-US stocks (EFA) while above their 200-day average; T-bills otherwise. | `trend_filter` weights {EFA: 1}, base {BIL: 1}, gate EFA, `ma_days` 200, `confirm_days` 3 | 15% | `ma_days` 150/175/200/225/250 | EFA from 2001-08 |
| **D6** | Global Equity Momentum (Antonacci GEM) | Monthly: hold whichever of US (SPY) or non-US (EFA) stocks rose more over 12 months, if that rise is positive; otherwise 7-10y Treasuries (IEF). | `rank_select` universe [SPY, EFA], score `ret`, `lookback_days` 252, `top_n` 1, `require_positive`, fallback [IEF], `cadence_days` 21 | 20% | `lookback_days` 126/189/252/315/378 | EFA from 2001-08 |

### Family B — managed futures (trend following across markets, held as a fund)

| Code | Name | Rule | Engine spec | Size | Sweep | History |
|---|---|---|---|---|---|---|
| **D7** | Managed Futures (KMLM) | Hold KMLM: a trend-following index across commodities, currencies and global bonds, no stocks. | `buy_hold` {KMLM: 1} | 15% | none (a fund, no parameter) — G5 not applicable | KMLM from 2020-12: **short history** |
| **D8** | Managed Futures (DBMF) | Hold DBMF: replicates the average managed-futures hedge fund (can hold stocks). | `buy_hold` {DBMF: 1} | 15% | none — G5 not applicable | DBMF from 2019-05: **short history** |

### Family C — profit from falls (long-only, through inverse funds) — owner asked

| Code | Name | Rule | Engine spec | Size | Sweep | History |
|---|---|---|---|---|---|---|
| **D9** | Nasdaq Trend, Short in Downtrends | Nasdaq-100 (QQQ) while above its 200-day average; the -1x Nasdaq fund (PSQ) otherwise. | `trend_filter` weights {QQQ: 1}, base {PSQ: 1}, gate QQQ, `ma_days` 200, `confirm_days` 3 | 15% | `ma_days` 150/175/200/225/250 | PSQ from 2006-06 |
| **D10** | S&P Trend, Short in Downtrends | S&P 500 (SPY) while above its 200-day average; the -1x S&P fund (SH) otherwise. | `trend_filter` weights {SPY: 1}, base {SH: 1}, gate SPY, `ma_days` 200, `confirm_days` 3 | 15% | `ma_days` 150/175/200/225/250 | SH from 2006-06 |

### Not proposed this round

- **Hot stocks re-run** (momentum / quality momentum / 52-week-high leaders on ~150 large US names). Needs the bigger
  universe built first (BACKLOG P13), and US large-cap stocks moved with your sleeve at 0.86 even as a factor fund — so
  on this book it would almost certainly be "the same bet". Suggest a separate round after this one.
- **Single-stock shorts**: parked (BACKLOG P15).
- **3x inverse funds (SQQQ)**: decay makes them a days-long instrument, not a sleeve.

## What to expect (honest, before measuring)

- Family A and B should pass G1 (gold, Treasuries and managed futures are expected near 0 against the Nasdaq, measured
  not assumed). D5 and D6 are stocks part of the time and may land around 0.5-0.7 — they may fail G1.
- Family C is a Nasdaq / S&P bet whenever the trend is up, so it will very likely fail G1, and on this book D9 is "the
  same bet" as Volatility-Targeted Leverage under D97 anyway (same market). Measured because you asked; expect it to be
  reported, not shipped.
- None of these is likely to raise the chance of a 29%/yr goal on its own. They are expected to cut the chance of the
  book halving. Both numbers go on every card (owner decision 4: shown both ways).

## Also in this wave (already approved, no review needed)

- The book-effect line says which test failed and by how much ("adds return, but makes the worst drop 9.1 points
  deeper; your limit is 2").
- Its two limits become settings: `book_effect_ratio_gain` (0.02) and `book_effect_drop_tolerance_pts` (2).

## Questions for the owner

- **Q1. The list.** Approve all ten, or drop some? (My suggestion: keep all; Family C is cheap to measure and answers
  your question with numbers.)
- **Q2. Short history.** KMLM and DBMF have 5-7 years. Either (a) they can never pass G4 and stay out, or (b) they may
  ship marked **short history** if they pass G1-G3 and G5-G6. Suggest (b), with the badge on the card.
- **Q3. The gate.** Approve G1-G6 as written (G1 at 0.50)?

Not financial advice.
