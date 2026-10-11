# InvestWise Pro — Status

_Last updated: 2026-10-11 by Claude (plan v3 Wave C complete: PR 110, 111). Last product PR: **PR 111**. Gate: local SQLite + local Postgres + ruff, then a GitHub PR whose CI must be green before merge._

**What this file is:** the changelog. Newest first, one `## PR NN — …` entry per shipped PR, covering what changed, why,
how it was verified (tests, smoke result with SHA, QA), and what is still broken on purpose. Add an entry every time you ship.

**What it is not:**
- The open-work list. That is **[`docs/BACKLOG.md`](docs/BACKLOG.md)**.
- The design. That is **[`docs/`](docs/README.md)**.
- Decisions are D-numbered in [`docs/DECISIONS.md`](docs/DECISIONS.md).
- History before PR 88, plus the old "Where this repo is" spine, backlog and ops tables (with their evidence), is in
  [`docs/archive/status/STATUS-HISTORY-to-PR87.md`](docs/archive/status/STATUS-HISTORY-to-PR87.md).
  Code comments and tests that cite "STATUS.md PR 58", "STATUS.md PR 57" and so on (any PR ≤ 87) mean that file. Their
  `STATUS.md:NNNN` line numbers are from before the split.

## Where things are — 2026-10-08

- **Production:** `88bb5d4` (PR #1 docs merge on top of PR 104d.1), schema `0028_plan_thresholds`, `sw.js` `iw-v51`.
  Read live on 2026-10-07 at 22:00 UTC:
  - Today: 0 actionable cards, 1 heads-up.
  - Jobs: all `failures: 0`.
  - VAPID: pinned (`vapid_pinned_by_env: true`, `vapid_env_matches_db: true`).
  - Data: live Yahoo + Frankfurter.
- **Active plan:** [`docs/plans/EXECUTION_PLAN_2026-10-08_hardening.md`](docs/plans/EXECUTION_PLAN_2026-10-08_hardening.md),
  PR 105–119. **Approved 2026-10-08; execute wave by wave.** Decided: D-e, D-a, D-b (DECISIONS D69–D72). Next: **Wave A (PR 105, 106)**.
  New session? Start from [`docs/plans/HANDOFF_2026-10-11.md`](docs/plans/HANDOFF_2026-10-11.md).
- **Critical, latent: fixed in PR 105 (2026-10-11).** The sleeve-drift trim oversell and the "Replace book" NAV
  multiplication; plus `/portfolio/add` replacing a held position (now pays from cash). PR 106 (sleeve funding
  obeys the book target) followed the same day. PR 120 (one goal chance on Today and Plan) closed Wave A. Wave B (PR 107-109) shipped the same day. Wave C started the same day (D73, D74). PR 111 closed Wave C. Next: **Wave D** (PR 112-114, legacy removal; needs D-j) after the owner's go-ahead.
- **Dated:** Railway `railway.json` (Config-as-Code) stops working on **2026-12-01**. Migrate before 2026-11-15 (plan PR 119).
- **Owner ops (plan Wave 0):**
  - Check Railway Postgres backups.
  - Delete the orphan `redis-volume`.
  - ~~Delete the untracked `candidate_search.py` draft~~ and ~~commit the v8 / v2.2 plans + Plan-tab design~~: done by
    Claude 2026-10-11 (PR #3).
  - Rotate the GitHub PAT and the Railway token.

---

## Docs overhaul — 2026-10-07 (no app change)

- Wrote `docs/`: README (index), PRODUCT, ARCHITECTURE, DOMAIN, DATA_MODEL, API, FRONTEND, DEVELOPMENT, OPERATIONS,
  DECISIONS (D01–D68), HISTORY, BACKLOG, GLOSSARY.
- Rewrote the root README.
- Updated CLAUDE.md: test count, the moot `/dashboard` rule, a docs map, and the known funding exceptions.
- Moved 22 root plan files, 2 reviews, STATUS-V2, STATUS-ARCHIVE and PRODUCTION.md into `docs/archive/` (names unchanged).
  ROTATION.md moved to `docs/ROTATION.md`.
- Split STATUS.md: entries for PR 87 and earlier, plus the old top block, went to `docs/archive/status/STATUS-HISTORY-to-PR87.md` verbatim.
- Moved historical scripts to `scripts/archive/`: 128 deploy scripts, 64 pre-PR 88 smokes, the August phase shippers, the one-shot repairs.
  Repointed `tests/test_sleeves_mean_what_they_say.py` to `scripts/archive/deploy/_common.ps1`, and updated the "See ROTATION.md" messages.
- `docs/AGENTS.md`: fixed its closing "all four are still open" (two were fixed in PR 58) and added a currency note.
- **Left for the owner:** deleting `investwise.db` and `frontend/` (BACKLOG L2), and committing the untracked plans/design (above).

## PR 111 — logging out ends this browser's session

**Plan v3, Wave C, second PR (D73, D-f option 2: "only the specific browser currently used").** No migration:
`revoked_tokens` already existed for refresh-token rotation, so the planned PR 111.0 was dropped.

**What was wrong (S2, reproduced live 2026-10-11).** The PR 111 smoke against the PR 110 build minted a throwaway
token, logged it out, and replayed it: 200. `/auth/logout` deleted the cookie and nothing else. The 30-day SUPERADMIN
JWT inside it stayed valid to anyone holding a copy, and `require_role` never consulted `revoked_tokens`.

**What.**
- `/auth/logout` decodes this browser's cookie (and a bearer token, if one is sent) and writes each `jti` to
  `revoked_tokens`.
- `require_role` refuses a revoked `jti` with 401 "this session was logged out". **PR 111.1:** the revoked set lives
  in memory and is reloaded from `revoked_tokens` at boot, so there is no database read in the auth path. The first
  version read the table per request (cached 60 s). On Postgres that put a pooled connection into every authenticated
  request, and CI showed asyncpg's `Connection._cancel` left un-awaited across loops in
  `test_rbac_enforced_when_auth_on` (the CLAUDE.md cross-loop trap). One process (no `--workers`), so in-memory is
  exact.
- Other devices and the agent key are unaffected. To end every session at once, rotate `JWT_SECRET`.

**Tests.** `tests/test_logout_ends_this_session.py` (4):
- replaying a logged-out cookie or bearer token gets 401, and it still does after a cold cache;
- another device's session still works, and so do a fresh login and the agent key;
- a garbage cookie at logout is harmless.

The two replay tests fail on HEAD.

**Smoke.** `scripts/smoke/smoke-pr111-logout.ps1 -Sha <sha>`. It uses a throwaway m2m token minted by the agent key,
never the owner's session. Against the PR 110 build it FAILs at "the same token after logout -> 200", which is S2,
reproduced live.

---

## PR 110 — authorisation and honesty at the edges

**Plan v3, Wave C, first PR.** Owner decisions at the go-ahead (2026-10-11):
- **D74:** "me as admin can do everything." SUPERADMIN keeps every route.
- **D73 (D-f option 2):** logout ends only this browser's session. That is PR 111, and it needs no migration, so
  PR 111.0 is dropped.

**What was wrong, and what changed.** Every item is proven by a test that fails on HEAD.
- **S1.** 18 write routes accepted a READ_ONLY token: card accept, dismiss and restore; heads-up dismiss; position
  PUT/DELETE; rules CRUD, toggle and evaluate; push subscribe, unsubscribe, test and check; broker connect and sync.
  All are ANALYST+ now, and broker is SUPERADMIN. Compute-only POSTs stay READ_ONLY. Nothing changes for the owner:
  the browser session and the agent key are SUPERADMIN.
- **S4.**
  - `/providers/health` (leaked held tickers through `quote:<TICKER>`) and `/adversary/diagnostics` are SUPERADMIN.
  - `/markets/summary` needs sign-in and is cached 15 min. It was one Gemini call per anonymous hit.
  - `/docs`, `/redoc` and `/openapi.json` are off in production.
- **C6.** The mock broker is refused in production. Two bodyless POSTs used to write TEVA/GOLD/BOND into the real
  book with no ledger row.
- **C7b.** `/whatif` falls back to demo observations only when `demo_signals` is on, and reports `grounded`. An empty
  book was shown "Buy TEVA (NYSE)".
- **C8.** `/ask` runs the digest's `unsupported_numbers` check, with numbers in the question allowed. An answer that
  quotes a figure the data doesn't contain is withheld (`verified: false`). Every answer ends with "Not financial
  advice.".
  - The portfolio, holding and macro summaries get the same check plus the disclaimer.
  - Web research is labelled `verified: false`, because it quotes outside figures by design, and is always
    disclaimed.
  - Found while fixing: research labelled a USD price "₪". It now uses the ticker's own currency.
- **S3.** Push unsubscribe deletes only the caller's row; the owner also matches legacy rows saved under the
  superadmin name. Subscribe refuses to move an endpoint to another subject.
- **S5.** `core/security.py`, `require_api_key` and the `api_key` setting are deleted. The false boot warning
  "write endpoints are unauthenticated" is replaced by an ERROR when `REQUIRE_AUTH` is off in production.
- **L15.** Grounded research falls back to ungrounded only on HTTP 400. A 429 is now one call and the billing
  message, not two calls and "temporarily unavailable".

**Not in this PR:** C7 (`/decision-feed/weekly`, no UI caller) goes with the legacy deletion (plan PR 113).

**Class-closers.** `tests/test_the_doors_are_shut.py` (12, all fail on HEAD):
- write routes ANALYST+ unless COMPUTE_ONLY;
- no unauthenticated route outside the PUBLIC allowlist;
- a behavioural READ_ONLY-403 / owner-allowed check.

**Smoke.** `scripts/smoke/smoke-pr110-doors.ps1 -Sha <sha>`. The broker check runs only with a proven `-Sha`: on
the old build it would write. **Ran against `cd673f0` (GitHub PR #11 merge): 13 PASS, 0 FAIL.**
- Anonymous: 401 on all three gated routes; `/docs` and `/openapi.json` 404.
- The agent key gets 200 on all three.
- `/ask` answered (verified); `/whatif` grounded.
- The broker check passed on "No brokerage connection" (sync without connect), so it proved less than it claimed.
  It now calls `/broker/connect`, and a manual call confirmed "the mock aggregator is disabled in production".

---

## PR 109 — reading Today never deletes or retires a rule

**Plan v3, Wave B, third PR (D-b).** Re-proven at HEAD with the C9 probe: one GET of Today deleted the AMZN `buy_dip`
and GOOGL `price_below` watch alerts and the NVDA stop before any card showed.

**What was wrong, and the root cause.**
- **C9, deletes on read.** `list_rules` called `purge_orphan_rules` on every read: Today's build, the Rules tab, and
  every warm-up, push and digest build. The purge deleted every rule on an unheld ticker, watch alerts included. A
  "legacy safety net" in the same function also set `active=False` on read.
- **The inferred stop-loss retirement is PROVEN.** The banner reconciliation "healed" a triggered rule with no served
  card by acknowledging it, and `resolve_rule(..., "acknowledged")` on a one-shot rule sets `active=False`. A read of
  Today could therefore switch off a triggered stop whose card was not served (test: V stop, card withheld,
  `active` 1 → 0 on HEAD). The banner already counts only carded rules (`renderRuleBanner`), so the heal protected
  nothing on screen.
- **The dismissed-card heal ran in the build too.** That meant every background build wrote rules.

**What.**
- **D-b.** `ENTRY_TYPES` (`buy_dip`, `price_below`, `price_above`) on unheld tickers are watch alerts:
  - never purged or retired;
  - quoted by the evaluator (`params.watch_price`, `watch_at`) and evaluated like any rule;
  - read from the stored price on Today (no network). At price 0, a fired `price_above` would have been cleared as
    "back under".
- `purge_orphan_rules` clears only exit rules of positions you no longer hold, and only the evaluator calls it.
- `list_rules` and `triggered_rule_recs` hide orphaned exit rules instead of deleting them, so the screen is right
  in between evaluator runs.
- The build no longer resolves rules:
  - a dismissed card's latch is cleared by `rules_service.heal_dismissed_rules` inside `evaluate_user`;
  - an uncarded triggered rule is reported as `rule_banner.uncarded`.
- **Not done as written:** the plan's `read_only=True` flag on `build_recommendations`. The build no longer writes
  rules at all, so there is nothing for the flag to switch. Remaining GET writes are documented (DOMAIN §4): buy-back
  hold release, handled strategy signals, and standing-alert latch clears.

**Why it was silent.** Every write was "self-healing" with a comment justifying it. No test compared the rule table
before and after a read.

**Tests (all 5 fail on HEAD).** `tests/test_reading_today_never_deletes_anything.py`:
- two reads of Today and one of the Rules tab leave every (id, active) unchanged;
- an unserved triggered stop stays armed;
- the Rules tab lists watch alerts and hides the orphaned stop;
- the evaluator quotes watch alerts (GOOGL fires at 85 ≤ 90) and deletes only the NVDA stop;
- the evaluator, not a read, resolves a dismissed rule.

**Smoke.** `scripts/smoke/smoke-pr109-reading-never-deletes.ps1 -Sha <sha>`. Against `b7d9234`: the rule set is
identical across reads (the live book has no orphaned rules left to delete); FAILs only on the missing
`rule_banner.uncarded`. **Ran against `887113f` (GitHub PR #10 merge): 6 PASS, 0 FAIL, 1 SKIP** (no watch alerts on
unheld tickers on the live book). **Wave B complete.**

**Pixel QA (owner, 2026-10-11):** PR 120 (Today's chance = Plan's) and PR 108 (Markets regime card) passed. PR 107's
07:00 digest check is due the morning of 2026-10-12.

---

## PR 108 — one regime acts, and the futures cross-check is measured right

**Plan v3, Wave B, second PR (D-a).**

**What was wrong, and the root cause.**
- **C11 (re-proven live 2026-10-11).** The futures "day change" used `chartPreviousClose`. With `range=5d` that is the
  close before the window, about four sessions back, and `previousClose` is always `None` on that endpoint. ES=F
  showed −0.18% on a +0.56% day, NQ=F −1.19% on +0.45%, and CL=F +2.69% on +0.39%. `_fetch_one` now uses the
  previous daily bar: the last non-null close before the last bar.
- **C10 / D-a.** Today's "Markets look risk-off" card read `cached_regime()`, the futures regime the code itself calls
  display-only, which had no TTL. The strategies gate on `engines/regime` (SPY's 200-day trend, realised-volatility
  percentile, breadth). New `regime_service`:
  - `regime_proxy()` moved here from the route, so services no longer import a route.
  - `refresh_regime_proxy()` refreshes at most hourly; `futures_warm` and the Markets route call it.
  - `cached_regime_proxy()` is cache-only and empty when cold or more than 6 h old. Today reads it, and a cold cache
    means no card rather than a guess.
  - `cached_regime()` (futures) gets a 30-minute TTL and ignores stale snapshots.
- **Two more futures readers on Today, found while fixing:**
  - The reconciled "Markets look risk-off — phase the rebalance in" heads-up read a futures-only `rationale` field, so
    it would have printed "risk-off ()".
  - The Today AI portfolio summary described the futures regime.

  Both now describe the strategies' regime (`regime_service.describe`).
- **A PR 105 sibling the guard couldn't see.** The AI summary's holdings `value_pct` was `price × qty` over an ILS
  NAV, so a USD holding read at about 27% of its real weight. It now goes through `fx.holding_value_ils`. The PR 105
  AST guard keys on `*ils*` names and this key is `value_pct`, so it slipped past.
- **Markets tab.** The regime card leads with "Market regime (what your strategies read)" plus its components; the
  futures reading is one labelled cross-check line. `sw.js` → `iw-v53`.
- **C16 `_OBJ_RETURN`: investigated, no change.** The hand-typed objective returns feed only the older "behind your
  goal" card. That card stands down whenever a fresh goal verdict exists (`goal_story_active`; it does on the live
  book), takes its headline from the measured return, and labels the objective-table lines as such.

**Why it was silent.**
- The futures numbers were plausible, just shifted by four sessions.
- No test compared the two regimes or pinned the risk card's source.

**Tests (all 5 fail on HEAD).** `tests/test_one_regime_measured_correctly.py`:
- the live 2026-10-06..09 bars give ES +0.56 / NQ +0.45 / CL +0.39 / VIX −3.7;
- futures risk-off with the proxy risk_on gives no card;
- the proxy risk_off gives the message, naming SPY's trend and the 91st percentile, never "()";
- a cold or old cache says nothing;
- the Markets route computes the proxy once per hour.

**Smoke.** `scripts/smoke/smoke-pr108-one-regime.ps1 -Sha <sha>`. Against `1661441` it FAILs, as it should: all three
futures signs are wrong against Yahoo's own previous bar, and the page and `sw.js` are unchanged. **Ran against
`b7d9234` (GitHub PR #9 merge): 10 PASS, 0 FAIL.** ES +0.56, NQ +0.45 and CL +0.39 all match Yahoo's previous bar;
the proxy reads risk_on 0.95, with no risk-off message on Today.

**QA (Pixel).** Markets tab: the regime card shows your strategies' regime with its components, and the futures
cross-check line under it. The tiles' day changes match a broker app.

---

## PR 107 — a job that fails says so

**Plan v3, Wave B, first PR.** Re-proven at HEAD (`8056cb8`) with the C4 probe: with a raise injected, or pointed at a
database with no tables, six jobs recorded `last_ok: True`.

**What was wrong, and the root cause.**
- **C4.** Six jobs caught every exception and returned their healthy-empty shape, so `JOB_STATE` and `/push/status`
  read green while nothing ran: `backtest_refresh`, `strategy_signals`, `push_evaluate`, `push_digest`,
  `futures_warm`, `market_refresh`. (`{"sent": 0}` is also what a quiet hour looks like.) All six now raise, and
  `_guarded` records the failure.
- **A seventh, found while fixing: `nav_snapshot`.** A per-user snapshot failure was logged and counted, and the job
  still reported success with `failed: 1` inside a green result. That is a lost day of history that nothing alerts
  on. It now commits the rows that worked and then raises.
- **Push fan-out.** One bad subscriber aborted the run for everyone after it, and the wrapper hid that too. Each
  subscriber now runs in its own `try`; failures are counted, the heartbeat records `failed=N`, and the run raises at
  the end.
- **L10.** A futures outage overwrote a good cache with an empty one, so the regime silently read "neutral". The last
  snapshot is now kept, marked `stale`, and the warm job raises `FuturesUnavailable`.
- **L11.** Boot called the futures warm and the market refresh directly on the startup thread, so boot waited for the
  slowest of 8 Yahoo fetches. Both are now primed with `next_run_time=now` through `_guarded`. Separately,
  `_http_text`'s timeout cap was not a cap: leaving its `with ThreadPoolExecutor` joined the hung worker (10 s against
  a nominal 6 s). It now uses a shared pool, so a hung worker is abandoned.

**Why it was silent.**
- The exceptions were caught inside the jobs, so the guard (`_guarded`) never saw them.
- No test ran these jobs (C5).

**Tests (fail on HEAD: 11 of 27).** `tests/test_every_job_says_when_it_fails.py` captures every `add_job` from
`start_scheduler()`. For every job it requires:
- a healthy run that records `last_ok: True`;
- an injected failure that records `last_ok: False`;
- a `FAIL_WITH` entry, so a new job without a failure test fails CI.

It also covers one failing push subscriber, the futures outage keeping the last snapshot, the HTTP cap, and boot not
waiting for the futures.

**Docs.** ARCHITECTURE §7 (the PR 107 rule replaces the known-defect note; job table). BACKLOG: C4, C5, L10, L11 closed.

**Smoke.** `scripts/smoke/smoke-pr107-jobs-fail-loudly.ps1 -Sha <sha>`. Against `8056cb8` it FAILs, as it should:
`market_refresh` hasn't run since boot, because it was not primed. **Ran against `1661441` (GitHub PR #8 merge): 10
PASS, 0 FAIL.** All 11 jobs registered; `market_refresh`, `futures_warm`, `price_refresh` and `standing_orders` ran at
boot; none failed. **Next morning:** confirm the 07:00 digest ran
green and was delivered (`recent_notifications`).

---

## PR 120 — one goal chance on every screen

**Found by the owner, 2026-10-11:** Today's health card said "35.3% chance of ₪82,692 in 5.2 years". At the same
moment the Plan tab said 16.3%, and 31% once the drawdown guard was paused.

**Root cause.** Three numbers answered one question, and PR 99's "the same figure everywhere it appears" missed the
health score:
- Today's **On track** (`plan_health`), which is half the health score.
- The Today **"Chance of hitting your goal"** row.
- The **assistant / digest context**, which called it "the ONLY probability".

All three read `/plan/projection`'s `probability_meets_target`: one lognormal over the whole book, with no protective
rules and no rebalancing. The Plan tab reads the goal model's path simulation with the rules as they are, and that
model counts active standing orders. Even with the rules out, the two engines differ by about 5 points (35.3% vs
30.1%).

**What.**
- On track, the Today chance row and the assistant/digest context all quote `goal_headline.p_goal_pct`, the Plan
  number. Live today that is 31%, against 30.1% without the rules.
- With no fresh goal verdict they fall back to the projection's figure, labelled "rough estimate: protective rules
  not simulated".
- The On track info tip now says where the number comes from.
- The projection chart keeps its own no-rules run, labelled as the chart's (unchanged since PR 99).
- `sw.js` → `iw-v52`.
- Also: the sleeve-funding refusal prints drift in points (106.1's "0.05 -> 0.05" hid a rise).

**What the owner sees.** Today's On track goes from 35 to 31 on the live book. The health score moves with it, since On
track is half of it.

**Tests (fail on HEAD).** `tests/test_one_goal_chance_on_every_screen.py` (3):
- with a verdict, health, projection and context agree;
- without one, the fallback is labelled;
- the Today row reads `goal_headline`.

**Smoke.** `scripts/smoke/smoke-pr120-one-goal-chance.ps1 -Sha <sha>`. Against `19cdfc0` it FAILs, as it should:
"Today On track 35 (no_deposits) vs Plan 31.0%"; the page row and `sw.js` are not updated. **Ran against `8056cb8`
(GitHub PR #7 merge): 6 PASS, 0 FAIL, 0 SKIP.** Today now says "31% chance of ₪82,692 in 5.2 years, with your
protective rules as they are (30.1% without them)". **Wave A complete.**

**QA (Pixel).**
- Today's health card and the Plan goal card show the same chance.
- The On track info tip reads correctly.

---

## PR 106.1 — the drift gate measures the whole book, cash included

**Incident: PR 106's smoke was red on `a303f9e`.** Production "Fund sleeves" (dry run) returned a refusal, "drift 0.02 ->
0.06", that named no class, and the smoke counted the refused plan's BND leg as a sale.

**Root cause, two bugs.**
- **Code.** `funding_drift` weighed invested holdings only, against a book target that includes a Cash slice (0.64%).
  On the live book that put Fixed Income at 12.5% (invested basis) instead of 12.11% of NAV (target 12.14%). So the
  refusal's "which class is under target" list came out empty. `/mix` and `rank_trim_candidates` both weigh against
  NAV; the gate now does too (cash is a class, and `from_cash_ils` spends it). Percentages in the refusal print as
  many digits as tell them apart ("12.11% held, 12.14% target").
- **Smoke.** Section 1 judged the sell legs of a refused plan, which never execute.

**Why it was silent.** The PR 106 tests used books with no cash, where the two bases agree.

**What the owner sees.** "Fund sleeves" on the live book now refuses instead of selling ₪377 of BND. The SOXL sleeve
is ₪957 short; ₪580 of cash is spendable. The refusal says it would have to sell Fixed Income (12.11% held, 12.14%
target), and adding cash funds it.

**Tests.** `test_the_gate_measures_the_whole_book_cash_included` reproduces the live book; it fails on `a303f9e`.

**Smoke.** The same script, fixed: a refused plan counts as selling nothing, and the refusal must name the class.
**Ran against `19cdfc0` (GitHub PR #6 merge): 6 PASS, 0 FAIL, 0 SKIP.** The refusal reads "...It would have to sell
Fixed Income (12.11% held, 12.14% target)". It printed the drift as "0.05 -> 0.05" (a real rise, 0.0496 -> 0.0504,
hidden by two decimals). PR 120 prints the drift in points.

---

## PR 106 — sleeve funding obeys the book target

**Plan v3, Wave A, second PR.** The owner chose to go ahead the same day rather than wait a night after PR 105.

**What was wrong, and the root cause.**
- **C1, live on the owner's book.** The production "Fund sleeves" dry run (`95cbd7e`, 2026-10-11) sold ₪377 of BND:
  Fixed Income was 12.11% held against a 12.14% book target, while Cash was the only class over. Re-proven on the
  probe book (Grow + `bal_6040` core + SOXL 10%: FI 20% held vs 32% target). Funding sold ₪37,100 of BND as "the most
  overweight class (20% vs 10%)".
  - **Root cause:** `_fund_sleeve`, `fund_plan`'s `_fits` and the solver's `_funding_preview` called `plan_funding`
    without `book_target`, so they measured against the objective table. The AST guard that should have caught it
    scanned two named files.
  - **Fix:** all three resolve `book_target(...)["target"]` once and pass it. The solver preview also passes the
    sleeves' `buying_class`.
  - **Result:** the probe book now sells V (Equities, 80% vs 58%), labelled as a same-class swap.
- **C2, no post-trade check.** `propose_funded_buy` simulates its trade; sleeve funding didn't (drift 0.44 → 0.64).
  - New `funding_service.funding_drift` simulates legs + sells against the book target.
  - `strategy_service._drift_gate` refuses when total drift rises and a sell comes from a class the legs don't buy.
    A same-class sell is a swap and passes.
  - The refusal names the class: "It would have to sell Fixed Income (20% held, 32% target), which your plan says
    you hold too little of."

**Why it was silent.**
- The funding guard listed `MODULES` by hand, so a new module opted out by existing. It now walks all of `app/`.
- The probe book's numbers looked plausible: "most overweight class" was true against the wrong table.

**Risk, as the plan said.** Some sleeves will now report "not enough fundable money" where they used to sell bonds.
That is the rule working.

**Docs made true.**
- DOMAIN §3 and §10 now say C1/C2 are closed. They also cover PR 105's closures, which that PR left stale.
- The CLAUDE.md funding rule no longer lists C1/C2 as open.
- BACKLOG: C1 and C2 closed.
- Plan: PR 120 moves to right after PR 106 (owner, 2026-10-11).

**Tests (fail on HEAD).**
- `tests/test_sleeve_funding_obeys_the_book_target.py` (3): fund-plan dry run, single sleeve + solver preview, and
  the drift gate.
- `tests/test_every_funding_call_passes_the_book_target.py` now walks all of `app/`.

**Smoke.** `scripts/smoke/smoke-pr106-sleeve-funding-target.ps1 -Sha <sha>`. Against `95cbd7e` it FAILs, as it should:
"sells BND (Fixed Income): 12.11% held vs 12.14% target, while Cash is over". **Against `a303f9e` (GitHub PR #5 merge):
4 PASS, 2 FAIL. That was an incident, fixed forward in PR 106.1** (below). Nothing harmful was live: the new gate
refused the trade, so nothing would have been sold.

---

## PR 105 — every share count is sized in the price's own currency

**Plan v3, Wave A, first PR.** Every fault was re-proven at HEAD (`9541472`) before it was fixed. The probes in
`docs/plans/evidence-2026-10-08/` hard-code the docs-overhaul worktree path, so they were re-run as copies pointed at
the main checkout.

**What was wrong, and the root cause.**
- **C2b, sleeve-drift trim (critical, latent).** "Trim about ₪18,550 of SOXL back to your 25% sleeve" sold 185 of 300
  shares (₪68,635). The trim was `int(_amount / current_price)`: shekels over a dollar price. Now it sells the share
  of the position the gap is (`qty × gap / actual`), the PR 102 form. Re-proven: 300 → 115 shares.
- **C3+, Replace book (critical, latent).** A ₪37,100 USD book became ₪137,641. `_replace_book` sized
  `weight × budget_ILS / price_USD`. It now sizes on the ILS price of the quote's major unit, and takes the market from
  the quote. **D-e was already enforced:** the server has refused replace while sleeves exist since C6
  (`strategy_service.py:1169`). The plan's "the server does not enforce it" was wrong; a test now pins the refusal.
- **C3, valuations.** The assistant context, the risk validation, the sector-hedge card, the fee scan and `/backtest`
  valued `qty × native price` as ILS. All five now go through `fx.holding_value_ils` / `position_value_ils`.
- **C3r + one more site: agorot.** `fx_rate("ILA")` is 1.0, so a `.TA` quote in agorot was read as shekels: 100×
  too few shares, and an agorot price stored on the row. This hit `_apply_redeploy_cash` (in the plan) **and** the
  sleeve-fund buy loop (`strategy_service.py:1105`, not in the plan). One helper, `fx.quote_in_ils`, now does both
  steps (major unit, then FX) for every buy path that quotes.
- **C15 / L-ledger: proven, and worse than inferred.** `POST /portfolio/add` (Holdings › add commodity by amount)
  called `upsert_positions`, an intake writer that **replaces** the row. "Add $1,000 of IAU" to 100 held shares left
  7.54 shares (₪18,550 → ₪3,710). It also took no cash and wrote no ledger row. It now goes through `buy_into_book`
  (blends), pays from cash, refuses when cash is short ("Add money to cash first"), and records a `holdings_add` Buy
  leg. **Owner's decision (2026-10-11):** buys come out of cash; money goes into cash first.
- **Found by the new trim measurement: a "Trim CASH" card.** The concentration trim ranked `exposure_ticker`, which
  includes the cash row, so a cash-heavy book got "sell about ₪13,225 of CASH (~13225 shares)". Cash is excluded now.

**Why it was silent.**
- `test_card_claims` skipped trims (`elif kind == "trim": pass`).
- The C3 sites pass on an all-ILS test book.
- No test priced a `.TA` buy.
- Nothing read the holding back after `/portfolio/add`.

**Ruled out.** VAPID / C2c / L6c (closed by the investigation). The live book: 0 actionable cards and no trim on
Today, so none of these fired on production in the window measured.

**Tests (all fail on HEAD).**
- `tests/test_every_share_count_is_sized_in_its_own_currency.py` (9 tests).
- `tests/test_card_claims.py` now prices every trim's shares from the book (`test_every_trim_sells_what_its_card_says`).
- **Class-closer:** `tests/test_no_native_value_is_called_shekels.py` walks all of `app/`. It fails on any
  `qty × price` stored under an `*ils*` name or key without an FX step, and it proves itself on a planted line.

**Smoke.** `scripts/smoke/smoke-pr105-own-currency.ps1 -Sha <sha>`. Dry run against `88bb5d4` (no `-Sha`): 5 PASS,
0 FAIL, 3 SKIP. **Ran against `95cbd7e` (GitHub PR #4 merge): 8 PASS, 0 FAIL, 1 SKIP.** The skip: no sized trim card on
Today. `/portfolio/add` refused "not enough cash" (₪730 in cash) and the book was untouched.

---

## PR 104d — every card threshold is a setting (Card-thresholds sheet)

**Decision (Eran, 2026-10-07).** *"I want every decision to be controlled and changeable through the app."*

**What.**
- `app/services/thresholds.py`: SPEC (label, what it decides, bounds) for ten thresholds; `defaults()` read from the
  constants that own them; `clean()` (known keys, bounds); `effective(plan)`; `use(plan)` sets the user's overrides
  in a context variable; `get(key, default)` reads them. Set once per API request (`acting_user`), per Today build,
  per goal solve / preview, and per user when rules are evaluated; worker threads inherit it. Nothing set = today's
  behaviour exactly.
- Readers: smallest tax card ₪50 and smallest trade card ₪100 / 0.5% (`headsup`); smallest funded trade ₪250 and the
  cash hard floor ₪150 (`funding_service.min_trade_ils()` / `cash_hard_floor_ils()`, and `MIN_TRADE_ILS` /
  `CASH_HARD_FLOOR_ILS` still resolve per call via module `__getattr__` for the ~80 importers); rebalance band ±3
  (`advanced_rules._band_default()`, "raise the cap to target + band"); take-profit slice 25%
  (`rules_service.house_take_profit_fraction()`, `TAKE_PROFIT_FRACTION` via `__getattr__`); gain line +25%;
  smallest goal-odds gain worth a card 5 pts (`frontier_service._material()`); the retune's guard levels 15/20%.
- `PUT /plan {"thresholds": {...}}` merges (null resets one), 422 on unknown / out of bounds; `/plan` returns
  `thresholds`, `thresholds_overrides`, `thresholds_defaults`, `thresholds_spec`.
- Plan > Goal: **Card thresholds** row → sheet: each value with what it decides, its default, Reset; Save (re-solves
  when the goal-odds gain or guard levels change). The ₪100-vs-₪250 conflict is visible as two settings he can align.
  `sw.js` → `iw-v51`.

**Tests.** `tests/test_every_threshold_is_a_setting.py` (4). **Smoke.** `scripts/smoke/smoke-pr104d-thresholds.ps1`. Ran 2eff74a: 4 PASS 1 FAIL -- the FAIL was the script (guard levels compared as text "15.0,20.0" vs "15,20"); fixed to compare numbers, 104d.1. Values are the defaults. Pixel QA: Today speed, risk dials, Edit buttons, thresholds sheet OK; Reset shows only on a value you saved (by design). **QA passed 2026-10-07** (104c, 104c.1, 104d: all checks incl. Reset and out-of-bounds); smoke 96427f5 5 PASS 0 FAIL. Execution plan v2 complete.

---

## PR 104d.0 — migration 0028 alone: the card thresholds as one settings object

**What.** `plans.thresholds` (JSON, nullable): OVERRIDES of the card thresholds — an absent key keeps today's default,
so NULL changes nothing and a threshold added later needs no migration. Add-only, guarded, mirrored into main.py's
startup DDL, model column added. Nothing reads it yet (PR 47). PR 104d reads it and adds the Card-thresholds sheet.

**Smoke.** `scripts/smoke/smoke-pr104d0-migration.ps1 -Sha <sha>` (schema `0028_plan_thresholds`).

**104c.2 on production (`c0be12e`):** Railway log "warm-up: built Today for 1 user(s)" at 10:48:29, right after
startup; the timed first request (17.2 s) raced it, the next 0.8 s.

---

## PR 104c.2 — Today is warm before you open it

**Measured on production right after 104c.1's deploy:** first `/recommendations` **22.2 s** (buy ideas 15.8 s — the
screener fetching a year of history per universe ticker — war room 3.5 s), the next one **0.9 s**. Eran's "very long
time to upload" was the cold cache, which every deploy empties and the history tier drops after an hour.

**What.** `app/services/warmup.py`: `warm_once()` builds Today for every user; `warm_loop()` runs it 20 s after start
and every 45 min (inside the history cache's hour), started from `lifespan` as a task in the APP's own event loop when
the scheduler is enabled (no cross-loop engine); cancelled on shutdown. Building Today sends no notification.

**Tests.** `tests/test_today_is_warm_before_you_open_it.py` (2) — runs the job (CLAUDE.md: a job nothing calls is a
job nobody has tested).

**104c.1's CI was red (run #296):** every test passed, but `tests/conftest.py` fails the run on UNREGISTERED SKIPS —
two `test_card_claims` tests skipped with "war room approved nothing on this book". 104c.1's war-room cache served
them a previous test's answer (same book, same prices, different mocked signals). Locally the "2 skipped" was read
as noise; it was the guard working. Fix: the cache lifetime is a setting, `war_room_cache_ttl_sec` (600 s), and the
test suite sets 0 (`WAR_ROOM_CACHE_TTL_SEC`); the cache test sets production's value itself.

---

## PR 104c.1 — speed, what the replay still simulates, Edit buttons, the two "What would it take?" answers

**From Eran's QA of 104c (2026-10-07).**
1. *"It takes very long time for the goals, holdings prices, and cards to upload."* Measured on production: each
   endpoint 0.2–1 s; `/recommendations` 3.5 s of which **war_room 2.8 s**; and Today fetched its six blocks **one after
   another**, so the goal and projection waited behind the card build. Now every Today request starts at once
   (`loadToday` → `TP`), and the war-room payload is cached per book + prices for 10 min
   (`_war_room_payload_cached`; only grounded answers kept).
2. *"I paused all the relevant rules but it still shows a gap."* Still armed on the sleeves: QQQ stop-loss + trailing
   stop, SOXL and QQQ **take-profits** (simulated, but not shown — My mix listed only stops and the guard), guard 30%.
   Take-profits on sleeve tickers now appear in My mix and the Edit-rules sheet; the goal card names what is still
   armed with an "edit rules" link.
3. Edit buttons: centred against their row, highlighted (green, bold).
4. *"It recommends 60% but the best split is 40+15 = 55."* Two questions: the Target answer keeps today's sleeve
   RATIO (40:19) and scales; the split sizes each sleeve alone. When the split reaches the typed excess with LESS in
   sleeves it now says so and says to prefer it; the "normally smaller" line shows only when true.
5. *"My portfolio didn't change in the last couple of days."* Checked: every holding priced at the 2026-10-06 close;
   nav history has every trading day (24,558 → 23,479 → 24,166 → 24,611 → 24,637 → 24,618, 25 Sep → 6 Oct) — it moved
   ~₪1,200 over the week, ~₪25 over the last three days. Nothing stuck.

**Tests.** `tests/test_today_loads_fast.py` (2); layout test (+take_profit). `sw.js` → `iw-v50`.
Also: `frontier_service.latest()` orders verdicts by `created_at`, which came from the database default (SQLite:
whole seconds), so two verdicts written in one second came back in either order — the cause of the order-dependent
`test_a_rules_change_dates_the_goal` failure (CI 104c.0, local 104c.1), and in the app a re-solve right after another
could serve the older one. `record()` now stamps `created_at` itself, to the microsecond.

---

## PR 104c — the Risk-dials sheet; the crash-risk limit and on-track bar are settings (step c of 3)

**What.**
- `settings_of(plan)` returns `halve_limit_pct` (plan's, else 1.0) and `goal_confidence` (`on_track_pct`/100, else
  0.70) with their sources; `compute_for` / `preview` pass them to `goal_solve(p_star=, halve_floor_pct=)` and
  `core_solve(p_star=)`; `_tune_protection(floor=)` picks the best setup within the plan's limit.
  `_goal_verdict_current` treats a verdict solved under other limits as not current (Save re-solves).
  `PUT /plan` accepts `halve_limit_pct` 0.1–100 and `on_track_pct` 50–95.
- Plan > Goal > **Risk dials → Edit**: one sheet — objective, risk tolerance, Kelly ceiling, trust in backtests,
  **crash-risk limit**, **on track at**, and under More: investment flavor, target yield. Preview from the last
  solve's measured setups ("best protection within X%: … chance, … halving"; on/off track at the bar). Save → PUT
  /plan → re-solve → reload. The goal card's "best within your limit" line links to it ("change the limit").
- **Removed:** the old "Goal settings" form — "Years invested" (the deadline governs), the second objective/risk
  control, the Kelly/trust selects, the single "Save my plan" — and its functions (`savePlan`, `recalcTarget`,
  `renderDials`, `renderGoal`, `renderRoi`, `renderPlanNote`). `loadPlan` is a fetch. `sw.js` → `iw-v49`.
- `HALVE_FLOOR_PCT` moved beside `GOAL_CONFIDENCE`; both are now only the defaults of an unset plan.

**Tests.** `tests/test_every_limit_is_a_setting.py` (4). **Smoke.** `scripts/smoke/smoke-pr104c-dials.ps1` (read-only).
**Next (104d, Eran 2026-10-07 "every decision"):** a Card-thresholds sheet for the remaining constants — minimum card
sizes (₪50 tax / ₪100 or 0.5%), the ₪250 funding minimum (conflicts with D4), cash hard floor ₪150, rebalance band ±3,
take-profit slice 25%, the +25% gain line, material odds gain 5 pts, the retune's guard levels 15/20%.

---

## PR 104c.0 — migration 0027 alone: the goal's two limits become plan settings

**Decision (Eran, 2026-10-07).** *"where did I set this limit of 1% maximum chance?"* — nowhere in the app: it was his
chat answer on 2026-10-05 ("Guard 15%: 0.8% / 22.7%"), coded as `frontier_service.HALVE_FLOOR_PCT = 1.0`. Then:
*"add the 1% dial as part of PR104c. I want every decision to be controlled and changeable through the app."*
(Memory: every decision is a setting.)

**What.** `plans.halve_limit_pct` (crash-risk limit, %, NULL = 1.0) and `plans.on_track_pct` (the goal's on-track
bar, %, NULL = 70). Add-only, nullable, guarded like 0021–0026, mirrored into main.py's startup DDL, model columns
added. **Nothing reads them yet** (PR 47: a migration ships alone). PR 104c reads them.

**Smoke.** `scripts/smoke/smoke-pr104c0-migration.ps1 -Sha <sha>` (schema `0027_plan_goal_limits`).

**PR 104b: Pixel QA passed** (Eran, 2026-10-07).

---

## PR 104b — the Edit-goal and Edit-rules sheets, with a live odds preview (step b of 3)

**What (canvas AC-EditGoal, AC-Protect; Eran's QA: "my mix has the rules but I can't change it").**
- `POST /plan/frontier/preview` → `frontier_service.preview()`: the goal's odds as they WOULD be — another ROI,
  deadline or monthly deposit (`_goal_context(roi_pct=, target_date=, monthly=)`, the same goal formula; a deposit
  raises a "ROI on every shekel" goal by the extra deposits compounded, PR 99's rule), or other rules armed / the guard
  elsewhere (`_rules_context(armed=, guard_pct=)`). Same model and rules replay as the nightly solve, `goal_solve(
  light=True)`: no resampling, no retune search, no stress case, no lever search. **Stores nothing.**
- Plan > Goal: rows **Target / Monthly deposit / Risk dials** with Edit. **Edit your goal** sheet: ROI, deadline,
  monthly deposit (one standing order is created / changed / removed; with several the field is read-only → Cash
  tab), "If you save this": goal amount, chance with your rules, with none (and its halving), halving. Save → PUT /plan
  (+ standing order) → re-solve → reload.
- Plan > My mix: **Edit rules** → sheet: each stop on the sleeves' tickers (ticked = armed) and the drawdown guard
  (level + armed), pre-filled with the app's suggested retune when there is one; live preview; Save → toggles /
  PATCHes the rules → re-solve → reload.
- One bottom sheet for the app (`openSheet` / `closeSheet`), preview debounced 650 ms with "Updating…". `sw.js` →
  `iw-v48`. Risk dials still edit in the old form until step (c).
- From Eran's QA (2026-10-07): a restored "BND … protect it with a stop" card offered a second stop (64.42) while one
  at 64.46 was armed. `_holding_review_recs(armed_stops=)`: no stop card for a holding with a stop-loss armed.
  (His other question — "I restored the last rule, why is it still 16.7% and not 28.7%?": stops on BND/GOOGL/VTI/
  UGA/USO are not simulated by the goal model; the gap 16.7% → 29.7% is the drawdown guard alone, which holds halving
  at 0.5% vs 71% under his 1% limit. Restore brings a card back, it does not undo a rule.)

**Tests.** `tests/test_the_sheets_preview_the_odds.py` (2): later deadline / deposit raise the goal, lower ROI lowers
it, rules-as-they-would-be change the replay, nothing is stored or changed; `test_plan_tab_layout` (+1).
**Smoke.** `scripts/smoke/smoke-pr104b-sheets.ps1` (read-only).

---

## PR 104a.2 — a paused rule is a decision; Ignore works on Plan; the review says what to do

**From Eran's QA of 104a.1 (2026-10-06).**
1. *Holding review "if you have recommendation, why it's not a recommendation card?"* — BND "Exit", GOOGL/VTI
   "Trim" were TREND states; on each one's own history selling on the signal did worse than holding, so by design no
   sell card. The row label now says the action: **Protect** / **Sell** when a card exists ("→ the card on Today: …"),
   **Hold** with "the trend says exit, but selling on it did worse than holding…" otherwise.
2. *"After I stopped them I've got a new recommendation card"* ("4 protective rules ready to arm" for SOXL, QQQ) —
   `suggest_rules_for_holdings` counted only ARMED rules, so the stops the retune had paused were offered back. Now
   every rule counts, armed or paused: a paused or spent rule is a decision about that holding.
3. *Ignore did nothing* on the Plan tab — `snooze` reloaded Today; now `_reloadView`.
4. *"I accepted removing all the rules to reach 28.7%"* — the retune paused the five stops and KEPT the guard at 15%,
   which holds halving at 0.5% (71% with no rules) under his 1% limit (PR 97). Goal card now says it is the best
   chance within that limit; the My mix chip reads "cost your goal 13 pts, keep halving at 0.5% (not 71%)".

**Tests.** `test_trading_rule_suggestions::test_a_paused_rule_is_a_decision_not_a_gap`. `sw.js` → `iw-v47`.

---

## PR 104a.1 — Accept on the retune re-solves the goal; the rules trade-off in words

**From Eran's QA of 104a (2026-10-06).** (1) *"with my rules it's only 0.7% chance but without them it 28.7%... the
rules should help me"* — the screen showed only half the trade-off. Live: rules on 0.7% chance / **0% halving** /
worst 5% ₪22,601; rules off 28.7% / **69.2% halving** / worst 5% ₪6,663. The stops buy crash protection with growth:
after a stop fires the sleeve sits out until its price falls back below the sale price (SOXL out 64% of the time).
(2) *"I clicked on the accept button but nothing happened"* — **it had worked** (all five stops paused, guard
10% → 15%), but the verdict on screen was solved on the OLD rules and nothing re-solved it, so the 0.7%, "cost your
goal 28 pts" and the card itself all stayed. (3) No Hebrew check in QA lists unless asked.

**What.**
- `frontier_service.rules_fingerprint()` (armed stops + guard: id, level, active) is stored on every verdict
  (`rules_fp`); `_goal_verdict_current` treats a verdict whose rules moved as not current (goal cards and
  `book_expectation` stand down; `goal_story_active` deliberately still owns the story so the older behind-goal card
  does not return with a second number). Old verdicts without `rules_fp` are judged as before.
- Accept on `frontier_rules` re-solves at once (`compute_for(source="rules_changed")`) and says the new chance;
  the button shows "Applying…" meanwhile.
- Plan > Goal: the chip reads "28.7% without them, but a 69% chance the book halves" with one line on why; "needs
  26%/yr · expected 18%" rounded; when the odds are out of date the card says so with **Re-solve now**.
- The protection summary names sleeves by what they hold ("SOXL and QQQ"), per the plan's example. `sw.js` → `iw-v46`.

**Tests.** `tests/test_a_rules_change_dates_the_goal.py` (3).

---

## PR 104a — the Plan tab: Goal · My mix · Explore (step a of 3)

**Decision (Eran, 2026-10-06; Execution plan v2.2).** D6 = **A + C**: A's three sub-tabs, each built the C way
(status rows + edit sheets with a live odds preview), protective rules in My mix. D5 = "What would it take?" folded
at the foot of Explore. Reference screens: canvas "InvestWise Plan tab options", row "Suggested: A's sub-tabs, built
the C way…" (AC-Goal, AC-Mix, AC-Explore, AC-EditGoal, AC-Protect). Built in three steps, each shipped and
Pixel-QA'd: **(a) the sub-tabs with today's blocks moved in** ← this; (b) the Goal and Rules sheets; (c) the
risk-dials sheet and the removals ("Years invested", the duplicate objective/risk).

**Root cause of the old screen (from the canvas).** One long scroll: objective + risk twice with two save paths;
"Years invested" governs nothing (the deadline does); risk set in five places; two solvers side by side; browsing
the catalog in the middle of managing the book; one Save at the bottom.

**What.**
- `#view-plan` → a Goal · My mix · Explore switch (`planTab`, remembered per device).
- **Goal:** summary card — goal by deadline, progress, the one headline chance (with your rules; "without them"
  and "needs X%/yr · expected Y%" chips; on/off-track chip); "Ways to close the gap" = the goal cards (Accept works
  in place, `_reloadView`), with the retune as a one-line link to My mix; the old goal form below as "Goal settings"
  until (b)/(c) replace it.
- **My mix:** held vs target for each sleeve, the core, commodities and cash (core target = 100 − sleeves −
  commodities − cash, as `book_target` composes it); "… pts to fund"; Fund all sleeves / Change core (the core
  picker opens in place); **Your protective rules**: stops (profit-protect, trailing, stop-loss) on the sleeves'
  tickers and the drawdown guard, "cost your goal N pts" from the verdict's rules replay, the Retune card; link to
  Holdings › Rules for take-profits, caps and buy-back holds.
- **Explore:** the strategy catalog; "What would it take?" in a closed fold at the bottom.
- The core's own objective/risk selector is gone from the sleeves panel (it duplicated Goal settings). `sw.js` →
  `iw-v45`.

**Tests.** `tests/test_plan_tab_layout.py` (4): the sub-tabs, which block is in which, D5's fold, the rules filter.
**Smoke.** `scripts/smoke/smoke-pr104a-plan-tabs.ps1 -Sha <serving sha>`. **QA.** `qa/QA-2026-10-06-pr104a-plan-tabs.md`
(8 checks, Pixel 9).

---

## PR 103 — shorter cards

**Root cause.** Every goal card opened with the same ~150-word goal paragraph (goal, paths, market, stress case,
rules replay, core spread, money/time) before its own claim, and every card rendered Why, the action, Impact and a
How list at the same weight. The card's ask was the fourth thing on it.

**What (approved; word limits are guidelines).**
- One shape: **title with its number → one or two plain sentences → "Accept will…" → before → after chips →
  Details**. `summary` is the card's own first two sentences (`first_sentences`) unless the card writes one; the
  full why, impact, steps, funding legs and audit trail sit behind *Details*. Warnings, "it is back because…" and
  a decision card's options stay in view. The "app can do this / guidance" tag is gone (PR 102: every card can).
- Goal cards: `goal_summary` on the payload, rendered once as **"Your goal"** above the goal group; each goal card
  keeps its own claim (`summary`), what Accept does, and `chips` (`frontier_service._chip`). The protection card
  follows the plan's example: *"Retune your protection: goal odds 1% -> 16%"*, *"After a stop fires, the app keeps
  … out … That costs your goal 28 points."*, chips odds / halving / median / worst 5%. `sw.js` → `iw-v43`.

**PR 103.1 (same day, from production `4d53052`).** Three cards still missed the shape: the protection card's
Accept listed all five rules with their levels (99 words) — now *"Accept will pause 5 stop rules and move the
drawdown guard 10% -> 15%. Nothing is bought or sold; one tap re-arms them."*, the list stays under Details; the
"Your goal" box was the whole 227-word goal paragraph — now `goal_summary_head` (goal, the one headline chance,
the money/time line) with the rest behind *More*; the BND stop card's body was raw moving-average figures — now
*"BND's moving averages point down on both horizons and it is 6.8% below its 52-week high."* `sw.js` → `iw-v44`.
**PR 103 / 103.1 CI red on `test-postgres` only — and it was a real bug (PR 103.2).** First read as test
pollution (wrong; 103.1's sleeve-clearing changed nothing). The failing line was the protection summary's sleeve
ORDER: Postgres JSONB returns object keys sorted, and the rules replay's per-sleeve figures (`pct_time_out`, …) were
paired with sleeves by zipping `rules["sleeves"]` — an object — against those lists. On production (Postgres) the
"time out of the market" figures could sit under the wrong sleeve since PR 95. SQLite keeps insertion order, so
`test` was green. Fix: the verdict stores `rules.sleeve_ids` (a list); `rule_sleeve_order()` reads it, falling back
to the verdict's own sleeve rows (a list, order intact) for verdicts stored before. Test reproduces the sorted keys.

**Guard.** `tests/test_cards_are_short.py` (4): flags a card whose title + summary + Accept passes ~2× the example
(150 words); today's longest on the adversarial books is 87. **Smoke.** `scripts/smoke/smoke-pr103-short-cards.ps1`.

**PR 102 on production (`0d5d006`, smoke checks from Python).** 4 cards, all with an Accept: retune protection
(`frontier_rules`), target 6.2%/yr (`set_plan`), core back to Classic 60/40 ₪129 (`core_to_basket`), protect BND
with a stop (`create_rules`); 1 heads-up ("QQQ is closing on your take-profit"); the ₪5 tax harvest dropped; no
trade card under the ₪124 floor; banner clean. CI `37484402108` green on all three jobs.

---

## PR 102 — every card can be acted on; the rest is a heads-up

**Root cause.** Twelve card types gave advice with no Accept (`apply: none`); their only button was "Mark as done",
which cleared the card with nothing having happened, and `moved_on` then re-fired some of them. "Card" meant two
different things — an ask, and a piece of knowledge — with one shape and one button.

**Decisions (Eran).** D2: heads-ups at the bottom of Today, above the Holding review. D3: the near-cap heads-up
offers "raise the cap to target + band". D4: ₪50 floor for tax harvests; ₪100 or 0.5% of the book for the rest
(read as whichever is larger).

**What.**
- `app/services/headsup.py`: `split(recs)` → cards (Accept does something, big enough), heads-ups (one line: the
  card's own first sentence — no new text), dropped (row 9 "Put idle cash to work"; tax harvests saving < ₪50).
  A trade card under `max(₪100, 0.5% × NAV)` becomes a heads-up; a triggered rule's card is exempt.
- Response: `headsups`, `headsups_dropped`; heads-ups count as carded for the rule banner (a triggered rule shown
  as a heads-up is never healed). `POST /recommendations/headsups/{id}/dismiss` stores the id for good.
- Row 1: the plan-cap "Trim {ticker}" card (guidance-only since 09-22) now sells the excess — and its share count
  was `int(trim_ils / price)`, shekels over a native price (a USD ticker came out too many shares by the USD/ILS rate, ~3×); it is now
  the position's own fraction, qty × (w − cap) / w.
- Row 6: over its plan target the gain card's Accept trims the excess (`trim`, every row). Row 7 / D3: near-cap →
  heads-up with an `edit_rule` act to target + `DEFAULTS["rebalance_band"]` (3) when |cap − target| ≤ band; Accept
  finds a heads-up's act. Rows 2, 3, 5, 10, 11 and the fundamentals verdicts (row 4 — no measurement backs them,
  the bar is "only when measured") and sector concentration (row 8 — no funded swap exists yet) → heads-ups.
- Today: collapsed "Heads-up · N"; swipe left or ✕ dismisses; no Mark as done. `sw.js` → `iw-v42`.

A heads-up keeps its source card under `card` (audit trail, numbers); tests read cards through
`tests/_cards.all_cards()`.

**Guard.** `tests/test_every_card_can_be_acted_on.py` (5): walks the real card set for every adversarial book
and fails on any card whose kind `apply_recommendation` does not handle. **Smoke.**
`scripts/smoke/smoke-pr102-actionable.ps1 -Sha <sha>` (read-only).

**PR 100 on production (`63f2d42`, smoke checks from Python).** No card carries boilerplate why/impact; the SOXL
gain card reads "SOXL +36% since you bought: ₪2,184 of gain" (hidden — marked done 10-02 — and, with no trade
asked, no longer re-fired with a false "to move"); the QQQ near-cap card likewise; chart payload You 54.71% / SPY
15.37%; history recorded since 2026-08-16, one gap 08-17 → 08-24. CI `37380897108` green on all three jobs.

---

## PR 100 — every number says what it measures

**Eran's rule for this PR:** don't make the cards more complicated — no card gets a new sentence; each fix deletes
a false one or replaces it with a shorter true one.

**Root causes.**
- **F4, the SOXL card.** `_ensure_why_impact` filled any missing why/impact from a per-dimension table, falling
  back to *"Moves your portfolio closer to your plan."* The gain card ("SOXL is up 36% — decide what to do") has
  dimension `holding` and no impact of its own, so it claimed a trim moved him toward his plan while SOXL sat at
  33% of a 40% target and its buy-back rule said the plan wanted ₪1,673 **more**.
- **F5, "still ₪187 / ₪2,155 to move".** `moved_on(done=True)` read `est_amount` as an ask on every card; the
  near-cap card carries its headroom and the gain card its unrealised gain.
- **F6, "(vs SPY +38.95%)".** The caption printed the **excess** where a reader sees SPY's return (SPY did +15.3%).
- **F7, account history.** "Recorded every trading day" printed beside "Not recorded 17–24 Aug", 4-decimal
  percentages, and a 13-date list inline.

**What.**
- No generic why/impact: a card without its own says nothing (`why`/`impact` = ""); the audit trail is unchanged.
- `_profit_recs(targets=)` (from `plan_ticker_targets`): a gain card **under or at its plan target** (or with no
  target known) is one line — *"SOXL +36% since you bought: ₪2,155 of gain"* / *"Your take-profit at 183.17 covers
  it."* — no trim suggestion. **Over target** it is *"Trim SOXL to target: 48% of your book vs 40%"*, sized on
  the excess (≥ `MIN_TRADE_ILS`); Accept for it comes in PR 102.
- `moved_on`: a card whose `apply.kind` is `none` never says money is still to move.
- Benchmark chart: *"You +54.5% · SPY +15.3%"* (and CAGR the same way); title *"If you had held today's holdings
  for the past year"* (span from the data). Account history: *"Recorded since 16 Aug (one gap: 17–24 Aug)"*,
  2 decimals on screen, change dates and multiple gaps behind *Details*. `sw.js` → `iw-v41`.

**Smoke.** `scripts/smoke/smoke-pr100-true-labels.ps1 -Sha <sha>` (read-only; UI copy by eye). **Tests.**
`tests/test_every_number_says_what_it_measures.py` (5); `test_the_app_says_when_to_take_profits` updated.

**PR 99.1** (`5ff69b0`): live; CI green on all three jobs; `btm_vol_target_tqqq` re-measured → `yahoo:total_return`,
benchmark SPY; goal cards back (views fresh).

---

## PR 99.1 — a stale measurement silences the goal

**Found on production right after PR 99 (`ccb33aa`).** PR 101's a5 → a6 bump made every sleeve backtest stale
until 03:30; the goal solve dropped both sleeve views (`ENGINE_VERSION_MOVED`), fell back to priors, read the book
at **0.19%/yr** (17.9% the day before) and the goal cards offered **"Set a target this book can reach: −7%/yr"**.
PR 101 promised "no card acts on an out-of-date number"; the goal cards were the one place that did not check.
Fixed on prod at once by `POST /strategies/backtests/refresh` (22 rows re-measured in 5 s, a measurement only).

**What.**
- `frontier_service.stale_views(v)`: a verdict with a sleeve view unused for `ENGINE_VERSION_MOVED` speaks for
  nothing — `_goal_cards` returns none and `book_expectation` returns None (screens fall back to the measured
  blend, labelled). `goal_story_active` is unchanged, so the older behind-goal card stays down: the goal goes quiet.
- A target lever at or below 0%/yr is never offered (card or "aim for" text).
- `backtest_service.measure`: an opted-in regime-gated headline is a fresh dict and lost `return_basis` **and**
  `benchmark_ticker` — `btm_vol_target_tqqq` was stored `yahoo:price_only` on dividend-adjusted data. Both carried.

**Tests.** `tests/test_a_stale_measurement_silences_the_goal.py` (4).

**PR 99 on production (`ccb33aa`, after the re-measure; smoke checks run from Python).** Headline **0.4% with your
rules (29.7% without)** — the same figure on the goal cards and the projection caption (its own no-rules run:
38.3%). Model 39.1%, history 34.0%. Two goal cards: "Retune your protection: odds 0.4% → 16.8%, halving 0% → 0.4%"
and "Set a target this book can reach: 6.1%/yr" (sized after the retune: rules-path amount 27,585 < model 32,774 /
history 32,613; v1's 9% was the no-rules figure). No deposit or deadline card; the money-and-time line shows (asks
30%/yr, book ~18.5%). Re-derived: v1's "+680/month" was 89.9% with the goal frozen and **32.2%** on the goal after
Accept (171,255). Holdings unchanged. CI `37374139627` green on all three jobs, `test-postgres` included (covers
PR 101 too).

---

## PR 99 — the goal cards only promise what Accept delivers

**Root cause.** The goal is *30%/yr on every shekel put in* (Eran, 09-28), so a deposit or a later deadline makes
the goal itself bigger. `goal_solve`'s levers (~604-650) solved `P(W >= target)` with `target` frozen: "add
680/month → 70%" and "move the deadline to 2039 → 70%" were true of a goal Accept would no longer have (real chance
after Accept 30.4% / 26.9%, below today's 38.1%). Silent because the engine reproduced the cards' own 70.2% —
the arithmetic was right, the question was wrong.

**Decisions (Eran, 2026-10-05).** D1 (b): size the suggestions on the book after "Retune your protection".
Headline = chance with the rules as they work, **rules-off from the same replay** beside it; projection caption
quotes it; no rules replay → lower of model and history. Deposit lever grows the goal by the **added** deposit only.

**What.**
- `goal_solve(goal_roi=)` (`_goal_context` passes `annual_roi(plan)` for a contributions-derived goal; a fixed
  amount passes None and nothing grows). Deposit lever: goal after Accept = target + `_fv_monthly(extra, roi,
  years)` (month-end deposits through `goal_service.compound`'s convention); deadline lever: target × (1+roi)^e.
  Each must reach 70% on the model **and** history **and** the rules replay it is sized on, else it is not offered.
- `verdict.headline` `{p_goal_pct, without_rules_pct, basis}`; `levers.sized_on` (`after_retune` /
  `rules_as_they_are` / `no_rules`) and `sized_p_goal_pct` gate the lever cards; the target lever's amount is the
  smallest of model / history / rules (`paths.rules_stats` now returns `amount_at_confidence`).
- `levers.money_time_note` when neither lever exists and the book earns less than the goal asks → one line on the
  goal summary: *"Adding money or time doesn't help this goal: it asks 30%/yr on every shekel, and the book is
  expected to make about 18%."*
- Cards: "Your chance today: X% with your protective rules as they work (Y% without them)" replaces the three
  separate chance figures; deposit / deadline cards state the goal after Accept. `/plan/projection` carries
  `goal_headline`; the caption quotes it and labels the chart's own figure as the no-rules run. `sw.js` → `iw-v40`.

**Check vs the plan's evidence.** `_fv_monthly`: 2039 deadline → 674,544 (plan 674,543); +680/month → 171,255
(plan 173,172, which counts the first deposit now rather than at month end).

**Smoke.** `scripts/smoke/smoke-pr99-goal-levers.ps1 -Sha <sha>`. **Tests.**
`tests/test_goal_cards_promise_what_accept_delivers.py` (5); `test_frontier_goal` / `test_rules_paths` /
`test_one_basis` updated to the new wording and fields.

**PR 101 on production (`95f0e5c`, verified 2026-10-05 with the smoke's checks run from Python):** all 7 holdings
re-measured on engine a6, every one `total_return`; BND hold +1.23%/yr vs signals 0.97 / 1.05 → no edge; no "Sell
… BND" card (BND now shows the no-edge "protect it with a stop" card — PR 102 sorts that into a Heads-up); USO's
short signal still beats holding. CI: lint + test green; `test-postgres` not acquired by a hosted runner (infra),
re-run requested.

---

## PR 101 — backtest history includes dividends (total return)

**Root cause.** Every backtest (holdings review, sleeves' measured returns, core baskets, the goal model's inputs,
both solvers, walk-forward) fetched ten years through `backtest_service._fetch` → `guarded_history`, which reads
Yahoo's `indicators.quote.close`: the plain close, dividends left out. A fund that pays out its return looks like
it slowly loses value. BND measured **−1.76%/yr** held over 2016-10 → 2026-10, so the 20/50 sell signal (0.05%/yr)
"beat holding" and Today showed **"Sell all of BND"**. Silent because nothing compared a measured CAGR with a
total-return figure; every number moved together.

**What.**
- `get_total_return_history` on the provider (Yahoo: `indicators.adjclose`, same request) and
  `registry.guarded_total_return_history` → `(rows, basis)`, its own cache key (`histtr:`). No adjusted series (FMP,
  builtin, or Yahoo omits it) → plain closes, basis **`price_only`** (Eran: fall back, labelled).
- `_fetch(basis=)` records each ticker's basis; strategy and core rows store `return_basis` /
  `price_only_tickers` and `data_source = <provider>:<basis>`; holdings-review measurements carry `return_basis`.
  A price-only figure says "price history, dividends not included" on the card and in the Holding review list.
- `ENGINE_VERSION` a5 → **a6**: every stored measurement is stale until re-measured (nightly, or the refresh
  endpoints), and no card acts on a stale one.
- **Unchanged (Eran):** live prices, charts, the performance/benchmark chart (price-only, relabelled in PR 100),
  stops / take-profits / 200-day trend rules, screener, signal service — they keep `guarded_history`. `sw.js` → `iw-v39`.

**Measured on live Yahoo** (this code, 2016-10-06 → 2026-10-05): BND hold **+1.23%/yr**, 50/200 1.05%, 20/50 0.97%
→ no edge, the sell card goes. SPY hold 15.54%. USO short signal still beats holding (8.78% vs 4.75%).

**Smoke.** `scripts/smoke/smoke-pr101-total-return.ps1 -Sha <sha>` (re-measures the holdings review and one
strategy; executes nothing). **Tests.** `tests/test_backtests_include_dividends.py` (6); fake `_fetch`s take `basis`.

---

## PR 98 — one return basis across the app

**Why.** The Plan screen's "expected compound return ~22.5%/yr vs your 30% target", the projection chart and the
risk report's goal probability all read the measured backtest blend (`book_assumptions`). The goal cards read the
goal model (blended confidence, today's volatility): ~17.9%/yr at 50% vol on the live book. Two stories about the
same book on two screens.

**What.**
- `frontier_service.book_expectation()`: when a fresh goal verdict speaks for this plan and book (same freshness
  test as the cards), its own figures for the book as it stands become **the** expected return and volatility, with
  basis `frontier` (confidence, regime, recent weight, age).
- `routes/plan._measured()`, which feeds `/plan`, `/plan/projection` and the behind-goal card, uses it and keeps the
  measured blend alongside (`portfolio_expected_roi_pct_measured`).
- `portfolio_risk_service` uses it for the risk report's goal projection.
- The Plan screen labels the basis ("From your goal model — the same numbers as your goal cards …") and shows the
  measured figure beside it. With no current verdict, every screen is exactly as before. `sw.js` → `iw-v38`.

**Smoke.** `scripts/smoke/smoke-pr98-one-basis.ps1 -Sha <sha>`. **Tests.** `tests/test_one_basis.py` (3).

---

## PR 97 — the goal reacts to today's market

**Decisions (Eran, 2026-10-05).** Crash-risk limit **1%** (*"Guard 15%: 0.8% / 22.7%"*), so the protection card now
proposes pausing the SOXL/QQQ exits with the guard at **15%**. Then *"continue to PR97"*: faster reactions.

**What.**
- **Recent volatility** (`frontier_inputs.vol_scale` / `ewma_vol`): each asset's volatility is blended between its
  10-year level and an exponentially weighted recent one (63-day half-life); correlations stay the long run's. The
  weight follows the app's **price-derived regime** (`engines/regime.latest`, the same function the backtests gate
  on): risk-on 40%, neutral 50%, **risk-off 70%**. Applied to the covariance (`estimate(vol_scale=)`), to the
  historical paths and rules replay (`paths.recentre(vol_scale=)`), and to `/plan/frontier/inputs` (so the smoke's
  re-derivation still matches).
- **The market on every goal card**: regime, trend, volatility percentile, the recent weight, and the asset whose
  volatility moved most against its long-run level.
- **Re-solve when a rule fires**: `rules_service.evaluate_all` (the 30-minute price job) calls
  `frontier_service.resolve_after_rule` when a protective rule newly fires (stops, profit-protect, take-profit,
  buy-back, trend break, drawdown guard, strategy signal). At most once an hour; a failure never costs the rule
  evaluation.

**Smoke.** `scripts/smoke/smoke-pr97-faster-reactions.ps1 -Sha <sha>`. The rule-fired re-solve cannot be triggered
from a smoke without a real firing; it is covered by a test that runs the rules pass. **Tests.**
`tests/test_faster_reactions.py` (10) + PR 96 tuning test updated for the 1% limit.

---

## PR 96 — protection tuned for the goal (actionable)

**Why.** PR 95 measured the 28 armed rules on the goal's paths. As executed, they cut the odds from 32.9% to 0.5%:
the sleeve stops' buy-back holds keep SOXL out 62% of days, while the drawdown guard alone does nearly all the
crash protection. Eran, 2026-10-05: "continue to PR96".

**What.**
- `frontier_service._tune_protection`, on the same replay: {retire the sleeve exits (trailing / activated
  profit-protect / hard stop on sleeve tickers), keep them} × {guard at today's level, 15%, 20%}. Take-profits stay
  (measured neutral).
- **The pick: the best goal odds among setups with ≤ 5% chance of halving** (`HALVE_FLOOR_PCT`). Card only if
  ≥ +5 points.
- Card `frontier_rules` ("Retune your protection for your goal") names every rule it pauses and the guard change.
  `apply_rules_tuning` **pauses** (active=False, one tap re-arms on the Rules tab, never deleted), re-levels the guard
  via `update_rule` (or arms one via `create_rule`), and refuses untouched if the rule set moved since the solve.
  Nothing is bought or sold.
- `rules_spec` carries the rule ids.

**Expected on the live book (PR 95's measurement, blended):** retire the SOXL/QQQ exits and set the guard to **20%** →
**24.7%** goal odds with **2.9%** chance of halving (today 0.5% / 0%). Guard 15% would be 22.7% / 0.8%. The 5% floor
picks 20%; a stricter floor is a one-constant change if Eran prefers.

**Smoke.** `scripts/smoke/smoke-pr96-protection-tuning.ps1 -Sha <sha>`. It does not accept. **Tests.**
`tests/test_protection_tuning.py` (6), including accept → paused + guard moved, and a moved rule set refused untouched.

---

## PR 95 — blended is the default (prior = 5 years); the protective rules replayed on the goal's paths

**Decisions (Eran, 2026-10-05).** Blended becomes the default, with **shrunk as the stress line** on every goal
card. *"Prior = 5 years"*: blended is Black-Litterman with τ = 0.2, so a backtest weighs ~T/(T+5). Today the SOXL
rule gets ~49% (the old 50/50-of-estimates formula really gave it ~61%), and the weight rises on its own as
out-of-sample years accumulate. Supersedes "shrunk by default" (PR 90/91). The next PRs: rules-aware odds (this
one), then faster reactions, then one return basis app-wide.

**What.**
- `engines/paths.simulate_rules`: the user's **armed** rules replayed on the same filtered-history paths, with the
  semantics the app executes (read from the code):
  - trailing exit = the tightest of trailing_stop / activated profit_protect / stop_loss on a sleeve ticker;
  - take-profit sells `trim_fraction` (25%), next rung +10%;
  - the **buy-back hold** keeps a sold holding out until its price is **below the sale price**;
  - drawdown guard sells 25% of the most volatile sleeve and holds it until the drawdown is < max(L/2, L−2.5);
  - monthly two-sided rebalance of everything not held out; deposits monthly.
- Three runs on identical paths: rules off, as today, and the **rebound fix** (buy back only after a 15% rebound).
- `rules_spec` maps the DB rules onto sleeves (core-ticker rules listed as not simulated).
- Every goal verdict carries `rules` and `stress`. Cards state the shrunk stress odds, the rules-on/off/fix odds and
  halving chances, and why when the rules cost ≥ 5 points.
- Assumptions stated: every rule card is accepted the day it fires; sleeve rules act on the sleeve's stream.

**On the live book (real data, 28 armed rules, blended, 2026-10-05):**

| | goal odds | halving |
|---|---|---|
| rules off | 32.9% | 39.6% |
| **rules as today** | **0.5%** | 0% |
| rebound fix | 1.0% | 0% |
| sleeve stops/trails only | 7.8% | 9.4% |
| take-profits only | 33.3% | 38.7% |
| drawdown guard only | 18.8% | 0.7% |
| guard 15% + take-profits, no sleeve stops | 22.7% | 0.8% |

**The sleeve stops are what cost the goal.** After an exit the buy-back hold keeps SOXL out **62%** of days and QQQ
**49%** in rising markets. **The drawdown guard alone does nearly all the crash protection.** The take-profit ladder
is neutral to slightly positive. **Not yet acted on**: retiring the sleeve stops or re-levelling the guard changes
his rules, and is offered as a decision (next PR).

**Smoke.** `scripts/smoke/smoke-pr95-rules-aware.ps1 -Sha <sha>`. **Tests.** `tests/test_rules_paths.py` (9) plus updated
dial/goal tests.

---

## PR 94 — which core serves the goal

**What.** `frontier_service.core_solve`: every catalog core is **priced from its own tickers' real ILS history**,
never through a proxy (NVDA modelled as VTI is a lie by approximation). It is added as one more asset beside the
sleeves, and the current core is priced the same way, so the comparison is like for like. Score = the **lower** of
the model's and the history's goal probability.

A switch requires all of:
- **+5 points** of score;
- no loss on either method;
- best in **≥ 50% of 30 redraws**;
- never pushing the book further past the Kelly cap.

Cores whose tickers were not fetched are **listed as not modelled**. The card (`frontier_core`) switches the core
via `sleeve_service.set_core`, then realigns through `_apply_core_to_basket` (prices every buy before selling,
re-plans from live rows). It refuses untouched if the core changed. Every goal card now says how much changing the
core can move the odds.

**On the live book (real data, 2026-10-05):** **KEEP under every estimate.** The core moves the odds by ≤ ~2 points:
- shrunk: Diversified Global Growth 17.8 vs 60/40 15.8, but it pushes the book to 1.22× Kelly, so it is ineligible;
- blended: all 41.4–42.0;
- measured: All-Weather 71.5 vs 71.1 (halving 18.8% → 15.6%, immaterial to the goal).

**The goal is decided by the sleeves, not the core** (the core is 37% of the book). Nine catalog cores are not
modelled (single names / income / short-bond tickers not in the fetched history).

**CI.** GitHub created two runs for the single push of `bea66a2` (#273/#274: same commit, event and second).
`ci.yml` now has a `concurrency` group per workflow+event+commit with `cancel-in-progress`, so a duplicate cancels
the older run.

**Smoke.** `scripts/smoke/smoke-pr94-goal-core.ps1 -Sha <sha>`. **Tests.** `tests/test_frontier_core.py` (6), including
an end-to-end switch.

---

## PR 93 — does the goal survive real markets? Filtered historical paths

**Why.** PR 92 decides with a lognormal: no crash days, no volatility clustering, no 3× compounding drag through a
drawdown, which is exactly where an 88%-vol leveraged sleeve's risk lives. The planned "mean-CVaR" step, re-aimed
at the goal: every number a goal card states is checked a second way.

**What.** `app/engines/paths.py` (pure): 2,000 stationary-block paths (21-day blocks) to the deadline, drawn from the
book's **real ILS daily returns**, each asset **re-centred on the user's chosen estimate** (filtered historical
simulation). History supplies the shape, his confidence the centre, so a 'shrunk' user is never shown 'measured' odds
through the back door. Deposits are linear on a path (final = nav·C_T + c·S), so the deposit lever is an exact
percentile with no search. In `goal_solve`:
- a trade card is withdrawn if it **lowers the odds on history** (`TAIL_DISAGREES`, 1-pt tolerance);
- "on track" needs ≥ 70% on **both** (`TAIL_SHORT` otherwise);
- levers hold both ways: the **larger** deposit, the **smaller** reachable amount, and the deadline lever only if
  history also reaches 70% at the new date.

Cards state the history odds, the **worst-5% outcome** at the deadline, and the **chance the book halves** at some
point.

**On the live book (real data, 2026-10-05):**
- **Shrunk:** model 23% / **history 15.8%**; **55% chance the book halves at some point**; worst 5% ≈ ₪7,850 from
  ₪24,611. Deposit lever **₪720 → ₪890/month** (history binds). The deadline lever (+18.75y) does not survive.
- **Blended:** 46% / 42%; deposit ₪320 → ₪400; +3.5y holds (71.5% on history).
- **Measured:** 71% / 72.7%, on track on both.

**Smoke.** `scripts/smoke/smoke-pr93-tail-check.ps1 -Sha <sha>`. **Tests.** `tests/test_paths.py` (6, closed forms) +
goal tests updated (17).

---

## PR 92 — the goal is the objective; the frontier and Kelly are how

**Why.** Eran, 2026-10-05: *"the end goal is to meet the ROI goals the user set … the kelly parameter and the
efficient frontier are tools and not the objectives."* Measured first, on production inputs: PR 91's ½-Kelly
resize **lowers** the goal's odds under every estimate (shrunk 23% → 5%, blended 46% → 41%). And "the lowest risk
whose *median* reaches the goal" would trim a 71% book to a coin flip. The goal needs 26.0%/yr, not 30%: the book
is ahead of its contribution schedule (₪24,611 → ₪82,692 by 2031-12-31).

**Decisions (Eran, 2026-10-05).** *Meeting the goal = at least a 70% chance.* *The goal picks the risk, capped at
full Kelly; the Kelly dial is an optional extra ceiling.* This **supersedes** PR 91's "0.5 is the default" for plans
that have an ROI target. Without a target, PR 91's risk mode (dial default by objective) still applies.

**What.**
- `engines/frontier.py`: `required_log_growth`, `goal_probability` (lognormal on the book, deposits at median
  FV), `median_wealth`, `fv_contributions`.
- `frontier_service.goal_solve`: on each of 60 bootstrap draws, walk a 16-point frontier from the cap (1.0× or
  the user's lower dial) to the safest mix. Take the **safest mix with ≥ 70%**, else the **best odds**. Sleeves may
  **shrink or grow** (to the plan's concentration cap, 40% on High); the core is held to its basket.
  Outcomes: ON_TRACK / LESS_RISK / REBALANCE / IMPROVE / OUT_OF_REACH. Material = +5 pts of odds, or −3 vol pts
  while still ≥ 70%.
- **Lever cards** when the odds stay under 70%, each with the existing actionable kinds:
  - *add ₪X/month* → `set_standing_order`, solved to reach exactly 70%;
  - *set a target this book can reach* → `set_plan target_roi_pct`, the amount reached at 70% converted back
    through `goal_service.compound`;
  - *move the deadline* → `set_plan target_date`, only when ≤ 15 years.
- **Mix card** (`frontier_resize`, extended): shrinks as before. A **growing** sleeve is funded by
  `strategy_service.load_basket(mode="fund")`, the path that only sells what the plan holds too much of. The
  shrink legs are recorded by the handler and `ledger_recorded` set, so nothing is double-counted.
- **One goal story:** while a fresh goal verdict is current (same ROI, deadline, objective, sleeves, dials), the
  older `_behind_goal_recs` card stands down. It projects at the app-wide measured return; the goal cards use the
  user's return confidence.
- `GET /plan.frontier` gains `mode` (goal|risk) and `goal_confidence`. The Plan tab note explains the ceiling.
  `sw.js` → `iw-v37`.

**On the live book, real data (2026-10-05), at 70% and a 1.0× cap:**
- **Measured:** ON_TRACK (71%), no cards.
- **Blended:** OUT_OF_REACH (best 47%), no trade helps materially. Levers: +₪320/month, or +3.5 years, or a goal
  of ₪45,834.
- **Shrunk** (his setting): OUT_OF_REACH (today 23%; the stable trade raises growth but *lowers* odds to 19%,
  so no mix card). Levers: **+₪720/month**; the target lever (the amount reachable at 70% is about today's NAV); the
  deadline lever is +18.75y, past the 15y limit, so no card.

**Smoke.** `scripts/smoke/smoke-pr92-goal-seeking.ps1 -Sha <sha>`. It re-derives today's goal probability and the
deposit lever from production's inputs, and does not accept.

**Tests.** `tests/test_frontier_goal.py` (16), including accepting a deposit lever, a grow card end-to-end, and
the older goal card standing down.

---

## PR 91 — the frontier card: Accept executes a resampled sleeve resize, on the user's dials, against his target

**Why.** Eran, 2026-10-05: cards must be *"actionable … once I approve them will execute it"*. The Kelly fraction is
*"the default but I want to be able to change it"*. Return confidence defaults to shrunk, with *"blended as an
option"*. And the recommendations must be measured against the ROI he set (30%/yr).

**What.** `app/services/frontier_service.py` (new) plus wiring.
- **Solve:** max μ'x − (λ/2)x'Σx, λ = 1/(plan's Kelly fraction). Sleeves are **shrink-only**; the core is held to
  its basket (one composite asset); cash is fixed. Resampled 100× (21-day block bootstrap). The proposal is the
  mean and the band is p10–p90. A sleeve acts only above its whole band and by ≥ 2 pts.
- **Materiality gate:** a stable resize must close ≥ ⅓ of the gap to the Kelly fraction or cut vol ≥ 3 pts,
  else `IMMATERIAL` (no card). Found on real data: under 'blended' the stable answer was "sell all of QQQ" for
  0.64× → 0.61× Kelly.
- **Dials** (migration 0026): `PUT /plan` takes `kelly_fraction` 0.25–1.0 (422 outside; full Kelly is the cap)
  and `return_confidence` shrunk|blended|measured. `GET /plan.frontier` returns the effective values and their
  source. The Plan tab has both selects; a dial is sent only when changed, and a change triggers a re-solve.
  `estimate()` applies the confidence only to assets with a measured view.
- **Target-aware:** every verdict carries `scenarios` for all three confidences (growth before and after, plus
  the most any long-only mix compounds, i.e. the λ = 1 optimum) and a `target` block. The card says out loud
  when the target is unreachable on the chosen estimate, and when the resize would drop the book below target
  if the backtests are right.
- **Card ↔ verdict:** the card hides when the verdict is > 36h old, or the objective, a sleeve size, the Kelly
  fraction or the confidence changed.
- **Accept** (`frontier_resize`): refuses untouched if the book moved or a buy can't be priced. Otherwise it
  resizes, sells the same fraction of every ticker the sleeve holds (CGT), buys the core basket from the actual
  net proceeds, and re-levels caps. One ledger call at the boundary.
- **Store/jobs:** `SolverVerdict` rows tagged `frontier-f1` (`solver_store.latest()` excludes them). The
  nightly job runs the frontier pass. `POST /plan/frontier/refresh` and `GET /plan/frontier` are new. Cold
  cache: one retry after 35s (breaker recovery); still refused → `UNAVAILABLE`. This was seen on PR 90's deploy.
- **Engine:** FISTA, an exact capped-simplex projection and vectorised Ledoit-Wolf. The real resample went from
  62s to 11s with an identical answer.
- `sw.js` → `iw-v36` (new Plan controls).

**On the live book, real data (2026-10-05):**
- **Shrunk** (default): `btm_trend_soxl` 40% → **10.5%** (band 9.0–12.6%). The book goes from 1.14× to 0.52×
  Kelly and vol from 41.9% to 20.9%; growth 10.1% → 9.6%/yr. The card says: "Your target is 30%/yr … the most
  any mix of these assets compounds is 10.85%/yr, so the target is not reachable on this estimate … If the
  backtests are right (measured), the book compounds 39.56% today - above your target - and 17.29% after:
  below it."
- **Blended:** `IMMATERIAL`, no card. The max is 28.2%/yr, so 30% is unreachable on this estimate too.

**Smoke.** `scripts/smoke/smoke-pr91-frontier-card.ps1 -Sha <sha>`. It does **not** accept.

**Tests.** `test_frontier_resize.py` (15, incl. an end-to-end accept re-measured and the nightly job) and
`test_frontier_dials.py` (14).

---

## PR 90 — migration 0026: `plans.kelly_fraction` + `plans.return_confidence` (add-only, ships alone)

_2026-10-05, Eran's decisions on the frontier card:_ "0.5 is the default but I want to be able to change it"
(Kelly fraction, 0.25–1.0, capped at full Kelly because above it the model gives less growth for more risk), and
return confidence defaults to **shrunk**, with "blended as an option to change" (Measured stays available, as
part of the 'add all three' choice). NULL means today's behaviour: the objective's Kelly default and 'shrunk'.
Guarded like 0021–0025, mirrored into `main.py`'s ADD COLUMN list, and the model columns added. Shipped
**alone**, before PR 91 reads it (PR 47). Local check on SQLite: upgrade → head; `downgrade -1` removes both;
re-upgrade on the table without them (the production shape) adds them and stamps 0026.

**Why the dials exist (measured on production inputs, 2026-10-05).** Plan: Grow, 30%/yr target, risk tolerance High.
Under the shrunk estimate the best compound growth these assets offer at any Kelly fraction is ~10.8%/yr, so the
30% target is unreachable. Under the raw OOS backtest, today's plan compounds 39.6%/yr at 0.45× Kelly, and
the resize would cut it to 17.3%. Blended: 24.0% → 13.4%. **The card's verdict depends on how far the backtest is
trusted more than on the Kelly fraction**, so PR 91's card will show both estimates against the user's target.
BL already weights the SOXL backtest at 21% (±40%/yr std error over 4.7y) and QQQ's at 49%.

**Smoke.** `scripts/smoke/smoke-pr90-migration-0026.ps1 -Sha <sha>`. The schema must read `0026_plan_frontier_settings`
before PR 91 ships.

---

## PR 89 — the frontier's inputs: ILS returns, shrunk covariance, Black-Litterman (read-only)

**What.** `app/services/frontier_inputs.py` + `GET /api/v1/plan/frontier/inputs` (diagnostic; nothing
reads it until PR 90).
- **Universe:** VTI / VXUS / BND / IAU+DBC as class proxies, plus each **sleeve's simulated rule
  stream** (pre-tax; tax lives in the measured CAGR), plus ILS cash.
- **ILS:** every risky return is compounded with the daily USD/ILS change (`ILS=X`). If FX history is
  missing it falls back to USD and says `fx_basis: "spot"` / `degraded: FX_SPOT`.
- **Covariance:** `frontier.ledoit_wolf`. Cash is appended as a zero row, after every inversion.
- **Prior:** reverse-optimised from the plan's fixed `OBJ_TARGET` (Equities split 60/40 US/intl),
  with λ calibrated so the reference earns the instrument table's premium. **With no views the
  optimiser returns the plan's own target.** That is tested, and the smoke checks it on prod data.
- **Views:** one per sleeve with a usable OOS measurement. Ω = variance / OOS years (no knob).
  Unusable measurements are named (`NOT_MEASURED`, `ENGINE_VERSION_MOVED`, `NO_OUT_OF_SAMPLE`, …).

**Measured on real data before shipping (2026-10-05, Yahoo + prod sleeves).** 2,215 shared
sessions (2016-10 → 2026-10), FX historical, shrinkage 0.003, nothing excluded. Sleeves:
`btm_trend_soxl` 40% and `btm_qqq_hold` 19%, both with views. The sleeve stream matches the stored
backtest: SOXL rule 83.95% USD vol vs 84.32% stored (the difference is CGT lumps); QQQ 22.56% vs 22.56%.
**Finding:** `btm_trend_soxl` is 87.8% vol in ILS (the rule holds SMH, not cash, when it is off). BL
shrinks its 59% OOS CAGR (4.7y at 93% vol) to a 32% drift, and at that vol the **expected
compound growth is −6%/yr**. That is a fact for PR 90's card, not a trade.

**Smoke.** `scripts/smoke/smoke-pr89-frontier-inputs.ps1 -Sha <sha>`.

**Tests.** `tests/test_frontier_inputs.py` (20). **Behaviour change:** one new read-only endpoint.

---

## PR 88 — the efficient-frontier engine (pure math; nothing reads it yet)

**Why.** Validation on 2026-10-05: the plan's targets are constants (`OBJ_TARGET`, each catalog
`target_allocation`); `portfolio_risk.analyze` measures a covariance matrix that no weight decision
reads; T2/T6 optimise sleeve SHARE only, on drawdown. Eran approved a five-PR pipeline —
`EXECUTION_PLAN_2026-10-05_frontier.md` — of which this is the first.

**What.** `app/engines/frontier.py`, numpy only, no I/O:
- `ledoit_wolf` — shrunk, annualised covariance; the shrinkage is returned, not hidden.
- `optimize` — max mu'x − (λ/2)x'Σx, long-only, capped, with **fixed weights** (the sleeves) inside
  the covariance but never moved. `risk_aversion=inf` is minimum variance (the no-return fallback).
- `frontier`, `for_volatility` — the constrained frontier; an unreachable target says which end.
- Kelly: `kelly_weights`, `growth`, `kelly_position` (fraction of Kelly along the book's own mix;
  `over_kelly` when holding less would grow faster). λ = 1 / Kelly fraction:
  Grow ½, Balanced ⅓, Preserve/Income ¼ — never full Kelly.
- Returns go in as CAGR → `return_convention.drift_pct`; growth comes out via expm1.

**Tests.** `tests/test_frontier_engine.py` (18): 2-asset closed forms (min-var, mean-variance),
long-only corner, caps/infeasible caps, the core leaning away from equity beside a 64% sleeve,
Ledoit-Wolf shrinkage/invertibility, projection, frontier dominance, Kelly arithmetic (3× a
10%/22% asset grows ~0.2%/yr vs ~7.7% at full Kelly).

**Smoke.** `scripts/smoke/smoke-pr88-frontier-engine.ps1 -Sha <sha>` on the new shared
`_smoke-common.ps1` (every PR now ships one — CLAUDE.md ship rules). Its endpoint fields were
verified against production on 2026-10-05 (serving `501e8a7`): `/mix target_source` =
`sleeves+core`; `GET /plan/target` is 422 without params, so the script reads
`/plan/target/accepted`. **First live engine reading:** 22.49% stated CAGR, 50.46% realised
vol, so the book sits at **0.88× full Kelly** (4% risk-free assumed). That is not over-Kelly, but it
is above Grow's ½. The `.ps1` itself has not been run yet: an isolated session cannot launch
PowerShell.

**Behaviour change: none.** Next: PR 89 (ILS inputs + Black-Litterman).

---

