# InvestWise Pro — Status

_Last updated: 2026-10-23 by Claude (PR 144: production says when it is broken). Last product PR: **PR 144**. **Active plan:** [`docs/plans/EXECUTION_PLAN_2026-10-23_after-review.md`](docs/plans/EXECUTION_PLAN_2026-10-23_after-review.md) (start a new session there). Sleeve plan [`docs/plans/SLEEVE_OVERLAP_PLAN_2026-10-14.md`](docs/plans/SLEEVE_OVERLAP_PLAN_2026-10-14.md): **complete** (PR 131-136b); follow-ups in BACKLOG P14-P19, O6, V3, C15-C17. 2026-10-22 review: PR 139-148 plan in the review doc. Gate until GitHub Actions minutes reset on 2026-11-01: full suite on local SQLite and local Postgres (Python 3.12), ruff on app and tests, and the PR's smoke against a locally served build; then the same smoke on production after deploy._

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
  obeys the book target) followed the same day. PR 120 (one goal chance on Today and Plan) closed Wave A. Wave B (PR 107-109) shipped the same day. Wave C started the same day (D73, D74). PR 111 closed Wave C. Wave D started the same day (D76). Wave D (PR 112-114b) complete. Wave E started the same day (D77-D81). Next: **PR 121** (the protection retune searches properly).
- **Done (PR 119):** the deploy is defined in `.railway/railway.ts`; `railway.json` (retired by Railway on 2026-12-01) is gone.
- **Owner ops (plan Wave 0)** — first flagged 2026-10-08, age 4 days on 2026-10-12; the Postgres backup schedule is already set and `redis-volume` deleted (see PR 119 Ops note), so only the token rotation is genuinely open:
  - Check Railway Postgres backups.
  - Delete the orphan `redis-volume`.
  - ~~Delete the untracked `candidate_search.py` draft~~ and ~~commit the v8 / v2.2 plans + Plan-tab design~~: done by
    Claude 2026-10-11 (PR #3).
  - Rotate the GitHub PAT and the Railway token.

---

## PR 144 — a broken build or background job says so, on Today and on the phone (D121)

**Report (2026-10-22 review; plan §4.2; BACKLOG C23).** Nothing watched production. CI is off until 11-01 and never gated
a deploy (Railway deploys every push); a failing job was one line on `/push/status`.

**Root cause (PROVEN: live read and reproduction).**
- **9 of 13 jobs unwatched.** Production `97ac021`, 2026-10-23 15:25 UTC: the 9 nightly jobs (`nav_snapshot`,
  `strategy_signals`, `push_digest`, ...) have no history at all, and nothing would notice if they never ran.
- **Buried imports are invisible until the job runs.** The `nav_snapshot` failure class (an import inside the function,
  7 nights of 7) is invisible to import time, ruff and the suite. Reproduced by `test_a_lazy_import_that_cannot_resolve_is_found`.
- **No heads-up was ever pushed.** `evaluate_and_notify` reads `built["recommendations"]` only, so PR 143's "rules not
  checked" heads-up never reached the phone.

**Ruled out.** A broken job today (the 4 frequent jobs: `failures: 0`); a missing package on production (the self-check
found `pywebpush` missing only in the local venv; it is pinned in `requirements.txt` and production's push library reports OK).

**Fix.**
- **Boot check.** `app/worker/self_check.py` resolves every `from X import Y` inside a function of each job's module.
  `scheduler.run_boot_check()` (just before the scheduler starts) records it as the job `boot_check` (persisted, PR 143)
  and marks each job with no history `registered_at` now.
- **Heads-up on Today.** `_job_health_headsup` puts ONE HIGH heads-up on Today naming every job that is failing, has not
  succeeded in 26 h (or never, since registration), or a failed `boot_check`. `price_refresh` stays with PR 143's rule-check
  heads-up.
- **Pushes.** `evaluate_and_notify` now pushes the `rule_check` and `job_health` heads-ups, once per heads-up id per 12 h.

**Tests.** `tests/test_pr144_production_says_when_it_is_broken.py` (8):
- the buried-import reproduction;
- a guard that runs `start_scheduler` against a recording scheduler and checks the imports of all 13 real jobs;
- the boot-check record;
- the heads-up's failing, stale, never-ran and broken-build cases, and its silences;
- one push per id, with other heads-ups not pushed.

Full Postgres suite: **2,879 passed, 0 failed, 0 skipped**. Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr144-production-says-when-broken.ps1`. Production before the merge (`97ac021`): PASS 4
FAIL 2, the gap itself ("no boot_check"; "no history at all for" the 9 nightly jobs). Production after deploy: PROD_RESULT.

**Still open.** Plan §4.2 part 3 (a daily production smoke as a scheduled task) waits on the owner (plan D-1).
## PR 147.1 — the ledger repair fills the old unpriced trades from what was measured

**Report.** PR 147 stopped new rows being written at price 0 / ₪0; the older ones (114 of 400 trade rows on `3b782d8`)
stayed. Owner, 2026-10-23: repair them if recommended (BACKLOG C22).

**What it does.** `ledger_repair.preview` gains `unpriced_trades`: every Buy/Sell with a quantity and price 0 and/or
value 0 that is not voided or already repriced. Each row's VALUE comes from the row itself, else from the rule event that
executed it (`cash_added_ils + tax_ils`, paired by ticker, +/-3 minutes and share count, each event used once), else
shares x the price that half hour x today's FX. Its PRICE comes from the market that half hour (`_price_at`: the 30-min
bar, else the day's close), in the ticker's own currency. MEASURED when the value came from the row or a rule event AND
the price agrees with it within 5%; otherwise ESTIMATE; no price that half hour = UNRESOLVED (listed, never written).
`apply(confirm=True)` writes MEASURED rows (`include_estimates` adds ESTIMATE), fills only the missing field, appends
`repriced 2026-10-23: <confidence> via <method>; was price X / value Y` to the note, and is idempotent. Skips are reported
in `reprice_skipped`, so the older sections' contract is unchanged. Nothing is written by the preview; the owner applies.

**Tests.** `tests/test_pr147_1_reprice_old_ledger_rows.py` (2): the 10-23 SOXL stop (rule event -> measured), the 10-21
XOM buy (own value -> measured), a drift trim (estimate), an unpriceable ticker (unresolved), a clean row untouched;
apply writes measured only, then estimates on request, and a second apply changes nothing. The 17 existing repair tests
pass. Full Postgres suite: **2,871 passed, 0 failed, 0 skipped**. Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr147-1-reprice-preview.ps1` (read-only, never POSTs the repair). Production before the
merge (`97ac021`): PASS 3 FAIL 1, "no unpriced_trades section" (the build before this one). Production after deploy (`9d20605`): **PASS 11 FAIL 0 SKIP 0**. The preview lists 114 unpriced trades: 87 measured (71 from the row's own value, 16 from a rule event; ₪142,563) and 27 estimate (no record of the cash; ₪52,256: core_to_basket 9, trim 8, sell_position 6, buy_funded 4). Examples: SOXL stop 10-23 15.7829 @ 82.11 = ₪4,027.63 (measured), XOM buy 10-21 @ 177.89 = ₪890.91 (measured). **Owner, 2026-10-23: apply the measured rows only** (`POST .../ledger/repair {"confirm": true}`, run by the owner; record the result here).

---

## P1 — compare goals: the chance of other goals with your rules as they are (D122)

**Why (owner decision D122, 2026-10-23).** Keep the 30% halving limit and revisit the goal. Production's model: 30%/yr
(₪82,692 by end-2031) is 0.2% likely with the protective rules, 18.5% without them (halving 53.9%). The solve already
offered three levers (extra deposit, extra years, the %/yr reachable at the on-track bar); on this book with the rules
none was found and the confident %/yr was 3.7%, which says what is out of reach but not what is in reach.

**What it does.**
- **The grid.** `frontier_service._goal_grid`, inside the nightly goal solve's rules replay, prices 20 goals:
  %/yr 30/25/20/15/10 x the deadline as set or +2 years x no extra deposit or +₪1,000 a month. A "ROI on every shekel"
  target is rebuilt from the money put in (`target / (1+roi)^years`) plus what the deposits grow to at that cell's ROI.
  Each cell is replayed with the rules as they are and without them, on the same 500 paths, with its halving risk.
  Fixed-amount goals get no grid.
- **Time budget.** 120 s (`GRID_BUDGET_SEC`): cells past it are "not computed tonight", so the 600 s job limit is never
  at risk. It took 20 s on the test book.
- **On the page.** The Plan tab's goal panel gains a collapsible "Compare goals" table. Cells at or over the on-track bar
  are green; hovering shows the amount and halving risk with and without the rules. Shell `iw-v73`.

**Tests.** `tests/test_d122_the_goal_comparison.py` (4):
- each target rebuilt from the money put in;
- 40 replays on the same paths, standing orders kept;
- end to end on the PR 99 fixture: the grid is present, the own-goal cell matches the headline, and an easier goal is
  never less likely; none for a fixed amount;
- the time budget.

Full Postgres suite: GATE_RESULT.

**Smoke.** `scripts/smoke/smoke-p1-goal-comparison.ps1`. Production before (`47e9ebe`): PASS 3 FAIL 1 SKIP 3 (no grid;
shell `iw-v72`). After the deploy AND the 04:10 UTC solve: PROD_RESULT.

**Next (owner).** Read the table on the Plan tab and choose a goal; changing it is the Plan settings' existing goal edit.

---

## PR 147 — every trade the ledger records carries its price and value (D120)

**Report (2026-10-22 review).** Ledger rows written at price 0.00.

**Root cause (PROVEN: production ledger and rule events; all five new tests fail on `99258a9`).** 140 of 440 production
ledger rows (08-29 .. 10-23) have price 0 and/or value 0: `buy_funded` 42, `sleeve_fund` 32, `trim` 27, `core_to_basket`
18 (all before 10-14), `sell_position` 10 and a few others -- today's SOXL stop (15.78 shares) reads "₪0". The generic
recorder (`_record_apply` -> `ledger.record_legs`) reads `price` and `value_ils` from each handler's result, and:
`trim` and `sell_position` report the sale as a bare ticker with `shares` and `cash_added_ils` only; `buy_funded` reports
`bought` as a ticker with `value_ils` and no price; the sleeve funder's sell legs carry `value_ils` and no price.

**Blast radius.** The money was right: each handler credits cash and reports `cash_added_ils` (SOXL stop 10-16 +4,932.96,
VXUS trim 10-20 +630.66, and the ₪4,026 now reserved for SOXL). What was wrong is the record: the ledger cannot say what a
sale raised; Holdings' "vs your last buy" skips price-0 buys, so XOM shows none (its only buy, 10-21, ₪891, has price 0);
`net_flows_ils` reads only Adjust rows and is unaffected; the per-holding chart uses quantities, which the recent rows have.

**Fix.** `trim` / `sell_position` details carry `price` and `gross_ils`; `buy_funded` carries `price`; the recorder's
single-ticker paths use them; the sleeve funder records the sold price; `record_leg` logs an ERROR for any Buy/Sell with
a quantity and no price or value.

**Not done here: the 140 older rows.** Rewriting them is a production data repair. It is possible for most (value from
`cash_added_ils + tax_ils`, price from value / quantity / FX), not for the August trims recorded with quantity 0.
Owner's call; BACKLOG C22.

**Tests.** `tests/test_pr147_the_ledger_says_what_it_sold.py` (5): a stop sale, a cap trim, sleeve funding and a funded buy
reported as one ticker each record a price and value; a leg without them is logged. Full Postgres suite: **2,869 passed, 0 failed, 0 skipped**. Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr147-the-ledger-says-what-it-sold.ps1`. Production before the merge (`99258a9`, `-Since`
10-20): **FAIL 20**, every trim, stop, funded buy and sleeve-funding sale since 10-20 (the bug). Production after deploy (`3b782d8`): **PASS 3 FAIL 0 SKIP 2**: no trade yet since the deploy (section 1 waits for one); 114 of 400 older trade rows still lack a price or value (PR 147.1 repairs them); smoke-all 22/3 (Gemini credits).

---

## C15 — the core realign trims only sleeves that are over, and never a basket ticker (D119)

**Report (BACKLOG C15).** The core realign (`core_to_basket`, rec_2d1727 / rec_f6f017) kept selling TQQQ right after sleeve
top-ups bought it.

**Root cause (PROVEN: production ledger and reproduction; two of the new tests fail on `abd4d39`).** `_core_plan` sizes
the "trim sleeves" leg from `sleeve_occupancy`, adding up only the sleeves ABOVE their size; `_apply_core_to_basket` then
sold that amount "pro-rata across the sleeve holdings" -- every sleeve, including those under their size. "Fund my
sleeves" then refilled them. The ledger, 10-19 .. 10-21: 17 core sales bought back by `sleeve_fund` within minutes (TQQQ
10-19 21:11 sold 8.44 -> 21:14 bought 9.68; 10-20 03:59 sold 5.60 + 2.56 + 4.80 + 2.38 -> 04:01 bought 6.48; 10-21 05:12
sold 1.37 -> 05:15 bought 6.54; SOXL, UGL, QQQ and BIL the same way), and five accepts that sold and bought QQQ in the same
moment (QQQ is both a sleeve holding and in the Diversified Global Growth basket; 10-20 03:59 sold 1,358, bought 2,311).
On 10-20 04:00-04:01 the card was accepted four times, churning each time. Reproduced: SOXL 60 shares against a 20% sleeve,
TQQQ 10 against 25%; one accept sold 5.71 TQQQ.

**Ruled out.** The drift trims (`trim`, rec_2e52d5: their zero-price rows are PR 147); `rebalance_to_objective` (its own
card, which bought TQQQ); a hold (none existed on TQQQ before 10-23). **Today:** the card trims no sleeve (they are
₪6,672 short), and PR 141 blocks TQQQ buys while the SOXL hold lasts, so the churn could not repeat this week.

**Fix.** `sleeve_occupancy` reports each sleeve's `held_by_ticker_ils`. `_core_plan` splits each over-size sleeve's
overshoot across its own holdings into `sleeve_trim_from_ils`, leaving out every ticker the core basket holds; the trim
leg is sized from that; the executor sells exactly that split; the card names it ("... (from SOXL, UGL, the sleeves that
are over)") and carries it in `meta.plan`.

**Tests.** `tests/test_c15_the_core_trims_only_what_is_over.py` (4): a short sleeve is not sold, the over one is; a basket
ticker is not sold to be bought back; the trim never takes a sleeve below its size; the card names the sources. The 124
core / occupancy / realign tests pass. Full Postgres suite: **2,864 passed, 0 failed, 0 skipped**. Ruff clean.

**Smoke.** `scripts/smoke/smoke-c15-core-trims-only-what-is-over.ps1`. Production before the merge (`abd4d39`, no
`-Since`): the whole visible ledger shows the bug as NOTEs, 17 bought-back core sales and 5 same-ticker accepts. Production
after deploy (`99258a9`, `-Since` 12:13 UTC): **PASS 3 FAIL 0 SKIP 3**. The SKIPs are honest: the core card trims no sleeve today (they are ₪6,672 short) and nothing has been accepted since the deploy; the card still offers its buy (QQQ). `smoke-all`: 22 passed, 3 failed (Gemini credits).

---

## PR 143 — a rule check that fails says so; job history survives a deploy (D118)

**Report (2026-10-22 review; BACKLOG C21).** Rule-evaluation failures were silent, and the scheduler's record of each job
was lost on every restart.

**Root cause (PROVEN: live read and reproduction; all four new tests fail on `1984d6f`).**
- The rules are checked in ONE place, inside the 30-minute `price_refresh` job (`pricing_service._refresh_all`), behind
  `except Exception: logger.warning("rule evaluation failed", exc_info=False)`. A failing check left the job `last_ok:
  true` with no `rules` key and a log line without the error. Reproduced: the job did not raise and was recorded green.
- `rules_service.evaluate_all` looped over books with no guard: the first exception ended the loop, leaving every later
  book unchecked (reproduced).
- `scheduler.JOB_STATE` is memory only. Read on production at 11:05 UTC, four minutes after the PR 142 deploy: every job
  `runs: 1`, the nightly jobs (`nav_snapshot`, `strategy_signals`, `push_digest`, ...) no history at all. The app deploys
  several times a day, so "did the 06:15 signal job run?" could not be answered from the app.
- Nothing user-facing read job state: only `/push/status`.

**Ruled out.** A broken check on production today (the 11:01 run carries `rules: {subjects: 1, triggered: 0}`); another
caller of the rule check (`push_evaluate` sends notifications only; `strategy_signals` steps holds and targets).

**Fix.**
- `_refresh_all` re-raises as `RuntimeError("rule check failed after N price(s) were refreshed: ...")`, logged with the
  traceback; prices are already written. `_guarded` records the failure with its reason.
- `evaluate_all` guards each book, rolls back, carries on, and raises at the end naming how many failed.
- `scheduler._record` also writes `kv_settings job_state:<id>` (a throwaway NullPool engine on its own thread; a write
  failure is logged and never fails the job), keeps `previous_run`, and `start_scheduler` reloads them first.
- `_rule_check_headsup`: a HIGH heads-up on Today, "Your trading rules have not been checked since ...", when the last
  check failed (with its error) or none has succeeded for 75 minutes, while the scheduler runs and a rule is armed.

**Tests.** `tests/test_pr143_a_failed_rule_check_is_loud.py` (6): the job fails; the failure is recorded with its reason;
one bad book does not skip the next; history reloads after a simulated restart and the next run continues it; the
heads-up's three states and its silences; Today carries it. Full Postgres suite: **2,859 passed, 1 failed, 0 skipped**. The failure was an artefact of editing `scheduler.py` (adding `previous_run`) while the run had it loaded: `test_the_scheduler_primes_the_price_refresh_through_the_guarded_path` reads `start_scheduler`'s source by line number and got the neighbouring function. Every test that touches the scheduler, the price job or the rule check (115, the PR 143 file included) re-run on the final code: all pass. Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr143-rule-check-is-loud.ps1`. Production before the merge (`1984d6f`, `-Since` 11:00):
PASS 4 FAIL 0 SKIP 2 (the history skip is the bug: nothing from before 11:01 survived). Local build with the scheduler on
migrated Postgres, restarted twice: PASS 5 FAIL 0 SKIP 1 ("3 job(s) remember a run from before" the restart; `runs: 2`).
Production after deploy (`abd4d39`, 11:37 UTC): **PASS 4 FAIL 0 SKIP 2**: the 11:37 rule check ran and is recorded (1 book, 0 triggered); no 'not checked' heads-up; nothing failing. The history SKIP is expected on this deploy (nothing was persisted before it) and must PASS on the next one. **It did:** on the C15 deploy (`99258a9`), PASS 5 FAIL 0 SKIP 1, "3 job(s) remember a run from before 12:13 UTC: standing_orders, futures_warm, price_refresh". `smoke-all`: 22 passed, 3 failed (Gemini credits).

---

## PR 142 — Ignoring a fired stop keeps the stop on, re-armed below today (D117)

**Report (2026-10-22 review; BACKLOG C20).** Tapping Ignore on a fired stop card left the position in the book and switched
its stop off.

**Root cause (PROVEN by reproduction: `test_ignoring_a_fired_stop_leaves_it_armed` fails on `0a9d570` with `active: False`,
`breached_now: True`).** Ignore (`dismiss_recommendation`) calls `resolve_rule(..., "dismissed")` for a `rule_*` card, and the
30-minute evaluation's `heal_dismissed_rules` does the same as "acknowledged". `resolve_rule` sets `active = False` on every
`_ONE_SHOT` type, which includes stop-loss, trailing stop and profit-protect. The only thing left on Today was PR 138's
"Your SOXL stop is switched off and the price is below it" card; ignoring that too left nothing.

**Ruled out.** Keep (`keep_and_relevel` re-arms at a new level and stays on); Accept (the stop sold the position, so there is
nothing to protect); the protection retune (PR 140 forbids it). **Not seen on production yet:** the visible rule-event log
(200 events, back to 08-23) has no ignored stop.

**Fix (owner, 2026-10-23: "Re-arm below today").** `_re_arm_ignored`, called from `resolve_rule` for `dismissed` and from the
heal for Ignore marks only: a stop-loss moves to `keep_level` (10% under today's price, never a level already crossed); a
trailing stop or profit-protect restarts its peak at today's price. The event is `kept` with `by: ignore`; the rule's note
says what moved. The card's "how" now says so. A position that is gone falls through to the old resolve. Done is unchanged.

**Tests.** `tests/test_pr142_ignore_keeps_the_stop.py` (3): stop-loss re-armed at 90 from 110 with the event, the note, the
card text and no "switched off" card; a trailing stop's trail restarted from today; a later fall through the new level fires
again and un-mutes the card. All 3 fail on `0a9d570`. Full Postgres suite: **2,853 passed, 1 failed, 0 skipped**. The one failure was PR 109's `test_a_dismissed_rule_is_resolved_by_the_evaluator`, whose last line asserted the old behaviour (a dismissed stop ends up off); it now asserts D117 (on, re-armed at 90), and the file passes 5/5. The app code was unchanged between the run and the fix, so the suite was not re-run.

**Smoke.** `scripts/smoke/smoke-pr142-ignore-keeps-the-stop.ps1`. Production before the merge (`0a9d570`): PASS 4 FAIL 0
SKIP 2 (no stop has fired since PR 139's SOXL exit; nothing ignored). Local branch build on migrated Postgres, a SOXL stop
fired, then Ignored: before PASS on the card text; after PASS 5 FAIL 0 SKIP 1 ("SOXL stop_loss on at 90: Ignored
2026-10-23: kept SOXL; stop moved from 110 to 90"). Production after deploy (`1984d6f`): **PASS 4 FAIL 0 SKIP 2**. The two SKIPs are honest: no stop has fired since the deploy, so there is no card to read and nothing ignored yet; the SOXL stop hold and the one switched-off rule (on the heads-up) are as before. `smoke-all`: 22 passed, 3 failed (the Gemini credits only; the banner check now counts heads-ups).

---

## PR 141 — a stop on one 2x/3x fund holds every 2x/3x fund (D116)

**Report (2026-10-22 review, gaps G3 and G6; BACKLOG C19).** On 10-23, after the owner accepted the SOXL stop-loss (sold at
82.11; PR 139 recorded the stop hold), the production Fund-my-sleeves dry run showed SOXL `held_back` and funded the
`btm_vol_target_tqqq` sleeve with the same money. The held-cash card ("₪4,028 of your cash is waiting for SOXL") listed
TQQQ first among the places to invest it. The daily drift / cold-start card kept asking to top up the bet the stop had closed.

**Root cause (PROVEN: live payloads above, and four of the new tests fail on `9d5a9f7`: load-basket bought 38.59 TQQQ; the
card offered TQQQ and UPRO; the cold-start card asked to fund the TQQQ sleeve).** A stop hold covered exactly one ticker.
Nothing treated SOXL and TQQQ as one bet: the same-bet test compares the strategies' signals (0.78, under the 0.80 limit),
while their daily returns over the month before correlated 0.95 and fell together on 8 of the 8 sessions SOXL dropped
more than 3%.

**Ruled out.** A buyer ignoring the PR 139 hold (every buyer honoured it for SOXL itself; the dry run said so); a
correlation-limit bug (0.78 is what the signals measure; the limit is doing what it was built for, which is not this).

**Fix (owner, 2026-10-23: "any 2x/3x fund"; "elsewhere, non-blocked only").**
- `strategy_profile.GEARED` = the leveraged set without the -1x PSQ and SH.
- `buyback_hold.with_group`: a stop hold on a geared fund adds every other geared fund, with `via` (the stopped ticker) and
  the same hold rule id. Derived on every read, never stored; ending the SOXL hold ends the block. `stop_held` and `active`
  both return the group, so every buyer that already honoured holds (card executors, the `buy_into_book` backstop, sleeve
  funding, load-basket, replace-book, the cash redeploy, the core realign, the book target) now honours it with no new call
  site. Refusals say "TQQQ is a 2x/3x fund, and your trailing stop sold SOXL (also 2x/3x) at 82.11 ...".
- `_held_cash_card`: still waits for SOXL only ("Buy back" buys only SOXL); offers no blocked fund as a place; says the other
  2x/3x funds wait too. Still recommends investing part of it when that is the better pick.
- A blocked sleeve gets no top-up or cold-start card, so no daily drift push (G6).

**Not changed.** A stop on a plain fund (VTI) holds only that fund; a take-profit (price) hold on a geared fund holds only it;
your own Holdings > Add (`recording=True`) still records a 2x/3x purchase.

**Tests.** `tests/test_pr141_one_bet_one_hold.py` (8): the group and what is outside it; the refusal text; load-basket and
Fund-my-sleeves on the TQQQ sleeve after a SOXL stop; a card buy and the writer backstop refused, a manual entry recorded,
the block gone once the SOXL hold ends; the held-cash card's places; no top-up card. Ruff clean. Full Postgres suite: **2,851 passed, 0 skipped, 0 failed**.

**Smoke.** `scripts/smoke/smoke-pr141-one-bet-one-hold.ps1`. Against production BEFORE the merge (`9d5a9f7`): **PASS 6 FAIL 3**,
the bug itself (TQQQ funded in the preview; TQQQ offered; nothing said about the other funds). Against the locally served
branch on migrated Postgres with a fired SOXL stop: **PASS 10 FAIL 0 SKIP 0**. Production after deploy (`0a9d570`, serving 09:47 UTC): **PASS 9 FAIL 0 SKIP 0**. The TQQQ sleeve is no longer funded
in the preview; the held-cash card offers VTI, QQQ, GOOGL, VXUS, BND, AGG, USO, COMT, VCIT and no 2x/3x fund.
`smoke-all` on the same build: 21 passed, 4 failed. Three are the Gemini credits. The fourth, "banner 2 vs 0 cards", was the
smoke's own miscount (fixed in the next PR): the VTI and QQQ band rules fired at 09:47 (the new build's start-up evaluation;
they have fired five times since 10-08) and, with nothing to fund a buy, sit on Today as `rule_*` heads-ups, which the server's
banner lists as carded and the check did not count. Ruled out as PR 141's doing: the held-back cash is unchanged (₪4,026 vs
₪4,028 before; it is capped at spendable cash, which the SOXL reserve already filled), so the core share and the band targets
it feeds did not move.

**Also fixed: a test that stopped running on a date.** `test_the_endpoint_always_says_whether_it_has_a_provisional_point`
(PR 138) never seeded history; it ran only while snapshots another test left behind (dated up to 2026-09-18) were inside
the 1M window. On 2026-10-23 they fell out, it skipped, and the suite's skip guard failed the gate (also on `main`: the
same skip run alone on `9d5a9f7`). It now seeds three recent days of its own and removes them after.

**Also verified.** PR 139.1 (`9d5a9f7`) on production: `smoke-all` 22 passed, 3 failed; section 8 now passes; the 3 failures
are the Gemini credit balance (billing, owner).

**Still open.** C15 (the core realign selling TQQQ after sleeve top-ups) is next, separately: with TQQQ blocked while the
SOXL hold lasts, it cannot be reproduced on the live book today.

---

## PR 139.1 — the held-cash card says what a stop hold waits for

**Report (2026-10-23, found by the production smoke after PR 140 deployed).** After the SOXL stop-out (05:45 UTC) Today showed
"₪4,028 of your cash is waiting for SOXL" with the reason "the app agreed not to buy back SOXL until your drawdown guard
re-arms". SOXL waits for its sleeve's re-entry; no guard was involved. A card may not claim what is not true.

**Root cause (PROVEN: production payload, and the new test fails on `9a8e85b` with that exact sentence).**
`_held_cash_card` chose its wording from the price only: a hold with a level AND a current price "waits until it trades
below"; anything else "waits until your drawdown guard re-arms". A stop sells the whole position, so the ticker has no price
row (`price_now = None`) and every stop hold read as a guard hold.

**Ruled out.** A hold of the wrong kind (the stored row is `kind: stop`, `strategy_id: btm_trend_soxl`, level 82.11); the
"Buy back" option silently buying nothing under PR 139's check (it releases the hold before it buys).

**Fix.** Stop holds first: "until its sleeve's signal (btm_trend_soxl) is out and back in (your stop loss sold it at
82.11)", or "until you end the hold" when no sleeve owns the ticker. The other two sentences are unchanged.

**Also: `smoke-all.ps1` section 8.** It reported "no redeploy card despite 3,588 spendable above the floor". The ~₪4,028 is
reserved for SOXL and has its own card; the redeploy card rightly leaves it alone. The check now subtracts held cash.

**Tests.** `test_the_held_cash_card_says_what_a_stop_hold_waits_for` (stop with and without a sleeve; the guard sentence
unchanged). Every test that touches held cash, plus `test_every_card_can_be_acted_on`, on Postgres: 64 passed. Full Postgres suite: **2,842 passed, 1 skipped, 0 failed**.

**Still open, for PR 141 (G3).** The same card RECOMMENDS investing the SOXL stop-out money elsewhere, TQQQ first (another 3x
fund, correlation 0.83 with SOXL's sleeve).

---

## PR 140 — the protection card never switches off a stop; paused protection is on Today (D115)

**Report (2026-10-22 review, gap G1; BACKLOG C16b).** "Retune your protection" was accepted on 10-06, 10-10, 10-11 and 10-20.
Three times a paused stop was re-armed and fired within minutes, already breached (SOXL 10-12 13:40 -> 13:42, 10-16 19:42 ->
19:46, 10-22 20:08 -> 20:22). On 10-22 the owner re-armed about twenty rules by hand across SOXL, TQQQ, QQQ, VTI, VXUS, XLE,
COMT, UGA and USO.

**Root cause (PROVEN: reproduced on HEAD `606ebec` by the first two tests of `tests/test_pr140_protection_stays_on.py`,
before they were inverted).**
1. `_tune_protection` had ONE exit switch, `retire_sleeve_exits`, applied to every replay column -- the sleeves and, since
   PR 123, every held ticker with a rule. With a goal far above what the book can earn, the model scores every exit as
   pure cost: on 10-23 production read 0.3% goal chance with the rules, 21% without (55.7% chance of halving without).
   Whenever retiring was inside the halving limit, the card paused every stop on the book at once. This was the design
   (D60, PR 96/121), not a slip: `test_the_best_goal_odds_under_the_halving_floor_wins` asserted it.
2. `apply_rules_tuning` set `active = False` and wrote no rule event and no note. The list of what it paused existed only
   in the Accept's HTTP response (`_record_apply` writes trades only; the done mark keeps the amount), so "who switched my
   stops off?" had no answer in the database.

**Ruled out.** The other writers of `active = False` for the 10-22 SOXL stop `c89d8251`: the orphan retire (SOXL held
throughout), the sleeve-cap retire (touches `max_weight` only), the sleeve-removal retire (the rule carries no strategy
stamp, and no sleeve was removed after it was made); a toggle (the audit log has none for it before 10-22 20:08).

**Fix (owner decisions, 2026-10-23).**
- The retune never proposes switching off a stop-loss, trailing stop, profit-protect, trend break or the guard
  (`DOWNSIDE_RULE_TYPES`). It still moves the guard's level, sell size and re-arm gap, and may pause take-profits.
- `apply_rules_tuning` refuses a spec that names one (`STOPS_STAY_ON`) before anything moves; a verdict solved before this
  PR that still proposes it shows no card (the next solve replaces it).
- Every pause writes a `paused` rule event and a note ("Paused by 'Retune your protection' on <date>"); `toggle_rule`
  writes `paused` / `resumed` events too (`action.by` = card or you). History labels: Paused, Re-armed, Kept. `sw.js` iw-v72.
- One heads-up lists every downside rule that is off and not already crossed (a crossed one has PR 138's HIGH card), with
  one act, "Re-arm all" (`rearm_rules`, each re-arm through `toggle_rule`). Its id is the paused set, so Ignore never hides a
  later pause. Auto re-arm: BACKLOG C18.
- The card's title gives goal odds and halving side by side.

**Tests.** `tests/test_pr140_protection_stays_on.py` (7): the retune never proposes pausing a stop even at +20 points; Accept
refuses a stop and moves nothing (not the guard either); a take-profit pause leaves an event naming the card and a note;
your own pause and re-arm leave events; a stale verdict shows no card; one heads-up with Re-arm all, keyed by the paused set;
Re-arm all through Today switches them back on. Updated for D115 (each says so): `test_protection_tuning` (3),
`test_the_retune_searches_properly` (2), `test_every_limit_is_a_setting` (1), `test_a_stop_that_is_off_is_a_card` (1).
Full Postgres suite on `0f83cba` (the gate): 2,840 passed, 1 skipped, 1 failed -- `test_cards_are_short` still built its card from a verdict that paused two trailing stops, which the card now (correctly) hides. Updated for D115, and the action line no longer says "one tap re-arms them" when nothing is paused. The 64 tests that build or read goal and protection cards then passed on Postgres; ruff clean.

**Smoke.** `scripts/smoke/smoke-pr140-protection-stays-on.ps1 -Sha <sha> -Since <deploy time>` (read-only). Against production
on `606ebec` (field check): PASS 1, FAIL 1, SKIP 4 -- the FAIL is real and is what this PR fixes: the UGA trailing stop is off
and nothing on Today says so. Against this build served locally on a migrated Postgres, after pausing a VTI stop, an XLE
trailing stop and a SOXL take-profit by hand: **PASS 7, FAIL 0, SKIP 2** (no protection card: that needs a goal solve on live
history). Beyond the smoke, Re-arm all on that heads-up switched the two downside rules on, left the take-profit off, wrote
two `resumed` events, and the heads-up cleared.

**Verified live (2026-10-23).** Merged as #52 (`9a8e85b`), live 06:33 UTC. Smoke with `-Sha 9a8e85b -Since 06:33Z`: **PASS 4,
FAIL 0, SKIP 3** -- the UGA trailing stop that was off is now on the "1 protective rule is switched off" heads-up with Re-arm
all; no protection card on Today; no pause since the deploy. `smoke-all.ps1`: PASS 21, FAIL 4 -- three Gemini credit failures
(billing) and one false FAIL from the script's cash check, fixed in PR 139.1.

---

## PR 139 — a stop-out is not bought back by the app (D114)

**Report (2026-10-22 review, gap G2).** SOXL was stopped out twice and re-bought by the app both times. Production ledger and rule
events: 10-12 trailing stop sold 19.68 at 130.43; 10-14 "Fund this sleeve" bought 8.75 at 127.57; 10-15 sleeve funding bought 8.46
at 110.54; 10-16 trailing stop sold 16.97 at 93.96; 10-19 sleeve funding bought 6.81 at 98.65 (above that sale); 10-22 SOXL 82.11.

**Root causes (PROVEN: reproduced on HEAD `156e29c` by `tests/test_a_stop_out_is_not_bought_back.py`, and seen live).**
1. A stop-loss / trailing-stop exit is a `sell_position` with no hold flag (`rules_service.execution_plan`), so the sale recorded
   nothing. Live: the 10-12 and 10-16 exits' executed detail has no `buyback_hold`; no `buy_back` rule existed after either.
2. The only hold (PR 75, take-profits) ends the moment the price is below the sale price: in a falling market, at once.
3. The Plan buttons (`load-basket`, `/plan/sleeves/fund`) run the funder with `honour_holds=False` by design (PR 75), and those
   are the buys that happened (audit: `/strategies/btm_trend_soxl/load-basket` and `/plan/sleeves/fund` before each re-buy).
4. Found while fixing: a `buy_back` row on a ticker you no longer hold was an "orphaned exit" to the evaluator, so it was
   deleted by `purge_orphan_rules` and hidden by the list. Any hold after a FULL exit vanished on the next evaluation.

**Ruled out.** A hold that existed and was released early (no `buy_back` row and no `buyback_hold` in either exit's detail);
a Today card as the buyer on 10-14 / 10-19 (the audit shows the Plan-screen routes at those minutes); a price-feed fault
(the fills match the day's closes).

**Fix.**
- `buyback_hold`: a STOP kind. No price ends it. `record_stop` names the owning sleeve (from its catalogue weights, so a sleeve
  that is out still owns its ticker); the 06:15 signal job (`step_signal`) marks it out when the sleeve's target for the ticker
  is zero and ends it at the next "in". No signal reading yet counts as NOT out. A later take-profit never downgrades it.
- Protective exits (stop-loss, trailing stop, profit-protect, trend break) record one on Accept.
- Every buyer honours it: `_fund_sleeve` / `fund_plan` drop the ticker and say why (status `held_back`, `refused: STOP_HOLD`);
  each card executor calls `assert_buyable` BEFORE its first write (position writers commit as they go, so a later refusal
  would leave half a trade); redeploy-cash skips the leg; replace-book refuses; `buy_into_book` refuses as a backstop unless
  `recording=True` (your own Holdings > Add, a record of a broker trade). The Accept route turns a refusal into 400, card not done (the wrapper lives in the route so `apply_recommendation` keeps its source, which two tests read).
- `buy_back` is an ENTRY type (cause 4). A stop hold is never quoted (no price matters).
- Holdings > Rules: "Stop hold · until the sleeve re-enters"; ending one asks a confirm that names the stop and its price. `sw.js` iw-v71.

**Tests.** `tests/test_a_stop_out_is_not_bought_back.py` (9): the three reproductions (no hold, Plan re-buys, a lower price ends
it); the hold names its sleeve and ends only on out-then-in, unknown is not out; a funded-buy card refuses with nothing sold;
your own add is exempt; ending the hold lets the Plan fund it; a later take-profit keeps the stop hold; an AST guard that every
`buy_into_book` caller in app/ checks the hold (mutation-tested: removing the fee-swap check fails it). Full suite on `f769e40`, Python 3.12: **Postgres 2,834 passed, 1 skipped, 0 failed** (the gate); SQLite 2,832 passed, 1 skipped, 2 failed, both `test_the_pins_are_installable_and_installed` from the sandbox's newer pywebpush / py-vapid (22 / 22 pass with the pinned versions). Ruff clean on app and tests. The first full run found two things, both fixed here: a regression of mine (two tests read `apply_recommendation`'s source; the HeldBack wrapper now lives in the Accept route), and a pre-existing Postgres crash in PR 137's `test_an_ignore_is_not_overruled_by_an_old_done` (the shared session borrowed inside `asyncio.run()`; it failed the same way on `156e29c`), now a NullPool engine in its own loop.

**Smoke.** `scripts/smoke/smoke-pr139-stop-out-hold.ps1 -Sha <sha> -Since <deploy time>` (read-only). Production can't serve it yet
(Railway outage), so it was run twice: against production on `ae46931` to prove every field it reads exists (PASS 1, FAIL 0,
SKIP 3: no hold on the book yet; it lists the 10-12, 10-16 and 09-14 SOXL exits as "no hold (expected before PR 139)"); and
against this build served locally on a migrated database, after a SOXL stop fired and was accepted: **PASS 6, FAIL 0, SKIP 0**.
On that local book, beyond the smoke: the SOXL top-up card left Today, "Fund this sleeve" and "Fund my sleeves" refused with
the stop named, SOXL stayed at 0; after ending the hold, the sleeve funded (7.99 SOXL). Smoke fix found on the way: PowerShell
reads an ISO `Z` time as local (+03:00 on the owner's Mac), which silently dropped the newest exits; times are now compared in UTC.

**C16b narrowed, not closed.** Why the stops were OFF: SOXL stop `c89d8251` (made ~10-19, unstamped, SOXL held throughout) had
one pausing writer in its life per the audit log, the protection card Accept at 10-20 14:21:50. Strong by elimination only:
`apply_rules_tuning` writes `active=False` and no event, so nothing records what it paused. PR 140.

**Verified live (2026-10-23).** Merged as #51 (`606ebec`), Railway deployed 05:26 UTC. Smokes on production with
`-Sha 606ebec`: PR 137 PASS 5 / SKIP 1 (no sleeve top-up on Today), PR 138 PASS 6, PR 139 PASS 2 / SKIP 3 until a stop
exited. The owner accepted the open SOXL stop card (`c89d8251`) at 05:45 UTC: its executed detail carries a stop hold
(82.11, `btm_trend_soxl`, signal not out), and the PR 139 smoke with `-Since 2026-10-23T05:26:00Z` then read **PASS 6,
FAIL 0, SKIP 0**. A dry run of "Fund my sleeves" on production marks `btm_trend_soxl` `held_back` with the stop named; SOXL
stays at 0. `smoke-all.ps1`: PASS 22, FAIL 3 -- all three are Gemini "AI credits are exhausted" (billing, not code). That
script calls `POST /rules/evaluate` despite its read-only header (it triggered nothing); fix in PR 145.

---

## PR 138 — Ignore always works, a sleeve that cannot be funded says so loudly, the chart shows today, a stop that is off is a card (D110-D113)

**Report (2026-10-22).** (1) The account chart trended up while the portfolio value fell. (2) Ignore did nothing on a sleeve-move card. (3) Wanted a bold
notice when a sleeve cannot be funded, with the option to drop it. (4) No card appeared while SOXL fell 20% in a session.

**Root causes (all PROVEN on production reads / reproduced).**
- Chart: the series ends at the last daily snapshot (10-21, 21,122.55 ILS); the live total on the same screen includes today's SOXL -20%. Both were
  correct; nothing said they covered different days. Fix: `provisional_point` adds ONE dashed "Today so far" point, only when the comparison is honest
  (same holding count, no trade or money moved since the snapshot); otherwise a reason and no point.
- Ignore: `rec_0164ed` is `persistent`. `persistent` was subtracted from the IGNORED set as well as from DONE, so every Ignore was written and then
  discarded on the next read. Fix: `settle_mutes` — persistent only protects against Done.
- Unfundable sleeve: PR 137 made it a quiet heads-up. Now `payload.blockers` carries a red banner built from the funder's own refusal, with two
  choices: open the plan, or drop the sleeve (`DELETE /plan/sleeves/{id}`, sells nothing). Swiping the heads-up does not silence it.
- No card in the drop (PROVEN from live rules): SOXL stop-loss 83.85 and TQQQ stop 65.73 were switched OFF with price below/near them, and an inactive rule
  raises nothing; the drawdown guard (25%) was not reached (book -20.2%); no card read a one-day move. Why the stops were OFF is NOT proven (the audit
  log holds no toggle for 5 of the 6 rules). Fix: a HIGH card "Your X stop is switched off and the price is below it", Accept = `edit_rule` re-arming
  it (nothing is sold); and a non-actionable heads-up for a one-day move of 10% or more (setting `big_day_move_pct`). Facts only, never "sell".

**Ruled out.** A stale chart cache; wrong NAV maths (the 21,122.55 figure matches the snapshot); a client bug in the Ignore button (the POST returned 200).

**Not fixed / open.** Why the stops were switched off. Railway deployments failed on 2026-10-22 during a Railway builds outage ("failed to fetch snapshot"):
platform side, not code; redeploy once Railway says builds are back.

**Tests.** `test_an_ignore_works_on_a_persistent_card`, `test_a_sleeve_the_app_cannot_fund_is_a_banner`, `test_the_chart_says_where_it_stops`,
`test_a_stop_that_is_off_is_a_card`. Smoke: `scripts/smoke/smoke-pr138-ignore-blocker-chart-stops.ps1`.

## PR 137 — the sleeve-drift card that kept coming back (D107, D108)

**Report (2026-10-22).** "It doesn't matter if I click Accept or Ignore, this card is always coming back to Today" (TQQQ sleeve drift,
`rec_2e52d5`, banner "it asked for about 1,423 ILS when you filed it and asks for about 2,261 ILS now"). Two separate bugs, both proven.

**Root cause 1 — Ignore lost to an old Accept (PROVEN, reproduced end to end on HEAD).**
- The card had a DONE mark from 10-20 (1,423 ILS) and a newer IGNORE mark from 10-21 (2,261 ILS).
- The two maps were merged with `{**dis_map, **done_map}`, so the done mark always won, however old.
- `moved_on` compared today's number with the stale 1,423, saw a move over 25%, and un-muted the card. Pressing Ignore again re-filed
  the ignore mark, which lost again.
- Fix: `resolve_marks` keeps ONE mark per card id, the newest of its ignore and done marks (a tie goes to done). The PR 38 "done, but the
  book contradicts it" branch is now chosen by which mark won, not by membership of the done map.

**Root cause 2 — Accept did nothing (PROVEN by a live read-only dry-run of the funder).**
- The top-up the card offered is always refused by the funder's `_drift_gate` (it would move the book away from the plan). The card never
  asked the funder first, so Accept returned HTTP 400, no trade was made and no done mark was written. Hence the card came straight back.
- Fix: the sleeve-drift card now previews with `load_basket(mode="fund", dry_run=True, honour_holds=True)` before it offers Accept. A
  refusal turns the card into a Heads-up (`apply.kind` none, `meta.held_back` carries the funder's reason). A preview that errors leaves
  the card as it was; a preview must never kill Today.

**Ruled out.**
- Card id churn: the id is sha1(category|ticker), without the amount, so it is stable.
- The client sending the wrong state: `RECMAP` sends the card's amount and severity, as designed.
- TTL expiry: the ignore (7 days) and done (14 days) marks were both live.
- Core realign selling TQQQ (an early hypothesis): retracted. See BACKLOG C15, which is still open.

**Tests.** `tests/test_an_ignore_is_not_overruled_by_an_old_done.py` (8, including the end-to-end reproduction);
`tests/test_a_topup_the_funder_refuses_is_not_a_card.py` (4); `tests/test_a_done_the_book_contradicts.py` updated (source-grep string).
Full local SQLite suite: 2,793 passed, 1 environmental failure that also fails on a clean HEAD export. Ruff clean.

**Added to this PR (2026-10-22, same day): why the sleeve could not be funded at all (D109).**
- Symptom: Plan > My mix showed the TQQQ sleeve 7.3 pts short and the core 6.7 pts over, and nothing could fix it.
- Root cause (PROVEN: live dry-run of the funder + the ranking code). `rank_trim_candidates` orders sales by plan fit and tax only. On a book
  sitting on its class targets every class is "at target", so tax alone decides, and a loss-making Commodities holding (UGA) ranks level with a
  loss-making Equities one (GOOGL). The funder sold GOOGL 1,310 and UGA 951 to fund an Equities sleeve; its own drift gate (off target 1.2 -> 9.6 pts)
  then refused the whole plan. XOM and the other core strays, which are Equities and would have been a neutral swap, were never reached.
- Fix: `plan_funding(..., swap_first=True)`, used by `_fund_sleeve` and `fund_plan`: sales inside the class being bought go first, order inside
  each group unchanged. Other funding callers (cards, target solver) are untouched.
- Tests: `tests/test_a_sleeve_is_funded_by_swaps_before_rebalances.py` (4; the first reproduces the bug without the flag). Full suite 2,797 passed,
  1 environmental failure (same as before).
- Ruled out: the plan targets (Equities held 88.98% vs 89.29% target: not the cause), the core-realign card (it is a Heads-up for a different
  reason: the strays are "not sold here" by design), the PR 137 mute logic.
- Not verified live: the fixed plan has only been run against a fixture shaped like the live book. After deploy, Plan > "Fund this sleeve" should
  preview GOOGL + XOM (+ XLE) instead of GOOGL + UGA.

**Not run, on purpose.** The local Postgres job (no Postgres on this Mac; nothing here touches SQL). GitHub Actions (billing-blocked, BACKLOG O6).
The smoke script `scripts/smoke/smoke-pr137-held-back-topup.ps1` was written without PowerShell on this Mac, so it was neither parse-checked nor run:
run it after the merge.

**Still open.** BACKLOG C15 (core realign keeps selling TQQQ right after top-ups, net -991 since 10-14: unexplained), C16 (the `fund_plan` card
has no funder preview), C17 (the refusal alert shows only the short error).

---

## PR 136b — wave 5, part 2: the optimiser's card (D106)

**What.**
- `book_optimizer.card`, from the stored result and only while it is about the sleeves held:
  - a better book by `material_odds_pts` (5) or more -> "The best book we can measure for your goal: X% -> Y%" (HIGH):
    goal chance and chance of halving before -> after (with the halving limit), the steps, what step B found on
    protection and core; Accept sets the sleeves (`sleeve_move`; sells nothing);
  - an empty book -> "Your starter book: goal chance X%"; it reaches Today even with no holdings (the empty-book
    early return now carries it), and Accept sets up the plan;
  - nothing material while under the on-track bar -> a heads-up saying how many changes were measured, that none
    clears the bar, and the price of the halving limit: the chance without the protective rules and its chance of
    halving. The limit is the owner's to change.
- `score_books` also returns the chance of halving with no rule acting; the optimiser stores both "without rules"
  figures and the on-track bar.

**Tests.** `tests/test_pr136b_optimizer_card.py` (7): a better book is actionable and Accept sets the sizes without a
trade; nothing material under the bar -> the heads-up with the price of the limit; a small gain is not a card to act
on; no card about another book; on track with nothing to gain -> no card; the starter card reaches Today for an empty
book; the starter card's Accept sets up the plan. Local SQLite: 2,781 passed, 1 failed (the pre-existing `dismissed_count` test). Local Postgres (full): 2,781 passed, 1 failed (the same). CI blocked (billing); merged on local results (owner-authorised). Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr136b-optimizer-card.ps1` (read-only); all six unrun smokes (134d-136b) in one run: `scripts/smoke/smoke-sleeve-plan-all.ps1`. Result: SMOKE136B. `run-optimizer.ps1` now says "keep" for the core plainly instead of naming a best core that is not worth switching to.

---

## PR 136a — wave 5, part 1: the goal optimiser (D105)

**Owner, 2026-10-18:** "ok, continue" into wave 5; for a new user with an empty book, include a starter book; the core
"a variable and not hard coded". PR 135c live as `2de5e54` (wave 4 complete).

**What.**
- `app/services/book_optimizer.py`. Step A: up to 3 greedy steps -- add a catalogue strategy (never a same-bet
  look-alike of what is held, never Explore-only; at its suggested size within the room) or remove a sleeve; every
  candidate scored beside the current book in ONE solve (one window); the step's winner re-sized by the goal model's
  own proposal and re-checked; kept only when it gains `optimizer_min_step_pts` (new setting, 0.5) and the chance of
  halving stays within `halve_limit_pct` (or no higher than today's when today's is already over). Step B: the whole
  change in one full solve -- the protection retune against the halving limit and the core choice on that book.
- `frontier_service.score_books(nominal_nav=..., full=...)`: an empty book is scored on a nominal amount (a %/yr goal
  is scale-free: target = amount x (1 + ROI)^years); each score carries the model's `proposed` sizes; `full` adds the
  protection retune and core choice. `_goal_context(nominal_nav=...)`.
- The core comes from `_core_basket` on every solve: the user's chosen core, else the objective's mix -- pinned by a
  test that switches cores. **The goal model supports only %/yr goals**: a plan without a yearly ROI target gets
  `NO_GOAL` (so the "how much will you start with?" question for fixed-amount goals has no model to feed yet).
- Job `book_optimizer` 05:10; `GET /plan/optimizer`, `POST /plan/optimizer/refresh`; `scripts/run-optimizer.ps1`
  prints today vs the best book, the steps, and step B. No card yet (PR 136b). Nothing traded. 33 settings.

**Tests.** `tests/test_pr136a_book_optimizer.py` (11): candidates never add a look-alike or an Explore-only, remove
each; an empty book only adds; no room, no add; steps best first and only above the minimum; a step that breaks the
halving limit is not taken; the minimum is a setting; a real run on a book with a goal is stored and served; an empty
book gets a starter book; no goal, no optimiser; the core is read at run time. Local SQLite: 2,774 passed, 1 failed (the pre-existing `dismissed_count` test). Local
Postgres (full): 2,774 passed, 1 failed (the same). CI blocked (billing); merged on local results (owner-authorised). Ruff clean.

**First production run (owner, 2026-10-18, `scripts/run-optimizer.ps1` on `28c9187`):** 10 candidates measured, halving limit 30%, minimum step 0.5 -- **no change qualifies**: today and best both 8.9% goal chance, 2.9% chance of halving; the protection retune on that book reaches 10.0% (halving 4.3%), not material; the core stays (Classic 60/40). The goal (~29%/yr) is out of reach of every sleeve, size, protection and core change the app can measure within a 30% halving limit; the lever left is the limit itself (~26% without the rules, ~67% halving).

**Smoke.** `scripts/smoke/smoke-pr136a-optimizer.ps1` (read-only); in `smoke-sleeve-plan-all.ps1`. Result: SMOKE136A.

---

## PR 135c — wave 4, part 3: an overweight sleeve is a choice -- trim, or raise the size (D104)

**Owner, 2026-10-14** (plan v1, item 4): overweight must get a card too, "option 3 (trim or raise the size) with a
recommendation that is based on the agents analysis". PR 135b live as `44c1b71`.

**What.**
- `sleeve_moves`: the nightly scoring adds a "raise" move for each sleeve held above its size by more than
  `sleeve_drift_band_pts` (the plan's size set to what is held); it is never a card of its own.
- `raise_recommendation` (pure; the rule fixed before any card showed it): RAISE only when the goal chance does not
  fall and the chance of halving does not rise by `material_odds_pts` (5) or more; otherwise TRIM, also when unmeasured.
- The per-ticker overweight card (`sleeve_drift`) becomes `overweight_choice` when a scored raise exists for the
  sleeve behind it: options `trim` (the existing trim) and `raise` (a `sleeve_move`, sells nothing), the recommended
  one marked with its reason. One card per position, as before. Accept needs `choice=trim|raise`.
- Today: two tiles, "Trim <ticker> back to N%" and "Raise the sleeve to M%". `sw.js` iw-v69.
- **Fix -- one window per strategy.** The owner's first production scoring (2026-10-18) read the book at 7.7% while the
  goal card said 9.0% and the light preview 8.5%: every hedge was loaded into ONE solve, and the youngest fund (KMLM,
  2020) shortened the price window for all of them. Each added strategy is now scored in its own group beside the book
  on that group's window; every move carries its own `before`; the headline is the plain solve (= the preview).
  **Measured on that run (owner's book, before the fix): no move clears the 5-point bar** -- the best, "remove
  Volatility-Targeted Leverage", +1.2 points -- so no card showed, as designed. The comparison also showed where the
  goal chance goes: 25.8% without the ~30 armed rules, 9.0% with them; the chance of halving 66.7% -> 0.5%.

**Tests.** `tests/test_pr135c_overweight_choice.py` (9): the rule's cases; an overweight sleeve gets a raise move that
never becomes its own card; each added strategy is scored on its own window (regression for the fix); on a book with SOXL over its 10% sleeve: both options offered with RAISE recommended when
it helps and TRIM when it costs goal chance; Raise sets the size and trades nothing; Trim sells the excess; Accept
without an answer is refused; no scored raise -> the plain trim card. Local SQLite (full, after the window fix): 2,761 passed, 1 failed (the pre-existing `dismissed_count` test). Local Postgres (full): 2,761 passed, 1 failed (the same). CI blocked (billing); merged on local results (owner-authorised).
Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr135c-overweight-choice.ps1` (read-only). Result: SMOKE135C.

---

## PR 135b — wave 4, part 2: the sleeve cards (D103)

**What.**
- `sleeve_moves.select` (pure): the best replacement for each side of a same-bet pair (HIGH); up to two moves that
  raise the goal chance by `material_odds_pts` (5) or more; one "safer, lower chance" move that cuts the chance of
  halving by that much (owner decision 4: both ways).
- `sleeve_moves.cards`: Today's cards from the stored scores, only while they are about the sleeves held. Each says
  goal chance before -> after and chance the book halves before -> after, names every size that changes, and that
  Accept buys and sells nothing.
- Accept (`sleeve_move`, `apply_move`): sets every sleeve to the card's sizes -- removals and shrinks first -- re-arms
  the caps, refuses untouched if the book moved since scoring. The trim card and "Fund this sleeve" carry the trades.
- Recommendations: the cards join the Today build beside the goal cards (degrades alone as `sleeve_moves`).

**Tests.** `tests/test_pr135b_sleeve_cards.py` (8): raises capped and best first; one safer card only when material;
nothing material -> no card; the best replacement for a look-alike always shows; a card quotes both numbers and says
Accept sells nothing; no card when the scores are about a different book; Accept refuses when the book moved;
**card claims** -- applying a scored move produces its sizes, and re-scoring gives the goal chance the card quoted.
Local SQLite: 2,752 passed, 1 failed (the pre-existing `dismissed_count` test). Local Postgres (full): 2,752 passed, 1 failed (the same). CI blocked (billing); merged on local results (owner-authorised). Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr135b-sleeve-cards.ps1` (read-only; never accepts). Result: SMOKE135B.

---

## PR 135a — wave 4, part 1: every sleeve move scored by the goal model (D102)

**Owner, 2026-10-18:** "ok, continue" (wave 4 of the sleeve plan: actionable sleeve cards, each showing what its move
does to the goal's chance and to the chance the book halves, both ways). PR 134d live as `dbd71d0`.

**What.**
- `frontier_inputs.load_context(extra_sleeves=...)`: catalogue strategies not held load at 0% beside the held ones.
- `frontier_service.score_books`: the goal's chance (with rules), the chance of halving, the median and growth for
  each of several hypothetical sleeve sets -- one context load, one rules replay, the light goal solve per book.
- `app/services/sleeve_moves.py`: `candidate_moves` (each hedge -- badged, not Explore-only, not the same bet as a
  held sleeve -- at 1x and 2x its size out of each High / Very high sleeve; remove each sleeve; for a same-bet pair,
  replace either side with a different-bet strategy at its size) -> `compute` -> stored per user in `kv_settings`.
  Job `sleeve_moves` at 04:40 (raises only when every book failed); `GET /plan/sleeve-moves` (never computes),
  `POST /plan/sleeve-moves/refresh`; `scripts/refresh-sleeve-moves.ps1` scores on demand and prints the moves.
- No card yet (PR 135b). Nothing traded.

**Tests.** `tests/test_pr135a_sleeve_moves.py` (7): hedge moves at 1x / 2x from the leveraged sleeve, other sleeves
untouched; no hedge beside a look-alike; a remove per sleeve; replace both ways for a same-bet pair, never into a
look-alike or an Explore-only; end to end on a seeded book with a goal (scored, stored, served); a book without sleeves
is not scored. `test_every_job_says_when_it_fails` covers the job. Local SQLite: 2,744 passed, 1 failed (the pre-existing `dismissed_count` test). Local Postgres (full): 2,744 passed, 1 failed (the same). CI blocked (billing); merged on local results (owner-authorised).
Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr135a-sleeve-moves.ps1` (read-only). PR 134d smoke: SMOKE134D. Result: SMOKE135A.

---

## PR 134d — every option-3 pass joins the catalogue; Quality Momentum 5 is Explore only (D101)

**Measured on production, 2026-10-18** (owner, on `8ac8554`; PR 134c smoke **PASS 11, FAIL 0, SKIP 0**: the three
hedges badged, none the same bet as the owner's sleeves, S1 measured with MMC dropped).

**Option 2 (hot stocks, 114 names after MMC) — all fail the Beat-the-Market gate on their own** (QQQ 20.65%/yr):

| | /yr | Worst drop | Risk | S1 beats QQQ | S2 selection edge | S3 overfit | S4 sweep | Corr with VTL sleeve |
|---|---|---|---|---|---|---|---|---|
| S1 Momentum 10 | 10.17% | -32.97% | Medium | fail | fail (-8.43) | holds up | pass | 0.642 |
| S2 Quality Momentum 5 | 14.32% | -36.79% | Medium-high | fail | fail (-4.28) | holds up | fail | 0.547 |
| S3 52-Week-High Leaders | 7.62% | -21.55% | Medium | fail | fail (-10.98) | holds up | pass | 0.534 |

**Option 3 re-run** (book alone 30.81%/yr, worst drop -51.38%, return/risk 0.722; QQQ 20.65%): passes at 1x / 2x —
D1 1x only (0.742, exactly +0.020; it failed on 10-16), D2 both (2x now passes: 20.85% > QQQ), D3, D4, D7, D8, D9, D11,
D12, and **S2 both** (28.86% / 26.53%, return/risk 0.755 / 0.772). Fail: D5, D6, D10, D13, S1, S3. The window moved
two days and the book's worst drop deepened (-49.65% -> -51.38%), which is why borderline rows flipped.

**Owner, 2026-10-18: "add the passed strategies".**

**What.**
- `strategy_catalog`: `div_gtaa5` (badged "Borderline: passed by the narrowest margin"), `div_trend_risk_parity`,
  `div_gold_trend`, `div_treasury_trend`, `div_kmlm` ("Short history (since 2020)"), `div_qqq_trend_short` (a Nasdaq
  bet both ways: next to a Nasdaq sleeve it can replace, not join — D97), `r2_quality_momentum_5` (universe US115 less
  MMC, as measured). Each badged "Hedge for a leveraged sleeve", each description stating its measured 1x trade.
  The catalogue now lists 18 strategies.
- `explore_only`: Quality Momentum 5 is shown and measured, and `apply_strategy` refuses it as a new sleeve — the app
  cannot hold and rotate individual stocks yet. Explore shows a disabled "Explore only" button.
- `rank_select` gets a plain-language rule line; a card with no fixed basket says it picks from a list.
- Style chips: a selection rule's style comes from its universe; EFA, IEF, TLT, VNQ, KMLM, DBMF and the geared / -1x legs (SSO, UBT, UGL, PSQ, SH) join the ticker buckets -- they fell through to "single stock" (32% assumed volatility, "Concentrated"), which also mis-stated the static risk of anyone holding them unmeasured.
- `sw.js` iw-v68.

**Tests.** `tests/test_pr134d_more_hedges.py` (16); the catalogue-shape test accepts `rank_select` (a universe, not a
basket); PR 134c's "only these three" test now pins only that the three are present. Local SQLite (full, after the style fix): 2,735 passed, 1 failed (the pre-existing `dismissed_count` test). A first run caught `test_p2_cards::test_every_card_carries_style_and_horizon` (no style for a basket-less rule), fixed above. Local
Postgres (full): 2,735 passed, 1 failed (the same). CI blocked (billing); merged on local results (owner-authorised). Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr134d-more-hedges.ps1` (read-only). Result: SMOKE134D.

---

## PR 134c — three hedges join the catalogue (D100); round 2 no longer aborts on a missing ticker

**Round 2 measured on production, 2026-10-16** (owner ran `scripts/measure-round2.ps1` on `6167c34`; PR 134b smoke
**PASS 9, FAIL 0, SKIP 0**, including D8's book effect on one window from 2019-05-08 — the production check of PR 134a).

**Option 2 did not run:** the provider has no history for MMC, and one missing name aborted S1-S3 (and their option-3
rows). Fixed here (below); option 2 is re-run after deploy.

**Option 3 — taken out of Volatility-Targeted Leverage** (book alone 30.53%/yr, worst drop -49.65%, return per unit of
risk 0.735; QQQ 20.97%; D7 from 2020-12, D8 from 2019-05):

| Code | x | Moved | Book /yr | Worst drop | Return/risk | Verdict |
|---|---|---|---|---|---|---|
| D1 GTAA-5 | 1 / 2 | 20 / 40% | 25.30 / 19.79 | -41.95 / -36.45 | 0.747 / 0.739 | fails F2 / F1, F2 |
| D2 Trend Risk Parity | 1 / 2 | 20 / 40% | 25.57 / 20.10 | -39.52 / -30.75 | 0.765 / 0.775 | **passes** / fails F1 |
| D3 Gold Trend | 1 / 2 | 10 / 20% | 28.53 / 26.32 | -44.67 / -39.93 | 0.764 / 0.790 | **passes / passes** |
| D4 Treasury Trend | 1 / 2 | 15 / 30% | 26.47 / 21.82 | -41.43 / -34.23 | 0.758 / 0.762 | **passes / passes** |
| D5 Non-US Trend | 1 / 2 | 15 / 30% | 26.79 / 22.66 | -41.81 / -37.57 | 0.744 / 0.735 | fails F2 |
| D6 GEM | 1 / 2 | 20 / 40% | 25.05 / 19.34 | -44.05 / -40.84 | 0.710 / 0.657 | fails F2 / F1, F2 |
| D7 KMLM | 1 / 2 | 15 / 30% | 27.71 -> 25.64 / 23.12 | -49.2 -> -41.0 / -36.28 | 0.665 -> 0.729 / 0.794 | **passes / passes** |
| D8 DBMF | 1 / 2 | 15 / 30% | 32.12 -> 29.52 / 26.23 | -49.64 -> -41.82 / -38.22 | 0.732 -> 0.784 / 0.825 | **passes / passes** |
| D9 Nasdaq trend + PSQ | 1 / 2 | 15 / 30% | 27.78 / 24.29 | -40.74 / -36.36 | 0.767 / 0.766 | **passes / passes** |
| D10 S&P trend + SH | 1 / 2 | 15 / 30% | 26.63 / 22.20 | -41.46 / -33.41 | 0.750 / 0.738 | fails F2 |
| D11 2x Trend Risk Parity | 1 / 2 | 15 / 30% | 27.14 / 23.44 | -43.11 / -36.77 | 0.757 / 0.768 | **passes / passes** |
| D12 2x Gold Trend | 1 / 2 | 10 / 20% | 28.89 / 27.11 | -45.18 / -41.24 | 0.769 / 0.799 | **passes / passes** |
| D13 3x Treasury Trend | 1 / 2 | 10 / 20% | 27.24 / 23.95 | -44.62 / -41.72 | 0.741 / 0.742 | fails F2 |

Every pass LOWERS the book's return and cuts its worst drop; none beats the market alone (PR 134a). D9 is "the same
bet" as Volatility-Targeted Leverage under D97 (Nasdaq both ways); D3 and D12 are near-duplicates (0.996).
**Owner: "only the best 3"** — DBMF (best return per risk), 2x Gold Trend (smallest return cost), 2x Trend Risk Parity.

**What.**
- `strategy_catalog`: `div_dbmf`, `div_2x_gold_trend`, `div_2x_trend_risk_parity`, specs identical to the measured
  ones, each badged "Hedge for a leveraged sleeve" (DBMF also "Short history (since 2019)"), descriptions carrying the
  measured trade. Cards carry `badges`; Explore shows them. `risk_parity` gets a plain-language rule line.
- `round2.measurable_spec`: a universe member with no price history is dropped from that run and reported in
  `universe_dropped` (the registered spec is unchanged); used by the gate, S5 and option 3.
- `sw.js` iw-v67.

**Tests.** `tests/test_pr134c_hedges_join.py` (11). Local SQLite: 2,590 passed, 2 failed — the catalogue shape test, whose overlay allow-list predated `risk_parity` in the catalogue (now allowed; 20/20 on re-run), and the pre-existing `dismissed_count` test. Local Postgres (full): 2,591 passed, 1 failed (the pre-existing test). CI blocked (billing); merged on local results (owner-authorised). Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr134c-hedges.ps1` (read-only; measures S1 to prove option 2 now runs). Result on `8ac8554` (owner): **PASS 11, FAIL 0, SKIP 0**.

---

## PR 134b — round 2: hot stocks on 115 US names (option 2) and diversifiers funded from the leveraged sleeve (option 3) (D99)

**Owner, 2026-10-15:** after none of wave 3's 13 candidates passed, "once you complete 134a I want you to run both
option 2 and option 3"; option 2 approved as written; option 3 at the suggested size and at double ("both"). Plan:
[`docs/plans/ROUND2_STOCKS_AND_SLEEVE_FUNDING_2026-10-15.md`](docs/plans/ROUND2_STOCKS_AND_SLEEVE_FUNDING_2026-10-15.md).
Correction recorded there: the approved universe has **115** names (first described as ~125); the list itself is
exactly what was approved.

**What.**
- `app/services/round2.py`: US115; S1 Large-Cap Momentum 10 (`mom_12_1`, top 10, buffer 20), S2 Quality Momentum 5
  (`risk_adj_mom`, top 5, buffer 10), S3 52-Week-High Leaders (`high_52w`, top 6, buffer 12), monthly, T-bills when
  SPY is below its 200-day. `evaluate_stock_blocking`: the Beat-the-Market gate (beats QQQ after tax; selection edge;
  not "likely overfitted"; sweep within 5) plus the book effect and the same-bet verdict against each sleeve held;
  `near_duplicates_blocking` for S5.
- Option 3: `book_effect.compare_blocking(fund_from=...)` takes the candidate's room out of a named held sleeve
  (core untouched; refuses a sleeve not held) and reports QQQ's return over the same window.
  `evaluate_funding_blocking`: D1-D13 and S1-S3 out of Volatility-Targeted Leverage at 1x / 2x; F1 the book still beats
  QQQ, F2 the book-effect rule.
- `GET /strategies/round2/stocks/{S}/measure`, `/strategies/round2/stocks/near-duplicates`,
  `/strategies/round2/funding/{code}/measure?scale=1|2` (read-only). `scripts/measure-round2.ps1` runs it all on
  production and saves the JSON. Nothing joins the catalogue; what passes does in PR 134c.

**Tests.** `tests/test_pr134b_round2.py` (11): US115 once each; S1-S3 match the approved text and their scores exist;
each runs on the engine; `fund_from` shrinks only that sleeve; a sleeve not held is refused; option 3 at 1x and 2x with
F1 / F2; S-candidates take option 3 too; scale is 1 or 2. Local SQLite: 2,540 passed, 1 failed (the pre-existing `dismissed_count` test). Local Postgres (full, resumed after a low-memory stop): 2,540 passed, 1 failed (the same). CI blocked (billing); merged on local results (owner-authorised). Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr134b-round2.ps1` (read-only; D4 x1 end to end, and D8's book effect on one window —
the production check of PR 134a). Result on `6167c34` (owner): **PASS 9, FAIL 0, SKIP 0**.

---

## PR 134a — wave-3 results: none of the 13 passes; the book effect compared young funds on the wrong window

**Measured on production, 2026-10-15** (owner ran `scripts/measure-diversifiers.ps1` on `66e152d`; PR 133 smoke
**PASS 12, FAIL 0, SKIP 0**). Window 2016-10 to 2026-10 unless noted; QQQ 20.81%/yr over it. Book effect against
the owner's book at each candidate's suggested size, core making room: the book alone is **30.41%/yr, worst drop
-49.61%, return per unit of risk 0.732**.

| Code | Strategy | /yr | Worst drop | Risk (measured) | Corr QQQ | Book /yr | Book drop | Return/risk gain | Gate |
|---|---|---|---|---|---|---|---|---|---|
| D1 | GTAA-5 | 3.83% | -22.75% | Medium | 0.62 | 29.06% | -49.34% | -0.017 | fails B1, G2 |
| D2 | Trend Risk Parity | 4.42% | -12.93% | Medium | 0.30 | 29.38% | -49.44% | -0.001 | fails B1, G2 |
| D3 | Gold Trend | 6.52% | -32.35% | Medium | 0.07 | 30.37% | -49.56% | +0.013 | fails B1, G2 |
| D4 | Treasury Trend | 0.95% | -14.92% | Medium | -0.20 | 29.25% | -48.86% | -0.002 | fails B1, G2, G3 |
| D5 | Non-US Stocks Trend | 4.62% | -19.59% | Medium | 0.45 | 29.59% | -49.34% | -0.013 | fails B1, G2 |
| D6 | GEM | 3.95% | -34.23% | Medium | 0.77 | 28.72% | -51.80% | -0.050 | fails B1, G2 |
| D7 | Managed Futures (KMLM) | 6.95% (2020-12+) | -31.01% | Medium | -0.16 | *wrong window* | | | re-measure |
| D8 | Managed Futures (DBMF) | 9.80% (2019-05+) | -20.39% | Medium | 0.17 | *wrong window* | | | re-measure |
| D9 | Nasdaq Trend, Short in Downtrends | 7.83% | -25.72% | Medium | 0.04 | 30.49% | -47.36% | +0.006 | fails G2, G4 |
| D10 | S&P Trend, Short in Downtrends | 1.69% | -31.57% | Medium | -0.11 | 29.44% | -48.69% | -0.007 | fails B1, G2, G3 |
| D11 | 2x Trend Risk Parity | 6.06% | -31.99% | Medium | 0.32 | 29.95% | -50.30% | -0.002 | fails B1, G2 |
| D12 | 2x Gold Trend | 8.13% | -53.04% | High | 0.07 | 30.78% | -49.97% | +0.019 | fails G2 (by 0.001), G3, G4 |
| D13 | 3x Treasury Trend | -8.94% | -84.81% | Very high | -0.22 | 29.10% | -49.47% | -0.008 | fails B1, G2, G3, G4 |

G6: D3 and D12 are near-duplicates (0.996). No candidate beats QQQ on its own; three raise the book's return a
little (D9 +0.08, D12 +0.37 points; D8 to be re-measured). The closest to G2 is 2x Gold Trend, 0.001 short of the
+0.02 bar, and it also fails G3 and G4 (a -53% drop, deeper than QQQ's). As pre-registered, nothing is changed after
the fact: **none joins the catalogue.** The specs and their results stay in `diversifier_candidates`.

**Bug found and fixed.** `book_effect.compare_blocking` aligned each run on its own tickers, so a candidate on a
young fund compared the book over 2016-2026 WITHOUT it against 2019-2026 WITH it (D7: 2020-12, D8: 2019-05). It
also touched the "improves your book" line on catalogue cards for any strategy with a young fund (Factor Stack holds
AVUV, listed 2019). Both runs now align on every ticker either needs (a selection universe's late listings still
join late). Test: `test_pr129_book_effect::test_a_young_fund_is_compared_on_its_own_window_not_a_different_decade`.
D7 and D8 are re-measured after deploy (`scripts/measure-diversifiers.ps1 -Only D7,D8`).

**Tests.** Local SQLite 2,529 passed, local Postgres (full) 2,529 passed; one failure each, the pre-existing `dismissed_count` test. CI blocked (billing); merged on local results (owner-authorised for this plan). Ruff clean.
**Smoke.** The re-measure of D7 / D8 is the check: both windows must start on the fund's first day. Result: SMOKE134A.

**Next (owner, 2026-10-15: "run both option 2 and option 3"):** [`docs/plans/ROUND2_STOCKS_AND_SLEEVE_FUNDING_2026-10-15.md`](docs/plans/ROUND2_STOCKS_AND_SLEEVE_FUNDING_2026-10-15.md), approved: S1-S3 on US115 with a T-bill fall-back; every candidate funded from Volatility-Targeted Leverage at its size and at double. PR 134b.

---

## PR 133 — wave 3: different strategies that beat the market, gated (D98); the book-effect verdict says why

**Owner, 2026-10-15:** approved the candidate list after review, with three changes: "the main goal is not only to
diversify but to beat the market (diversification is just a plus)"; "yes, add them [leveraged versions], but I want to
see the risk level per strategy"; short-history funds "ship with a badge". The list and gate are in
[`docs/plans/DIVERSIFIERS_2026-10-15.md`](docs/plans/DIVERSIFIERS_2026-10-15.md). Earlier, on every strategy card's
"does not improve your book": "is that suggestion that no one of the sleeves are good to be taken?".

**What.**
- `app/services/diversifier_candidates.py`: D1-D13 pre-registered exactly as approved, each with its expected risk
  tier and suggested size — Family A (GTAA-5, trend risk parity, gold / Treasury / non-US trend, GEM), B (KMLM, DBMF;
  short history), C (Nasdaq / S&P trend with a -1x leg), D (2x trend risk parity, 2x gold trend, 3x Treasury trend).
- `app/services/diversifier_gate.py`: B1 (beats QQQ after tax, or raises the whole book's return), G2 (book effect),
  G3 (above T-bills, shallower drop than QQQ), G4 (longest history, or the "short history" badge), G5 (sweep within 5),
  G6 (near-duplicates above 0.90), G1 ("different engine" badge at correlation <= 0.50). Candidate, QQQ and T-bills
  always run on one series dict, so they share one window.
- Engine: SSO, UBT, UGL, PSQ, SH rebuildable for the long history; a -1x fund is rebuilt as earning (1 - L) x the
  T-bill rate less |L| x the spread and fee, not as negative borrowing.
- `GET /strategies/diversifiers`, `GET /strategies/diversifiers/{code}/measure`,
  `GET /strategies/diversifiers/near-duplicates` (read-only; slow). `scripts/measure-diversifiers.ps1` runs all 13 on
  production and saves the JSON. **Nothing joins the catalogue in this PR**; the ones that pass do in PR 134a.
- Book effect: `failed` names the test that failed and its margin ("the worst drop gets 9.1 points deeper; your limit
  is 2"); the line on every strategy card shows it. `book_effect_ratio_gain` (0.02) and
  `book_effect_drop_tolerance_pts` (2) are settings (32 in all). `sw.js` iw-v66.

**Tests.** `tests/test_pr133_diversifiers.py` (16): the list is D1-D13 with risk tiers; every spec runs on the
engine; specs match the approved text; -1x rebuild; the verdict names the failed test; both limits are settings and
flip a verdict; B1 / G3 / G1 on constructed prices; the short-history badge; near-duplicates; multi-leg candidates sit
in T-bills when nothing trends. Local SQLite: 2,528 passed, 1 failed — the pre-existing `dismissed_count` test. Local Postgres (full): 2,528 passed, 1 failed — the same pre-existing test. Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr133-diversifiers.ps1` (read-only; measures D4 end to end). Result on `66e152d` (owner): **PASS 12, FAIL 0, SKIP 0** (D4 measured end to end; the book-effect line named both failed tests).

---

## PR 132 — every sleeve must be a different bet (D97)

**Owner, 2026-10-15:** "each sleeve (that I chose) MUST be different and not similar/depending on the same stocks that
other sleeve already use"; on the limit, "what is your recommendation? 0.8? 0.75? 0.7?" (0.80 chosen, plan v3);
held look-alikes "card only but the cards should be actionable" (the card is wave 4). Wave 2 of the
[sleeve plan](docs/plans/SLEEVE_OVERLAP_PLAN_2026-10-14.md).

**What.**
- `app/services/sleeve_diversity.py`: ticker -> market map (TQQQ / QLD / QQQ are one Nasdaq-100; SOXL / SMH semis;
  cash never counts); a strategy's exposure profile (largest share per market across its on / base / risk-off /
  universe states); `overlap_pct`; `same_bet` (shared market >= `sleeve_overlap_alert_pct` 40, or stored correlation
  >= `sleeve_correlation_limit` 0.80); the correlation of daily returns rebuilt from the engine's own `targets_for`.
- Correlations are measured offline and only read online: `refresh_correlations` stores the matrix in `kv_settings`
  (`strategy_correlation:v1`) and keeps the last good one when prices fail; job `strategy_correlation` at 03:50
  (after the backtest refresh); `POST /strategies/correlations/refresh` on demand; `GET /strategies/correlations` the
  table with each pair's shared market, correlation and verdict.
- Enforced where you act: `apply_strategy` refuses a NEW sleeve that is the same bet as one held (`replace` names the
  sleeve to swap instead); `swap_sleeve` refuses a swap into a bet another remaining sleeve already makes. Resizing a
  held sleeve is never refused.
- `/plan/sleeves`: `same_bet` (catalogue strategy -> the held sleeves it would repeat) and `held_same_bets`.
- Explore: "⚠ Same bet as your … sleeve: <why>" and "Replace <sleeve> with this" instead of "Add as a sleeve"; a refused
  add offers the same replace. Both limits on the Card-thresholds sheet (30 settings). `sw.js` iw-v65.

**Measured on the catalogue.** Shared market (exact, no prices needed): the six Nasdaq strategies are one bet with
each other (100%, or 66.7% for dual momentum, which also holds VTI / VXUS); Semis and Factor Stack share no market with
any of them. **Correlations could not be measured locally**: this machine's price feed returns synthetic, independent
random walks (2,600 calendar-day rows 2019-2026, QQQ vs TQQQ read 0.008). The method is tested on constructed series
(identical moves 1.0, independent ~0). The real table comes from production after deploy (the 03:50 job or the
refresh endpoint) and the smoke prints it; if 0.80 does something unexpected there, the owner sees it before it is
relied on — it is a setting.

**Tests.** `tests/test_pr132_every_sleeve_a_different_bet.py` (15); `test_every_job_says_when_it_fails` covers the new
job; `test_every_decision_is_a_setting` counts 30; PR 131's tests seed their second TQQQ sleeve directly (a book that
already holds two must still be measured). Four older tests (`test_c2_sleeves` x3, `test_c4_per_sleeve` x1) built a two-TQQQ book through the add path, which now refuses it; they test the shared-ticker arithmetic such books still need, so they use a new `look_alikes_allowed` fixture (conftest) that switches the refusal off for them only. Local SQLite: 2,508 passed, 5 failed — those four (then 30/30 on re-run with the fixture) and the pre-existing `dismissed_count` test. Local Postgres (full, resumed after a low-memory stop): 2,512 passed, 1 failed — the pre-existing `dismissed_count` test. **CI did not run** (GitHub billing); merged on the local results at the owner's word (2026-10-15). Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr132-every-sleeve-a-different-bet.ps1` (read-only). Result on `d74e6da` (run by the owner after `POST /strategies/correlations/refresh`, 2026-10-15): **PASS 11, FAIL 0, SKIP 0**. Real correlations: every catalogue pair 0.59-0.99; Semis vs Volatility-Targeted Leverage 0.78 (the owner's two sleeves, just under 0.80, so allowed together); Factor Stack vs it 0.86 (same bet by correlation alone, no shared ticker); every strategy the owner does not hold is a look-alike of that sleeve. 0.80 kept for now (recommendation: revisit, e.g. 0.75, once wave 3 adds genuinely different strategies).

---

## PR 131 — one holding split between the sleeves that share it (D95); one goal chance (D96)

**Owner, 2026-10-14/15:** "the sleeves holds much more than they should but I'm not getting any alert" (Plan mix:
Volatility-Targeted Leverage 45.7% / 25%, Trend-Filtered Leveraged Nasdaq 38.4% / 20%, Semis 13.5% / 15%), then "how
come the chances of meeting the goals are still not aligned?" (Today 43.7% / 44%, Plan 20% with rules, 28.7% without),
"show the 20%/28%". Wave 1 of the approved [sleeve plan](docs/plans/SLEEVE_OVERLAP_PLAN_2026-10-14.md) (v3).

**Root causes.**
- `sleeve_occupancy` credited each sleeve with the WHOLE position of every ticker it targets. Two sleeves both run TQQQ,
  so its 38.4% was counted twice: rows added to 97.6% while the sleeves together held 59.2% of a 60% target — no real
  overweight, so no card was right. The same rows fed the core's "sleeve overshoot" (~39 points of NAV that did not
  exist; the core may sell that much from the sleeves once it is underfunded), the health sleeve score, and the
  measured-return weights (leveraged sleeves ~84% of the book instead of ~38%).
- Every screen reads the goal verdict through `book_expectation`, which returns nothing unless the verdict is fully
  current (age, rules fingerprint, sleeve sizes, dials). Then Today and "Where this could end up" fell back to the
  projection's rough no-rules run — on the inflated measured return above. Two models, one label.

**What.**
- `sleeve_occupancy`: a shared ticker is split pro-rata to each sleeve's target weight on it (D95); rows carry
  `shared_with`; per-sleeve held now adds up to `allocated_held_pct`. `/plan/sleeves` passes `shared_with` through.
- `frontier_service.goal_stale_reason` (why a verdict is not current; `_goal_verdict_current` unchanged in effect) and
  `goal_chance` (the chance, current or not, with `current`, `stale_reason`, `stale_text`, `age_hours`). Withheld when
  the goal itself changed or the measurements are stale (`STALE_WITHHELD`).
- The projection's `goal_headline`, Today's On track (`on_track_basis: goal_model_stale` + "As of … — reason"), the
  assistant context and the Plan goal card (`/plan` → `goal_chance`) all quote it. The rough no-rules figure stands in
  only when there is no quotable verdict, still labelled "rough estimate"; the Today row then says "(rough, no rules)".
- `POST /plan/frontier/refresh?only_if_stale=true`: skipped when current or solved in the last 6 minutes
  (`AUTO_RESOLVE_MIN_AGE_H`); the screens call it once per load for an old chance.
- Mix panel chip: "N pts to fund · M pts over", or "on plan" (it counted only under-funding). `sw.js` iw-v64.

**Correction to the plan (v3, section C).** The goal lever cards (deposit / target / deadline) already exist when the
solver says OUT_OF_REACH (`test_frontier_goal`); they stand down whenever the verdict is not current, which is likely
why the owner never saw one. The owner keeps the goal fixed, so wave 5 replaces them with the optimiser.

**Tests.** `tests/test_pr131_one_number_per_sleeve.py` (11): split by target weight; rows add to the union; a ticker
one sleeve wants unchanged; measured-return weights; an old verdict quoted with its reason on projection, Today and
Plan; current says current; a different goal withheld; the auto re-solve skips (CURRENT, RECENT_SOLVE); every reason
has words; the chip counts both sides; the screens label and re-solve.
Local SQLite: 2,495 passed, 1 failed — `test_portfolio_totals::test_dismissed_recommendations_are_counted_and_restorable`,
which **fails identically on untouched `origin/main` (c5b8e43)** on this machine: live market data is rate-limited
locally, so the second Today build does not rebuild the dismissed card and `dismissed_count` reads 0. Not this PR;
CI is the arbiter. Local Postgres (full, resumed after a low-memory stop): 2,495 passed, 1 failed — the same pre-existing `dismissed_count` test. **CI did not run:** GitHub refused every job (failed account payment / spending limit); the owner chose to merge on the local results (2026-10-15). Ruff clean.

**Smoke.** `scripts/smoke/smoke-pr131-one-number-per-sleeve.ps1` (read-only): rows add to the union; projection, Plan
and Today quote one chance and label an old one; the auto re-solve skips a current goal; shell iw-v64; sleeves behind
the login. Result on `51d46a3` (run by the owner, 2026-10-15): **PASS 11, FAIL 0, SKIP 0** — the live book now runs two sleeves (Volatility-Targeted Leverage 40.4% / 40%, Semis 24.4% / 26.5%), rows add to the union 64.8%, and projection, Plan and Today all quote 20.0% (28.7% without rules), current.

---

## PR 130 — round 2: 3x Nasdaq Trend to Cash and 3x Trend with a Volatility Brake join; MH3 gives way (D94)

**Owner, 2026-10-13:** approved the round-2 candidates (plan section 7, pre-registered before measurement), then, after
seeing that daily correlation joins every pair of Nasdaq rules, chose to apply the near-duplicate test within a measured
tier (D94).

**What.**
- Engine: `vol_managed` (exposure = min(ceiling, target vol ÷ QQQ's 20-day vol), built from BIL/QQQ/QLD/TQQQ with a
  0.15 hysteresis band) and a `trend_gate` on `vol_target` (its targets while QQQ is above its 200-day, otherwise `off`).
  The live signal reads the same `targets_for`, so a card describes exactly what was measured.
- Catalogue: `btm_tqqq_trend_cash` (R6: TQQQ over QQQ's 200-day, otherwise BIL) and `btm_tqqq_trend_vol_brake` (R7).
  `btm_qld_trend` (MH3) retired with a flag (D94), moving to Hold the Nasdaq index. R1-R5 kept in `strategy_candidates`.
- `book_effect.for_all_candidates` covers every pre-registered candidate, catalogue ones included. The PR 129 smoke counts
  them instead of expecting 11.

**Measured.** Plan section 7 has the table. R6: 33.5%/yr (QQQ 21.2%), worst drop -56.6%, 2000-2016 4.3%/yr at -66%
(Trend Nasdaq: 2.1% at -85.5%). R7: 29.5%/yr, -43.2%, 2000-2016 4.8%/yr at -62%. Neither improves the owner's book by the
D93 rule (-0.004 each); each adds about 2.6-3.3%/yr to it for ~1.5 points of drawdown.

**Tests.** `tests/test_pr130_round2.py` (12); PR 125 and PR 129 tests updated for the new retired entry and 15 candidates.

**Smoke.** `scripts/smoke/smoke-pr130-round2.ps1 -Sha <sha>`. Merged without GitHub CI for the same billing reason (owner's
instruction); local SQLite and Postgres both 2,484 passed (the one failure is Python-3.13-only and fails on main too).

---

## PR 129 — what a strategy does to your whole book (D93)

**Owner, 2026-10-13:** "You can proceed with option A (with the relevant fixes) but I want to add to the test also the
'make the whole book better' parameter." Option A: the gate stays as approved (only MH3 passed; D3 replacements to be
designed). The book effect is recorded and shown beside it.

**What.**
- `services/book_effect.py`: the book `target_solver._book_for_solve` loads (core + sleeves at today's sizes), measured
  by `blend.measure_blend` without and with the strategy at its suggested size (or the size you run, for one you hold),
  the core making room and never more than the core has. `improves` = return per unit of volatility up >= 0.02 and
  worst drop <= 2 points deeper. A proxy-measured rule enters the blend as its proxy fund.
- `services/strategy_candidates.py`: the ten pre-registered candidates that failed, kept as specs (not offered, funded
  or signalled) so they can be measured against the book and re-measured exactly as registered.
- `GET /api/v1/strategies/{id}/book-effect` and `GET /api/v1/strategies/candidates/book-effect`, both behind
  `acting_user` (401 without a login, like `/preview`). The literal route is declared first.
- `blend.measure_blend` now aligns with `late_tickers`, so a selection sleeve's universe joins the book's calendar.
- Explore: each strategy card fills in "Added at N% of your book: return per unit of risk a → b, worst drop … —
  improves / does not improve your book" after the tab renders, one at a time. `sw.js` iw-v63.
- `docs/plans/...` build order: PR 129 was "MH2 live picks", dropped because MH2 failed the gate.

**Recorded 2026-10-13 on the live book** (read with the owner's key: core = CVX, VTI, BND, VXUS, UGA, USO, JPM, GOOGL
at held weights; sleeves Trend Semis 40%, Hold the Nasdaq 19%; cash floor 0.66%), by running this PR's
`compare_blocking` offline on the production data window because the endpoint was not yet deployed (CI blocked by
GitHub billing). Book without the candidate: 30.61%/yr, worst drop -45.2%, return per unit of volatility 0.761.

| Candidate | Size | Return / risk | /yr | Worst drop | Improves the book |
| --- | --- | --- | --- | --- | --- |
| M1 Low-Volatility Quality | 25% | 0.773 (+0.012) | 30.11% | -43.3% (-1.9) | no (gain under 0.02) |
| H3 Top-Down Sector Leaders | 10% | 0.769 (+0.008) | 31.45% | -45.9% (+0.7) | no |
| MH1 Large-Cap Momentum 10 | 25% | 0.754 (-0.007) | 31.96% | -48.4% (+3.2) | no |
| H1 Quality Momentum 5 | 15% | 0.754 (-0.007) | 31.20% | -46.0% (+0.8) | no |
| M3 Sector Rotation | 25% | 0.749 (-0.012) | 30.61% | -45.7% (+0.5) | no |
| MH3 2x Nasdaq Trend (in catalogue) | 20% | 0.748 (-0.013) | 33.42% | -49.6% (+4.4) | no |
| H2 52-Week-High Leaders | 15% | 0.741 (-0.020) | 30.16% | -46.3% (+1.1) | no |
| MH2 Magic Formula (QVAL) | 25% | 0.738 (-0.023) | 30.39% | -45.5% (+0.3) | no |
| VH1 Leveraged Risk Parity | 15% | 0.737 (-0.024) | 29.68% | -46.4% (+1.2) | no |
| M2 Accelerating Dual Momentum | 30% | 0.701 (-0.060) | 28.22% | -49.3% (+4.1) | no |
| VH2 Leveraged Sector Momentum | 10% | 0.683 (-0.078) | 31.13% | -49.3% (+4.1) | no |

The book's 30.6%/yr is the backtest of its target (a 40% Trend Semis sleeve that currently holds nothing), not of
today's holdings.

**Tests.** `tests/test_pr129_book_effect.py` (11).

**Smoke.** `scripts/smoke/smoke-pr129-book-effect.ps1 -Sha <sha>`. **Ran against `d11833f` (GitHub PR #34 merge), with the
owner's key: 7 PASS, 0 FAIL, 0 SKIP.** Production measured all 11 round-1 candidates against the live book: none improves
it (closest M1 +0.007, H3 +0.002; MH3 -0.017 for +4.6 points of drawdown), matching the offline table above to a few
hundredths. **Merged without GitHub CI** (owner, 2026-10-13: "Skip the ci in GH"): Actions refused to start any job
("recent account payments have failed or your spending limit needs to be increased"). Local SQLite and Postgres gates
passed.

---

## PR 127 — the eight rotation candidates measured; Trend-Filtered 2x Nasdaq joins the catalogue

**Plan.** PR 127 of [`docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md`](docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md).
Every candidate was measured on its pre-registered settings; the full table, with the Very-high pair from PR 128 and MH2
on its proxy, is section 6 of that file.

**Result.** 1 of 11 passed the gate: **MH3, Trend-Filtered 2x Nasdaq** (`btm_qld_trend`): 27.2%/yr against QQQ's
21.2%, worst drop −52.7% (so it shows as High, D88), test 3 "holds up" against its own funds, sweep spread 0.8 points,
and over 2000–2016 2.3%/yr against QQQ's 2.0% with a −84% worst drop. Every candidate that stepped away from mega-cap
tech lost to holding QQQ over this decade; the closest was H3 Top-Down Sector Leaders (19.7%/yr, −1.5).

**What.** `btm_qld_trend` in `CATALOG` (`trend_filter`, QLD over QQQ, `long_history`), fundable like the other trend
sleeves. QLD and the other geared funds the engine now measures join `strategy_profile._LEVERAGED`. Dual Momentum
Rotation and Factor Stack stay: their replacements did not pass.

**Open.** Owner question sent 2026-10-13: keep the gate as approved and design replacements (D3), or add a
"makes the whole book better" way to pass.

**Smoke.** `scripts/smoke/smoke-pr127-catalogue-results.ps1 -Sha <sha>`. **Ran against `47259c7` (GitHub PR #33 merge):
8 PASS, 0 FAIL, 1 SKIP** (the new entry is first measured at the 03:30 refresh).

---

## PR 128 — leverage over 2000–2016, risk parity, and the two Very-high candidates (D92)

**Plan.** PR 128 of [`docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md`](docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md).
Shipped before PR 127, whose scope waits on an owner question (whether a strategy can also pass by improving the whole
book).

**What.**
- `YahooMarketDataProvider.get_long_history`: daily bars by date range (`period1/period2`) from 2000. `range=max` returns
  monthly bars, which is why `_daily` stops at 10y; this path refuses an answer under 150 bars a year.
  `registry.guarded_long_history` puts it behind the history tier.
- `strategy_backtest.SYNTHETIC`, `synthetic_leveraged`, `synthetic_cash`: rebuilt leveraged funds and T-bill cash.
  Calibration, synthetic minus real CAGR on 2011–2026 at a 0.75% spread: TQQQ −0.6, UPRO −0.3, TMF −0.9, SOXL (from
  SOXX) +0.1, TECL +1.4, QLD −0.4, CURE +0.3, ERX +2.1, FAS −5.0; daily correlation 0.98–0.999. SOXL is rebuilt from
  SOXX because from SMH it ran 11.6 points a year too high.
- `backtest_service.long_history(spec)`: the spec over 2000-01-01 → 2016-10-01 against its base (or its own funds),
  stored as `robustness.long_history` for specs flagged `long_history`. Flagged: Trend Nasdaq, Trend Semis,
  Volatility-Targeted Leverage, Hold the Nasdaq index.
- `risk_parity` overlay (VH1): legs inverse to their own 60-day volatility, each held only while its index is above its
  200-day average, an off leg's share in cash.
- The card shows "Worst drop incl. 2000–2016" with the comparison in its tooltip. `sw.js` iw-v62.

**Measured (pre-registered settings, production data window, engine a7).**

| | 2016–2026 /yr | Worst drop | vs base or own funds | 2000–2016 /yr | 2000–2016 worst drop | Gate |
| --- | --- | --- | --- | --- | --- | --- |
| VH1 Leveraged Risk Parity | 4.1% | −48.4% | −7.1 (own funds) | 8.9% vs own funds 11.1% | −43.0% | fails tests 1 and long history |
| VH2 Leveraged Sector Momentum | 8.4% | −87.3% | −12.8 | −7.7% vs QQQ 2.0% | −93.1% | fails tests 1, 4 (sweep spread 22.4 pts), long history |
| Trend Nasdaq (kept) | 36.7% | −62.4% | +15.4 | 2.1% vs QQQ 2.0% | **−85.5%** | information only |
| Trend Semis (kept) | 48.7% | −79.4% | +13.4 | 3.4% vs SMH 3.3% | −85.5% | information only |
| Vol-Targeted Leverage (kept) | 32.3% | −63.1% | +11.0 | −0.1% vs QQQ 2.0% | −96.2% | information only: loses to QQQ |

Neither Very-high candidate enters the catalogue (plan D3: the tier is left short and replacements designed). The
`risk_parity` kind stays, tested, for a replacement to use.

**Tests.** `tests/test_pr128_long_history.py` (9).

**Smoke.** `scripts/smoke/smoke-pr128-long-history.ps1 -Sha <sha>`. **Ran against `2903327` (GitHub PR #32 merge): 3 PASS,
1 FAIL, 4 SKIP.** The FAIL was the smoke, not the deploy: it read `/sw.js` (404); the shell is served at `/app/sw.js`,
which returned iw-v62. Script fixed in PR 127; re-run: **4 PASS, 0 FAIL, 4 SKIP** (the four rows re-measure at 03:30).

---

## PR 126 — the selection engine: rank a universe, hold the winners (D90, D91; engine a7)

**Plan.** PR 126 of [`docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md`](docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md).
Engine only: nothing in the catalogue uses it until PR 127.

**What.**
- `rank_select`: score a universe (12-1 momentum, risk-adjusted momentum, blended 1/3/6-month, return over N, low
  volatility, nearness to the 52-week high), hold the top N with a rank buffer, on a cadence; optional market gate,
  per-name trend veto, positivity veto with a fallback pair, two-stage sector-then-stock selection, a rank-on-the-index /
  hold-the-fund map with a switching margin, equal or inverse-volatility weights. Unfilled slots ride in the base.
- `align(late=...)`: a universe's late listings JOIN the calendar (NaN before their first price) instead of cutting it.
  Before this, MNDY (2021) would have cut a 44-stock backtest to five years.
- `clean_spikes`: drops a one-day print of 2x or more whose neighbours agree within 10% (Phase 0: TSEM.TA 2017-05-04).
  TECL's real March 2020 swings survive it (tested). Counted in `bad_prints_dropped`.
- Warm-up: a backtest starts on the first day the strategy holds anything. A strategy with no base used to sit in cash
  for a year of warm-up and be scored for it (0.3 points on Accelerating Dual Momentum, about 1 on Low-Volatility).
- **D91** `selection_edge_pct` = return minus `equal_hold_cagr_pct` (every candidate, equal weight, same cadence), plus
  a `survivorship` label. `proxy` specs are measured on the proxy fund and carry `measured_by_proxy`.
- **D90** `out_of_sample.verdict` is now judged against holding the strategy's own instruments (`own_hold_decay_pct`);
  `verdict_vs_benchmark` keeps the SPY reading. `ENGINE_VERSION` a6 → a7: every stored row is stale until the 03:30
  refresh recomputes it (or `POST /strategies/backtests/refresh`).

**Checked against outside numbers before believing the engine.** On the production data window: a 12-1 top-10 on the
44 names measured 17.4%/yr against the MTUM fund's 16.2%; a low-volatility top-12 9.6%/yr against USMV's 10.1%; an
independent day-by-day replay of the dual-momentum targets gave 5.55%/yr gross against the engine's 4.54% net of tax and
costs.

**Tests.** `tests/test_pr126_selection_engine.py` (20). `ENGINE_VERSION` pins in two older tests moved to a7.

**Smoke.** `scripts/smoke/smoke-pr126-selection-engine.ps1 -Sha <sha>`. **Ran against `0b18880` (GitHub PR #31 merge):
5 PASS, 0 FAIL, 1 SKIP** (the 8 rows are stale until the 03:30 refresh re-measures them on a7). Stack gate before the
push (PRs 126-129 together): SQLite and Postgres 2,451 passed, 1 Python-3.13-only failure.

---

## PR 125 — Beat the Market clean-up: two strategies retired, tiers measured, a backtest that stopped over-trading (D87–D89)

**Plan.** First PR of the Beat the Market expansion approved 2026-10-13
([`docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md`](docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md), Claude doc linked
there). Phase 0 (the data check) ran first and is recorded in that file's section 4: nothing blocked the plan.

**What.**
- **Retired with a flag (D87):** Swing: Dip-Buy (17.9%/yr against QQQ's 21.3%, deeper drop) and Swing: Channel Breakout
  (21.2%/yr at nearly twice QQQ's worst drop). Each entry keeps resolving through `strategy_catalog.get` and keeps being
  measured; it is no longer offered as a new sleeve (`apply_strategy`) or a swap target (`swap_sleeve`), and the
  sleeve-count search only considers it for a book that still runs it. Explore folds them into "Tried and did not beat
  holding" with their numbers; a sleeve still on one shows a single button, "Move to Hold the Nasdaq index", which is the
  D47 swap (same size, nothing traded). `sw.js` iw-v60.
- **Measured tiers (D88):** `risk_label` is now the band the measured worst drop falls in (Medium ≤ 36%, Medium-high ≤ 45%,
  High ≤ 60%, Very high above). `designed_risk` keeps the hand label; `/strategies/backtests` adds `measured_risk`.
  Channel Breakout (-61.3%) read "High" beside Trend Nasdaq (-62.4%) "Very high"; both now read Very high.
- **Drift band (D89):** `_simulate` takes `drift_band` (default 0.01, so cores, blends and the frontier are unchanged). Volatility
  targeting uses 0.10.

**Root cause of the vol-targeting trade count (proven, not inferred).** The plan said "widen the rebalance band". Measured
on the shipped engine over the production window: widening `rebalance_band` did nothing (0.15 → 162.3 trades a year,
0.25 → 157.9, 0.5 → 201.7). Instrumented: the target moved 10.6 times a year, yet the book was re-trued ~80 times a year,
~70 of them for price drift alone, because `_simulate` re-trues any drift of 1% and a 3x leg beside its 1x base drifts
that far most days. Ruled out: the hysteresis band (above) and the vol estimate itself (10.6 changes a year). Fix:
re-true an unchanged target only past `drift_band`. At 0.10: 24.7 trades a year, 32.27%/yr against 32.03%/yr, worst drop
63.1% against 63.25%. Blast radius: every multi-leg spec at the 1% default; Factor Stack's 69 trades a year on a
buy-and-hold basket is the same effect and is left alone (it is replaced in PR 127 if MH1/MH2 pass). It was silent
because the trade count is reported, not gated, and tax drag hid it inside a plausible-looking 8.8%.

**Not changed on purpose.** `ENGINE_VERSION` stays `a6`: no field changes meaning, only volatility targeting's spec. Its
stored row updates at the 03:30 refresh. Measured on today's data it moves to "Very high" (63.1% worst drop), not "High".

**Tests.** `tests/test_pr125_catalogue_cleanup.py` (23, all failing on the old tree). `test_sleeves_mean_what_they_say`
and `test_the_sleeve_holds_what_the_rule_wants` added `btm_swing_dip` as a fresh sleeve; they now use `btm_trend_tqqq`
(identical basket). Gate: SQLite and Postgres 2,401 passed; the one failure (`test_no_card_rewrites_the_target`) fails
identically on `main` under the sandbox's Python 3.13 (docstring dedent) and not on CI's 3.12.

**Smoke.** `scripts/smoke/smoke-pr125-catalogue-cleanup.ps1 -Sha <sha>`. Public endpoints only. Run against the old build
before the push: 4 PASS, 14 FAIL (every new field), as expected. **Ran against `d0a2508` (GitHub PR #30 merge):
12 PASS, 0 FAIL, 1 SKIP** (volatility targeting is re-measured at the 03:30 refresh).

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

## PR 118b — thin intake routes (L7; Wave F, pure moves; plan v3 complete)

**What.** The business logic leaves `routes/intake.py` (953 → 635 lines) unchanged, by the same AST-driven move as 118a:
- `get_portfolio` → `services/portfolio_view.holdings`;
- `add_holding` → `intake_service.add_by_amount`;
- `_row_to_position` / `_persist` → `intake_service.row_to_position` / `persist`;
- `_last_buy` → `ledger_service.last_buy`;
- `_orders_payload` → `standing_order_service.orders_payload`.

`guarded_quote` is now a module name in `intake_service`, so the two tests that stand in for the quote target it there.
`PERF_RANGES` stays in the route.

**Gate.** Chunked local SQLite + Postgres:
- 2 real failures: `test_the_conversion_and_the_axis` read the NAV code's source in the route. It now reads `portfolio_view.py`.
- the known environment-dependent dismissed-cards test.

**Smoke.** `scripts/smoke/smoke-pr118b-thin-intake-routes.ps1 -Sha <sha>`. `/portfolio`, its rows and
`/portfolio/standing-orders` return exactly the keys recorded from production before the move (`8e19cdd`).

---

## PR 124 — a rule card says what it would do to your goal (D86)

**Owner, 2026-10-13:** "I would expect that the information on the goals chances would be also written in the
recommendation cards". The examples were "UGA is in an uptrend" (arm a 17% trailing stop) and "Arm the discipline for
Trend-Filtered Semiconductors".

**What.**
- **`POST /api/v1/plan/frontier/rule-impact {rules}`** (read-only) calls `preview(extra_rules=)`:
  - `_rules_context(extra=)` adds the proposed rules as if armed (ids `new:<n>`). A proposed stop on a holding with no
    rule yet gets its own column, the PR 123 way.
  - `goal_solve` (light) replays with and without them on the same 500 paths (`_replay_without`) →
    `rules.compare = {with, without, goal_pts}`.
  - The response lists each proposed rule as modelled, or not with the reason ("an entry, alert or weight rule, not a
    sale the goal model replays").
- **Today:** every card whose Accept creates rules (`apply.kind` create_rule/create_rules) gets a line under "Accept
  will". It reads "Goal impact: measuring…" and is filled in one card at a time after Today renders, cached for the
  session: "Goal impact: goal chance +0.4 pts (27.1% → 27.5%); chance the book halves 71.8% → 71.0%. SOXL strategy
  signal: not in the goal model (…)."
- `sw.js` → `iw-v59`.

**Tests (4).** `tests/test_a_rule_card_shows_its_goal_impact.py`:
- a proposed stop is compared with and without on the same paths;
- with no proposed rules there is no comparison;
- the endpoint passes the card's rules and rejects an empty list;
- Today renders the line on every rule-proposing card.

**Gate.** Chunked local SQLite + Postgres:
- one real failure: the new read-only POST had to join the COMPUTE_ONLY list in `test_the_doors_are_shut`. Fixed.
- the environment-dependent `test_dismissed_recommendations_are_counted_and_restorable`, which also fails on `main` locally.

**Smoke.** `scripts/smoke/smoke-pr124-rule-card-goal-impact.ps1 -Sha <sha>`. **Ran against `8e19cdd` (GitHub PR #28 merge): 6 PASS, 0 FAIL.**
- Today has 3 rule-proposing cards: QQQ uptrend, UGA uptrend, and the SOXL discipline card.
- UGA's proposed 17% trailing stop measures 0.0 pts. UGA's existing 15% trailing stop and stop-loss fire first, so the
  card says the new rule adds nothing.
- The SOXL entry signal is named as not modelled.

**QA (Pixel).** Today: "UGA is in an uptrend" and the SOXL discipline card each show a "Goal impact" line within a few
seconds.

---

## PR 118a — thin plan routes (L7; Wave F, pure moves)

**Plan v3, Wave F (structure, no behaviour change).** Owner go-ahead 2026-10-12.

**What.** The business logic leaves `routes/plan.py` (1,126 → 549 lines) unchanged, by an AST-driven move:
- **`services/plan_view.py`:**
  - `positions`, `book_measured`, `frontier_basis_text`, `portfolio_stats`, `goal_of`, `plan_dict`, `PERIOD_MULT`;
  - `sleeves_view` and `core_options_view`. These sit here rather than in `sleeve_service`, which would otherwise import
    itself.
- **`services/projection_service.py`:** `project()` (was the `goal_projection` handler) and `EXCESS_PP`.
- **`book_target.mix_report()`:** was the `mix_check` handler.
- **`services/war_room.war_room_payload`:** was `routes/war_room._war_room_payload`; the route keeps the old name as an
  alias.

Three services stopped calling route handlers as functions:
- plan health (projection + mix);
- the assistant context (projection);
- Today (book stats, war room).

The routes keep validation, `Depends` and response shaping.

**Tests.**
- New guard `tests/test_services_never_import_routes.py`: no file in services, engines, agents, the worker or core
  imports `app.api`.
- Moved targets: 7 tests import from `plan_view` / `projection_service`; 2 monkeypatch the war-room service.

**Gate.** Local SQLite and Postgres, run in six resumable chunks (the session restarts overnight killed hour-long
runs). The first pass found 8 failures, all from the move:
- 5 tests patched `signal_service` through the route module;
- an agent-roster line anchor (`services/war_room.py:18`) had been shifted by two added header lines.

Both are fixed. On the re-run, Postgres passed all 6 chunks. SQLite failed one test,
`test_portfolio_totals::test_dismissed_recommendations_are_counted_and_restorable`, which also fails on `main` on this
machine: it depends on live screener data that the provider rate-limited. CI is the check for it.

**Smoke.** `scripts/smoke/smoke-pr118a-thin-plan-routes.ps1 -Sha <sha>`. **Ran against `b973837` (GitHub PR #27 merge): 11 PASS, 0 FAIL.** All 6 moved endpoints return the keys they returned before the move. The 6 moved endpoints return exactly the keys
recorded from production before the move; `/plan` current value = NAV; plan health and Today build.

---

## PR 123.1 — hotfix: the goal solve failed on a per-asset volatility scale (incident)

**Incident, 2026-10-12, after PR 123 (`1ebe2aa`) deployed.** The first re-solve returned nothing, and the deploy log
showed `ValueError: The truth value of an array with more than one element is ambiguous`
(`goal_solve`, frontier_service.py:610). In production `est_kw["vol_scale"]` is one factor per asset (`fi.vol_scale`);
PR 123's held-ticker column did `float(vol_scale or 1.0)` on it. The tests passed because they never set `vol_scale`.
Every goal solve failed from this deploy until the hotfix, the nightly one included; the last good verdict (17:02)
kept being served.

**Fix.** A held ticker's column takes the mean of the per-asset scale (a scalar or no scale works as before).

**Test.** `test_a_per_asset_vol_scale_does_not_break_the_replay`: the replay with an array `vol_scale`. It fails on
`1ebe2aa` with the production error.

**Gate.** The local full-suite runs were killed twice by session restarts that night. The targeted goal/rules tests
passed locally (27), and CI's full `test` and `test-postgres` jobs were the gate. The fix was merged only when they were
green, because production could not re-solve until it shipped.

**Merged** `2dd8635` (GitHub PR #26) on 2026-10-13 07:55 on a green CI. The 04:10 nightly solve had failed on the broken build.

**Lesson.** A test fixture that omits a production input proves nothing about that input. A PR 123 test now passes
`vol_scale` the way production does.

---

## PR 123 — every rule shows its effect on your goal (D85, closes P3)

**Owner, 2026-10-12:**
- "it says to me that the rules are harming my chances but I have 30 rules... I need to know which one of them need my
  attention";
- "why does it not run on all the rules?";
- "you need to add to the rules cards the impact on the chances of meeting the goals".

**What was true.** The goal replay modelled the book as sleeves + one core basket + cash. A stop on CVX, JPM, GOOGL,
UGA, USO, VTI, VXUS or BND had no column to act on, so 22 of the 30 rules were "not simulated", and the card could only
say "your rules cost growth".

**What.**
- **Held tickers are modelled.** `_rules_context` finds every held ticker outside the sleeves that carries a sale rule
  (stop, trailing stop, activated profit-protect, take-profit). `rules_spec(holding_tickers=)` builds its spec,
  `fi.ticker_ils` prices it on the solve's calendar (carried forward, ≥90% coverage, ILS/USD only), and `goal_solve`
  gives it a column. The column is carved out of the core's share and re-centred on the core's drift, so it asks what
  the rule does, not whether the stock beats the market. The Edit-rules preview models them too.
- **Per-rule effect.** `_per_rule_impact`: each rule is switched off alone, on 500 paths, against the same paths with
  every rule on. It records:
  - `goal_pts` (negative = the rule costs the goal);
  - the halving chance with and without the rule;
  - `covered` when another rule on the same holding acts first;
  - for rules not replayed, `modelled: false` and a reason (an alert or weight rule, not held, profit-protect not active
    yet, too little history).
- **Retune (D82):** it now also searches the held tickers' take-profits and exits.
- **Rules tab:** `GET /rules` adds `goal_impact` per rule, plus whether the solve predates a rule change. Each rule card
  reads "Goal chance −3.4 pts · chance the book halves 6% without it, 1% with it", or why it is not modelled.
- **Plan goal card:** "Most of it comes from the drawdown guard (−10.2 pts), …; every rule's effect is on Holdings ›
  Rules."
- **Rules fingerprint:** it now counts take-profits too, so toggling one marks the verdict stale.
- `sw.js` → `iw-v58`.

**Tests (7).** `tests/test_every_rule_shows_its_goal_impact.py`:
- `ticker_ils` alignment, FX and coverage;
- `rules_spec` models a held ticker;
- every rule gets an effect or a reason (covered, alert, guard);
- the light preview models holdings but skips the per-rule pass;
- the fingerprint counts take-profits;
- GET /rules carries `goal_impact`;
- the retune can pause a held ticker's take-profit.

**Gate.** Local SQLite 2373 and Postgres 2373 passed; ruff clean.

**Smoke.** `scripts/smoke/smoke-pr123-rule-goal-impact.ps1 -Sha <sha>` (after one re-solve). **Ran against `2dd8635` (after the PR 123.1 hotfix, GitHub PR #26): 7 PASS, 0 FAIL.**

**What the first per-rule solve said (2026-10-13 08:00):**
- 8 held tickers modelled: USO, VXUS, JPM, CVX, UGA, VTI, BND, GOOGL. 26 active rules measured, 22 modelled, 4 weight
  rules with a reason.
- No holding rule moves the goal chance by a full point (−0.4 to +0.6 each). 9 are covered by a tighter rule on the
  same holding: the trailing stops on BND, CVX, GOOGL's stop-loss, JPM, UGA, USO, VTI and VXUS.
- The ~72% chance of halving comes from the sleeves. The drawdown guard is paused, so the D83 card offers guard
  25% / sells 50% / re-arms 5 points under: goal 27.1% → 14.3%, halving 71.8% → 2.2%. The solve takes 65 s, up from
  ~20 s.

**QA (Pixel).**
1. Holdings › Rules: each rule card has a "Goal chance …" line or a "not in the goal model (reason)" line.
2. The Plan goal card names the rules the cost comes from.

---

## PR 122 — an ignored goal card stays ignored (hotfix)

**Owner, 2026-10-12:** "this recommendation card keep coming back to me after I click on the ignore button (it should
disappear for one week)". The card was "Set a target this book can reach: 4.1%/yr".

**Cause.** The seven goal/frontier cards built their id from the stored solve's row id (`rec_goal_target_{row.id}`).
Every re-solve writes a new row:
- the nightly solve;
- a thresholds save;
- a refresh (several today: deploys and the PR 121 checks).

So the same card returned under a new id, and Ignore (keyed by id) no longer matched it.

**Fix.** Stable ids per card kind: `rec_frontier_book`, `rec_goal_{core,protect,mix,deposit,target,deadline}_book`. A
mute now lasts its window (Ignore 7 days by default). A severity rise still un-mutes it (`moved_on`). The
`rec_goal_protect_` prefix the Plan tab reads is unchanged.

**Tests.** `tests/test_an_ignored_goal_card_stays_ignored.py`:
- dismiss, re-solve (a new row), and the card stays hidden. This fails on `main`, where it came back as
  `rec_goal_target_<new row id>`;
- no card id carries `{row.id}`.

**After deploy.** Cards ignored before this fix used the old ids, so Ignore the target card once more; it then stays
hidden for 7 days.

**Gate.** Local SQLite 2366 and Postgres 2366 passed; ruff clean.

**Smoke.** `scripts/smoke/smoke-pr122-goal-cards-stay-ignored.ps1 -Sha <sha>`. **Ran against `35b7452` (GitHub PR #24 merge): 5 PASS, 0 FAIL** (Today's target card is `rec_goal_target_book`).

---

## W0-5 — remove what nothing uses (L2)

**Owner, 2026-10-12:** "yes, you can delete them".

**What.**
- `git rm investwise.db` (a Phase-2 SQLite snapshot with no `alembic_version`) and `frontend/package-lock.json` (an abandoned
  React/Vite scaffold).
- The CORS default changes from `http://localhost:5173` to empty, and the middleware is added only when `FRONTEND_ORIGIN` is
  set. The app is same-origin (`/app`).
- `docker-compose.yml` drops the ghost origin; `.env.example` documents the empty default.

**Not changed in production yet.** Railway still sets `FRONTEND_ORIGIN=http://localhost:5173`, so production keeps allowing
that origin until the variable goes. Removing it is one owner step after this merges: delete the `FRONTEND_ORIGIN` line from
`.railway/railway.ts` (Claude can do it), then `railway config plan` shows "1 to change" (the variable deleted) and
`railway config apply` removes it.

**Tests (3).** `tests/test_no_ghost_files_or_origins.py`:
- the files are not tracked;
- the default is empty;
- a ghost origin gets no CORS headers when none is configured.

**Gate.** Local SQLite 2364 and Postgres 2364 passed; ruff clean.

**Smoke.** `scripts/smoke/smoke-w0-5-remove-unused.ps1 -Sha <sha>`. **Ran against `64128a8` (GitHub PR #23 merge): 4 PASS, 0 FAIL, 1 SKIP** (CORS: Railway still sets `FRONTEND_ORIGIN`).

---

## PR 117 — keyboard and focus (L12, D84)

**Plan v3, Wave E, last PR.** D-i: both looks were built and shown, plus today's accidental hybrid. The owner, 2026-10-12:
"I liked option A but I do want the blue highlight" → **D84**.

**What (`index.html` only).**
- **One `.seg`:** look A (full-width rounded track, 14px, 9px padding) with the selected segment in solid blue. The second
  block is deleted, and so are the inline patches it needed (`#planSeg` display/flex, `#h_mode`/`#h_rng` width).
- **Theme:** `:root` gains `--warn` (#e5b857, the colour every fallback already used). The undefined `--muted` → `--mut`,
  which fixes the bright "Ignored" / "Marked done" labels. `--fg` → `--ink`.
- **Focus:** a global `:focus-visible` ring. `.lvl` inputs lose `outline:0` and their row gets a `:focus-within` ring.
- **17 click targets** (4 modal ✕, 8 links, the health chips, the rules banner, the rating, the agent line) get
  `role="button" tabindex="0"`; the ✕ also gets `aria-label="Close"`. The 4 modals get `role="dialog" aria-modal="true"`.
- **One key handler:**
  - Enter/Space press a button-role element;
  - Escape closes the sheet, else the open modal;
  - Tab wraps inside an open sheet.

  The sheet saves the element that opened it, focuses its first control, and gives focus back when it closes.
- `sw.js` → `iw-v57`.

**Tests (4).** `tests/test_the_app_works_from_a_keyboard.py`:
- every `var(--x)` is defined or has a fallback;
- every clickable span/div is a button (backdrops allow-listed);
- `.seg{` appears once, with the blue selection;
- there is a focus ring, the key handler covers Enter/Space/Escape, and the sheet returns focus.

**Gate.** Local SQLite 2361 passed. The local Postgres run was stopped by the host for low memory; the owner accepted CI's test-postgres for this PR (green).

**Smoke.** `scripts/smoke/smoke-pr117-keyboard-focus.ps1 -Sha <sha>`. **Ran against `acc625f` (GitHub PR #22 merge): 7 PASS, 0 FAIL. Wave E complete.**

**QA (Pixel + desktop).**
1. The Pixel: Holdings › Positions/Rules, Plan › Goal/My mix/Explore and a holding's chart range all show the blue look-A
   control.
2. Desktop: Tab shows a blue ring; Enter on "what's this?" opens the explainer; Escape closes it.
3. Desktop: open Plan › Card thresholds, Tab cycles inside the sheet, Escape closes it and focus returns to the button.
4. Today › history: "Ignored" reads grey, not bright.

---

## PR 116 — say the true thing (L3, L4, L5, L6, L9, L14; D80, D81)

**Plan v3, Wave E.**

**What.**
- **L4, the cash target is not a floor.**
  - Renamed `funding_service.cash_floor_pct()` → `cash_target_fraction()` and `cash_floor_ils()` → `cash_target_amount_ils()`.
    These are not `cash_target_pct/ils`: those names belong to the plan columns, and the column is a percentage while the
    function returns a fraction, which is the exact confusion L4 is about. The old names stay as deprecated aliases.
  - The funding payload adds `cash_target_ils` / `cash_target_pct` beside the old keys.
  - `headsup.MIN_TRADE_ILS` → `MIN_CARD_TRADE_ILS` (alias kept).
  - "floor" → "cash target" wherever the value shown is the soft target: the raise-cash card, the plan-health cash line,
    the sleeve-funding refusal, the funded-buy how-line, the buy-back card, the phase-in tip, two Plan-tab lines and the
    on-plan help text. "Hard floor" stays where it is the ₪150 minimum.
- **L6, the copy names real places.** "Cash tab" → "Holdings › Set liquid cash" (the Edit-goal sheet now links to it, and
  the goal-deposit card's how-line changes). "Goal settings" → "Tap Edit next to Target". "the Rules tab" →
  "Holdings › Rules".
- **L5.**
  - Fixed the stale docstrings: the `tables.py` table count, NavSnapshot/nav_history "never written", buyback_hold storage,
    `is_core`, and the frontier pipeline map. `market_state.py` was already gone.
  - Deleted the dead `pyproject.toml` warning filter.
- **L9.** `core.money.money` → `display_money`, `tables.money` → `db_money`, with aliases. A guard forbids importing the
  ambiguous name.
- **L14 / D80.** With a core whose basket holds commodities (All-Weather: IAU, DBC), the commodity card and the redeploy
  research's commodity swaps use only those funds, ranked by the same screen with the same trend-dead filter. New setting
  `commodity_buys_core_only` (1 = on, default; 28 settings). Production's core (`bal_6040`) holds none, so nothing changes
  there today. The "best trend right now" idea is parked as BACKLOG P12.
- **L3 / D81.** Sentry is deleted (the `main.py` block and `settings.sentry_dsn`).
- **Not done: W0-5** (deleting `investwise.db` and `frontend/`). It needs the owner's permission; asked in the PR 116 report.
- `sw.js` → `iw-v56`.

**Tests (8).** `tests/test_say_the_true_thing.py`:
- no copy names a tab that does not exist;
- the soft target is not called a floor;
- the payload names the target and keeps the old keys;
- the commodity card buys the core basket when a core is chosen;
- without a core, or with the setting off, it buys the screen leader;
- redeploy research swaps only within the core's commodities;
- there is no Sentry;
- the two money helpers have their own names.

**Gate.** Local SQLite 2357 and Postgres 2357 passed; ruff clean.

**Smoke.** `scripts/smoke/smoke-pr116-say-the-true-thing.ps1 -Sha <sha>`. **Ran against `83af09f` (GitHub PR #21 merge): 7 PASS, 0 FAIL.** The plan-health cash line reads "between your ₪150 cash target and ₪1,500 redeploy line".

**QA (Pixel).**
1. Plan › Health: the cash line says "cash target".
2. Plan › Edit goal with 2+ standing orders: the "change them in Holdings › Set liquid cash" link opens Liquid cash.
3. Plan with no goal: "Tap Edit next to Target".

---

## PR 121.1 — the halving limit is a hard limit (D83)

**Owner decision, 2026-10-12.** "I want to treat the limit as a hard limit."

**What.**
- `_tune_protection` adds `breach` (today's chance of halving is over the limit). A breach makes the best setup inside
  the limit material even when it lowers the goal odds.
- The card reads "Bring your crash risk inside your limit: halving X% -> Y%". Its why says the risk, the limit and the
  cost ("Staying inside it costs N points of goal odds"), and the impact line keeps the goal odds before -> after.
- Accept is unchanged: it arms or re-levels the guard and pauses what the setup pauses.
- Inside the limit nothing changes: a costlier setup is still never offered. Since the card exists, the money/time
  suggestions are sized on the retuned book (D1 b).

**Tests (4)** in `tests/test_the_retune_searches_properly.py`:
- production's shape (no guard, 68.3% vs 2.6%) → arm guard 20%;
- inside the limit a costlier setup is not offered;
- the breach sentence;
- the card reaches Today and Accept arms the guard.

**Gate.** Local SQLite 2349 and Postgres 2349 passed; ruff clean.

**Smoke.** `scripts/smoke/smoke-pr121-1-hard-limit.ps1 -Sha <sha>` (after one re-solve). **Ran against `b153a73` (GitHub PR #20 merge): 5 PASS, 0 FAIL.**
By the time of the run, the breach card had already appeared and been accepted. Production now has a drawdown guard at
20% (note "armed by the goal's protection card"). The re-solve measures today at 2.2% halving, inside the 2.6% limit, and
18.9% goal odds, so no protect card shows. An older, inactive 30% guard row remains on Holdings › Rules.

**QA (Pixel).** Today shows "Bring your crash risk inside your limit…" with both chips. Accept it only if you want the
guard armed: it arms the guard at the level on the card, and one tap on Holdings › Rules pauses it again.

---

## PR 121 — the protection retune searches properly (D82)

**Plan v3, Wave E.** Owner's question, 2026-10-11: why the goal card said "already the best". Live, that rested on two
setups (guard 15% vs 20%, sleeve stops already paused), and "take-profits measured neutral" was never re-measured.

**What.**
- **The search.** `_tune_protection` now crosses:
  - the sleeve exits, kept or paused;
  - each sleeve's take-profits, kept or paused (all, none, or one at a time);
  - the guard's level × sell size × re-arm gap.

  It runs in two passes: every structure at today's guard plus every guard at today's structure, then every guard at the
  best structure. Each setup is screened on 250 paths, capped at 120 setups, and the best 3 are re-measured on all 1,000.
  The pick is still the best goal odds with halving at or under the limit. On the test book the solve takes about 2×
  as long without resampling (6 → 13 s); the Card-thresholds sheet now says "~1 min" when it re-solves.
- **Four new settings (27 in all):**
  - `guard_sell_fraction_pct` (default 25) and `guard_rearm_gap_pts` (default 2.5): the live guard's sell size and re-arm
    gap. `advanced_rules.derisk_fraction()` / `guard_rearm_pct()` read them in the de-risk card, the re-arm test and the
    replay's `rules_spec`.
  - `guard_fractions_tried` (25, 50) and `guard_rearm_gaps_tried` (2.5, 5): what the retune tries.

  `guard_levels` widens from (15, 20) to (15, 17.5, 20, 25). The live defaults are unchanged.
- **The card can propose:**
  - pausing a take-profit (`rules_spec` now carries `tp_rule_ids`; Accept pauses by id, like the stops);
  - moving the guard's sell size / re-arm gap. Accept writes these plan settings and refuses untouched if they moved
    since the solve.

  The suggestions are sized on the retuned book through the same `_setup_specs`.
- **The sentences.**
  - The card: "N setups of your protective rules were replayed…". It says "Pausing the take-profits was measured too and
    did not do better" only when it was measured.
  - The Plan goal card: "Of the N protection setups measured, this is already the best…".
  - "Every setup" and "measured neutral" are gone from the code.
- `sw.js` → `iw-v55`.

**Tests (9).** `tests/test_the_retune_searches_properly.py`:
- a take-profit that costs the goal is paused;
- guard 17.5% beats 15% under the limit and is proposed;
- the sell size and gap are searched;
- the grid is wide and capped, and the finalists are re-measured in full;
- the sentences claim only what was measured;
- the new settings default to today's behaviour and the live guard reads them;
- `tp_rule_ids` are carried;
- Accept pauses a take-profit and writes the guard settings;
- moved settings are refused untouched.

Updated: the old pins on 6 candidates, 23 settings, `guard_levels` (15, 20) and the guard spec's keys.

**Gate.** Local SQLite and Postgres each ran 2345 tests. The only failures were those two old pins, which pass after
the update on both databases. ruff is clean.

**Also recorded (owner, 2026-10-12).**
- **PR 119 merge smoke:** `b0a8e87`, 7 PASS, 0 FAIL. Merge deploy `69e6bd79` built from the DOCKERFILE.
- **Ops:** the daily Postgres backup schedule is set; `redis-volume` is deleted (OPERATIONS §7).

**Smoke.** `scripts/smoke/smoke-pr121-protection-retune.ps1 -Sha <sha>`. **Ran against `65e6a13` (GitHub PR #19 merge): 8 PASS, 0 FAIL**
- The first run reported a false FAIL: the script compared `15.0` with `15` as text. It is fixed in PR 116.
- One on-demand re-solve took 22 s and measured 20 setups, take-profits included.

**Finding → D83 (owner, 2026-10-12: "treat the limit as a hard limit").** Production has no guard armed, and today's
rules have a 68.3% chance of halving against a limit of 2.6%. Fourteen setups are within the limit; the best is guard
20% at 18.9% goal odds against 30.9% today. The card offered only gains, so it said nothing. Meanwhile the Dials sheet
said "re-arming protection is what the app would suggest", which the card never did. Fixed in PR 121.1.

**QA (Pixel).**
1. The Plan goal card's protection sentence names a count of setups.
2. The Card-thresholds sheet shows the 4 new rows, and the list rows accept "25, 50".
3. If a "Retune your protection" card appears, its action lists exactly what Accept changes.

---

## PR 119 — the deploy is infrastructure as code (Railway retires `railway.json` on 2026-12-01)

**Plan v3, dated item (deadline 2026-11-15).** Done on 2026-10-11. The owner applied the Railway change; Claude
prepared and verified it.

**What we found before touching anything:**
- **Everything that made the deploy safe lived only in `railway.json`:** the Dockerfile build, migrations before
  deploy, the `/health/ready` healthcheck and restart-on-failure. The service's own settings were empty, with
  builder **RAILPACK**.
- **`railway config migrate`'s generated file was incomplete.** It kept the start command, healthcheck and preDeploy,
  left the builder as a comment, and dropped the restart policy and the 300 s pre-deploy timeout. Applying it as
  generated would have built without our Dockerfile (non-root user, no secrets in the image).
- **The first `railway config plan` was destructive.** It proposed deleting **all 32 service variables**
  (`JWT_SECRET`, `DATABASE_URL`, the OAuth and VAPID secrets…) and detaching the GitHub source, because an IaC file
  declares the full desired state. It was caught at plan time and not applied.

**What.** `.railway/railway.ts` declares:
- the GitHub source (`main`);
- all 32 variable names as `preserve()` (values never in the file; add a name before adding a variable in Railway);
- the typed `build` (DOCKERFILE, `Dockerfile`);
- the typed `deploy`: start command, `alembic upgrade head && alembic current` with a 300 s timeout, `/health/ready`
  with a 300 s timeout, 3 restart retries.

`restartPolicyType` is left implicit: ON_FAILURE is Railway's default and its API stores it as null, so declaring it
was a permanent phantom diff. `railway.json` is deleted. The three deploy tests read the TypeScript file
(`tests/_deploy_config.py`), and `test_the_deploy_is_infrastructure_as_code.py` pins the builder, retries, timeout
and that `railway.json` never returns.

**Sequence:**
1. Read-only: `railway config migrate` dry run and `config pull --json`.
2. `migrate --apply` cleared the Config-as-Code setting. No deploy was triggered.
3. `config plan`: destructive, so the file was fixed.
4. `config plan`: `0 to add, 2 to change, 0 to destroy`.
5. The owner ran `config apply` (Claude's attempt was blocked by the tool's safety check).
6. The apply triggered deployment `73db81bd` of `fd525ab`: **SUCCESS**. Its manifest reads DOCKERFILE / `Dockerfile` /
   `/health/ready` 300 / pre-deploy 300 / ON_FAILURE / 3.
7. Read-only after: 32 + 11 Railway-provided variables intact; source `eranganot/Investwise-pro@main`;
   `config plan` reports "already up to date".

**Windows note.** The `railway` npm SDK checks the CLI version through `$_`, which Git Bash and npm shims break. Run
plan/apply from PowerShell with `$env:_` set to the real `railway.exe` (OPERATIONS §6 item 7).

**Gate.** Local SQLite 2333 + Postgres 2335 passed, ruff clean. The SQLite run crossed midnight and two
`test_standing_orders.py` tests failed once; both pass on re-run on both databases, so they are date-boundary flakes
(probably "today" read twice). They are not caused by this PR.

**Smoke.** `scripts/smoke/smoke-pr119-railway-iac.ps1 -Sha <sha>`. Verified against production on `fd525ab` before the push: 7 PASS. **Ran against `b0a8e87` (GitHub PR #18 merge): 7 PASS, 0 FAIL.**

---

## PR 115 — every decision is a setting; one minimum trade (D-c, D-d)

**Plan v3, Wave E, first PR.** Owner decisions at the go-ahead (2026-10-11), recorded as D77–D81:
- **D-c:** one minimum.
- **D-d:** all 13 constants editable.
- **D-g:** core-basket commodities. A "best trend right now" pick is parked, at the owner's request.
- **D-h:** delete Sentry.
- **D-i:** screenshots at PR 117.

**What.**
- **13 more `thresholds.SPEC` keys** (23 in all), each defaulting to the constant beside the code that reads it, read
  through `thresholds.get` under the request's or job's `use(plan)`:
  - `concentration_cap_pct`: default = your risk tolerance's row. `effective_caps` honours the override, so every buy
    cap, trim card and health score follows it.
  - `sleeve_drift_band_pts`.
  - `min_resize_pts`, `material_gap_close_pct`, `material_vol_drop_pts` (the goal solver's resize bars).
  - `ignore_mute_days`, `done_mute_days`, `refire_change_pct`.
  - `near_rule_pct`, `near_trail_giveback_pct`, `near_cap_pts`.
  - `ideas_shown`, `max_idea_cards` (whole numbers).
- **D-c, one minimum.** `thresholds.card_floor_ils(nav)` is the larger of the card's ₪ / %-of-book minimums and the
  smallest funded trade. `headsup.split` uses it, and below it the commodity card is silent: it used to degrade into an
  un-actable "Buy ₪180 of X" heads-up. `/plan` reports `thresholds_effective_card_floor_ils`, and the Card-thresholds
  sheet states it ("Smallest trade card today: ₪…"). `sw.js` → `iw-v54`.
- **Not made settings, by choice (inventory P4):** `CASH_FLOOR_PCT` and Kelly (already overridden per plan),
  `OBJ_TARGET` (the core/frontier sets the book target), ranking internals and computational sizes.

**Tests (6, all fail on HEAD).** `tests/test_every_decision_is_a_setting.py`:
- the 13 defaults equal today's constants;
- each override reaches its reader, including the cap through `effective_caps`;
- the floor is never under the funded minimum;
- a small commodity gap is neither a card nor a heads-up;
- `/plan` has 23 settings and the floor.

**Smoke.** `scripts/smoke/smoke-pr115-settings.ps1 -Sha <sha>`. **Ran against `fd525ab` (GitHub PR #17 merge): 8 PASS, 0 FAIL.** 23
settings live; the concentration-cap default is 40% (risk tolerance High); the smallest trade card is ₪250.

**QA (Pixel).** Plan › Card thresholds shows the 13 new rows and the "Smallest trade card today" line.

---

## PR 114b — migration 0029 drops the legacy tables (D76, stage 3 part 2, ships alone)

**Plan v3, Wave D, last PR.** It runs after PR 114a is live (nothing maps these any more).

**What.** `alembic/versions/0029_drop_legacy_tables.py` drops:
- `user_actions`, `decision_items`, `decision_feeds`, `broker_connections`, `tax_profiles`;
- `positions.bucket_id`, with its FK, via `batch_alter_table`, so SQLite rebuilds and Postgres drops the column and
  its constraint;
- then `buckets`.

Children go before parents. Every drop is guarded, because `0001_initial` builds from today's metadata and a fresh
database never had these tables. The downgrade recreates them empty, `bucket_id` FK included.

**Data dropped.** 1 `decision_feeds` row and 3 `decision_items` rows, from one call to the deleted
`/decision-feed/generate`. Everything else was empty (read-only counts in PR 113's entry).

**Verified locally on Postgres 16 (mandatory for this stage).** On a scratch database: upgrade head, downgrade -1
(the tables and the `fk_positions_bucket_id_buckets` constraint come back), insert a feed row, upgrade head. All six
tables, the column and the FK are gone; only `positions_account_id_fkey` remains; head is 0029.
`tests/test_the_legacy_tables_are_dropped.py` does the same round trip on SQLite in CI.

**Smoke.** `scripts/smoke/smoke-pr114b-drop-legacy.ps1 -Sha <sha>`: schema 0029; `/portfolio`, Today, the health check
and the rules list all read normally. **Ran against `bf224a7` (GitHub PR #16 merge): 6 PASS, 0 FAIL.** Production schema is
`0029_drop_legacy_tables`. A read-only query confirmed none of the six tables and no `positions.bucket_id` remain; 8
positions and 29 rules read normally. **Wave D complete.**

---

## PR 114a — the legacy tables are no longer mapped (D76, stage 3 part 1)

**The plan's order is inverted, deliberately.** It said "114.0: a migration drops the tables; 114: remove the model
classes". But `positions.bucket_id` is mapped on the live `Position` model. Dropping the column while the old code
serves would fail every positions query until the next deploy, which means NAV, Today and Holdings. D17 ("a migration
ships alone; nothing reads it until the next PR") describes ADDS. For a DROP the safe order is the mirror image: the
code stops mapping first, then the schema follows. So:
- **PR 114a (this, code only):** the models `Bucket`, `TaxProfile`, `DecisionFeed`, `DecisionItem`, `UserAction` and
  `BrokerConnection` leave the ORM, along with `Position.bucket_id`, `User.decision_feeds` and `Entity.buckets`. The
  tables stay in the database, unmapped and harmless.
- **PR 114b:** migration 0029 drops them, shipped alone.

**Tests.** `test_persist_feed_with_items` deleted. The migration-chain, startup-DDL and schema tests pass on SQLite and
Postgres.

**Smoke.** `scripts/smoke/smoke-pr114a-unmap-legacy.ps1 -Sha <sha>`. Schema still 0028; `/portfolio` NAV equals the sum
of rows; Today and the health check build. **Ran against `523bbcf` (GitHub PR #15 merge): 5 PASS, 0 FAIL** (8 positions,
NAV = sum, health 61/100).

---

## PR 113 — legacy removal, stage 2: the decision-feed cluster (D76)

**Plan v3, Wave D, second PR.**

**Deleted.**
- Routes: `routes/decision_feed.py` (`/decision-feed/demo|generate|latest`) and `routes/learning.py` (`/actions`,
  `/learning/profile`).
- Functions: `/decision-feed/weekly` and `/tax/review`. `workflows.py` keeps `/health-check`, `/risk/alerts` and
  `/scenario`.
- `services/pipeline.py` and `agents/allocation_agent.py`.
- Engines: `learning_engine`, `xai_engine`, and `safety_engine`, which moved here from stage 1 because only the
  pipeline imported it.
- Two schemas left with no importer (`schemas/feed.py`, `schemas/explainability.py`), plus `schemas/output_contract.py`.
- `feed_service.build_recommendation`, the feed's output mapper, whose only caller was its own test.

**Kept on purpose.** `engines/state_machine.py` and the lag, decision, risk and tax engines (the war room and What-if
use them), and `services/demo_data.py` (the war room's `demo_signals` path).

**Tests.**
- Deleted: `test_xai.py`, `test_learning_engine.py`, `test_safety_engine.py`, the AllocationAgent veto tests (3), the
  feed API tests (3) and the output-mapper test.
- The roster test and `docs/AGENTS.md` changed in the same commit, as that test requires: the AllocationAgent row,
  anchor and section are replaced by a "deleted" note.
- `test_persist_feed_with_items` stays until PR 114 drops the models.

**Smoke.** `scripts/smoke/smoke-pr113-legacy-stage2.ps1 -Sha <sha>`. **Ran against `80c3aed` (GitHub PR #14 merge): 15
PASS, 0 FAIL.** The 7 deleted routes return 404/405; health-check, risk alerts, scenario, the war room, What-if and
Today all answer.

**Production rows before stage 3** (read-only, 2026-10-11, `transaction_read_only = on`):

| Table | Rows |
|---|---|
| `user_actions` | 0 |
| `decision_items` | 3 |
| `decision_feeds` | 1 |
| `broker_connections` | 0 |
| `tax_profiles` | 0 |
| `buckets` | 0 |
| positions with `bucket_id` set | 0 |

The 1 feed / 3 items came from a single call to the now-deleted `/decision-feed/generate`. PR 114.0 drops them.

---

## PR 112 — legacy removal, stage 1 (D76)

**Plan v3, Wave D, first PR.** Owner: "delete in 3 stages" (D-j → D76).

**Re-checked at HEAD:**
- `index.html` calls none of the routes below (0 hits each).
- The only scripts naming them are under `scripts/archive/`.
- No live module imports them.

**Deleted.**
- Routers (each held only legacy routes): `lag`, `whs`, `simulation`, `safety`, `tax` (`/tax/preview`), `risk`
  (`/risk/preview`), `allocation`, `backtest`, `fees`, `screener`, `jobs`, `broker`, `entities`.
  - `/auth/status` moved to `auth.py`, same path.
- Functions: `/market/quote` and `/fx/rate`. `market.py` keeps `/research/events`, `/data-status` and
  `/providers/health`.
- Celery (`worker/celery_app.py`, `worker/tasks.py`) and the `celery`/`redis` pins plus `redis_url`.
- The broker scaffold (`app/brokers/*`, `services/broker_sync.py`) and its settings.
- The `market_refresh` job and `services/market_state.py`: it scanned and discarded the result.
- The unused `google-genai` pin (L3).

**Not deleted, and why.**
- **`engines/safety_engine.py`.** The plan put it in stage 1, but `services/pipeline.py` (stage 2) still imports
  it. It goes in PR 113.
- **The engines behind the deleted routes.** Backtest, fee, screener, whs and simulation are still used by live
  code; only the HTTP routes were legacy.

**Tests.**
- Deleted: `test_broker_sync.py`, `test_jobs.py`, the safety-route API test, the screener-endpoint test, the
  market-refresh test, the broker half of the Tel Aviv test.
- Rewritten to live routes:
  - backtest and fees keep their `/recommendations` halves;
  - the audit probe uses `/recommendations/restore`;
  - the loop-freedom race uses `POST /portfolio/add` (the live route that quotes).
- Updated: the PR 107 job guard (10 jobs) and the PR 110 public allowlist.
- **Found while doing this:** the loop test left a `/__control/slow_async` route on the shared app, which the PR 110
  public-route guard then saw in some orders. It now removes the route afterwards.

**Smoke.** `scripts/smoke/smoke-pr112-legacy-stage1.ps1 -Sha <sha>`. Against `cb4c768` it FAILs, as it should: all 17
routes are still served and the scheduler has 11 jobs. **Ran against `26d2ab0` (GitHub PR #13 merge): 28 PASS, 0 FAIL.**
All 17 deleted routes return 404, every live neighbour returns 200, and the scheduler has 10 jobs.

---

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
reproduced live. **Ran against `cb4c768` (GitHub PR #12 merge, 111 + 111.1): 7 PASS, 0 FAIL.** **Wave C complete.**

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

