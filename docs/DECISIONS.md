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
- **D138 · 2026-10-26 · A stop on a geared equity fund holds the geared equity funds only, not 2x gold or the Treasury funds.**
  Amends D116. The group a stop holds is `strategy_profile.STOP_GROUP`: GEARED without UGL, TMF and UBT. A stop on one of
  those three holds only that fund. The leveraged ceiling (D125) still counts every geared fund. *Why:* reviewing the
  SOXL hold on 10-26 found that it also held UGL, which the 2x Gold Trend sleeve (added 10-25) buys when gold's signal
  is in. D116's evidence was equity on equity (SOXL / TQQQ); gold and Treasuries are not that bet. Owner, 2026-10-26
  ("continue with your recommendation"). → PR 154
- **D137 · 2026-10-26 · The SOXL stop hold stays as D114 says: no price ends it.**
  Reviewed with SOXL at 92.09, 12% above the 82.11 sale, and the SMH trend signal "in" (SMH 5.4% above its 200-day
  average), so the hold may last weeks. Considered and declined: ending a stop hold when the price has recovered (the
  whipsaw D114 was written against) and ending it by hand. Owner, 2026-10-26 ("continue with your recommendation").
- **D135 · 2026-10-25 · While a sleeve the app may buy is unfunded, the core realign swaps contents and keeps the core's size.**
  It sells off-basket names into the basket and sizes the basket to what the core holds (never below its target); it
  never trims the core to cash. Moving money from the core into sleeves is "Fund all sleeves". Supersedes PR 60's
  "an unfunded sleeve has first claim" deferral. *Why:* 10-23..10-25 the realign trimmed basket legs to cash, the cash
  card bought the core back (XLE, XLF included), seven accepts of one card. Owner, 2026-10-25 ("Swap contents, keep
  size"). → PR 150, C30
- **D134 · 2026-10-24 · Commodities live inside the core; the fixed 10% carve-out is a setting, default 0.**
  PR 71 carved the objective table's Commodities weight (Grow: 10%) out of any core that names none, and the Plan tab
  showed it as its own row. Now `commodities_carve_out_pct` (Card thresholds) decides it, default 0: the core's own
  basket holds commodities or not (All-Weather does; Diversified Global Growth does not), and a commodity bet beyond
  it is a sleeve. Commodity funds a core without commodities does not name are off-basket like any other holding --
  the core card offers to move them, nothing sells by itself. 10 restores PR 71 exactly. My mix shows commodity
  holdings inside the core row unless something is carved out. *Why:* owner, 2026-10-24: "I want to remove the
  commodities from my mix, it should be considered as part of the sleeves/core and not as stand alone"; chose
  "Inside the core". Supersedes the 2026-09-22 "10% carved from core". → D134 PR
- **D133 · 2026-10-24 · Before live stock rotation, three more rankings are measured against SPY; P20 waits for a TASE price source.**
  Measured on production (10 years to 2026-10-23): Quality Momentum 5 13.8%/yr vs QQQ 20.3% (selection edge -4.6%/yr,
  worst drop 37%); at 15% of the book it lowers return 24.7 -> 24.3%/yr and cuts the worst drop 3 points -- a hedge,
  not a way past the S&P. Pre-registered before any result, same universe, gate and tests: R1 30-day momentum (top 10),
  R2 12-month momentum with a 30-day filter (a name must be up over the last 21 sessions; top 10), R3 Quality Momentum
  with the same filter (top 5). Each round-2 measurement now also carries `vs_spy` (SPY held on the same engine; not
  part of the gate's pass) and reads the book effect at 15, 25 or 30%. Live rotation is built only for a ranking that
  passes the gate AND beats SPY. P20 (relative value across two listings) is parked with P6: the price provider
  cannot price TASE numeric symbols, and no dual-listed name is held. *Why:* owner, 2026-10-24: P17 "Test other
  rankings first"; P20 "Park with P6". → P17a
- **D132 · 2026-10-24 · One exit per holding: core funds have none, sleeve and 2x/3x holdings one trailing stop sized from volatility, no take-profits; the protection manager is the one place that proposes an exit.**
  Core funds (held, in no sleeve, not 2x/3x) carry no stop-loss, trailing stop, take-profit or profit-protect: the
  rebalance bands and the book's drawdown guard protect them. A sleeve's tickers and any 2x/3x fund held carry exactly
  one trailing stop at k x the holding's measured monthly volatility (k from 2, 3, 4, picked on the goal replay: the
  most at the bar inside the halving limit), clamped 5-60%; no history -> left as it is. No take-profits anywhere. The
  manager measures nightly (05:40 UTC) and on demand (`POST /plan/protection/refresh`) and offers the whole change on
  one card; Accept DELETES retired exits (a paused one would come back with the 3-day re-arm, D124), re-levels or adds
  the trailing stops, logs a rule event for each, and refuses whole if any rule moved. No generated card or rule
  suggestion arms an exit any more; rules the owner adds by hand are untouched. Refines D115 (a card never switches off
  downside protection) for core funds only, by the owner's choice; the goal's retune card (A2r) is not shown beside
  this one. *Why:* owner, 2026-10-24: "Bands + book guard only"; "From measured volatility"; take-profits "Drop them".
  Live that day: 39 active rules, most core funds with a fixed stop ~8% down, a 10-17% trail and a take-profit. → A3r
- **D131 · 2026-10-24 · The scheduled production smoke uses its own read-only key.**
  `SMOKE_API_KEY` (Railway variable), sent as `X-Agent-Key` like the agent key, acts as the owner for GET/HEAD only;
  any other method is refused 403 ("the smoke key is read-only"). It keeps the owner's role on the principal because
  `acting_user` writes the principal's role onto the user row. Set to the agent key's value by mistake it is ignored
  (the agent key keeps writing). The daily smoke's cloud environment holds it as `IW_SMOKE_KEY`; never the agent key,
  which can accept cards and change rules, and never in a task's text. *Why:* owner, 2026-10-24 (D-1: a daily smoke at
  08:30, message on FAIL only); a key that runs unattended every day should not be able to trade. → D-1
- **D130 · 2026-10-24 · The optimiser ranks by the amount at the bar and holds the book against SPY on one card.**
  Each step of the book optimiser ranks candidates by the amount reached at the goal's confidence bar with the rules as
  armed (a change counts at +2% of today's amount, A2r's bar), not by goal odds; it also measures the all-core book (no
  sleeves). Today shows one card, "Your book vs the S&P 500: X%/yr vs Y%/yr at your bar", listing every single change
  that adds 2% or more within the halving limit (add or remove a sleeve, the combined set, the protection retune), best
  first, each measured alone with its own amount, return and halving; Accept takes `?choice=oN` and runs that option
  through its own path (`sleeve_move` sells nothing; `frontier_rules` refuses if a rule moved). With nothing to offer it
  is a note quoting the all-core number and, below the index, that the sleeves do not earn their risk. A result stored
  before C29 keeps the old card. *Why:* owner, 2026-10-24: the book beats SPY at the bar in only "4.2%" of the cases,
  "I would expect a recommendation card that reviews my portfolio and says what I need to add/remove/edit"; chose "One
  card, ranked options". On 808b000 every book read 0.1-1.2% goal odds, so nothing passed `min_step` and Today said only
  "No sleeve change we can measure lifts your goal chance". → C29
- **D129 · 2026-10-24 · The protection retune may widen a stop up to x3, never remove one; it ranks by the amount at the bar.**
  Each replay column with an exit (a sleeve, or a held ticker) is tried at x1.5, x2 and x3 its current distance, all
  together and one at a time, beside the guard and take-profit options; a stop is never switched off (D115 stands).
  Candidates rank by the amount reached at the goal's confidence bar, then goal odds, then halving; a setup is
  material when it raises that amount by 2% or more (or the goal odds by the usual points, or the halving limit is
  breached). Accept edits each stop's level (a trailing percent, or a price stop's distance from today's price) and
  leaves an "edited" rule event. *Why:* owner, 2026-10-24 ("Widen up to x3"; "Rank by confident amount"): on 10-24 the
  28 setups tried all read 0.1% goal odds, so the ranking could not tell them apart, and none tried the stop widths.
  → A2r
- **D128 · 2026-10-24 · The goal model replays a fixed stop as a one-time sale at its price; the goal is held against SPY.**
  A price-mode `stop_loss` was modelled as a trailing stop at today's distance (`1 - level/price`), so a stop the price
  had drifted down to (TQQQ 65.73 vs 66.29) became a 0.8% trail for five years. It is now `stop_ratio`: the sleeve sells
  once when its path crosses the level, and the stop is spent. Trailing stops and activated profit-protects still
  trail. The C26 benchmark is SPY itself (fetched for the solve, re-centred on the US-equity drift the book is priced on)
  instead of VTI, with VTI's column as the fallback. *Why:* owner, 2026-10-24 ("The comparison should be spy and not
  vti"; protective rules: "come back with insights"). → A1
- **D127 · 2026-10-23 · Money a stop-held sleeve cannot use goes to the core, and is not reserved for that sleeve.**
  The core card's "an unfunded sleeve has first claim" deferral (PR 60) counts only the part of a sleeve's gap the app
  may buy: a ticker under a stop hold (with PR 141, every 2x/3x fund once one is stopped) is left out. `book_target`
  already gives that share to the core; the deferral now agrees with it. *Why:* owner, 2026-10-23, "Currently I have two
  triggered rules but no recommendation on how to act"; offered "into the core / park in cash / keep", chose "Into the
  core". → C27. *Correction (PR 149, 2026-10-25):* `book_target` did NOT give that share to the core -- it parked
  it in Cash as a take-profit hold (production 10-25: 23% of the book). It does now; take-profit holds still park cash.
- **D124 · 2026-10-23 · Switched-off protection re-arms itself after 3 days, never one already crossed.**
  A stop-loss, trailing stop, profit-protect, trend break or drawdown guard that has been off `rearm_after_days` (a
  Card-thresholds setting, default 3; 0 = never) is switched back on by the 30-minute rule check, logged `resumed` by
  `auto`. One whose condition is true right now stays off (PR 138's card: re-arming it would be an instant sale). A
  trailing stop restarts its trail from today (as Ignore does, D117). *Why:* on 10-22 about twenty stops were found off
  only when one that should have fired stayed silent; PR 140's heads-up shows them, nothing brought back one the owner
  forgot. Owner, 2026-10-23 ("3 days"). → C18
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
- **D60 · 2026-10-05 (PR 96/97) · The crash-risk limit is a 1% chance of a 50% peak-to-trough fall.** The protection card can pause stops / set the guard to 15%. *(Pausing stops superseded by D115.)*
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
- **D126 · 2026-10-23 · The goal is held against a US stock index fund; no lever offers a target below it.**
  The nightly goal solve also runs a 100% VTI book (`us_equity`, the app's US-market proxy; it tracks the S&P 500
  closely) on its own history paths: same re-centred drift, seed, deposits, deadline and confidence bar, no cash and no
  rules. Stored as `benchmark` with its return at the bar. When the book's confident return is below the index's, the
  "Set a target this book can reach" card and the "lower your target" lever are not offered; the goal cards and the
  optimiser note say "At your N% bar this book reaches X%/yr ...; a US stock index fund (VTI) on the same history
  reaches Y%/yr ... this book is not beating it." Above it, the target lever is offered as before and says it is ahead.
  *Why:* owner, 2026-10-23: "once I choose the beat the market strategies the goal is to get higher return than the
  s&p so a return of 3.7/yr would not be good enough". Live 10-23: median 15.6%/yr, volatility 43%, 3.7%/yr at the 65%
  bar with the rules armed. → C26
- **D125 · 2026-10-23 · No app buy takes the 2x/3x funds past 44% of the book.**
  `leveraged_ceiling_pct` (a goal setting on the Card-thresholds sheet, default 44, 0 = none) caps the share of the book
  in 2x/3x funds (`strategy_profile.GEARED`: the leveraged set without the -1x PSQ/SH). Planners clip their 2x/3x legs
  to the headroom (Fund my sleeves shares one headroom across sleeves); executors refuse before their first sale
  (`leveraged_ceiling.assert_within`, netting 2x/3x sold in the same trade); the book writer is the backstop; Today drops
  a 2x/3x buy that does not fit and lists it as blocked, and over the ceiling offers one trim of the largest 2x/3x
  holding. Your own rule's buy card stays and says it is over. Your own Holdings > Add is never refused. *Why:* the plan's
  leveraged sleeves (40% TQQQ + 23% SOXL) are 63% of the book fully invested, and the 2x/3x bet cost the most this
  month. Owner, 2026-10-23 ("44% (Recommended)"). *C24:* the target mix manages the 2x/3x sleeves to the ceiling too
  (`all_sleeve_targets` caps them pro rata; the excess is managed in the core). → PR 148, C24
- **D123 · 2026-10-23 · Two sleeves are the same bet when what they HOLD when invested moves together.**
  A third same-bet test beside shared market and the strategies' own correlation: the correlation of each sleeve's
  risk-on basket (`weights`, rebalanced daily), measured nightly (`invested_pairs`), against the same
  `sleeve_correlation_limit` (0.80). *Why:* SOXL-trend vs TQQQ vol-target measured 0.78 on strategy returns (diluted by
  the trend sleeve's time in cash) while SOXL and TQQQ moved 0.95 and fell together on 8 of 8 big SOXL down days.
  Owner, 2026-10-23 ("On daily returns"). Existing sleeves are not removed; the Plan tab marks the pair and adding a same
  bet is refused. → P14
- **D122 · 2026-10-23 · Keep the 30% halving limit; revisit the goal instead of loosening protection.**
  Production's model on 10-23: goal (30%/yr, ₪82,692 by end-2031) 0.2% likely with the protective rules, halving 0%;
  without them 18.5% and halving 53.9%. Required growth 31.9%/yr vs the book's expected 15.6%/yr at 43% volatility.
  Raising the limit trades a 1-in-2 halving risk for odds still under 1 in 5. Next: a goal comparison (lower %/yr,
  later date, monthly deposit) so the owner can pick a reachable goal (P1). Owner, 2026-10-23. → P16
- **D121 · 2026-10-23 · A build or a background job that is broken says so on Today and on the phone.**
  At boot, every scheduled job's in-function imports are resolved and recorded as the job `boot_check`; each job with no
  history is `registered_at` boot. One HIGH heads-up names every job that is failing, has not succeeded in 26 h (or never,
  since registration), or a failed `boot_check`. The app-health heads-ups (this one and PR 143's rule check) are pushed,
  once per id per 12 h. *Why:* Railway deploys every push and CI is off until 11-01; `nav_snapshot` once failed 7 nights
  of 7 unseen; and no heads-up was ever pushed (the push loop read `recommendations` only). → PR 144
- **D120 · 2026-10-23 · Every Buy and Sell the ledger records carries its price and value; a leg without them is an error.**
  Handlers that report a single-ticker sale (`trim`, `sell_position`) or buy (`buy_funded`) now pass `price` and
  `gross_ils` / `price`; the sleeve funder records the price of what it sold; `record_leg` logs an ERROR for a Buy/Sell
  with a quantity and no price or value (still written: the share count matters). Older rows are not rewritten here.
  *Why:* 140 of 440 production rows had price 0 and/or ₪0, every stop and trim among them, and Holdings showed no "last
  buy" for XOM. The money was right throughout (`cash_added_ils`); the record was not. → PR 147
- **D119 · 2026-10-23 · The core realign may trim a sleeve only by its own overshoot, and never a ticker its basket holds.**
  The trim is split per sleeve that is over its size, across that sleeve's holdings (`held_by_ticker_ils`), and the card
  names the tickers. A sleeve under its size is never a source; a sleeve ticker the core basket also holds is not sold to
  be bought back. *Why:* the trim was sized from the over-size sleeves and sold pro-rata across EVERY sleeve holding;
  production 10-19 .. 10-21 shows 17 core sales that "Fund my sleeves" bought back within minutes (SOXL, TQQQ, UGL, QQQ,
  BIL) and five accepts that sold and bought QQQ at once. → C15
- **D118 · 2026-10-23 · A rule check that fails is an incident on Today, and job history outlives a deploy.**
  The price job fails (with the reason) when the rule check fails; one book failing no longer skips the rest; Today
  carries a HIGH heads-up when the last check failed or none has succeeded for 75 minutes; job outcomes persist in
  `kv_settings`. *Why:* the rules are checked in one place and its failure was a log line without the error under a green
  job; and on 10-23, four minutes after a deploy, no job could say whether it ran last night. → PR 143
- **D117 · 2026-10-23 · Ignoring a fired stop keeps the stop on, re-armed below today.**
  Stop-loss: one ladder step (10%) under today's price, the same level Keep proposes. Trailing stop and profit-protect: the
  peak restarts at today's price, the trail width unchanged. Logged as `kept` by `ignore`; the card's "how" says so before
  you tap. *Why:* Ignore was a dismissal that `resolve_rule` turned into `active = False` for every one-shot type, so the
  position stayed and its stop was gone (reproduced; never yet hit on production, whose visible log since 08-23 has no
  ignored stop). Owner, 2026-10-23 ("Re-arm below today"). → PR 142
- **D116 · 2026-10-23 · After a stop sells a 2x/3x fund, the app buys no 2x/3x fund until that hold ends.**
  *Amended by D138 (PR 154): the group is the geared equity funds; 2x gold and the Treasury funds are not in it.*
  Every buyer (cards, Plan buttons, sleeve funding, the core realign, the cash redeploy) reads the group from the stop hold;
  it is derived, never stored. The held-cash card offers non-blocked places only and still recommends investing part of
  the reserve. -1x funds (PSQ, SH) are inverse, not geared, and are not in the group. A stop on a plain fund still holds
  only that fund. *Why:* 10-23, minutes after the SOXL stop-out, sleeve funding funded the TQQQ sleeve and the held-cash
  card put TQQQ first: the same bet on the day it matters (0.95 daily-return correlation over the month before; down
  together on 8 of 8 sessions SOXL fell more than 3%), while the strategies' signals measure 0.78 and pass the 0.80
  same-bet limit. Owner, 2026-10-23 ("any 2x/3x fund"; "elsewhere, non-blocked only"). → PR 141
- **D115 · 2026-10-23 · The protection card never switches off a stop; paused protection is always on Today.**
  The retune may move the drawdown guard and pause take-profits, never a stop-loss, trailing stop, profit-protect, trend
  break or the guard itself (`DOWNSIDE_RULE_TYPES`); Accept refuses a spec that names one. Every pause and re-arm (card or
  you) writes a rule event. Every downside rule that is off sits on ONE heads-up with "Re-arm all". The card's title gives
  goal odds and halving side by side. Auto re-arm: backlog. *Why:* the card's all-or-nothing switch paused every stop on
  the book four times (10-06 .. 10-20) because a goal out of reach scores any exit as cost; three paused stops fired late,
  already breached. Supersedes the stop half of D60. Owner, 2026-10-23. → PR 140
- **D114 · 2026-10-23 · A stop-out holds its ticker against every buyer until the sleeve's own signal goes out and comes back in.**
  Stop-loss, trailing stop, profit-protect and trend-break exits record a `buy_back` row of kind `stop`. No price ends it.
  It binds Today's cards AND the Plan buttons (unlike D-PR75's take-profit hold, which the Plan may override). A ticker no
  sleeve owns is held until you end it on Holdings > Rules, which asks a confirm naming the stop. Your own "add a
  holding" is a record of a broker trade and is never blocked. *Why:* SOXL was stopped out 10-12 and 10-16 and re-bought
  by sleeve funding within days both times, the second time above its own sale price. Owner: "start working on 139",
  2026-10-23. → PR 139
- **D113 · 2026-10-22 · A protective stop that is OFF while its condition is true is a card; a big one-day move is only a heads-up.** Accept
  re-arms the stop (nothing is sold). The text states facts and never says sell or keep. → PR 138
- **D112 · 2026-10-22 · The chart's last point may be a labelled provisional one**, only when live value versus the last snapshot is a fair
  comparison; otherwise it says why and draws nothing. → PR 138
- **D111 · 2026-10-22 · A sleeve the funder refuses is a red blocker on Today** with two choices (open the plan / drop the sleeve; dropping sells nothing),
  and swiping the heads-up does not hide it. → PR 138
- **D110 · 2026-10-22 · Ignore is never overruled by a card property.** `persistent` protects a card from Done only. → PR 138
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
