# Wave 3 — different strategies that beat the market: candidates and their gate (2026-10-15)

Status: **approved by the owner 2026-10-15** (list D1-D13, gate below, short-history badge). Pre-registered: each
strategy is measured exactly as written here, and a result is never "fixed" by changing the spec afterwards — a
changed spec is a new candidate. Part of [the sleeve plan](SLEEVE_OVERLAP_PLAN_2026-10-14.md), wave 3.

## Why these

Production correlations (PR 132 smoke, 2026-10-15): every strategy in today's catalogue moves with the owner's
Volatility-Targeted Leverage sleeve at 0.59-0.99; even Factor Stack, which shares no ticker with it, reads 0.86. Under
D97 nothing in the catalogue can be added to this book. The candidates below use a different engine — other assets,
or stocks only while they trend — from the research in the sleeve plan (Faber GTAA, managed futures, gold, Antonacci
GEM), the two "profit from falls" ideas the owner asked about, and leveraged versions with a real chance of beating
the market alone.

## The gate (fixed before measuring; owner-approved 2026-10-15)

**Owner: "the main goal is not only to diversify but to beat the market (diversification is just a plus)".** So the
first test is beating the market, in either of two ways, and "different" is a badge rather than a requirement (the
same-bet rule, D97, still decides whether a strategy may sit next to a sleeve you hold). Recorded in STATUS, pass/fail:

| # | Test | Pass when |
|---|---|---|
| B1 | **Beats the market** (required) | EITHER after-tax return above QQQ's over the measured window, OR, added at its suggested size with the core making room, it raises the whole book's after-tax return |
| G2 | **Improves your book** (required) | the book-effect rule (D93): return per unit of risk +0.02 or more and worst drop no more than 2 points deeper (both limits become settings in this wave) |
| G3 | **Earns its place** (required) | after-tax return above T-bills (BIL), with a worst drop shallower than QQQ's on the same window |
| G4 | **Holds up** (required, or badge) | the same as G3 over the longest history the funds allow; a fund younger than ~10 years ships marked **short history** if it passes everything else (owner) |
| G5 | **Robust** (required) | the parameter sweep stays within 5 points of return (Beat-the-Market test 4) |
| G6 | **Not a near-duplicate** (required) | two candidates correlated above 0.90 -> only the one with the better G2 result ships |
| G1 | **Different engine** (badge) | daily-return correlation with QQQ <= 0.50 -> marked "different engine"; also shown against each held sleeve |

**Risk level** is measured as for every strategy (PR 125), from the measured worst drop: Medium <= 36%, Medium-high
<= 45%, High <= 60%, Very high above. The tier in the tables is the EXPECTED one, from published results and the funds'
history; the card always shows the measured tier.

Passing strategies join the catalogue (Explore). Failures are kept as specs and reported with their numbers, as in
PR 127-130. Measured on production prices (local prices are synthetic), on the engine every sleeve already uses.
Leveraged funds are rebuilt before their listing date as PR 128 does (L x the daily index move, less fees and
borrowing), so G4 reaches 2000-2016 where the index exists.

## The candidates

Sizes are the suggested sleeve size used for B1/G2. "Otherwise" is what the rule holds when its condition fails. The
200-day test uses 3 confirming closes unless noted.

### Family A — other assets, held only while they trend

| Code | Name | Expected risk | Rule | Engine spec | Size | Sweep | History |
|---|---|---|---|---|---|---|---|
| **D1** | Global Tactical Allocation (Faber GTAA-5) | Medium (published worst drop ~10-15%) | Five equal 20% slots: US stocks (SPY), non-US developed (EFA), 7-10y Treasuries (IEF), commodities (DBC), real estate (VNQ). Each slot holds its fund only while it is above its 200-day average; otherwise that slot sits in T-bills. Re-checked monthly. | `rank_select` universe [SPY, EFA, IEF, DBC, VNQ], score `ret`, `top_n` 5, `trend_veto_days` 200, equal weights, `cadence_days` 21, base {BIL: 1} | 20% | `trend_veto_days` 150/175/200/225/250 | DBC from 2006-02 |
| **D2** | Trend Risk Parity (unleveraged) | Medium | US stocks (SPY), long Treasuries (TLT), gold (GLD), commodities (DBC), each sized inverse to its 60-day volatility, each held only while above its own 200-day average, its share in T-bills when not. Re-weighted monthly. | `risk_parity` legs {SPY: SPY, TLT: TLT, GLD: GLD, DBC: DBC}, `vol_days` 60, `trend_days` 200, `confirm_days` 1, `cadence_days` 21, cash BIL | 20% | `vol_days` 20/40/60/90/120 | DBC from 2006-02 |
| **D3** | Gold Trend | Medium (gold fell ~45% in 2011-2015 unfiltered; the trend exit cuts it) | Gold (GLD) while above its 200-day average; T-bills otherwise. | `trend_filter` weights {GLD: 1}, base {BIL: 1}, gate GLD, `ma_days` 200 | 10% | `ma_days` 150/175/200/225/250 | GLD from 2004-11 |
| **D4** | Treasury Trend | Medium (lowest on the list) | 7-10y Treasuries (IEF) while above their 200-day average; T-bills otherwise. | `trend_filter` weights {IEF: 1}, base {BIL: 1}, gate IEF, `ma_days` 200 | 15% | `ma_days` 150/175/200/225/250 | IEF from 2002-07 |
| **D5** | Non-US Stocks Trend | Medium to Medium-high | Developed non-US stocks (EFA) while above their 200-day average; T-bills otherwise. | `trend_filter` weights {EFA: 1}, base {BIL: 1}, gate EFA, `ma_days` 200 | 15% | `ma_days` 150/175/200/225/250 | EFA from 2001-08 |
| **D6** | Global Equity Momentum (Antonacci GEM) | Medium-high | Monthly: whichever of US (SPY) or non-US (EFA) stocks rose more over 12 months, if that rise is positive; otherwise 7-10y Treasuries (IEF). | `rank_select` universe [SPY, EFA], score `ret`, `lookback_days` 252, `top_n` 1, `require_positive`, fallback [IEF], `cadence_days` 21 | 20% | `lookback_days` 126/189/252/315/378 | EFA from 2001-08 |

### Family B — managed futures (trend following across markets, held as a fund)

| Code | Name | Expected risk | Rule | Engine spec | Size | Sweep | History |
|---|---|---|---|---|---|---|---|
| **D7** | Managed Futures (KMLM) | Medium | Hold KMLM: a trend-following index across commodities, currencies and global bonds, no stocks. | `buy_hold` {KMLM: 1} | 15% | none (a fund) — G5 not applicable | from 2020-12: **short history** |
| **D8** | Managed Futures (DBMF) | Medium | Hold DBMF: replicates the average managed-futures hedge fund (can hold stocks). | `buy_hold` {DBMF: 1} | 15% | none — G5 not applicable | from 2019-05: **short history** |

### Family C — profit from falls (long-only, through -1x funds) — owner asked

| Code | Name | Expected risk | Rule | Engine spec | Size | Sweep | History |
|---|---|---|---|---|---|---|---|
| **D9** | Nasdaq Trend, Short in Downtrends | High (choppy markets hit both legs) | Nasdaq-100 (QQQ) while above its 200-day average; the -1x Nasdaq fund (PSQ) otherwise. | `trend_filter` weights {QQQ: 1}, base {PSQ: 1}, gate QQQ, `ma_days` 200 | 15% | `ma_days` 150/175/200/225/250 | PSQ from 2006-06 |
| **D10** | S&P Trend, Short in Downtrends | High | S&P 500 (SPY) while above its 200-day average; the -1x S&P fund (SH) otherwise. | `trend_filter` weights {SPY: 1}, base {SH: 1}, gate SPY, `ma_days` 200 | 15% | `ma_days` 150/175/200/225/250 | SH from 2006-06 |

### Family D — leveraged diversifiers (owner: "yes, add them") — a real chance to beat the market alone

| Code | Name | Expected risk | Rule | Engine spec | Size | Sweep | History |
|---|---|---|---|---|---|---|---|
| **D11** | 2x Trend Risk Parity | **High to Very high** (2x of D2; worst drop expected 35-55%) | 2x US stocks (SSO), 2x long Treasuries (UBT), 2x gold (UGL), each sized inverse to its 60-day volatility, each held only while ITS index (SPY / TLT / GLD) is above its 200-day average, its share in T-bills when not. Re-weighted monthly. | `risk_parity` legs {SSO: SPY, UBT: TLT, UGL: GLD}, `vol_days` 60, `trend_days` 200, `confirm_days` 1, `cadence_days` 21, cash BIL | 15% | `vol_days` 20/40/60/90/120 | UBT from 2010-01 |
| **D12** | 2x Gold Trend | **High** (2x gold with a trend exit, expected 40-55%) | 2x gold (UGL) while gold (GLD) is above its 200-day average; T-bills otherwise. | `trend_filter` weights {UGL: 1}, base {BIL: 1}, gate GLD, `ma_days` 200 | 10% | `ma_days` 150/175/200/225/250 | UGL from 2008-12 |
| **D13** | 3x Treasury Trend | **Very high** (TMF fell ~75% in 2022 unfiltered; the trend exit should cut that sharply — measured, not assumed) | 3x long Treasuries (TMF) while 20y+ Treasuries (TLT) are above their 200-day average; T-bills otherwise. | `trend_filter` weights {TMF: 1}, base {BIL: 1}, gate TLT, `ma_days` 200 | 10% | `ma_days` 150/175/200/225/250 | TMF from 2009-04 |

### Not proposed this round

- **Hot stocks re-run** (momentum / quality momentum / 52-week-high leaders on ~150 large US names). Needs the bigger
  universe first (BACKLOG P13), and US large-cap stocks moved with the owner's sleeve at 0.86 even as a factor fund, so
  on this book it would almost certainly be "the same bet". Suggested as its own round.
- **Single-stock shorts**: parked (BACKLOG P15).
- **3x inverse funds (SQQQ)**: decay makes them a days-long instrument, not a sleeve.

## What to expect (honest, before measuring)

- Over 2016-2026 the Nasdaq returned ~21%/yr; GTAA's out-of-sample record is ~6%/yr. Families A-C will very likely not
  beat QQQ on their own; their route through B1 is the book level (raising the whole book's return). Family D is the
  route to beating it alone, at High / Very high risk.
- Family C is a Nasdaq / S&P bet whenever the trend is up; D9 is "the same bet" as Volatility-Targeted Leverage under
  D97 on this book whatever it measures.
- Every card shows both the goal chance and the chance of the book halving (owner decision 4).

## Also in this wave (already approved)

- The book-effect line says which test failed and by how much ("adds return, but makes the worst drop 9.1 points
  deeper; your limit is 2").
- Its two limits become settings: `book_effect_ratio_gain` (0.02) and `book_effect_drop_tolerance_pts` (2).

## Owner decisions (2026-10-15)

- List: D1-D10 as proposed plus the leveraged D11-D13 ("yes, add them, but I want to see the risk level per
  strategy" — every row carries its expected tier; the card shows the measured one).
- Beating the market comes first: B1, either on its own or for the book; "different" is a badge (G1).
- Short history: ships with a badge (G4).
- Gate: approved as written, with B1 added and G1 turned into a badge.

## How it ships

1. **PR 133** — the book-effect verdict says which test failed (and its two limits become settings); the 13
   candidates registered as specs; the gate evaluated by code; an endpoint measures them all and returns the table.
   Nothing joins the catalogue.
2. **Measured on production** — the owner runs one script (prices here are synthetic); the table goes into STATUS.
3. **PR 134a** — the ones that passed join the catalogue (Explore), with their measured risk tier and badges.

Not financial advice.
