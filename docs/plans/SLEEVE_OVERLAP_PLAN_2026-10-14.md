# Sleeves that differ, one goal chance, and cards that raise it — plan v2 (2026-10-15)

Status: **proposed, awaiting owner approval.** Wave by wave; stop and report after each.
Base: `origin/main` @ `c5b8e43` (PR 130). Replaces v1 of 2026-10-14 (same file).

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

## The plan

### Wave 1 — PR 131: true numbers (asks 1, 7) — D95, D96
- Shared holdings split between the sleeves that hold them, pro-rata to target weight (v1 wave 1, unchanged): rows
  add up to the 59.2%, `core_realign` overshoot becomes real, health and measured return corrected.
- **One goal chance, labelled.** When the verdict is not current, screens show the last verdict's chance with
  "as of <time> — <reason>, re-solving" instead of the rough projection, and Today queues a re-solve (the existing
  `POST /plan/frontier/refresh` path, rate-limited). The rough projection stays only on the projection chart, labelled
  "no rules, rough". `book_expectation` returns *why* it stood down (`stale_reason`), so the screen can say it.
- Mix panel chip: "N pts to fund · M pts over".
- Tests: attribution invariants; each stand-down reason yields the labelled last verdict, never the rough figure;
  health and Plan quote the same number. Smoke: Today's on-track == Plan's goal chance.

### Wave 2 — PR 132: sleeves must differ (ask 4) — D97
- **Exposure map** in the catalogue: every ticker gets an underlying market (Nasdaq-100: QQQ/QLD/TQQQ; semis:
  SMH/SOXL; US large: SPY/UPRO; US small value: AVUV; factors: MTUM/QUAL; T-bills: BIL; treasuries: IEF/TLT/TMF;
  gold: GLD/IAU; commodities: DBC; managed futures: KMLM/DBMF; non-US: VXUS/EFA/EEM).
- **Two sleeves are "the same bet"** when either test trips:
  1. shared underlying market >= `sleeve_overlap_alert_pct` (default **40**) of the smaller sleeve, counting both its
     risk-on and its fall-back legs (TQQQ and QQQ are both Nasdaq-100);
  2. daily-return correlation over the ten-year window >= `sleeve_correlation_limit` (default **0.85**, proposed).
- **MUST, enforced:** adding or resizing a sleeve that is the same bet as one you hold is refused, with the reason
  and a "replace X instead" option. Sleeves you already hold are not touched automatically; they get the HIGH card
  in wave 4.
- The Explore catalogue marks each strategy "same bet as your <sleeve>" and filters it out of "add" suggestions.
- Settings for both limits; `test_every_decision_is_a_setting` extended.

### Wave 3 — PR 133: diversifiers, researched and measured (ask 6, plus v1 ask 2) — D98
- Book-effect verdict names the failed test and its margin; `book_effect_ratio_gain` and
  `book_effect_drop_tolerance_pts` become settings (v1 wave 2).
- **A second gate for diversifiers**, fixed before measuring: (1) correlation to QQQ <= 0.5 over ten years;
  (2) improves this book by the book-effect rule; (3) holds up 2000-2016 or its longest history (proxy where short);
  (4) stable across its parameter sweep. Beat-the-Market candidates keep their own gate.
- Pre-registered candidates: GTAA-5 (Faber), managed futures (KMLM, proxy for history), gold trend (GLD over its
  200-day, else BIL), treasury trend (IEF/TLT over 200-day, else BIL), non-US equity trend (VXUS/EFA), GEM.
  Passing ones join the catalogue; failures are kept as specs and reported, as PR 127-130 did.

### Wave 4 — PR 134: sleeve cards — add, remove, replace, resize (asks 3, 5) — D99
One ranking, all measured by the goal model (chance with rules, chance the book halves, before and after):
- **Same bet (HIGH):** "Volatility-Targeted Leverage and Trend-Filtered Leveraged Nasdaq are one 45% Nasdaq bet".
  Recommends which to keep (the one the book misses most) and the best passing different strategy to replace the
  other. Accept goes through the Plan-tab change and `propose_funded_buy`; nothing in one tap that starts a 3x
  position (existing rule).
- **Replace / add:** a passing strategy that is different from every held sleeve and improves the goal chance or
  cuts the chance of halving without costing chance beyond the book-effect limits.
- **Remove:** a sleeve whose removal raises the chance (or cuts halving) by a material amount.
- **Resize:** the existing frontier resize, plus v1's overweight card with Trim / Raise the size and its measured
  recommendation (one card per position).
- Card claims (`tests/test_card_claims.py`): apply each, re-measure — the claimed chance and mix must reappear.

### Wave 5 — PR 135: "how to raise your chance" (ask 8) — D100
- One card when the goal is off track, listing every lever with its measured effect, best first: sleeve changes from
  wave 4, core change, rules (existing), a later deadline (+N years -> X%), a lower target at your confidence
  (₪Y at Z%), and a deposit — once the goal definition decision below is taken. Each lever opens its own card or
  setting. Numbers only from the solver; nothing invented. Ends "Not financial advice."
- When the solver says `OUT_OF_REACH`, the card says so plainly: no allocation the app can measure gets this goal to
  your confidence; the levers that do are time, amount and deposits.

### Every wave
Local SQLite + local Postgres + ruff on `app` and `tests` -> PR branch, CI green -> merge -> smoke with `-Sha` ->
STATUS (with result), BACKLOG, DECISIONS, doc pages. Stop and report.

## Decisions needed from the owner
- Q1. "Must differ" for sleeves already held: card only (proposed), or also block their resizes until resolved?
- Q2. Correlation limit 0.85 (proposed). At 0.85, SOXL beside a Nasdaq sleeve is likely "the same bet".
- Q3. BACKLOG P1, the goal definition: should deposits count toward the goal (fixed amount) or raise it ("30% on
  every shekel")? Without an answer the deposit lever stays out of wave 5.
- Q4. Diversifiers lower the expected return; with a 29%/yr goal they will usually lower the goal chance while cutting
  the chance of halving. Show them as "safer, lower chance" choices — or only when they also raise the chance?

## Assumptions (v1, still standing)
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

Not financial advice.
