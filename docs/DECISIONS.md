# Decision log

> Every significant product and technical decision, newest knowledge consolidated on 2026-10-07.
> Format: **ID · date / PR · decision.** *Why.* Consequence. → source.
> Sources under `docs/archive/` keep their original file names. "S" = `STATUS.md` (PR 88+) or
> `docs/archive/status/STATUS-HISTORY-to-PR87.md` (earlier).
>
> **To add a decision:** append the next ID in the right section, keep it to three or four lines, and cite the PR.
> If it reverses an earlier decision, mark the old one **Superseded by Dnn** instead of deleting it.

---

## Architecture and stack

- **D01 · 2026-06-07 · Python 3.12, FastAPI async, SQLAlchemy 2 async, Postgres (asyncpg); SQLite for zero-setup tests.**
  *The owner's choice at plan time.* The two backends are the source of the recurring "SQLite green, Postgres red" bugs (D27). → `archive/plans/EXECUTION_PLAN.md`
- **D02 · 2026-06-07 · A type-safe lifecycle state machine (frozen Pydantic stages, each embedding its predecessor).**
  *A stage cannot be skipped.* Today it is live only inside the war room ([DOMAIN.md §6](DOMAIN.md#6-engines-agents-and-the-war-room)). → `EXECUTION_PLAN.md` §4
- **D03 · 2026-06 · Drop React/Vite; the UI is self-contained single HTML files served by FastAPI.**
  *A simpler deploy with no build step.* `index.html` is ~4,100 lines; `frontend/` is a leftover. → `EXECUTION_PLAN.md` status line
- **D04 · 2026-06-08 · Deterministic engine + agent pipeline, not LLM agents.** An "agent" is a component with judgement or a veto; an
  "engine" computes. The war room still labels four engines as agents; this is documented, not hidden (PR 23 / [AGENTS.md](AGENTS.md)).
  → `archive/reviews/ARCHITECTURE_AUDIT.md`
- **D05 · 2026-06-14 · The LLM is Google Gemini, called through stdlib REST, not the SDK.** *The SDK's "client has been closed" errors
  under FastAPI's loop.* Gemini "outages" later turned out to be billing (429), not code.
- **D06 · 2026-06-14 · Google OAuth with an email allowlist, `REQUIRE_AUTH` in production, `X-Agent-Key` SUPERADMIN for scripts.**
  *The portfolio had been world-readable.* → `archive/plans/IMPROVEMENT_PLAN.md` Phase A
- **D07 · 2026-06-15 · An optional HS256 `JWT_SECRET`, so sessions survive redeploys.**
- **D08 · 2026-06-14 → 06-28 · Real market data behind the existing provider interfaces:** Yahoo + Frankfurter (FMP optional), behind a
  resilience tier (cache → rate limit → breaker → retry → single-flight). *Synthetic prices made the app's "financial substance 4/10"
  (`archive/reviews/APP_REVIEW.md`).*
- **D09 · 2026-06-22 · ILS is the base currency for valuation and display; the goal, projections and mix all derive from the FX-normalised NAV.**
  It has been violated and fixed repeatedly: T0.3 summed ₪ + $, PR 18 agorot/pence, PR 45 intake 100×, PR 53 risk report in USD, PR 89 frontier inputs.
- **D10 · 2026-06-21 · Installable PWA + VAPID web push; dismissals stored server-side.** Bump the service-worker version on every shell change.
- **D11 · 2026-07-18 · Cash is a first-class position (a synthetic `CASH` row), and every card is sized and funded by a funding engine.**
  Consequence: `CASH` is also a real NASDAQ ticker, which caused repeated "cash quoted as a bank stock" incidents (D33). → `archive/plans/PLAN_2026-07-18_alignment.md`
- **D12 · 2026-08-13 · A book is a core + N sleeves (`plan_sleeves`), funded on one budget; sleeves are enforced by a max-weight cap.** → `archive/plans/BEAT_MARKET_NEXT_PLAN.md`
- **D13 · 2026-08-14 · Target solver (Phase T):** maximise excess CAGR over a benchmark under a hard drawdown ceiling. Never conflate the two
  benchmarks. Blend by simulating, never by averaging headlines. The solver is a constraint the agents read, not an agent. **Abstaining is a
  first-class result.** → `archive/plans/BEAT_MARKET_TARGET_SOLVER_PLAN.md`
- **D14 · 2026-08-16 · Record real NAV daily (`nav_snapshots`, 22:10) with time-weighted returns over the contributions ledger.**
  *Past NAV was unrecoverable; deposits must not read as performance.*
- **D15 · 2026-08-23 (PR 3) / 09-28 (PR 73) · Every book change writes a `transactions` leg; a rebalance is trades and owes CGT.**
  A test hook fails any quantity change without a ledger row. History repairs are preview-first, measured not balanced, and need two agreeing records.
- **D16 · 2026-09-04 (PR 46–52, 57) · Alembic is the only schema author.**
  - Migrations run as the Railway **preDeployCommand**, and `AUTO_CREATE_TABLES=false`.
  - `/health/ready` gates traffic; `/health` reports `commit` and `schema_revision`.
  - The container runs as non-root.

  *Boot-time `create_all` plus a hand-kept ALTER list silently missed a column for 10 days; PR 47's `alembic && uvicorn` caused a 2-hour outage.*
- **D17 · 2026-09 (applied in PR 80, 90, 104c.0, 104d.0) · A migration ships alone, add-only, nullable, guarded and mirrored into the startup
  DDL. Nothing reads it until the next PR.** → [DEVELOPMENT.md §6](DEVELOPMENT.md#6-database-migrations)
- **D18 · 2026-10-05 · One goal-seeking pipeline:** inputs (ILS, Black-Litterman) → propose (frontier) → validate → size (Kelly) → book
  target → funding/cards. **Propose + Accept, never auto-rebalance.** The universe is asset classes plus a US/international split, not
  tickers. *"Three optimisers quoting three targets would break one-function-one-answer."* → `archive/plans/EXECUTION_PLAN_2026-10-05_frontier.md`

## Money truth and card honesty

- **D19 · 2026-06 · The advisor never invents numbers; it uses grounded context only and ends with "Not financial advice."** PR 35: the digest
  *cannot* compose a false sentence (post-verified).
- **D20 · 2026-08-15 · A card may not claim a portfolio change it does not produce.**
  - Claims come from the simulated post-trade mix.
  - Selling and buying within the same class is labelled a swap.
  - Every funded buy goes through `propose_funded_buy`.
  - `tests/test_card_claims.py` applies each card and re-measures the result.

  → `archive/plans/FIX_PLAN_card_claims.md`
- **D21 · 2026-08-15 · Funding sells only what is overweight:** one budget per class, spent down as it sells; sleeves and the core are never raided.
- **D22 · 2026-08-23 (PR 1) · Accepting a card conserves NAV; there is one `buy_into_book()` for every buy path.**
  *`_rebalance_to` minted +4.8% NAV per tap.* → `archive/plans/FIX_PLAN_2026-08-23_audit.md`
- **D23 · 2026-09-03 (PR 40) · A multi-leg buy prices every leg before the first write, and aborts untouched if it can't.**
- **D24 · 2026-09-22 (PR 69) · The server alone decides which cards show; the per-device snooze is gone.**
- **D25 · 2026-09-22 (PR 71) · One target mix:** sleeves + core + cash + a 10% Commodities carve-out from the core, summing to 1.0.
  `targets_for` replaces rather than merges. *The old merge summed to 110%, so cards fought each other.*
- **D26 · 2026-10-06 (PR 100–103):**
  - Every number says what it measures, and no card gets a new sentence.
  - Every card can be acted on, or it is a heads-up (enforced in CI).
  - Every card has one shape: title → 1–2 sentences → "Accept will…" → before/after chips → Details.

## Engineering process: incidents that became rules

- **D27 · 2026-08-24 (incident `5a6dfef`) · Cross-loop sessions build their own NullPool engine; run the Postgres suite locally before pushing.**
  *`5a6dfef` shipped with three failures that only `test-postgres` saw, while CLAUDE.md wrongly claimed that job couldn't be run locally.*
- **D28 · 2026-08-24 · CI is not a gate; Railway deploys on push regardless. A red run is an incident.** Branch protection can't be enforced
  on this plan, so `scripts/ops/pre-push` substitutes.
- **D29 · 2026-08-24 (PR 2) · A job nothing calls is a job nobody has tested: every scheduled job has a test that runs it.**
  *`run_nav_snapshot_blocking` imported a non-existent name inside the function and failed 7 nights out of 7.*
- **D30 · 2026-09-04 (PR 47 → 49) · Never trust "Deployment successful": verify the pushed SHA is serving, behind a DB-backed healthcheck.**
- **D31 · 2026-10-05 · Every PR ships a smoke test** (`scripts/smoke/smoke-prNN-<slug>.ps1` on `_smoke-common.ps1`, `Assert-Build`
  first, read-only). It checks the PR's claims *and its non-changes*, runs with `-Sha` after the push, and its result goes in STATUS. Verify
  field names against production first. *PRs 78–87 shipped without one.* (Owner rule.)
- **D32 · 2026-08 → 10 · Smokes never pass on missing data.** Write PowerShell in ASCII with no backticks, and remember its variables are
  case-insensitive. Give the owner exact commands.
- **D33 · 2026-08-10 · A guard protects the path it is on: when fixing a data bug, find every writer of the field first.**
  *`CASH` was re-quoted as NASDAQ:CASH through a second reprice path and NAV went ~9×. It recurred through the performance backfill and PR 68.*
- **D34 · 2026-09 · Git hygiene:** never `git add -A`; use `git commit -F` without a BOM; never amend a pushed commit; one commit per round;
  sandbox read-only git uses `--no-optional-locks` on the Windows mount.
- **D35 · 2026-09-02 → · Plans are re-validated against production before work begins.** Mark each row PROVEN or INFERRED. Every new test
  must fail on HEAD. Validate the instrument before believing the number.
- **D36 · 2026-10-07 (PR 104c.1) · `conftest.py` fails the run on any unregistered skip; caches have TTL settings tests can zero.**
- **D37 · 2026-10-05 (PR 94) · CI cancels duplicate runs for the same SHA.**
- **D38 · 2026-08-12 · Measure latency over a reused connection, with a control run in the same window.** *Two phantom defects were curl reconnecting.*
- **D67 · 2026-08-31 · STATUS split:** `STATUS.md` = the per-PR history; the open-work list lives elsewhere (since 2026-10-07:
  [BACKLOG.md](BACKLOG.md)). *The top "spine" repeatedly lagged HEAD.* 2026-10-07: older history moved to `docs/archive/status/`.

## Security and ops

- **D39 · 2026-08-31 (PR 26) · The repo went private on 08-30.** The published agent key and default password were retired: `auth_password`
  has no default, `AUTH_PASSWORD` is authoritative, and published values are refused. → [ROTATION.md](ROTATION.md)
- **D40 · 2026-09-05 · Rotate the Postgres password last, and only for a reason.** It is the only ops item whose failure mode is an outage.
- **D41 · 2026-09-05 · Broker integration is parked.** Accept mutates the tracked book only. Un-park only on a decision to place real orders.
  → `archive/plans/BROKER_INTEGRATION_PLAN.md`
- **D42 · 2026-09-06 · TASE: Israeli companies yes (PR 62); Israeli index funds backlogged** (the provider can't price numeric fund symbols).

## Investing and product (owner decisions)

- **D43 · 2026-08-25 · The solver ranks on the recent window, with both windows measured.** This went against the spec's recommendation.
- **D44 · 2026-08-29 (Phase W) · Walk-forward test: does fitting beat not fitting?** Result: not one strategy earns a win. The regime-gate threshold
  was never a measurement, so it became a user setting. → `archive/plans/PHASE_W_PLAN.md`
- **D45 · 2026-08-29 (PR 16) · Forward assumptions:** volatility is always measured; return is measured out-of-sample by default and never
  silently (`ASSUMPTION_RETURN_BASIS`). *A static 12%/55% table was behind four wrong screens.* → `archive/plans/PR16_MEASURED_ASSUMPTIONS_PLAN.md`
- **D46 · 2026-09-04 → 09-21 (PR 54, 64, 68.1) · Cash:** a **soft** target with a ₪150 **hard** floor. Spending the reserve needs a
  `reserve_reason`. The redeploy card fires above ₪1,500 and invests down to the target.
- **D47 · 2026-09-04 (B2, executed in PR 59) · Retire `btm_swing_dip`'s TQQQ overlay and hold QQQ** (the overlay returned 17.2% CAGR vs QQQ's 20.7%, with a deeper drawdown).
- **D48 · 2026-09-05 (PR 55/56) · The book-volatility headline = today's exposure.** PR 55.1's σ/√f was withdrawn after one deploy.
- **D49 · 2026-09-03 · A buy funded from a correctly-targeted class is shown as "corrects no drift."**
- **D50 · 2026-09-22 (PR 70) · Health score = On plan + On track (goal odds), not generic risk/tax; one priority scale for every card.**
  *The old score read risk 3/100 on a deliberately leveraged book.*
- **D51 · 2026-09-28 (PR 74) · The goal is ROI on the money put in.** It is frozen except on deposits and withdrawals, the deadline governs, and no
  card moves the goal. *The old target was NAV × 1.3⁶, so progress was mathematically stuck at 20.7%.* → `archive/plans/PLAN_2026-09-28_six_items.md`
- **D52 · 2026-09-28 (PR 72) · A rule-vs-sleeve conflict becomes a decision card** (sell and lower the sleeve, or keep and update the rule; never retire).
  The take-profit ladder re-arms on execution, and history is read-only.
- **D53 · 2026-09-29 (PR 77) · The holding review sells only with a measured edge** (the signal beat holding over ~10 y, net of costs and 25% CGT).
  Otherwise it arms a stop.
- **D54 · 2026-09-28 (PR 76) · One return convention: stated returns are compound, converted once.** *The projection had counted volatility twice.*
- **D55 · 2026-09-28 (PR 75) · Cash research, option B:** a held fund is replaced only by a fund, and a stock only by a stock.
- **D56 · 2026-09-29 (PR 80/81) · Four advanced rules:** a rebalance band (3 pts), profit-protect (+20% activation), trend-break (200-day) and a
  drawdown guard (10%). Their parameters live in `trading_rules.params`. A one-time card converts caps into bands.
  → `archive/plans/PLAN_2026-09-29_advanced_rules.md`
- **D57 · 2026-10-05 (PR 88–91) · Frontier and Kelly:**
  - Propose + Accept.
  - Kelly fraction by objective: Grow ½, Balanced ⅓, Preserve/Income ¼, never full.
  - The Kelly fraction and return confidence are plan settings (migration 0026).
- **D58 · 2026-10-05 (PR 92/93) · The ROI goal is the objective; the frontier and Kelly are tools.** On track = ≥ 70%, on both the model and fat-tailed
  history. The goal picks the risk, capped at 1.0× Kelly. **Supersedes PR 91's ½-Kelly default** (it lowered the goal odds).
- **D59 · 2026-10-05 (PR 95) · Return confidence defaults to "blended":** Black-Litterman with τ = 0.2 (the prior is worth 5 years); the shrunk estimate is shown as a stress line.
- **D60 · 2026-10-05 (PR 96/97) · The crash-risk limit is a 1% chance of a 50% peak-to-trough fall.** The protection card can pause stops / set the guard to 15%.
  Volatility is regime-weighted recent vol. Re-solve when a protective rule fires.
- **D61 · 2026-10-05 (PR 101) · Backtest history is dividend-adjusted (total return).** *On closes alone, BND looked like −1.8%/yr instead of +1.25%, producing a false "Sell all BND" card.*
- **D62 · 2026-10-05 (PR 99) · Goal cards compute the goal after Accept;** the deposit and deadline levers were removed (they raised the goal).
  The definition of the goal itself is left as is, with a review on the backlog.
- **D63 · 2026-10-06 (plan v2.2) · UI decisions:**
  - The heads-up list sits at the bottom of Today.
  - A near-cap card offers to raise the cap to target + band.
  - Minimum actionable size: ₪50 for tax, max(₪100, 0.5% of the book) for others.
  - "What would it take?" is folded into Explore.
  - Plan tab = A's sub-tabs built the C way (`docs/design/`).
  - Dividends first.

  → `archive/plans/EXECUTION_PLAN_2026-10-05_cards-truth-and-plan-tab.md`
- **D64 · 2026-10-07 (PR 104c/d) · Every decision is a setting.** Every limit, bar or threshold the owner decides is a plan setting with his value as the
  default, visible where it acts and editable in a sheet. *Trigger: "where did I set this limit of 1% maximum chance?"* (Owner rule.)
- **D65 · 2026-10-06 · Pixel QA lists skip the Hebrew/RTL check unless the PR touches it or the owner asks.** (Owner preference.)
- **D66 · 2026-08-29 · PR write-ups carry a "STILL BROKEN ON PURPOSE" block** (or "nothing knowingly left broken").
- **D69 · 2026-10-08 (plan v3, D-e) · The server refuses "Replace book" while sleeves exist,** mirroring the UI block, and the
  basket is sized in the quote's currency. *Replace mode multiplied NAV by the USD/ILS rate, and only the UI blocked it.* → PR 105
- **D70 · 2026-10-08 (plan v3, D-a) · One regime: Today's risk-off card is driven by `engines/regime`** (the regime the sleeves act on).
  Futures are a labelled cross-check on the Markets tab only. *The futures card contradicted the strategies and used a 4-day "day change".* → PR 108
- **D71 · 2026-10-08 (plan v3, D-b) · Entry/watch rules (`buy_dip`, `price_below`, `price_above`) on unheld tickers are kept;**
  only exit rules of sold positions are purged. *A GET of Today was deleting fired watch alerts.* → PR 109
- **D72 · 2026-10-08 · Plan v3 runs wave by wave:** each PR fully gated and smoked; stop and report at each wave's end; the next wave's
  open decisions are asked then. (Owner preference.)
- **D73 · 2026-10-11 · Logout ends only this browser's session** (D-f, option 2): `/auth/logout` revokes the cookie's `jti` in
  `revoked_tokens`; other devices stay signed in. No migration (the table exists). → PR 111
- **D74 · 2026-10-11 · The owner (SUPERADMIN) can do everything.** Tightening write routes to ANALYST+ (S1) never narrows what the
  owner's session or the agent key can do; it only stops a READ_ONLY token from writing. → PR 110
- **D75 · 2026-10-11 · Wave cadence adjusted by the owner:** PR 120 moved into Wave A; PR 105→106 shipped the same day, no night's wait.
- **D76 · 2026-10-11 · Delete the legacy subsystem in three stages** (D-j): leaf routes + Celery + broker + no-op job (PR 112), the
  decision-feed cluster (PR 113), the six tables (PR 114.0 migration alone, then PR 114). → PR 112–114
- **D77 · 2026-10-11 · One card minimum** (D-c): the smallest trade card is the larger of the card's ₪ / %-of-book minimums and the
  smallest funded trade; the Card-thresholds sheet states it; a commodity gap below it is silent (no un-actable heads-up). → PR 115
- **D78 · 2026-10-11 · All 13 remaining card decisions are settings** (D-d): concentration cap, sleeve drift band, resize minimum and
  materiality bars, mute/done days and re-fire change, near-rule/trail/cap heads-ups, buy-idea counts. → PR 115
- **D79 · 2026-10-11 · The two segmented-control looks are built and shown as screenshots; the owner picks** (D-i). → PR 117
- **D80 · 2026-10-11 · When a core is chosen, commodity buys use the core basket's commodity tickers** (D-g), as a setting that defaults
  on; a "best trend right now" commodity pick is parked in the backlog as future work. → PR 116
- **D81 · 2026-10-11 · Delete the Sentry block** (D-h): errors already surface through Railway logs, job status (PR 107) and smokes;
  Sentry was wired but never installed. → PR 116
- **D86 · 2026-10-13 · A card that proposes rules shows what they would do to the goal** (owner: "I would expect
  that the information on the goals chances would be also written in the recommendation cards"). The goal replay runs
  with the proposed rules added and without them, on the same paths. A proposed rule the model does not replay (an
  entry signal, an alert) is named. Measured on demand after Today renders, so Today is not slower. → PR 124
- **D87 · 2026-10-13 · Retire a catalogue strategy with a flag, never by deleting it** (plan D5): Swing: Dip-Buy and
  Swing: Channel Breakout did not beat holding QQQ, their own base. A retired entry stays in `CATALOG` (about ten paths
  resolve a sleeve through `strategy_catalog.get`), keeps being measured, is not offered as a new sleeve or a swap target,
  and a book still running one is offered the swap to its base. → PR 125, `docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md`
- **D88 · 2026-10-13 · A Beat the Market tier is read off the measured worst drop** (Medium <= 36%, Medium-high <= 45%,
  High <= 60%, Very high above), not hand-labelled; the designed label is kept for an unmeasured strategy. → PR 125
- **D89 · 2026-10-13 · A multi-leg backtest re-trues drift only past a per-strategy band** (`drift_band`, default 0.01 so
  nothing else moves; 0.10 for volatility targeting and every new multi-leg strategy). Measured: the vol-target sleeve's
  target moved 10.6 times a year but the 1% re-true made 162 trades a year. → PR 125
- **D90 · 2026-10-13 · The out-of-sample verdict is judged against holding the strategy's own instruments** (plan gate
  test 3, D2). Against SPY, holding TQQQ with no rule decays 53 points across the 2022 split while SPY decays 6, so every
  3x strategy read "likely overfitted". `verdict_vs_benchmark` keeps the old reading. → PR 126 (engine a7)
- **D91 · 2026-10-13 · A selection strategy is judged on its selection edge** (return minus an equal-weight hold of the
  same universe, on the same cadence), because its universe is today's list of survivors; a fundamentals rule is
  measured on a proxy fund and says `measured_by_proxy`; a backtest starts on the strategy's first live day. → PR 126
- **D92 · 2026-10-13 · Leveraged strategies are also measured over 2000–2016, and the card shows that worst drop** (plan
  gate, long-history test). Funds that did not exist are rebuilt as L × the daily index move less a 0.95%/yr fee and
  (L−1) × (T-bill + 0.75%) borrowing, calibrated on 2011–2026. Trend Nasdaq's worst drop over that span is −85.5%
  against the −62% its ten-year card showed: its "off" state is QQQ, which fell ~83% in 2000–2002. → PR 128
- **D93 · 2026-10-13 · Every Beat the Market candidate is also measured for what it does to the owner's whole book**
  (owner: "add to the test also the 'make the whole book better' parameter", with option A). Recorded and shown beside
  the gate, not a way to pass. Rule fixed before measuring: adding it at its suggested size, the core making room, must
  raise return per unit of volatility by >= 0.02 and deepen the worst drop by <= 2 points over the ten-year window. The
  ten failed candidates are kept as specs (`strategy_candidates`) so it can be measured for them too. → PR 129
- **D94 · 2026-10-13 · The near-duplicate test (gate test 5) applies within a measured tier** (owner, after round 2 was
  measured). Daily correlation cannot tell a 1x rule from its 3x version (0.99), so read across every tier it would let one
  Nasdaq rule ship. Within a tier the stronger edge ships: R7 in Medium-high (over R4), R6 in High (over R5 and MH3, which
  is retired with a flag and moves to Hold the Nasdaq index). → PR 130
- **D95 · 2026-10-15 · A ticker several sleeves want is split between them, pro-rata to target weight** (owner,
  2026-10-14: "the sleeves holds much more than they should"; split approved as assumption A1 of
  `plans/SLEEVE_OVERLAP_PLAN_2026-10-14.md`). Holdings carry no lot tags, so ownership is unknowable; pro-rata to target
  makes per-sleeve over/under add up to the per-ticker drift card. Fixed: the Plan mix rows (97.6% shown for 59.2%
  held), the core's "sleeve overshoot", the health sleeve score and the measured-return weights. → PR 131
- **D96 · 2026-10-15 · One goal chance: an old verdict is quoted with its age and reason, not swapped for the rough
  run** (owner: "how come the chances of meeting the goals are still not aligned?" — Today 44%, Plan 20%; "show the
  20%/28%"). Withheld only when it is about a different goal or built on stale measurements. Screens ask once per load
  for a re-solve. → PR 131
- **D97 · 2026-10-15 · Every sleeve must be a different bet** (owner: "each sleeve MUST be different and not
  similar/depending on the same stocks"). Same bet = shared underlying market (TQQQ/QLD/QQQ are one Nasdaq-100) >= 40%
  of the smaller sleeve, counting on, base and risk-off legs (cash never counts), OR ten-year daily-return correlation
  >= 0.80 (owner asked for a recommendation; 0.80 chosen). Both limits are settings. A NEW sleeve that is the same bet
  is refused with "replace instead"; held look-alikes are left alone (owner: card only; the card is wave 4).
  Correlations are measured nightly (03:50) on production prices and only read online. → PR 132
- **D98 · 2026-10-15 · Wave-3 candidates are gated on beating the market first; "different" is a badge** (owner: "the
  main goal is not only to diversify but to beat the market (diversification is just a plus)"; "yes, add them, but I
  want to see the risk level per strategy"; short history "ship with a badge"). Thirteen pre-registered candidates
  (D1-D13, `plans/DIVERSIFIERS_2026-10-15.md`): B1 beats QQQ after tax OR raises the whole book's return; G2 book
  effect; G3 above T-bills with a shallower drop than QQQ; G4 long history (or a "short history" badge); G5 sweep
  within 5 points; G6 no near-duplicate above 0.90; G1 correlation with QQQ <= 0.50 is the "different engine" badge.
  The book-effect verdict names the failed test, and its two limits are settings. Measured on production; passing ones
  join the catalogue in a follow-up PR. → PR 133
- **D99 · 2026-10-15 · Round 2: hot stocks on 115 US names, and diversifiers funded from the leveraged sleeve** (owner,
  after none of D1-D13 passed: "run both option 2 and option 3"; option 3 "both" sizes). S1-S3 (momentum 10, quality
  momentum 5, 52-week-high leaders) on US115 with a T-bill fall-back, judged by the Beat-the-Market gate; every
  candidate also measured taken out of Volatility-Targeted Leverage at 1x and 2x its size, passing when the book still
  beats QQQ (F1) and the book-effect rule holds (F2). Pre-registered in
  `plans/ROUND2_STOCKS_AND_SLEEVE_FUNDING_2026-10-15.md`; the universe is 115 names (first described as ~125). → PR 134b
- **D100 · 2026-10-16 · Three hedges join the catalogue, labelled as hedges** (owner: "only the best 3" of the eight
  that passed option 3). Managed Futures (DBMF; short history), 2x Gold Trend and 2x Trend Risk Parity, exactly as
  measured, each badged "Hedge for a leveraged sleeve": none beats the market alone, and each lowers the book's return
  while cutting its worst drop. Whether that raises the goal chance is wave 5's question. A universe member the price
  provider has no history for (MMC) is dropped from that run and reported, rather than aborting it. → PR 134c
- **D101 · 2026-10-18 · Every option-3 pass joins; a stock-picking one is Explore only** (owner: "add the passed
  strategies", after the 10-18 re-run). GTAA-5, Trend Risk Parity, Gold Trend, Treasury Trend, KMLM, Nasdaq Trend with
  PSQ and Quality Momentum 5 join beside the three of D100. GTAA-5 is badged "borderline" (failed on 10-16, passed on
  10-18 by exactly the bar). Quality Momentum 5 is `explore_only`: measured and shown, refused as a new sleeve until
  live stock rotation exists. Option 2's S1-S3 all failed the Beat-the-Market gate on their own. → PR 134d
- **D102 · 2026-10-18 · Sleeve moves are scored nightly by the goal model; cards only read them** (wave 4 of the
  sleeve plan, owner "ok, continue"). Every candidate move -- each hedge at 1x and 2x out of a leveraged sleeve (not
  next to a look-alike, D97), removing each sleeve, replacing either side of a same-bet pair -- is scored by the light
  goal solve (`frontier_service.score_books`, the one `preview` uses): goal chance with rules and chance of halving,
  before and after. Job `sleeve_moves` at 04:40 (after the 04:10 goal solve) and `POST /plan/sleeve-moves/refresh`;
  stored per user in `kv_settings`. → PR 135a (cards: PR 135b)
- **D103 · 2026-10-18 · Sleeve cards change the plan, not the holdings; at most two "raises" and one "safer"** (wave 4).
  From the stored scores, and only while they are about the sleeves held: the best replacement for each side of a
  same-bet pair (HIGH); up to two moves that raise the goal chance by `material_odds_pts` (5) or more; one "safer,
  lower chance" move that cuts the chance of halving by that much. Accept sets every sleeve to the card's sizes
  (refused untouched if the book moved) and re-arms the caps; the trim and "Fund this sleeve" cards then carry the
  trades, so the card never claims one. → PR 135b
- **D104 · 2026-10-18 · An overweight sleeve is a choice: trim, or raise the size; the goal model recommends** (owner,
  2026-10-14: "option 3 (trim or raise the size) with a recommendation that is based on the agents analysis"). The
  nightly scoring adds a "raise to what you hold" move for each sleeve over its size by more than the drift band; the
  existing per-ticker overweight card becomes one two-tile choice. Recommend RAISE only when it does not lower the goal
  chance and does not raise the chance of halving by `material_odds_pts` or more; otherwise (or unmeasured) TRIM.
  Raise sets the size and sells nothing. → PR 135c
- **D105 · 2026-10-18 · The goal optimiser: the best measured book, goal unchanged; a starter book for an empty
  portfolio; the core always the user's own** (wave 5; owner: keep the goal and optimise; "if I will have nothing in my
  book ... how it will know what to recommend me?" -> include; the core "a variable and not hard coded"). Step A: up
  to 3 greedy steps over adding (never a look-alike, never Explore-only) or removing a sleeve, each candidate scored
  beside the current book in one solve, the winner re-sized by the goal model; kept only if it gains
  `optimizer_min_step_pts` (0.5, a setting) and halving stays within `halve_limit_pct`. Step B: the full solve on the
  result (protection retune, core choice). An empty book starts all core and is scored on a nominal amount (a %/yr goal
  is scale-free). The goal model supports only %/yr goals; a plan without a yearly ROI target has no optimiser.
  Nightly (05:10) and on demand. → PR 136a (card: PR 136b)
- **D109 · 2026-10-22 · A sleeve top-up takes every swap inside the class it buys before any sale from another class.** *Why:* tax-only
  ranking on a book at its class targets sold a Commodities loser to fund an Equities sleeve and the drift gate refused the plan. → PR 137
- **D108 · 2026-10-22 · A card previews the funder before it offers Accept.** If the dry-run is refused, the card is a Heads-up
  that says why, not an Accept that returns 400. *Why:* the sleeve-drift Accept for TQQQ always failed, wrote no mark and came
  straight back. Extends 2026-10-05 "every Accept does something". → PR 137
- **D107 · 2026-10-22 · The newest action on a card wins.** One mark per card: the newer of its Ignore and Done (a tie goes to Done).
  *Why:* an old Done used to override a newer Ignore and re-fire the card on a stale amount. → PR 137
- **D106 · 2026-10-18 · The optimiser's card: act when it is material, say so plainly when it is not** (wave 5). A better
  book by `material_odds_pts` (5) or more -> "The best book we can measure for your goal: X% -> Y%" (HIGH), Accept sets
  the sleeves (sells nothing); an empty book -> "Your starter book", the one card an empty portfolio can have; nothing
  material while under the on-track bar -> a heads-up saying so, with the best measured and the price of the halving
  limit (the chance without the protective rules and its chance of halving) -- the lever left, which is the owner's.
  → PR 136b
- **D85 · 2026-10-12 · Every rule card shows its own effect on the chance of reaching the goal** (owner: "you need to
  add to the rules cards the impact on the chances of meeting the goals"). A held stock with a sale rule is its own
  column in the goal replay. Each rule's effect is the replay with that rule off, on the same paths, and a rule the model
  cannot replay says why. Closes BACKLOG P3. → PR 123
- **D84 · 2026-10-12 · Segmented controls use look A with a blue selection** (D-i, after the screenshots): a full-width
  rounded track, big tap targets, and the selected segment in solid blue. Screenshots: `docs/design/seg-looks-2026-10-12.png`
  and `seg-look-chosen-2026-10-12.png`. → PR 117
- **D83 · 2026-10-12 · The halving limit is a hard limit.** When today's rules leave the chance of halving above the
  limit, the retune card offers the best setup inside it even if that costs goal odds. The card states the risk and the
  cost, and the owner decides. Inside the limit, a change is still offered only when it gains odds. → PR 121.1
- **D82 · 2026-10-12 · The protection retune searches every dial it can change, and says how many setups it tried.**
  The guard's sell size and re-arm gap become settings that the live guard reads, so the retune may move them. Take-profits
  can be paused by the card. The default guard levels tried widen to 15/17.5/20/25. → PR 121
- **D68 · 2026-10-07 · Documentation lives in `docs/`.** Superseded plans and history move to `docs/archive/` with their file names
  unchanged (code comments cite them by name). The open list is `docs/BACKLOG.md`; `STATUS.md` holds PR 88 onward.
