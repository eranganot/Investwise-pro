# Round 2 — hot stocks on a bigger universe (option 2) and diversifiers funded from the leveraged sleeve (option 3)

Status: **approved by the owner 2026-10-15** (option 2 as written; option 3 at the suggested size AND at double size). Earlier: **nothing below is measured until the owner approves it** (owner, 2026-10-15:
"once you complete 134a I want you to run both option 2 and option 3"; standing rule: review new strategies before
measuring). Pre-registered once approved: measured as written, never edited after a result.
Context: wave 3's 13 candidates all failed (STATUS, PR 134a); [the sleeve plan](SLEEVE_OVERLAP_PLAN_2026-10-14.md).

## Option 2 — "hot stocks": stock-picking rules on ~125 large US stocks

**Why again.** PR 127 measured three stock-picking rules on 44 stocks; all failed "beat QQQ" by 4-9 points. The
universe was thin (BACKLOG P13). This round gives them ~125 of today's largest US stocks.

**Universe US125** (today's largest US stocks by sector — a survivorship-biased list, which is why gate test S2 judges
each rule against an equal-weight hold of the SAME universe; listings after 2016 join late, never cut the window):

- Tech / communication (38): AAPL MSFT NVDA GOOGL AMZN META AVGO TSLA ORCL CRM ADBE AMD INTC CSCO QCOM TXN IBM NOW
  INTU AMAT MU LRCX KLAC ADI PANW CRWD SNPS CDNS ANET MRVL PLTR NFLX TMUS CMCSA DIS UBER SHOP ABNB
- Financials (18): BRK-B JPM V MA BAC WFC GS MS C AXP SCHW BLK SPGI PGR CB MMC ICE CME
- Healthcare (20): LLY UNH JNJ ABBV MRK PFE TMO ABT DHR AMGN ISRG BSX SYK GILD VRTX REGN MDT BMY CVS ELV
- Consumer (17): WMT COST HD PG KO PEP MCD NKE SBUX LOW TJX BKNG CMG MDLZ PM MO CL
- Industrials (12): GE CAT HON RTX UNP UPS BA DE LMT ETN GEV ADP
- Energy, utilities, materials, real estate (10): XOM CVX COP NEE SO DUK LIN SHW PLD AMT

**Candidates.** All re-check monthly; when the market gate is off (SPY below its 200-day for 3 closes) the sleeve
holds **T-bills** (not QQQ as in PR 127: on this book a QQQ fall-back would make every one "the same bet" as your
Nasdaq sleeve).

| Code | Name | Expected risk | Rule | Engine spec | Size | Sweep |
|---|---|---|---|---|---|---|
| **S1** | Large-Cap Momentum 10 | High | The 10 stocks with the strongest 12-month return (skipping the last month), equal weight; keep a holding while it stays in the top 20. | `rank_select` universe US125, score `mom_12_1`, `top_n` 10, `buffer` 20, `cadence_days` 21, market gate SPY 200, base {BIL: 1} | 20% | `top_n` 6/8/10/12/15 |
| **S2** | Quality Momentum 5 | High to Very high | The 5 stocks with the best 12-month return per unit of volatility, equal weight; keep while in the top 10. | `rank_select` US125, score `risk_adj_mom`, `top_n` 5, `buffer` 10, `cadence_days` 21, market gate SPY 200, base {BIL: 1} | 15% | `top_n` 3/4/5/6/8 |
| **S3** | 52-Week-High Leaders | High | The 6 stocks closest to their 52-week high, equal weight; keep while in the top 12. | `rank_select` US125, score `high_52w`, `top_n` 6, `buffer` 12, `cadence_days` 21, market gate SPY 200, base {BIL: 1} | 15% | `top_n` 4/5/6/8/10 |

Score names are the engine's existing ones (PR 126), checked against `score_at`.

**Gate (the Beat-the-Market gate, PR 127, unchanged)** — S1 beats QQQ after tax over the window (the owner's first
goal); S2 positive selection edge vs an equal-weight hold of US125; S3 not overfitted across the 2022 split; S4 sweep
within 5 points; S5 not a near-duplicate (0.90). Recorded beside it: the book effect (D93) and the same-bet verdict
against your sleeves (D97). **Honest expectation:** large US stocks moved with your sleeve at 0.86 even as a factor fund,
so even a winner here will most likely be "the same bet" as Volatility-Targeted Leverage and could only REPLACE it.

**Cost of measuring.** ~125 tickers x 10 years from the price provider; if it rate-limits, the run retries and reports
which tickers were missing instead of measuring a smaller universe silently.

## Option 3 — the diversifiers, funded from your leveraged sleeve instead of the core

**The question is different from wave 3's**, so it is a new test, not a re-score: wave 3 asked "does adding X, paid
for by the 60/40 core, improve the book?"; this asks **"does moving part of the leveraged sleeve into X make the risky
part better?"**

**How it is measured.** The book as it is, against the same book with the candidate at its suggested size taken out of
**Volatility-Targeted Leverage** (40% -> 40% minus the size), the core and Semis untouched, over one window (the PR 134a
fix). Every wave-3 candidate D1-D13, plus any S-candidate that passes option 2.

**Pass (pre-registered):**
- F1 **still beats the market** — the whole book's after-tax return stays above QQQ's over the window; and
- F2 **makes the risky part better** — the book-effect rule at the limits in force (return per unit of risk +0.02 or
  more, worst drop no more than 2 points deeper; both settings).

Reported beside it: the change in the book's worst drop and return, so a "safer, a little less return" result is
visible even when it fails (owner decision 4: shown both ways). A pass here is the evidence for a wave-4 card
"move N% of your leveraged sleeve into X".

## Owner decisions (2026-10-15)

- Option 2: approved as written (US125, S1-S3, T-bill fall-back).
- Option 3: approved, measured at each candidate's suggested size **and** at double that size (e.g. 10% and 20% of the book out of Volatility-Targeted Leverage); F1 + F2 apply at each size.

## How it ships

1. **PR 134b** — US125 and S1-S3 registered; the book effect learns `fund_from` (which sleeve makes room); option 3
   evaluated by code; endpoints and `scripts/measure-round2.ps1`. Nothing joins the catalogue.
2. **Measured on production** by the owner's script; results into STATUS.
3. **PR 134c** — whatever passes joins the catalogue (Explore) with its measured risk tier and badges.

Not financial advice.
