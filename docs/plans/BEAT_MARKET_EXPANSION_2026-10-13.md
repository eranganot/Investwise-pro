# Beat the Market: strategy review and expansion (approved 2026-10-13)

The full plan, with the reasoning behind each strategy and the owner's decisions, is the Claude doc
"Beat the Market - Strategy Review & Expansion Plan"
(https://claude.ai/code/artifact/e2fb39d8-c23c-46fd-9a1c-68a43129308a). This file is the in-repo record a later session
builds from. **The settings below are pre-registered**: they were fixed before anything was measured. Changing one after
seeing a result makes it a new candidate and is reported as one (gate, section 3).

## 1. Decisions

| # | Decision |
| --- | --- |
| D1 | All 11 strategies below approved (H2 and H3 replace the rejected pullback basket and Tel Aviv momentum). |
| D2 | The revised acceptance gate (section 3), including test 3 judged against the strategy's own instruments. |
| D3 | A tier that ends with fewer than two new passing strategies is left short; replacements are designed and gated. |
| D4 | A name with missing screener data is excluded from selection, not scored neutral. |
| D5 | Swing: Dip-Buy and Swing: Channel Breakout are retired with a flag (DECISIONS D87), not deleted. |
| D6 | Keep the 44-name US universe for this round; revisiting it is BACKLOG P13. |
| D7 | Financials are excluded from the Magic Formula. |
| D8 | Tel Aviv shares stay in MH2: the 9 that pass the financials and debt filters. |

Replace or add: M2 replaces Dual Momentum Rotation; MH1 and MH2 replace Factor Stack. A replacement happens only if
the new strategy passes the gate. Everything else is added. The catalogue goes from 8 to 15.

## 2. The strategies (pre-registered settings)

Universe "US44" = the 44 US stocks in `app/services/universe.py`. A name joins a ranking from its first price, not the
backtest's (MNDY 2021, GLOB 2014, WIX 2013, ABBV 2013, META 2012). "Buffer k" = a held name is sold only below rank k.
"SPY gate" = the sleeve is in its base while SPY closes below its 200-day average for 3 sessions.

| Id | Designed tier | Universe | Score | Filter | Hold / weight | Rebalance | Base when off | Measured |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M1 Low-Volatility Quality | Medium | US44 | lowest 252-day realised vol | quality >= median (live only) | 12, inverse-vol | quarterly, buffer 24 | never off | direct + USMV check |
| M2 Accelerating Dual Momentum | Medium | SPY, SCZ | mean of 21/63/126-day returns | best score > 0, else TLT or BIL by the same score | 1 | monthly | TLT or BIL | direct |
| M3 Sector Rotation, Trend Veto | Medium | XLK XLV XLF XLE XLI XLY XLP XLU XLB XLRE | 126-day return | each sector above its own 200-day | 3, equal | monthly, buffer 5 | core (QQQ) | direct |
| MH1 Large-Cap Momentum 10 | Medium-high | US44 | 12-1 (252-day return skipping the latest 21) | SPY gate | 10, equal | monthly, buffer 20 | QQQ | direct + MTUM check |
| MH2 Magic Formula | Medium-high | US44 + 9 TASE | rank(earnings yield 1/PE) + rank(ROE) | no financials; debt-to-equity <= 200% (provider reports percent); complete data | 15, equal | yearly | n/a | proxy: QVAL |
| MH3 2x Nasdaq Trend | Medium-high | QLD | QQQ vs its 200-day, 3-session confirm | | 1 | on signal | QQQ | direct |
| H1 Quality Momentum 5 | High | US44 | 12-1 return / 252-day vol | quality >= median (live only); SPY gate | 5, equal | monthly, buffer 10 | QQQ | direct (no quality filter in the backtest) |
| H2 52-Week-High Leaders | High | US44 | price / max close of 252 days | SPY gate | 6, equal | monthly, buffer 12 | QQQ | direct |
| H3 Top-Down Sector Leaders | High | sectors as M3, then US44 by GICS sector | sector: 126-day return; stock: 12-1 | sector above its 200-day and with >= 2 universe stocks | top 2 sectors x top 2 stocks, 25% each | monthly; sell when its sector leaves the top 3 or it drops below rank 4 in-sector | QQQ | direct |
| VH1 Leveraged Risk Parity | Very high | UPRO, TMF | inverse 60-day vol | each leg only while SPY / TLT is above its 200-day; else that leg in BIL | 2 | monthly re-weight, daily gates | BIL per leg | direct + long history |
| VH2 Leveraged Sector Momentum | Very high | score SMH XLK XLF XLV XLE XBI XLRE QQQ SPY; hold SOXL TECL FAS CURE ERX LABU DRN TQQQ UPRO | mean of 21/63/126-day returns of the unleveraged index | winner's index above its 200-day; switch only on a 2-point lead | 1 | monthly | QQQ | direct + long history + walk-forward |

H3's ">= 2 universe stocks" was added on 2026-10-13 from the Phase 0 sector count, before any measurement: Materials and
Real Estate hold no US44 stock, Utilities one, and the five communication stocks have no sector fund in the list (XLC
starts in 2018).

Every multi-leg strategy uses `drift_band: 0.10` (PR 125): an unchanged target is re-trued only when the legs are 5
points off, as the live sleeve-drift trim does.

## 3. The acceptance gate

All five, recorded in STATUS pass or fail:
1. Beats its base after tax over the 10 years. Medium tier only: alternatively within 2%/yr of its base with a worst
   drop at least 25% shallower.
2. Stock strategies: positive selection edge against an equal-weight hold of the same universe.
3. Not overfitted, judged against buy-and-hold of the strategy's OWN instruments over the 2022 split (the engine's test
   compared against SPY; holding TQQQ alone fell 68.7%/yr to 15.6%/yr across it, SPY 18.4% to 12.3%).
4. Robust: the parameter sweep stays within 5 points.
5. Not a near-duplicate: daily returns correlated above 0.9 with another candidate -> only the better edge ships.

Recorded beside the five, not a way to pass (owner, 2026-10-13, D93): **book effect** -- added at its suggested size
with the core making room, does the owner's whole book gain >= 0.02 in return per unit of volatility while its worst
drop deepens by <= 2 points? `GET /api/v1/strategies/{id}/book-effect`; all candidates at
`/api/v1/strategies/candidates/book-effect`.

Hindsight-designed and many-knob strategies (VH1, VH2, H3) must also pass the long-history test (synthetic 3x series from
the underlying index, 2000-2016) and, with five or more free choices, the walk-forward check.

Tiers come from the measured worst drop: Medium <= 36%, Medium-high <= 45%, High <= 60%, Very high above (PR 125).

## 4. Phase 0 data check (2026-10-13)

- All 76 US tickers have daily adjusted history through 2026-10-12, most from 2000. Late listings above.
- Tel Aviv: 16 of 17 shares have 10 years (ORA from 2019-02). Before 2026 one session a week is absent (TASE traded
  Sunday-Thursday; the provider has no Sunday bars). One bad print: TSEM 2017-05-04 at 1,066 against ~7,700 either side.
  The live quote is current to the close; the daily history bar for shares lags a day. Fundamentals: P/E, ROE and margin
  for all 17; debt-to-equity for 11, in percent. Only MH2 (yearly, fundamentals) uses Tel Aviv, so none of this bites it.
- The app's provider caps history at 10 years because `range=max` returns monthly bars. A `period1/period2` request
  returns true daily bars to 2000 (SPY 6,734 sessions): PR 4 adds that path for the long-history test.
- Bad-print filter: drop a one-day move of 2x or more that reverses the next day. TECL's ~30% March 2020 swings are real
  and must survive it.

## 5. Build order

| PR | Scope |
| --- | --- |
| 125 | Catalogue clean-up: retire flag (D87), measured tiers (D88), `drift_band` (D89). |
| 126 | Engine: `rank_select` (late listings, scores, buffer, gates, two-stage, weights), selection edge, survivorship label, proxy measurement, gate test 3 against own instruments, bad-print filter. |
| 127 | Measure M1, M2, M3, MH1, MH3, H1, H2, H3 against the gate; passing ones enter the catalogue Explore-only. |
| 128 | `risk_parity`, rank-on-index map, long-history test; measure VH1, VH2 (with walk-forward). |
| 129 | Book effect for every candidate (D93); MH2 live picks dropped: MH2 failed the gate. |
| 130 | Round 2 (section 7): R6 and R7 join, MH3 retired (D94). |
| later | Only once a selection strategy passes: live rotation cards (labelled swaps, CGT shown), "How it picks", selection-edge and proxy badges, overlap note, Explore grouping. Passing strategies become fundable. |

## 6. Results (measured 2026-10-13, engine a7, production data window 2016-06 to 2026-10, after 25% CGT)

Test 1 compares with the strategy's base; M1, M2 and MH2 have none, so they are compared with QQQ, the catalogue's own
baseline (they also lose to SPY: M1 by 5.5, M2 by 10.6, MH2 by 3.0 points a year; each comparison is over the strategy's own window). Selection edge = return minus an
equal-weight hold of the same candidates.

| Strategy | /yr | Worst drop | vs base | Edge | Test 3 (own) | Sweep spread | 2000-2016 | Gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M1 Low-Volatility Quality | 9.6% | -29.5% | -10.9 (QQQ) | -6.3 | holds up | 1.9 | n/a | fails 1, 2 |
| M2 Accelerating Dual Momentum | 4.8% | -35.7% | -16.7 (QQQ) | -7.0 | holds up | 4.4 | n/a | fails 1, 2 |
| M3 Sector Rotation | 11.3% | -28.8% | -9.9 | -0.5 | weaker | 1.5 | n/a | fails 1, 2 |
| MH1 Large-Cap Momentum 10 | 17.4% | -34.6% | -3.8 | +0.7 | holds up | 2.7 | n/a | fails 1 |
| MH2 Magic Formula (QVAL) | 12.3% | -51.5% | -8.9 (QQQ) | n/a | n/a | n/a | n/a | fails 1 |
| **MH3 2x Nasdaq Trend** | **27.2%** | -52.7% | **+5.9** | n/a | holds up | 0.8 | 2.3% vs 2.0%, -84% | **passes; measures High** |
| H1 Quality Momentum 5 | 16.0% | -40.6% | -5.3 | -0.8 | holds up | 2.7 | n/a | fails 1, 2 |
| H2 52-Week-High Leaders | 12.5% | -27.8% | -8.8 | -4.3 | holds up | 2.9 | n/a | fails 1, 2 |
| H3 Top-Down Sector Leaders | 19.7% | -31.9% | -1.5 | +2.3 | holds up | 3.7 | n/a | fails 1 |
| VH1 Leveraged Risk Parity | 4.1% | -48.4% | -7.1 (own funds) | n/a | holds up | 1.1 | 8.9% vs own 11.1% | fails 1, long history |
| VH2 Leveraged Sector Momentum | 8.4% | -87.3% | -12.8 | -17.0 | holds up | 22.4 | -7.7% vs 2.0%, -93% | fails 1, 2, 4, long history |

Cross-checks before believing the engine: MH1 against the MTUM fund (16.2%/yr), M1 against USMV (10.1%/yr), and an
independent day-by-day replay of M2's targets (5.55%/yr gross against the engine's 4.54% net).

Consequences under the decisions: MH3 joins the catalogue (PR 127). M2 did not pass, so Dual Momentum Rotation stays;
MH1 and MH2 did not pass, so Factor Stack stays. Per D3 the Medium, Medium-high and High tiers get no new strategy from
this round, the Very-high tier none either, and replacements are to be designed. PR 129 (MH2 live picks) and PR 130
(rotation cards) have nothing to make live until a selection strategy passes.

## 7. Round 2 (approved 2026-10-13 14:40, pre-registered before any measurement)

The owner approved the doc's section 11. Designed after seeing the 2016-2026 results, so every one is a hindsight
design and must also pass the 2000-2016 long-history test. Settings below are fixed; a change makes a new candidate.

| Id | Code | Designed tier | Spec | Sweep |
| --- | --- | --- | --- | --- |
| btm_qqq_trend_cash | R1 | Medium | `trend_filter` QQQ over its 200-day, 3-session confirm; holds QQQ, base BIL | ma_days 150-250 |
| btm_qqq_vol_managed | R2 | Medium | `vol_managed` on QQQ's 20-day vol, target 18%, max 1.0x, band 0.15, legs BIL/QQQ/QLD/TQQQ | target 14-22 |
| btm_qqq_vol_managed_15 | R3 | Medium-high | as R2, target 25%, max 1.5x | target 20-30 |
| btm_qld_trend_cash | R4 | Medium-high | as R1 holding QLD, base BIL | ma_days 150-250 |
| btm_qqq_vol_managed_2x | R5 | High | as R2, target 35%, max 2.0x | target 30-40 |
| btm_tqqq_trend_cash | R6 | Very high | as R1 holding TQQQ, base BIL | ma_days 150-250 |
| btm_tqqq_trend_vol_brake | R7 | Very high | `vol_target` TQQQ on TQQQ's 20-day vol, target 45%, max 1.0, rest QQQ; `trend_gate` QQQ 200-day (3-session) else BIL | target 35-55 |

All carry `long_history` and `drift_band: 0.10` (R1, R4, R6 hold one fund at a time, so the band does not bind).

Gate, as section 3, with three round-2 specifics fixed now: test 1 compares with holding QQQ (all seven are Nasdaq
rules; a trend rule's own `base` is cash here, which would flatter it), and the Medium alternative applies when the
MEASURED tier is Medium; test 2 does not apply (no selection); test 5 runs across the seven and MH3. The book effect
(D93) is measured on the owner's book and recorded.

### Round 2 results (measured 2026-10-13, engine a7, production data window; QQQ 21.2%/yr, -35.1%; 2000-2016 QQQ 2.0%/yr)

| Code | /yr | Worst drop | vs QQQ | Tier | Test 3 | Sweep | 2000-2016 /yr, drop | Book effect (gain, drop) | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R1 | 13.8% | -25.3% | -7.5 | Medium | holds up | 0.8 | 3.7%, -31% | -0.012, -0.8 | fails test 1 |
| R2 | 14.9% | -27.0% | -6.5 | Medium | holds up | 2.2 | 5.7%, -50% | -0.014, +1.6 | fails test 1 |
| R3 | 19.6% | -34.3% | -1.8 | Medium | holds up | 3.1 | 6.2%, -63% | -0.011, +2.7 | fails test 1 (not 25% shallower) |
| R4 | 24.2% | -43.5% | +3.0 | Medium-high | holds up | 1.8 | 4.5%, -52% | -0.004, +1.0 | passes; loses test 5 to R7 |
| R5 | 24.6% | -46.9% | +3.2 | High | holds up | 2.7 | 6.8%, -77% | -0.011, +2.4 | passes; loses test 5 to R6 |
| **R6** | **33.5%** | -56.6% | **+12.3** | High | holds up | 3.3 | 4.3%, -66% | -0.004, +1.5 | **ships** |
| **R7** | **29.5%** | -43.2% | **+8.2** | Medium-high | holds up | 2.8 | 4.8%, -62% | -0.004, +1.4 | **ships** |
| MH3 | 27.2% | -52.7% | +5.9 | High | holds up | 0.8 | 2.3%, -84% | -0.013, +4.4 | retired: loses test 5 to R6 (D94) |

Daily return correlations above 0.9 joined every pair of Nasdaq rules (R4-R6 0.999, R2-R5 0.990, R6-MH3 0.904), hence
D94. No round-2 rule improves the owner's book by the D93 rule; R4, R6 and R7 come closest (-0.004), adding 2.4-3.3%/yr
for 1.0-1.5 points of drawdown.
