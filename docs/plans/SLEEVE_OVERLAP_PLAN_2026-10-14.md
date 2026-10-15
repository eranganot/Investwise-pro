# Sleeves that differ, one goal chance, and cards that raise it — plan v3 (2026-10-15)

Status: **proposed, awaiting owner approval.** Wave by wave; stop and report after each.
Base: `origin/main` @ `c5b8e43` (PR 130). Replaces v1 (2026-10-14) and v2 (same file).

## Owner asks

1. (v1) Fix the double count in "What you hold vs your plan". Approved.
2. (v1) Book-effect verdict says which test failed; its limits become settings.
3. (v1) Overweight sleeves get a card: trim or raise the size, with a measured recommendation.
4. (v2) **Every sleeve the owner chooses MUST be different**: not built on the same stocks or the same underlying
   market as another sleeve, so the book is diversified.
5. (v2) **Recommendation cards about sleeves**: add, remove, replace, and resize.
6. (v2) Research popular strategies and check that they make sense before they enter the catalogue.
7. (v2) **The goal chance is not the same on every screen** (Today 43.7% / 44%, Plan 20% with rules, 28.7% without).
8. (v2) **Cards that raise the chance of reaching the goal**, not only "remove rules".
9. (v3) **Keep the goal fixed** (no "lower the target / later deadline" levers) and optimise the book for it.
10. (v3) More, and more different, strategies: trend / regime-change ones and "hot stocks". Why no shorts?

## What the investigation found

### A. The double count (asks 1, 3)
`sleeve_occupancy` (`sleeve_service.py:294`) credits each sleeve with the whole holding of every ticker it targets.
Volatility-Targeted Leverage and Trend-Filtered Leveraged Nasdaq both hold TQQQ, so 38.4% is counted twice: the rows
add to 97.6% while the sleeves hold 59.2% against a 60% target. Wrong as a result: the mix panel; `core_realign`'s
"sleeve overshoot" (~39 pts of NAV that do not exist, which the core may sell once it is underfunded); the health
sleeve score; and the measured expected return (leveraged sleeves weighted ~84% instead of ~38%).
An overweight card already exists per ticker against the summed target (`recommendations.py:2557`), trim only.

### B. Why the goal chance differs (ask 7)
Every screen reads the goal verdict through ONE function, `frontier_service.book_expectation`, live and uncached.
It gives the verdict's chance only while `_goal_verdict_current` holds: under `MAX_AGE_H` old, same sleeve sizes,
same objective and dials, **same rules fingerprint**, no stale measured views. When any of these fails it returns
nothing, and Today ("On track", "Where this could end up") **silently falls back to the rough projection**: one
lognormal, no rules, at the measured blend's expected return — which is inflated by A's double count. That is the
43.7% / 44%. The 20% (with rules) and 28.7% (without) are the goal model. Two models, one label.
Which check failed on 2026-10-14 needs one production read (`probe.ps1`); the classifier blocked it from here.
Whatever it was, the defect is the same: a screen swaps models without saying so, on an inflated input.

### C. Why nothing raises the chance (ask 8)
The goal needs **29%/yr**; the goal model expects **18%/yr** for this book. The solver already computes levers —
extra monthly deposit, extra years, the amount reachable at your confidence — and has an `OUT_OF_REACH` outcome
("no trade helps materially; the levers do"). They surface only as text inside the goal card, not as cards, and
none is ranked against a sleeve or core change. The goal is "30% on every shekel": a deposit raises the target too
(BACKLOG P1, still an open owner decision), so deposits cannot help it as it is defined.

### D. The catalogue cannot satisfy "every sleeve different" (asks 4, 6)
Of 9 active strategies, **6 are the Nasdaq-100** (four on TQQQ, plus QQQ hold, plus dual momentum on QQQ); Semis
(SOXL) is a different ticker but moves closely with the Nasdaq; only Factor Stack (MTUM/QUAL/AVUV) is different.
Every earlier candidate was judged by the Beat-the-Market gate (test 1: beat its base / QQQ). A real diversifier
(trend-following futures, gold, bonds, non-US stocks) is built to zig when the Nasdaq zags and will almost never
pass test 1 — so the gate, not the research, is what has kept the catalogue one market deep.

### Research (ask 6) — what is popular and whether it makes sense
- **Global Tactical Asset Allocation (Faber):** five asset classes, each held only above its 10-month average, else
  T-bills. Reported 11.7%/yr, -9.5% worst drop (1972-2005); 6.05%/yr, -11.7% out of sample (2006-2025, vendor
  write-up). Makes sense as a diversifier; low return.
- **Managed futures (KMLM, DBMF):** trend following across commodities, currencies and bonds; among the top ETFs in
  2022 when stocks and bonds fell together. KMLM has no equity exposure; DBMF can hold equities. Correlation to the
  Nasdaq is asserted low, not shown in what I found — must be measured. Short histories (2019-20); needs a proxy.
- **Gold:** World Gold Council finds gold's correlation to stocks turns more negative in sell-offs (rose in 2022 and
  2025); Morningstar found gold funds' diversification weakening in 2025. Mixed — measure, do not assume.
- **Global Equity Momentum (Antonacci):** US vs non-US stocks by 12-month return, bonds when neither beats cash.
  Reviewed as robust in and out of sample (ReSolve, a party with an interest). Overlaps US equity when risk-on.
None of these is likely to raise the chance of a 29%/yr goal. They lower the chance of halving. That trade is the
owner's to make, and every card must show both numbers.

### Research, v3 additions — stock picks and shorting
- **The stock-pick rules already exist and were measured** (PR 126-127, `rank_select`, 44 US large caps): Large-Cap
  Momentum 10 17.4%/yr, Quality Momentum 5 16.0%, 52-Week-High Leaders 12.5% — all failed gate test 1 by 4-9 points
  against QQQ over a decade the Nasdaq dominated. The universe is thin (BACKLOG P13) and built from today's winners
  (survivorship). US large-cap momentum also moves closely with the Nasdaq, so under wave 2 it would be "the same bet"
  as a Nasdaq sleeve.
- **Shorting single stocks:** the app cannot hold or measure a short today — the book, funding and backtest engine
  are long-only end to end. The evidence is weak for doing it as a retail investor: across 162 anomalies, long-short
  profits averaged ~0.15%/month before borrow fees and about zero after (Muravyev, Pearson & Pollet 2022, as
  summarised by Wealth Management); the momentum short leg's return sits mostly in hard-to-borrow stocks; retail
  shorting signals look profitable only before fees and mainly in small caps (Kelley & Tetlock). Losses are unbounded
  and margin calls force exits at the worst time.
- **Inverse ETFs** fit the long-only book (you buy them): SH (-1x S&P), PSQ (-1x Nasdaq). 3x inverse (SQQQ) decays —
  reportedly below -99% since 2010 — and is only usable for days. A -1x fund held only while the trend is down is a
  measurable way to profit from falls.

## The plan

### Wave 1 — PR 131: true numbers (asks 1, 7) — D95, D96
- Shared holdings split between the sleeves that hold them, pro-rata to target weight: rows add up to the 59.2%,
  `core_realign` overshoot becomes real, health and measured return corrected.
- **One goal chance everywhere: the goal model, with your rules (today 20%), and "without them" beside it (28.7%).**
  The rough no-rules projection (44%) stops being called a goal chance anywhere. When the verdict is not current,
  screens show the last verdict labelled "as of <time> — <reason>, re-solving", and Today queues a re-solve
  (`POST /plan/frontier/refresh` path, rate-limited). `book_expectation` returns why it stood down.
- Mix panel chip: "N pts to fund · M pts over".
- Tests: attribution invariants; every stand-down reason yields the labelled last verdict, never the rough figure;
  health, "Where this could end up" and Plan quote the same number. Smoke: they match on production.

### Wave 2 — PR 132: sleeves must differ (ask 4) — D97
- **Exposure map**: every ticker gets an underlying market (Nasdaq-100: QQQ/QLD/TQQQ/PSQ; semis: SMH/SOXL; US large:
  SPY/UPRO/SH; US small value: AVUV; factors: MTUM/QUAL; T-bills: BIL; treasuries: IEF/TLT/TMF; gold: GLD/IAU;
  commodities: DBC; managed futures: KMLM/DBMF; non-US: VXUS/EFA/EEM).
- **"The same bet"** when either trips: (1) shared underlying market >= `sleeve_overlap_alert_pct` (default **40**)
  of the smaller sleeve, counting risk-on and fall-back legs; (2) correlation of the two STRATEGIES' daily returns
  over ten years >= `sleeve_correlation_limit` (default **0.80**, see below).
- **First the measured table:** the PR measures the correlation of every catalogue pair and puts it in the STATUS
  entry; if 0.80 does something unexpected on real numbers, I stop and show you before shipping the default.
- Adding or resizing a sleeve that is the same bet as one held is refused, with "replace X instead". Held sleeves:
  card only (owner decision 1), and the card is actionable (wave 4).
- Explore marks "same bet as your <sleeve>" and leaves those out of add suggestions. Both limits are settings.

### Wave 3 — PR 133: more, and different, strategies (asks 6, 10, v1 ask 2) — D98
- Book-effect verdict names the failed test and its margin; its two limits become settings (v1 wave 2).
- **Diversifier gate**, fixed before measuring: correlation to QQQ <= 0.5; improves this book by the book-effect rule;
  holds up 2000-2016 or its longest history (proxy where short); stable across its sweep.
- Pre-registered candidates, in three families:
  - *Diversifiers:* GTAA-5 (Faber), managed futures (KMLM, proxied), gold trend (GLD over 200-day else BIL), treasury
    trend (IEF/TLT), non-US equity trend (VXUS/EFA), GEM.
  - *Profit from falls (long-only):* Nasdaq trend with an inverse leg (QQQ above its 200-day, PSQ below, BIL in the
    3-session confirmation gap); the same on the S&P with SH.
  - *Hot stocks, re-run fairly:* the universe grows to the ~100 largest S&P names plus the Nasdaq-100 (P13), and
    momentum 10 / quality momentum / 52-week-high leaders are re-measured on it under their original gate. A rule
    that passes shows its current picks on a card ("this month's picks: …") — picks only ever come from a gated rule,
    with its measured record beside them, never from a screen or a guess.
- Passing ones join the catalogue; failures are kept as specs and reported.
- **Not in this plan: shorting single stocks.** It needs a short book (negative quantities, margin, borrow fees,
  forced exits) through every layer, and the evidence above says the edge mostly disappears after fees. Logged in
  BACKLOG as a separate project if you want it later.

### Wave 4 — PR 134: actionable sleeve cards — same bet, add, remove, replace, resize (asks 3, 5) — D99
All measured by the goal model (chance with rules, chance the book halves, before -> after):
- **Same bet (HIGH, actionable):** "Volatility-Targeted Leverage and Trend-Filtered Leveraged Nasdaq are one 45%
  Nasdaq bet." Recommends which to keep (the one the book misses most) and the replacement (the best passing
  strategy that is the same bet as none of your sleeves). **Accept = replace**: removes one, adds the other at the same
  size, funds it by selling the removed sleeve's holdings through `propose_funded_buy`, with every leg and its tax
  shown before confirm. Starting a 3x position still routes through "Fund this sleeve" (existing rule).
- **Add / replace:** a passing different strategy that raises the goal chance, or ("safer, lower chance" — decision
  4, both) cuts the chance of halving; the card says which, with both numbers.
- **Remove:** a sleeve whose removal raises the chance or cuts halving materially.
- **Resize:** the frontier resize, plus the overweight card with Trim / Raise the size and its measured
  recommendation (one card per position).
- Card claims: apply each, re-measure — the claimed chance and mix must reappear.

### Wave 5 — PR 135: the best book for your goal, goal unchanged (asks 8, 9) — D100
- **The optimiser:** `sleeve_count`'s greedy sleeve-set search (it already adds sleeves while each one helps and
  reports why it stopped) gets the goal as its objective: maximise the chance of reaching the goal with your rules,
  over which sleeves (catalogue, never two that are the same bet), their sizes, the core, and the protection setup —
  subject to the chance of halving staying under your limit (`halve_limit_pct`, already a setting).
- **One card, "the best book we can measure for your goal":** today's chance -> the best found, the chance of halving
  before -> after, and the moves that get there, each as its own wave-4 card, biggest gain first.
- No target or deadline levers (owner: the goal stays). Deposits stay out until the goal-definition decision.
- **If the best is still low, it says so with the number**: "the best measured book reaches X% with a Y% chance of
  halving; anything higher needs more risk than your halving limit allows." Raising that limit is shown as the one
  remaining lever, with its measured price.
- Every number from the solver; ends "Not financial advice."

### Every wave
Local SQLite + local Postgres + ruff on `app` and `tests` -> PR branch, CI green -> merge -> smoke with `-Sha` ->
STATUS (with result), BACKLOG, DECISIONS, doc pages. Stop and report.

## Owner decisions (2026-10-15)
1. Held sleeves that are the same bet: **card only, and actionable** (wave 4 Accept = replace).
2. Correlation limit: asked for a recommendation — **0.80 proposed**, confirmed against the measured table in wave 2.
3. Goal definition (deposits): **skipped for now**; deposit lever out of wave 5.
4. Diversifiers: **both** — shown when they raise the chance, and as "safer, lower chance" when they cut halving.
5. Goal stays fixed: no target/deadline levers.

## Why 0.80
Typical long-run daily correlations (to be measured in wave 2, not assumed): US large-cap equity funds with each other
~0.9; semis with the Nasdaq ~0.85-0.9; non-US developed with US ~0.8; US small value with the Nasdaq ~0.7; gold, bonds
and managed futures near 0. At 0.85 most US large-cap variants still pass beside a Nasdaq sleeve — not "different".
At 0.70 even non-US stocks and small value fail, leaving one equity sleeve plus diversifiers — too strict to build a
book that can chase a 29%/yr goal. 0.80 separates "another flavour of US growth" from "a different engine".
Trend-filtered strategies spend time in cash, so their measured correlations will be lower than their tickers'.

## Assumptions
- A1. Shared holdings split pro-rata to target weight. A2. "Raise the size" raises every sharing sleeve pro-rata.

## Sources (research)
- Faber GTAA: https://concretumgroup.com/global-tactical-asset-allocation/ ;
  https://www.quantconnect.com/forum/discussion/8285
- Managed futures: https://etfdb.com/managed-futures-content-hub/why-managed-futures-are-crisis-alpha-creators/ ;
  https://etfdb.com/managed-futures-channel/2022s-top-3-managed-futures-etfs/
- Gold: https://www.gold.org/goldhub/research/relevance-of-gold-as-a-strategic-asset/key-attributes-diversification ;
  https://global.morningstar.com/en-nd/markets/gold-is-starting-behave-like-equities-is-it-still-useful-hedge
- GEM: https://www.advisorperspectives.com/articles/2019/05/06/global-equity-momentum-a-craftsmans-perspective ;
  https://alphaarchitect.com/book-review-dual-momentum-investing/
- Short selling: https://www.wealthmanagement.com/equities/do-short-sale-costs-explain-anomaly-returns- ;
  https://business.columbia.edu/sites/default/files-efs/pubfiles/6123/Kelley_Tetlock_Retail_Short_Selling.pdf ;
  https://www.cxoadvisory.com/short-selling/exploitability-of-monthly-short-interest-for-individual-stocks/
- Inverse ETFs: https://247wallst.com/investing/2026/06/19/sqqqs-silent-killer-how-daily-resets-have-erased-99-97-since-launch/ ;
  https://myetfjourney.com/blog/inverse-etfs-explained

Not financial advice.
