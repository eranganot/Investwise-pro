# Execution plan v4: after the 2026-10-22 review (PR 147.1 onward)

> **Status: ACTIVE. Prioritised by the owner with Claude on 2026-10-23 (value against effort).** Work top to bottom
> through §4. Every item says what is already known, what is still to be proven, the approach, the tests and smoke it
> needs, the decision it waits on, and when it is done.
>
> **This file is the order of work. [`../BACKLOG.md`](../BACKLOG.md) stays the list, and
> [`../../STATUS.md`](../../STATUS.md) is the changelog.** When an item ships, update all three (§1.4).
>
> Supersedes [`EXECUTION_PLAN_2026-10-08_hardening.md`](EXECUTION_PLAN_2026-10-08_hardening.md) as the active plan.
> That plan's waves are complete.

---

## 0. Starting a new session on this plan

Read in this order, then pick the first item in §4 that is not marked DONE:

1. `CLAUDE.md` (repo rules), then this file's §1 (how to work) and §2 (where things stand).
2. `STATUS.md`: the newest 3–4 entries (PR 147, C15, PR 143, PR 142).
3. `docs/BACKLOG.md` for the row the item closes.
4. Check `git log origin/main --oneline -5` and `curl -s https://investwise-pro-production.up.railway.app/health`
   (`.commit`) to confirm what production serves before trusting §2.

---

## 1. How to work (the owner's rules, as practised 2026-10-22 .. 10-23)

### 1.1 Investigate before fixing (owner's standing rule)

- Root cause first, with **evidence**: a reproduction (a failing test on the current main) or a live production read;
  candidate causes **ruled out** with the observation that killed each; the exact code/state; the blast radius and
  why it was silent.
- **PROVEN** by observation: fix in the same turn. **INFERRED** only: stop and ask the owner.
- Record root cause and what was ruled out in the STATUS entry, not only in chat.
- A product choice (what a button should mean, a limit's value) is the owner's: ask with 2–3 options and a
  recommendation (`AskUserQuestion`), then build. Record the answer as a `D` entry.

### 1.2 The gate (until GitHub Actions minutes reset on 2026-11-01; BACKLOG O6)

GitHub Actions does not run (Free-plan minutes exhausted). Railway deploys every push to `main` regardless. So the
gate is local and must be green before a merge:

1. `ruff check app tests` (Python 3.12 venv).
2. Full suite **on Postgres 16** (owner, 2026-10-23: "focus mainly on Postgres"; the SQLite run is no longer required).
   Recipe: `service postgresql start`; role + DB `investwise`/`investwise`;
   `DATABASE_URL=postgresql+asyncpg://investwise:investwise@localhost:5432/investwise pytest -q`. A fresh database
   per run. ~40 minutes; **do not edit files while it runs** (a test that reads source by line number reads the
   wrong lines; seen in PR 143).
3. The PR's smoke against a **locally served build** (alembic-migrated Postgres DB of its own, `uvicorn` with
   `REQUIRE_AUTH=true`, a local-only `AGENT_API_KEY`, `GIT_COMMIT_SHA` set so `Assert-Build` passes), seeded with the
   situation the PR is about.
4. After the merge: wait for `/health .commit`, run the PR's smoke and `smoke-all.ps1` on production, record
   PASS/FAIL/SKIP in STATUS. **A FAIL is an incident** (the build is already live).

### 1.3 Smokes

- One per PR: `scripts/smoke/smoke-prNNN-<slug>.ps1` on `_smoke-common.ps1` (`Assert-Build -Sha -Schema` first).
  **Read-only** (GETs; a `dry_run=true` preview at most). **ASCII only.** pwsh 7.
- Check what the PR claims, and that what it claims not to change did not.
- Run it against production **before** the merge too: it should report the bug (FAIL or NOTE). That proves the
  smoke can see it and that every field it reads exists.
- Compare times in UTC (`Utc()` helper): PowerShell reads an ISO `Z` time as local.
- `-Since <deploy time>` turns pre-fix history into NOTEs and post-fix repeats into FAILs.
- The agent key is never printed: pipe output through `sed "s/$IW_AGENT_KEY/***/g"`.

### 1.4 Shipping one item

Branch from `origin/main` → reproduce (tests fail on main) → fix → tests pass → ruff → full Postgres gate → smoke on
a local build → docs:
- **STATUS:** an entry covering report, root cause, ruled out, fix, tests, smoke, and the production result
  (placeholder `PROD_RESULT`, filled in on the NEXT branch).
- **BACKLOG:** close or open rows.
- **DECISIONS:** a `D` entry for any new decision.
- **DOMAIN / ARCHITECTURE:** wherever the change made a page untrue.

Then: commit with `git commit -F <file>`, naming files explicitly (never `git add -A`). Open the PR via the REST API
(`gh api repos/eranganot/Investwise-pro/pulls`; GraphQL is blocked here), merge (the owner approved merging green
PRs), watch the deploy, smoke production. Never force-push.

### 1.5 Production access

Read-only, with the agent key (`x-agent-key`). **Never accept, ignore, re-arm, or POST anything that writes to
production.** A data repair is built as a preview (GET) plus a confirm (POST); the **owner** applies it.

Useful reads:
- `/health`
- `/api/v1/recommendations`
- `/api/v1/rules` and `/rules/events?limit=200` (the API caps at 200)
- `/api/v1/portfolio` and `/portfolio/ledger?limit=2000`
- `/api/v1/plan/sleeves`
- `/api/v1/push/status` (job history)
- `/api/v1/audit?limit=500` (routes only, no detail)
- `/api/v1/portfolio/ledger/repair` (preview)

---

## 2. Where things stand (2026-10-23, production `3b782d8`)

### 2.1 Shipped from the 2026-10-22 review (all live, all smoked)

| PR | What | D | Production smoke |
|---|---|---|---|
| 139 / 139.1 | A stop-out holds its ticker against every buyer until the sleeve's signal re-enters; the held-cash card says what a stop waits for | D114 | 6/0/0; smoke-all section 8 fixed |
| 140 | The protection card never switches off a stop; switched-off protection is one heads-up with "Re-arm all" | D115 | 4/0/3 |
| 141 | A stop on one 2x/3x fund blocks every 2x/3x fund (SOXL stop → no TQQQ buy); held-cash card offers non-blocked places only | D116 | 9/0/0 |
| 142 | Ignore on a fired stop re-arms it 10% below today (was: switched off) | D117 | 4/0/2 |
| 143 | A failed rule check fails the job and shows a HIGH heads-up; job history survives a deploy | D118 | 4/0/2, then 5/0/1 (history survived the C15 deploy) |
| C15 | The core realign trims only sleeves that are over their size, from their own holdings, never a basket ticker | D119 | 3/0/3 |
| 147 | Every new ledger Buy/Sell carries its price and value; a leg without them logs an error | D120 | 3/0/2 |

`smoke-all` on every build since: 22 passed, 3 failed. The 3 are **Gemini credits** (owner item, §3).

### 2.2 Verifications still owed (do these when the situation arises; record in STATUS)

- **PR 142** smoke sections 1 and 3 need a stop to fire and be ignored. Until then they SKIP.
- **PR 147** smoke section 1 needs a trade after the deploy (`-Since` = 2026-10-23 ~14:50 UTC).
- **C15** smoke sections 2 and 3 need a core-realign Accept after the deploy (`-Since` 12:13 UTC).
- **PR 141**: when the SOXL stop hold ends (the 06:15 signal job sees `btm_trend_soxl` go out and back in), confirm
  TQQQ funding resumes and nothing churns (C15 smoke with `-Since`).

### 2.3 The live book, briefly (for context, not for decisions)

- **Stop hold:** SOXL stopped out 10-23 05:45 at 82.11 and is on a stop hold. Every 2x/3x fund is blocked (PR 141).
- **Held cash:** about ₪4,026 is held for SOXL. The owner put ~₪1,910 of it to work at 10:28–10:30 UTC
  (held-cash redeploy: AGG, GOOGL, VXUS, QQQ, VTI, BND).
- **Sleeves:** about ₪6,672 short of their sizes, so the core card currently trims no sleeve.
- **Switched off:** one protective rule (the UGA trailing stop), shown on the "switched off" heads-up.

---

## 3. Owner actions (not code; do first, ~40 minutes)

| # | Action | Why | How |
|---|---|---|---|
| A1 | **Top up Gemini credits** | AI summary and Ask fail (HTTP 402); the only red in `smoke-all` | Google AI Studio → billing |
| A2 | **Rotate the agent key** | It was pasted in a chat on 2026-10-22 | Railway env `AGENT_API_KEY` → redeploy; give Claude the new one; update `scripts/.agent-key` locally |
| A3 | **O3: confirm database backups** | No backup = one bad migration loses the book | Railway → Postgres → Backups; note the schedule and a restore test in `docs/OPERATIONS.md` |
| A4 | **O2: rotate the GitHub PAT and the Railway token** | Never rotated since 2026-08-03 | GitHub settings; Railway project token |
| A5 | R2: delete the orphan `redis-volume` | 150 MB attached to nothing | Railway dashboard |
| A6 | O4: install the pre-push hook on the Mac | Branch-protection substitute | `scripts/ops-check.ps1` (verify it still applies on macOS) |
| A7 | Apply the PR 147.1 ledger repair | Owner-run production write | After §4.1 ships: review the preview, then `POST /api/v1/portfolio/ledger/repair {"confirm": true}` (add `"include_estimates": true` only if wanted) |

---

## 4. The work, in order

Effort: S < 2 h, M = half a day, L = days. Value: what it protects or gains for the owner's money or for the app's
reliability.

**Order from 2026-10-24** (owner: "As suggested", after PR 141-148, C15, C18, C24, C26, P1/P14/P16 shipped):
C27 (in its gate) → C16 → PR 145 → P19 (quick) → P17 → C28 / C17 / C13 (small) → O6 (from 2026-11-01) → PR 146 →
P9 → P18. Owner items: A2 and O2 (rotate the agent key and the GitHub/Railway tokens), A1 (Gemini credits), A6 (the
pre-push hook on the Mac), D-1 (where the daily smoke's key lives).

### 4.1 PR 147.1: reprice the old ledger rows — **DONE: applied by the owner 2026-10-23 (87 repriced)**

**Value M · Effort S · BACKLOG C22 · Owner approved ("If you recommend it, sure", 2026-10-23)**

- **Known (proven in PR 147):** 140 of 440 production rows had price 0 and/or value 0. On 3b782d8, 114 of 400 trade
  rows before the deploy still lack them. Cash was always right. Visible effect: Holdings shows no "last buy" for XOM
  (bought 10-21, ₪891, price 0), and the ledger cannot say what a stop or trim raised.
- **Built (branch `pr147-1-reprice-old-ledger-rows`, commit "PR 147.1 (WIP)"):** `ledger_repair._unpriced()` adds
  `unpriced_trades` to the existing preview/apply.
  - **Value:** the row's own value; else the rule event that executed it (`cash_added_ils + tax_ils`, paired by
    ticker, ±3 min and share count); else shares × the price that half hour × today's FX (estimate).
  - **Price:** `_price_at` (the 30-min bar, else the day's close).
  - **Confidence:** MEASURED when the value came from the row or a rule event and the price agrees within 5%.
    `apply(confirm)` writes MEASURED rows; ESTIMATE rows only with `include_estimates`. The note gets
    `repriced 2026-10-23: ...; was price X / value Y`. Idempotent. Separate `reprice_skipped` list.
  - Tests: `tests/test_pr147_1_reprice_old_ledger_rows.py` (2). The 19 repair tests pass.
- **Remaining:**
  1. A smoke `smoke-pr147-1-reprice-preview.ps1` (read-only): the preview answers, lists `unpriced_trades` with a
     confidence each, writes nothing (`read_only: true`), and after the owner applies it, no MEASURED row is left.
  2. Full Postgres gate → STATUS entry (fill PR 147's `PROD_RESULT` too: PASS 3 FAIL 0 SKIP 2 on `3b782d8`) → PR →
     merge → production smoke.
  3. Read the production preview and give the owner the counts by confidence and 3–4 example rows (including the
     10-23 SOXL stop and the 10-21 XOM buy). The owner applies it (§3 A7).
  4. Re-run the PR 147 smoke without `-Since`: the count of rows lacking a price should fall by the number written.
- **Done when:** the owner has applied it, the XOM "last buy" shows, and STATUS records the counts.

### 4.2 PR 144: tell the owner when production is broken

**Parts 1 and 2 DONE in PR 144 (D121). Part 3 waits on D-1.** **Value H · Effort M**

- **Problem (proven 2026-10-08 and again this week):** CI does not block a deploy, and Actions is off until Nov 1.
  A red build is live with no one told: `5a6dfef` served while `test-postgres` was red, and `run_nav_snapshot_blocking`
  failed 7 nights out of 7 unseen. PR 143 made a failed rule check visible on Today, but nothing watches the rest.
- **Approach (three parts; build 1 and 2, propose 3):**
  1. **Boot self-check.** At startup, resolve every scheduled job's entry point. Also import the inner names a job
     imports lazily: the `nav_snapshot` failure was an import inside the function. Check the schema revision
     matches head. Record the result as a job `boot_check` in `JOB_STATE`, persisted by PR 143.
  2. **"Something is failing" heads-up + push.** Generalise PR 143's `_rule_check_headsup`: one HIGH heads-up when
     any job's `last_ok` is false, or a daily job has not succeeded in 26 h, or `boot_check` failed. It names the
     jobs and errors. One push per new failure signature, through the existing rate limiter: a new trigger in
     `push_service._TRIGGER_RULES`.
  3. **A daily production smoke run by a scheduled task.** A Claude scheduled task (cloud) runs `smoke-all.ps1` and
     the newest PR smoke against production every morning and messages the owner on a FAIL. It needs the rotated
     agent key (§3 A2) stored for the task. **D-1:** does the owner want this, and at what time?
- **Tests:** a job with a broken lazy import fails `boot_check`; a failed job and a stale daily job each raise the
  heads-up; a healthy state raises nothing; one push per signature.
- **Smoke:** `smoke-pr144-production-says-when-it-is-broken.ps1`:
  - `boot_check` present and ok on the served SHA;
  - every registered job has a history entry or a `next_run`;
  - no failing-job heads-up while all jobs are ok;
  - NOT CHANGED: PR 143's rule-check heads-up still behaves.
- **Done when:** a deliberately broken local build shows the heads-up, and production shows `boot_check` ok.

### 4.3 P16 + P14: the limits that cap the goal chance — **DECIDED (D122, D123); P14 built; next: the goal comparison (P1)**

**Value H · Effort S (after decisions)**

- **P16, the halving limit.** The optimiser (PR 136a) finds no change that lifts the goal chance past ~9–10% within
  the 30% halving limit. Without the protective rules it is ~26%, but with ~67% halving risk. On 10-23 the
  protection solve read 0.3% goal odds with the rules and 21% without. **D-2:** keep 30%, or raise it, and to what?
  Show the owner the frontier at 30 / 40 / 50% before he chooses (`/api/v1/plan/frontier` and the stored verdict).
  The value is a setting (Card-thresholds sheet), so the change is small.
- **P14, the same-bet correlation limit (0.80).** PR 141 measured SOXL-trend vs vol-target-TQQQ at 0.78 on their
  signals (passes) and 0.95 on daily returns (the same bet). **D-3:** lower the limit to 0.75, or measure same-bet on
  returns of the underlying instruments rather than on strategy signals, or both? Recommended: measure on returns,
  because it is the property that mattered on 10-22. Then run `scripts/smoke/smoke-sleeve-plan-all.ps1` and record
  134d–136b in STATUS (the rest of P14).
- **Done when:** both are recorded as D entries, implemented as settings, and the goal card shows the new odds.

### 4.4 PR 148: a ceiling on the leveraged (2x/3x) share of the book

**Value M-H · Effort M · Decision needed (D-4: the value; Claude suggested ~44%)**

- **Why:** the 2x/3x bet is what cost the most this month (SOXL stopped out three times, 10-12 .. 10-23). PR 141 blocks
  re-entry after a stop; nothing caps the share before one.
- **Approach:**
  - A goal setting `leveraged_ceiling_pct` (Card-thresholds sheet, beside the card thresholds; the owner asked for it
    to live in the goal settings). `strategy_profile.GEARED` defines the set.
  - Every buyer refuses a buy that would take the geared share over the ceiling. Do it where PR 141's group block
    lives (`buyback_hold.assert_buyable` / `_fund_sleeve`), or as a sibling check, so all buyers inherit it.
  - The book target caps geared sleeves at the ceiling and parks the excess in the core.
  - A card appears when the book is over the ceiling ("trim to the ceiling"), sized like the cap trim.
  - Tests reuse PR 141's buyer list.
- **Smoke:** the setting reads back; no card on Today buys a geared fund past the ceiling; Fund-my-sleeves (dry run)
  respects it.

### 4.5 C18: auto re-arm paused protection — **BUILT (D124)**

**Value M · Effort S-M · Decision needed (D-5: after how many days? suggest 3)**

- **Owner, 2026-10-23:** heads-up first (PR 140, done), auto re-arm later.
- **Approach:** in the rule check (`evaluate_user`), a downside rule (`frontier_service.DOWNSIDE_RULE_TYPES`) switched
  off for more than `rearm_after_days` is switched back on, unless it is already crossed: that case is PR 138's
  card, never auto-armed into an instant sale. It writes a `resumed` event with `by: auto` and a note.
- **Tests and smoke:** the event, the crossed exception, the setting; the PR 140 heads-up drops the re-armed rule.

### 4.6 C16: the fund-sleeve card variant gets the funder's preview

**Value M · Effort S-M · Root cause INFERRED: prove first**

- **BACKLOG:** the `fund_plan` variant of the sleeve card is built without the funder's dry-run, so it can offer an
  Accept the funder refuses (the same class as PR 137).
- **First:** reproduce. Build the card on a book where the funder refuses (a stop hold, or the drift gate), and
  accept it. Then apply the cold-start card's pattern (`load_basket(... dry_run=True, honour_holds=True)` inside
  `_agent_tx`); a refused preview becomes guidance (no Accept) carrying the funder's reason.

### 4.7 PR 145: hygiene that prevents repeats

**Value M · Effort M · No decision needed**

1. **Postgres-only.** Tests default to the Postgres URL when set. Remove the "SQLite job proves less" trap from docs.
   Drop the SQLite CI job (keep it as a nightly at most, after Nov 1). Update CLAUDE.md "Known sharp edges" and
   BACKLOG O6, which still say "full local SQLite + Postgres".
2. **`smoke-all.ps1` read-only.** Section 5 POSTs `/rules/evaluate`, so every smoke-all run writes, firing rules and
   un-latching them. Replace it with reading `push/status` → `price_refresh.last_result.rules` (PR 143 records it).
3. **V3:** make `test_dismissed_recommendations_are_counted_and_restorable` deterministic (no live market data).
4. **The pre-push hook** (O4) checked in and documented for macOS.
5. **Docs pass:** DEVELOPMENT.md (the gate as in §1.2, the local-serve smoke recipe, the reproduce-first habit),
   OPERATIONS.md (backups from §3 A3, the Railway-deploys-regardless warning), ARCHITECTURE (job history,
   boot_check after PR 144).
6. **V1 / V2:** void the unrun QA docs (or re-scope). Owner OK needed only if he wants them kept.

### 4.8 O6: CI after 2026-11-01

**Value M · Effort S.** One Postgres job + ruff, on pushes to `main` and on PRs. No SQLite job. Then CI is the gate
again, and §1.2 point 2 becomes a pre-push habit rather than the only gate.

### 4.9 Small correctness items (S each)

- **C17:** a failed Accept's alert shows the reason and the legs, as the Plan tab does (`index.html`; the route's
  HTTP 400 detail already carries the refusal text since PR 139).
- **C13:** a test for the unspent-leg refund (`unspent_ils` in `_execute_funded_sleeve`).

### 4.10 PR 146: speed (measure first)

**Value M · Effort M.** The warm Today call is 1.2 s (smoke-all). Measure before changing anything: cold Today after a
deploy, the Plan tab, Holdings, the 30-min rule check, the nightly solves. Use `_tm.mark` timings and `/health`. Fix
the slowest measured path only, and record before/after in STATUS.

### 4.11 Added to the list (owner, 2026-10-24) and later

| # | Item | Value | Effort | Priority | Note |
|---|---|---|---|---|---|
| P19 | Default core mixes as settings | L | S | P2, a quick item after PR 145 | "every decision is a setting" |
| P17 | Live stock rotation (selection strategies as sleeves) | M | L | P2, after P19 | D101; the one new way to beat the S&P |
| P9 | Per-holding day attribution; an intraday "today" chart point | L-M | M | P3 | |
| P18 | Fixed-amount goals in the goal model | L for this owner | M | P3 | D105; he uses a %/yr goal |
| P20 | Relative value across two prices of one exposure (dual listing, fund vs NAV), gap vs its 30/60-day range | L-M | M | P3, after P17 | measure the gaps after costs first |
| C14 | Israeli inflation-indexed CGT basis | M | M | later | Needs an accountant's sign-off first |

### 4.12 Parked or proposed to drop (owner to confirm)

- **Drop:** P10 (promote engines to agents plus a "Chief" agent); V1, V2 (void).
- **Parked:** P1, P5, P6, P7, P8, P11, P12, P13, P15, O5, S6, L8 (reasons in BACKLOG).

---

## 5. Open decisions (ask at the start of the item that needs it)

| # | Decision | Needed by | Options / recommendation |
|---|---|---|---|
| D-1 | A daily production smoke as a scheduled task? Time? | §4.2 part 3 | **Decided: yes, 08:30 Israel time, message on FAIL only.** Key: a separate read-only `SMOKE_API_KEY` (D131), held as `IW_SMOKE_KEY` on the cloud environment |
| D-2 | The halving limit (P16) | §4.3 | **Decided D122:** keep 30%, revisit the goal (P1: goal comparison) |
| D-3 | Same-bet test: lower to 0.75, or measure on underlying returns | §4.3 | **Decided D123:** on what sleeves hold |
| D-4 | The leveraged ceiling value | §4.4 | **Decided: 44%** |
| D-5 | Auto re-arm after N days | §4.5 | **Decided D124: 3** |
| D-6 | Drop P10, void V1/V2 | §4.12 | Recommend drop/void |

---

## 6. Facts a new session would otherwise re-derive

- **Two kinds of hold.** Stop holds are `trading_rules` rows of type `buy_back` with `params.kind = "stop"`
  (`buyback_hold.py`). Every 2x/3x fund is blocked via `with_group` (derived, never stored). The 06:15
  `strategy_signals` job ends a stop hold on the sleeve's out-then-in.
- **The rule check** runs only inside the 30-minute `price_refresh` job (`pricing_service._refresh_all` →
  `rules_service.evaluate_all`).
- **Job history** lives in `kv_settings job_state:<id>` (PR 143) and is shown by `/api/v1/push/status`.
- **Ledger readers:**
  - `net_flows_ils` reads only `Adjust` rows.
  - Holdings' "vs last buy" reads Buy rows with `price > 0`.
  - The per-holding chart reads quantities.
- **`sleeve_occupancy`** splits a shared ticker pro-rata to each sleeve's target weight and reports
  `held_by_ticker_ils` (C15).
- **Same-bet correlation is measured on strategy signals:** SOXL-trend vs vol-target-TQQQ = 0.7804 against a 0.80
  limit. Daily returns of SOXL/TQQQ over the month to 10-22 = 0.947.
- **Test helpers that already model the live situations:** `tests/test_a_stop_out_is_not_bought_back.py`
  (`_book_with_a_fired_stop`, `_accept_the_stop`), `tests/test_c15_the_core_trims_only_what_is_over.py` (`_book`,
  `_accept_core`), `tests/test_pr141_one_bet_one_hold.py`.
- **Cross-loop rule:** any test or job opening a session from its own loop builds a throwaway `NullPool` engine in that
  loop (`own_session` fixture in tests).
