# Execution plan v3: hardening (PR 105–119)

> **Status: APPROVED by the owner on 2026-10-08. Execute wave by wave** (see §2a). Nothing has been implemented yet.
> Decided: D-e, D-a, D-b. Open: D-c, D-d, D-f, D-g, D-h, D-i, D-j, D-k. Ask each one **at the start of the wave that needs it**
> (§3), not earlier. Waves A and B are fully decided and can start immediately.
>
> **Starting a new session on this plan?** Read [`HANDOFF_2026-10-11.md`](HANDOFF_2026-10-11.md) first.
>
> **Basis:** every problem from the 2026-10-07 docs-overhaul review was re-investigated with the `investigate-issue`
> method: a direct observation (a scratch test reproducing the fault, or a live production read), the candidate causes
> killed one by one, and an Evidence Block marked PROVEN or INFERRED. The full evidence and the reproducing probes are
> in [`evidence-2026-10-08/`](evidence-2026-10-08/). Line numbers refer to `88bb5d4` (main after PR #1).
>
> Supersedes the "Correctness / Security / Cleanup" sections of [`../BACKLOG.md`](../BACKLOG.md) as the order of work.
> The backlog stays the list; this file is the sequence.

---

## 1. What the investigation found

### 1.1 Headline

| Severity | Count | Items |
|---|---|---|
| **Critical** (one tap changes the book by the USD/ILS rate) | 2 | **C2b**, sleeve-drift trim oversells; **C3+**, "Replace book" mints NAV |
| High | 5 | C1, C2a (sleeve funding sells an under-target class and raises drift); C4 (6 jobs report success when they fail); O3 (no backups); **R1** (Railway config deadline 2026-12-01) |
| Medium | 9 | C3 (FX leaks into assistant / risk validation / fees); C5 (5 jobs never run in a test); C9 (a GET deletes rules); C10, C11 (risk-off card driven by a wrong number from the wrong regime); C6; C8; S2; L-ledger (`/portfolio/add` ledger hole, inferred) |
| Low / debt | many | S1, S3–S6, C7/C7b, L2–L15, P2, P4 |
| **Closed by the investigation** | 3 | **O1 VAPID pin** (live: `vapid_pinned_by_env: true`, `vapid_env_matches_db: true`); **C2c** redeploy_cash (claim held: drift 0.50→0.23); **L6c** "$ amount" copy (correct: the server sizes in USD) |

**Nothing is on fire today.** A live read of production (2026-10-07 22:00 UTC) showed:
- **Cards:** zero actionable cards (one heads-up).
- **Health:** `/health` serving `88bb5d4` at schema `0028`, and all jobs with `failures: 0`.

The two critical bugs are **latent**:
- **C2b** fires as soon as a sleeve drifts more than 5 points above its target.
- **C3+** is blocked in the UI while you run sleeves (`index.html:3408`), but the server does not enforce the block, and it is the default for any book without sleeves.

### 1.2 Evidence summary

| ID | Verdict | What was observed | Root cause |
|---|---|---|---|
| **C2b** | PROVEN | Card "Trim about ₪18,550 of SOXL back to your 25% sleeve" → Accept sold 185 shares (₪68,635); SOXL 300→115 sh, ≈11.5% of the book, not 25% | `recommendations.py:2555-2558` `int(_amount / current_price)`: ₪ divided by a USD price. PR 102 fixed the sibling at `:1375`; `test_card_claims.py:139` skips trims |
| **C3+** | PROVEN | "Load basket" replace: a ₪37,100 book became ₪137,641 (×3.71) | `strategy_service.py:1183-1207` `_replace_book` sizes `qty = weight*budget_ILS/price_USD` |
| C1 | PROVEN | Grow + `bal_6040` core + SOXL 10%: `/mix` says FI target 32%, held 20%; sleeve funding sells ₪37,100 of BND "the most overweight class (20% vs 10%)" | `strategy_service.py:780, :891, ~:995`, `target_solver.py:603` call `plan_funding` without `book_target` → `OBJ_TARGET`. Guard test scans 2 files only |
| C2a | PROVEN | Same book: drift vs book target 0.44 → 0.64 after funding | Sleeve funding never runs the post-trade drift check that `propose_funded_buy` has |
| C3 | PROVEN | SPY ₪37,100 shown to the assistant as 10,000; class mix 17.5/65 vs real 44/44; structural beta 0.27 vs 0.51; fee savings ÷3.71 | `qty*price` in native currency at `context_service.py:59,66`, `recommendations.py:2928`, `agents/fee_agent.py:19`; INFERRED same at `recommendations.py:3791`, `routes/backtest.py:17` |
| C4 | PROVEN | `backtest_refresh`, `strategy_signals`, `push_evaluate`, `push_digest`, `futures_warm`, `market_refresh` record `last_ok: True` with an injected raise **and** with a missing-table DB | `try/except Exception` returning the healthy-empty shape: `backtest_service.py:595-601`, `strategy_signal_service.py:449-455`, `push_service.py:595-608`, `scheduler.py:151-155`, `market_state.py:26-29`. One bad subscriber also aborts the whole push fan-out |
| C5 | PROVEN | No test executes those 5 jobs (or their inner coroutines); `price_refresh` only partly | — |
| C11 | PROVEN (live Yahoo) | Futures "day change" is a ~4-session change: CL=F −2.38% vs true −0.56%; **sign flipped** for ES (+0.94 vs −0.30), NQ, VIX (−7.99 vs +0.47) | `markets_service.py:50` uses `chartPreviousClose` with `range=5d`; `previousClose` is always `None` |
| C10 | PROVEN | Futures say risk-off (VIX 22.5, −0.4%) while `engines/regime` says risk_on 0.95; Today shows the futures card ("raise cash 5–10%, trim volatile positions") | `recommendations.py:2165-2184` still reads `cached_regime()` (futures), which the code itself calls display-only; no TTL on `cached_regime` |
| C9 | PROVEN | One GET of Today deleted watch rules (AMZN `buy_dip`, GOOGL `price_below`, NVDA stop) on unheld tickers, including fired ones, before any card showed | `rules_service.purge_orphan_rules` (`:304-345`) via `list_rules` inside the build. INFERRED: the orphan heal (`recommendations.py:3061-3080`) can retire an unseen stop-loss |
| C6 | PROVEN | Two bodyless POSTs (READ_ONLY token) added TEVA 300 / GOLD 60 / BOND 100 to the real book, no ledger row | `brokers/registry.py:12-14` returns the mock before the `BROKER_ENABLED` check; `broker_sync.py:107` writes into "Personal" |
| C7b | PROVEN | `/whatif` on an empty book returns "Buy TEVA (NYSE)", "Rebalance GOLD (SPOT)", unlabelled. **UI path** (`index.html:3833`) | `whatif.py:53` `… or DEFAULT_OBSERVATIONS` ignores `demo_signals` (war room honours it) |
| C7 | PROVEN, narrow | `/decision-feed/weekly` same fallback, empty book only; UI never calls it | `workflows.py:162-163` |
| C8 | PROVEN | Stubbed `/ask` reply "returned 37.4% … TEVA will hit 412" returned verbatim, no disclaimer; `digest_service.unsupported_numbers` flags both | `ask_service.py:37-45` never verifies; same in `ai_service.py:86,113,130,159` |
| S1 | PROVEN | 25 of 67 write routes accept READ_ONLY (accept a card, PUT/DELETE a position, rules CRUD, broker sync). Only reachable via an m2m token (Google login is always SUPERADMIN) | Writes rely on `acting_user` alone |
| S2 | PROVEN | Bearer and replayed cookie both 200 after `/auth/logout` | `require_role` never reads `revoked_tokens`; 30-day SUPERADMIN cookie |
| S3 | PROVEN | Another subject deleted the owner's push subscription (1→0); subscribe can re-own an endpoint | `push_service.py:149-159` no subject predicate |
| S4 | PARTIAL | No anonymous route returns holdings/NAV. Real: `/providers/health` leaks held tickers (`quote:SECRETHOLDING`); `/markets/summary` makes an uncached Gemini call per anonymous hit; `/adversary/diagnostics` too when enabled; `/docs` maps 136 paths | Routers without auth deps |
| S5 | PROVEN | `require_api_key` has 0 route references; boot warning "write endpoints are unauthenticated" is false | Dead code |
| L11 | PROVEN | Boot time = slowest of 8 Yahoo futures fetches (12 s stub → 12.1 s); `_http_text`'s cap is not a cap (10 s vs nominal 6 s) | `scheduler.py:156` direct call in lifespan; `ThreadPoolExecutor` `with`-block joins the hung thread |
| L10 | PROVEN | A Yahoo outage overwrites a good futures cache with empty → regime silently "neutral" | `markets_service.py:108-123` caches `[]` |
| P2 | PROVEN | Below NAV ≈ ₪50k the ₪100 card minimum is inert (funding refuses < ₪250 first); the commodity card degrades into an un-actable "Buy ₪180 of X" heads-up | Two minimums, `recommendations.py:5095-5127` |
| **R1** | OBSERVED (Railway CLI) | `railway.json` "Config as Code is deprecated … existing files keep working until **2026-12-01**" | Platform change. `railway.json` carries `preDeployCommand` (migrations) and the healthcheck |
| O3 | Gap confirmed | No backup/restore anything in the repo; Railway backup status not visible from the CLI (`railway volume list`: `postgres-volume` 243 MB/5 GB) | — |
| R2 | OBSERVED | A detached `redis-volume` (150 MB) is still in the project; no Redis service uses it | Leftover from the Celery era |
| L-ledger | **PROVEN 2026-10-11, worse than inferred** | `POST /portfolio/add` on a held ticker **replaces** the position: IAU 100 sh + "$1,000" → 7.54 sh (₪18,550 → ₪3,710). No ledger row, no contribution, cash untouched | `routes/intake.py:352-398` calls `upsert_positions` (an intake overwrite) instead of `buy_into_book`. Fixed in PR 105: blend, record the leg, debit cash (owner: buys come out of cash) |
| `_OBJ_RETURN` | INFERRED | Hand-typed return assumptions (Grow 8.5% …) still size the "behind goal" levers after PR 98's "one return basis" | `recommendations.py:3153, :1918-1922`. Investigate in PR 108 |

The rest (L2, L3, L4, L5, L6, L7, L9, L12–L15, P4) are proven debt: wording, dead pins, stale comments, naming, a11y, and fat routes. The details are in `evidence-2026-10-08/inv-cleanup.md`.

## 2. Principles for executing this plan

1. **Money bugs first, then the instruments that would have caught them, then the rest.** Wave A stops wrong trades. Wave B
   makes failures visible. Wave C closes doors. Wave D removes dead weight. Wave E handles settings and polish.
2. **Each PR follows the house rules** ([DEVELOPMENT.md §8](../DEVELOPMENT.md#8-shipping-a-pr)):
   - Tests that **fail on HEAD** first. Most are the probes in `evidence-2026-10-08/probes/`, promoted into `tests/`.
   - SQLite + Postgres + ruff green.
   - A `smoke-prNN-<slug>.ps1` that runs `Assert-Build` first.
   - A STATUS entry recording the root cause, what was ruled out, and why it was silent.
   - A BACKLOG row closed.
3. **A migration ships alone** (D17). Only PR 111.0 (S2 sessions) and PR 114.0 (legacy tables) need one.
4. **No behaviour change hides inside a refactor.** L7 (fat routes) is last and pure-move.
5. **Every PR closes the *class*, not just the instance.** Each one ships a guard test so the next sibling fails CI. This is the
   lesson of C2b, a sibling of a bug PR 102 had already fixed.

## 2a. How to execute (owner's choice, 2026-10-08)

**Wave by wave.**
1. Ship every PR in a wave back to back. Each PR still gets its own commit, full local gate (SQLite + Postgres + ruff),
   push, smoke run with `-Sha`, and STATUS entry.
2. **Stop and report at the end of each wave.** Report what shipped, the smoke results and anything surprising.
3. Wait for the owner's go-ahead before starting the next wave. That go-ahead is also when the next wave's open decisions get asked.

Stop early, mid-wave, on any of:
- a red smoke
- a red CI run
- a test that won't go green
- a finding that contradicts this plan's evidence (re-run `investigate-issue`; don't push through)

Order: Wave A (PR 105, 106, 120) → Wave B (107, 108, 109) → Wave C (110, 111.0, 111) → Wave D (112, 113, 114.0, 114) →
Wave E (115, 121, 116, 117) → Wave F (118a, 118b). PR 119 (Railway config) must land before **2026-11-15**, whichever wave is running;
raise it with the owner at a wave boundary in early November at the latest.

## 3. Decisions

### 3.1 Taken (owner, 2026-10-08)

| ID | Decision | Applies in |
|---|---|---|
| **D-e** | **Yes:** the server refuses "Replace book" (load-basket `mode="replace"`) while any `plan_sleeves` exist, mirroring the UI block. This is in addition to the FX sizing fix | PR 105 |
| **D-a** | **The strategies' regime** (`engines/regime`: 200-day trend, realized vol, breadth, via the cached `gfinance.regime_proxy`) drives Today's risk-off card. Futures stay on the Markets tab as a labelled cross-check only | PR 108 |
| **D-b** | **Keep entry alerts:** `buy_dip`, `price_below`, `price_above` on tickers you don't hold are watch alerts and are never purged. `purge_orphan_rules` removes only exit rules (stop-loss, take-profit, trailing stop) of positions you no longer hold | PR 109 |
| Cadence | **Wave by wave** (§2a) | all |

These are recorded as D69–D72 in [`../DECISIONS.md`](../DECISIONS.md).

### 3.2 Still open (ask at the start of the wave that needs it)

| ID | Ask before | Recommendation |
|---|---|---|
| D-f | Wave C (PR 111) | Per-user "sessions valid after" timestamp |
| D-j | Wave D (PR 112–114) | Yes, delete in 3 stages |
| D-c | Wave E (PR 115) | One minimum: max(funded minimum, % of book) + commodity card silent below it |
| D-d | Wave E (PR 115) | All 13 constants become settings |
| D-g | Wave E (PR 116) | Commodity buys restricted to the core basket, as a setting, default on |
| D-h | Wave E (PR 116) | Delete the Sentry block |
| D-i | Wave E (PR 117) | Show screenshots of both `.seg` looks; owner picks |
| D-k | not blocking | Goal definition review (from v2.2) |

### 3.3 All options considered (reference)

| ID | Question | Options | Recommendation | Blocks |
|---|---|---|---|---|
| **D-a** | The Today "Markets look risk-off" card is driven by futures (C10/C11). What should drive it? | (1) The strategies' regime (`engines/regime`: 200-day trend, vol, breadth); (2) keep futures but label it "cross-check" and remove the "raise cash / trim" advice; (3) drop the card | **(1)**: one regime for the whole app; futures stay on the Markets tab as a cross-check | PR 108 |
| **D-b** | Rules on tickers you don't hold (`buy_dip`, `price_below`, `price_above`) are deleted by the next Today build (C9). | (1) Keep them: they are watch/entry alerts; purge only exit rules (stop, take-profit, trailing) of sold positions; (2) refuse them at creation with a message | **(1)** | PR 109 |
| **D-c** | Two minimum trade sizes (P2): card ₪100 / 0.5% vs funded leg ₪250. | (1) One number: card floor = max(funded minimum, % of book), and the card-minimum setting shows the effective value; (2) keep both, make the commodity card silent below the funded minimum | **(1)**, plus the commodity card fix | PR 115 |
| **D-d** | Which hard-coded decisions become settings (P4)? | The 13 listed in §4 PR 115, or a subset | **All 13** (no migration; the sheet renders them automatically) | PR 115 |
| **D-e** | Server-side: refuse "Replace book" when sleeves exist (C3+)? Today only the UI blocks it. | Yes / no | **Yes**, plus the FX fix | PR 105 |
| **D-f** | Logout should actually end the session (S2). | (1) Per-user "sessions valid after" timestamp (log out everywhere; one migration); (2) revoke the single cookie's `jti` | **(1)** | PR 111 |
| **D-g** | Commodity buys when a core is chosen (L14): sugar (CANE) vs the core's IAU/DBC. | (1) Restrict to the core basket's commodity tickers, as a setting; (2) leave plan-blind | **(1)**, default on | PR 116 |
| **D-h** | Sentry (L3): wired but not installed. | (1) Delete the Sentry block; (2) pin `sentry-sdk` and use it | **(1)** unless you want Sentry | PR 116 |
| **D-i** | The segmented control has two conflicting CSS definitions (L12). Which look? | Soft grey pill (first block) / blue "on" (second block) | Screenshot both in PR 117, you pick | PR 117 |
| **D-j** | Delete the legacy subsystem (decision feed, Celery, calculators, broker scaffold, 6 tables)? | Yes, in 3 stages / keep | **Yes** | PR 112–114 |
| D-k | (Still open from v2.2) Should deposits or extra years raise the goal target? | — | Your call; not blocking anything here | — |

## 4. The PRs

Format per PR: **Goal**, **Changes** (file:line at `88bb5d4`), **Tests first** (must fail on HEAD), **Smoke**, **QA**,
**Risk / rollback**, **Effort**. Effort: S ≤ ½ day, M ≈ 1 day, L > 1 day (Claude session-days).

### Wave 0: owner ops (no code; can start today, in parallel)

| # | Action | How | Done when |
|---|---|---|---|
| W0-1 | **Check Railway Postgres backups** (O3) | Railway dashboard → Postgres → Backups. Enable scheduled backups if the plan allows; note schedule + retention | Recorded in OPERATIONS.md §7 (Claude updates it from your note) |
| W0-2 | Remove the orphan `redis-volume` (R2) | Railway dashboard → volume → delete (no service uses it; verify "Attached to: N/A" first) | `railway volume list` shows only `postgres-volume` |
| W0-3 | Delete the dead draft `app/services/candidate_search.py` + `tests/test_candidate_search.py` from your main checkout (L13: a first draft of the shipped `sleeve_count.py`; 0 callers) | `Remove-Item` in `C:\dev\Investwise-pro` | Untracked files gone |
| W0-4 | Commit the two missing governing plans and the Plan-tab design file | Copy to `docs/archive/plans/` and `docs/design/plan-tab-options-2026-10-05.html` | In git |
| W0-5 | `git rm investwise.db` + `git rm -r frontend` (L2: proven unused) | One commit, or Claude does it in PR 116 if you allow deletions | Gone from `main` |
| W0-6 | Still-open rotations: GitHub PAT, Railway token (O2) | `scripts/ops/OPS_RUNBOOK.md` | `ops-check.ps1` shows DONE |

### Wave A: stop wrong trades

#### PR 105: every share count is sized in the price's own currency *(Critical; needs D-e)*

- **Goal:** no card or basket load can convert shekels into shares at a dollar price.
- **Step 0: prove or kill L-ledger.** Run `POST /portfolio/add` on a scratch book and read `transactions`/`contributions`/cash.
  If the hole is proven, fix it here (`record_leg`, and debit cash or record a contribution, per the existing `buy_into_book` path).
  If it is not, record that in STATUS.
- **Changes:**
  1. `recommendations.py:2555-2558`: sleeve-drift trim → `shares = round(qty_held * (gap_abs/100) / w_actual, 6)` (the PR 102 form), or `_amount / price_ils`.
  2. `strategy_service.py:1183-1207` `_replace_book`: `qty = weight*budget / (to_major_units(price) * fx_rate(quote.currency))`; set `market` from the quote.
     **D-e:** refuse `mode="replace"` server-side while `plan_sleeves` exist (mirror of the UI block).
  3. Route every native valuation through `fx.position_value_ils` (`fx.py:189-198`):
     - `context_service.py:59,66`
     - `recommendations.py:2928` (risk validation; also use `classify_holding`)
     - `recommendations.py:3791` (sector hedge)
     - `agents/fee_agent.py:19`
     - `routes/backtest.py:17`
  4. `_apply_redeploy_cash` (`recommendations.py:6386-6390`): add `to_major_units`, as `_apply_buy_funded` does. Prove with a `.TA` stub first.
- **Tests first:**
  - Promote `probes/scratch_funding/test_c2_drift_trim.py` and `test_c3_fx.py` into `tests/`.
  - Make `test_card_claims.py` **measure trims**: delete the `elif kind == "trim": pass` at `:139`, apply the trim, and assert the post-trade weight is within the band of the card's target.
  - Load-basket replace on a USD book: NAV after = NAV before ± 1%.
  - `gather()`: `sum(holdings.value_ils) == nav_ils` and the class breakdown = `current_mix` on a mixed USD/ILS book.
  - **Guard:** an AST test that no `quantity * current_price` (or `qty * price`) is assigned to a name or key containing `ils` without passing through `fx`. This is the class-closer.
- **Smoke `smoke-pr105-own-currency.ps1`:**
  - `Assert-Build` (schema unchanged, 0028).
  - `/recommendations` builds with `degraded == []`.
  - `/strategies/{id}/load-basket` `mode=replace, dry_run=true` returns 409/refusal while sleeves exist (D-e).
  - Non-changes: NAV in `/portfolio` equals the sum of rows; no card count change unrelated to trims.
- **QA:** none (no UI change), unless the L-ledger fix touches the Holdings add flow.
- **Risk:** low. It only shrinks oversized sells and fixes valuations. **Effort:** M.

#### PR 106: sleeve funding obeys the book target *(High)*

- **Goal:** "funding sells only what the plan says is overweight" holds on every path, not just Today's.
- **Changes:**
  - `strategy_service.py:780` (`_fund_sleeve`), `:891` and the final `_fits(running)` (~`:995`) in `fund_plan`, and `target_solver.py:603` (`_funding_preview`):
    resolve `book_target(...)` once and pass `book_target=` (plus `buying_class` in the solver preview).
  - Add a **post-trade drift gate** to sleeve funding: simulate legs + sells against the book target. Refuse, or relabel as a swap, when total drift rises, unless every sell is same-class.
  - Make the refusal copy say *which class is under target*.
- **Tests first:**
  - Promote `test_c1_sleeve_funding.py` (book: Grow + `bal_6040` + SOXL 10%, FI 20% vs 32% target). Assert no sell from a class at/under its book target while any class is over, and that FI weight does not fall.
  - Widen `test_every_funding_call_passes_the_book_target.py` `MODULES` to **walk all of `app/`**, so a new module can't opt out by existing.
- **Smoke:**
  - `POST /plan/sleeves/fund?dry_run=true`: no sell leg in a class whose `/mix` held ≤ target while any class is over (read-only dry run).
  - Non-change: Today card set unchanged.
- **Risk:** low-medium. Some sleeves will now say "not enough fundable money" where they used to sell bonds. That is the rule working; say so in STATUS.
- **Effort:** S-M.

### Wave B: make failures visible

#### PR 107: a job that fails says so *(High)*

- **Changes:**
  - Delete the catch-all `try/except` in:
    - `backtest_service.py:595-601`
    - `strategy_signal_service.py:449-455`
    - `push_service.py:595-608`
    - `scheduler.py:151-155` (`_warm_futures`)
    - `market_state.py:26-29`

    Let `_guarded` record the failure (the pattern PR 11/12 used).
  - Push fan-out: per-subscriber `try`, count `failed`, raise at the end if any failed.
  - Futures:
    - `futures_snapshot` keeps the previous snapshot (marked stale) when every fetch fails, and raises, so `futures_warm` records the outage (L10 minimal).
    - Boot: replace the direct `_warm_futures()` at `scheduler.py:156` with `next_run_time=now` (L11).
    - Make `_http_text` time out for real (`shutdown(wait=False, cancel_futures=True)` or a module-level executor).
  - Docs: ARCHITECTURE §7 (pooled vs NullPool engines).
- **Tests first:**
  - **One parametrized test over every job `start_scheduler` registers** (a fake BackgroundScheduler captures `add_job`), run through `_guarded` on a seeded SQLite DB with providers stubbed:
    (a) `last_ok is True` on a healthy run; (b) with a raise injected in the inner coroutine, `last_ok is False`.
    This enforces D29 for every future job too. Seeds: `probes/scratch_jobs/test_c4_swallow.py`.
  - Boot-time test: lifespan with a 10 s stubbed futures provider → first request in < 2 s.
  - The outage test inverted: the previous regime survives and the job reports failure.
  - Run under test-postgres (cross-loop class).
- **Smoke:**
  - `/push/status` → `scheduler.history` has all 11 job ids once they have run; no `last_ok:false`.
  - Non-change: digest still delivered at 07:00 (check `recent_notifications` the next morning; record in STATUS).
- **Risk:** low. Status turns red only when something is really broken. **Effort:** M.

#### PR 108: one regime, measured correctly *(Medium; needs D-a)*

- **Changes:**
  - C11: `markets_service._fetch_one` computes the change from the previous daily bar (`indicators.quote[0].close[:-1]`, last non-null) vs `regularMarketPrice`.
  - C10 (per D-a (1)):
    - Today's risk card reads the strategies' regime (`engines/regime.latest` via the cached `gfinance.regime_proxy`), cache-only on the Today path.
    - Futures are labelled as a cross-check on Markets only.
    - Add a TTL to `cached_regime`.
  - `_OBJ_RETURN` (`recommendations.py:3153`): run `investigate-issue`. If the levers still quote hand-typed returns, switch them to the measured/frontier basis (PR 98), or label them as an assumption.
- **Tests first:**
  - A recorded CL=F/ES fixture → `change_pct == -0.56` / `-0.30` (sign test).
  - proxy `risk_on` + futures `risk-off` → no "raise cash / trim" card; proxy `risk_off` → card appears and cites the proxy components.
- **Smoke:**
  - `/markets/futures` items: each `change_pct` sign equals (price − previous bar) sign.
  - `/recommendations` macro card (if any) cites the regime proxy.
- **QA:** Markets tab tiles plausible against a broker app (2 checks). **Effort:** S-M.

#### PR 109: reading Today never deletes anything *(Medium; needs D-b)*

- **Changes:**
  - Per D-b (1): `purge_orphan_rules` (`rules_service.py:304-345`) skips entry types (`buy_dip`, `price_below`, `price_above`).
  - Move purge and orphan-heal writes (`recommendations.py:2785-2842, 3027-3080`) out of `build_recommendations` into the 30-minute rule evaluator.
  - Give `build_recommendations` a `read_only=True` mode, used by warm-up, assistant, digest and push evaluation.
  - The banner reconciliation keeps working off the evaluator's state.
- **Tests first:**
  - Promote `probes/scratch_funding/test_c9_get_writes.py`: GET twice → `trading_rules` + `rule_events` row hashes unchanged (allow-list only the latch clears).
  - Entry rules on unheld tickers survive a GET.
  - The inferred stop-loss retirement: a triggered stop whose card builder raises must stay active after `warm_once()`.
- **Smoke:**
  - Count `/rules` before and after two `/recommendations` GETs: equal.
  - The banner `rule_banner.carded` still equals the visible rule cards.
- **Risk:** medium. The heals exist to prevent banner deadlocks, so the evaluator must own them before they leave the build. **Effort:** M.

#### PR 120: one goal chance on every screen *(Medium; added 2026-10-11; owner moved it to right after PR 106, end of Wave A)*

- **Found by the owner, 2026-10-11:** Today's health card said "35.3% chance of ₪82,692" while the Plan tab said
  16.3% for the same goal at the same moment.
- **Root cause (proven live):** three numbers answer one question.
  - Today's "On track" (`plan_health.py:229-247`) quotes `/plan/projection`'s `probability_meets_target`: one lognormal
    over the whole book, no rules, no rebalancing (35.3%).
  - The Plan headline is the goal solver's path simulation with the protective rules as they are (16.3%; 31.0% after the
    guard was paused). Its own rules-off figure is 30.1%, so the two engines disagree by 5 points even with the rules
    out.
  - PR 99 promised "the same figure everywhere it appears". The health score was missed, and On track is **half**
    the health score.
- **Changes:**
  - `plan_health` On track reads the goal headline (`book_expectation().headline.p_goal_pct`) when a fresh verdict exists. It
    falls back to the projection, labelled "rough estimate, rules not simulated", only when there is none.
  - The projection card keeps its chart, but its chance line quotes the same headline, with its own figure labelled as the
    chart's.
- **Tests first:** with a fresh verdict, health `on_track` equals the Plan headline. With none, the fallback is labelled.
  A guard test checks that every goal-chance key a route returns comes from one function.
- **Smoke:** `/plan-health` `on_track` equals `/plan/frontier` `headline.p_goal_pct` (±0.1).
- **QA:** Today's health card and the Plan goal card show the same chance (Pixel). **Effort:** S.

### Wave C: close the doors

#### PR 110: authorisation and honesty at the edges *(Medium)*

- **Changes:**
  - **C6:** unmount `/broker/*`, or refuse the mock outside development; ANALYST on both routes. *(Superseded if PR 112 deletes it first.)*
  - **C7b:** `whatif.py:53` honours `demo_signals` and returns `grounded`.
  - **C8:** `/ask` runs `digest_service.unsupported_numbers(out, ctx)` (numbers in the question are allowed). On failure it returns a grounded fallback with `verified:false`. Append "Not financial advice." if missing. Same for the `ai_service` summaries and research.
  - **S1:** `require_role(ANALYST)` on the 12 writing routes listed in `evidence/inv-security.md` S1; compute-only POSTs stay READ_ONLY.
  - **S3:** unsubscribe and subscribe are scoped to the caller (SUPERADMIN may match legacy rows saved under `superadmin_name`).
  - **S4:**
    - `/providers/health` and `/adversary/diagnostics` → SUPERADMIN (the smokes send `x-agent-key`).
    - `/markets/summary` → `acting_user` plus a 15-minute cache.
    - `docs_url`/`redoc_url`/`openapi_url=None` in production.
  - **S5:** delete `core/security.py`, `api_key`, and the false boot warning. Replace the warning with `if not require_auth: logger.error(...)`.
  - **L15:** `gemini_generate_grounded_ex` returns the classified error. Fall back to ungrounded only on HTTP 400.
- **Tests first:**
  - **Route-introspection guard:** every POST/PUT/PATCH/DELETE is ANALYST+ unless it is on an explicit COMPUTE_ONLY allowlist.
  - **Public-route allowlist:** any new unauthenticated route fails CI.
  - `/ask` invented-number stub → `verified:false`, disclaimer present.
  - S3 inverted (victim keeps the subscription).
  - The `whatif` empty book → no demo titles.
  - Grounded 429 → one call, billing message.
- **Smoke:**
  - Anonymous GET `/providers/health`, `/markets/summary`, `/docs` → 401/404.
  - With the key: 200.
  - Non-change: `/ask` still answers a grounded question.
- **QA:** Ask InvestWise returns an answer with the disclaimer; Markets AI summary still loads. **Effort:** M.

#### PR 111.0 + 111: logout ends the session *(needs D-f)*

- **111.0** (ships alone): migration `0029_user_sessions_valid_after`, nullable `users.sessions_valid_after`, mirrored into startup DDL. The smoke checks `schema_revision`.
- **111:**
  - `/auth/logout` sets it to now for the user; `require_role` rejects a cookie or bearer token whose `iat` is earlier (cached per user for 60 s).
  - Agent key unaffected.
  - Add a "Sign out everywhere" link.
- **Tests first:** mint a cookie, log out, replay → 401; another fresh login works; `X-Agent-Key` works.
- **Smoke:** a non-destructive smoke can't log you out, so it checks schema plus that a bad-`iat` token is refused.
- **Effort:** S + S.

### Wave D: remove dead weight *(needs D-j)*

All three stages come from `evidence/inv-security.md` §L1. None touches a UI path.

- **Do not delete:**
  - `engines/state_machine.py` and the lag/decision/risk/tax engines (used by `war_room.py`, `whatif.py`).
  - `schemas/state_machine.py`.
- **Delete functions, not files,** in the shared router files `workflows.py` and `market.py`.
- **Keep** `/adversary/diagnostics` (now SUPERADMIN).

#### PR 112, stage 1: leaf routes, Celery, broker, the no-op job
- **Delete:**
  - Routers: lag, whs, simulation, safety (+ `safety_engine`), tax/preview, risk/preview, allocation, backtest, fees, screener, jobs.
  - Celery: `worker/celery_app.py`, `worker/tasks.py`, and the `celery`, `redis` deps, plus `redis_url`.
  - Broker: `broker_sync`, `app/brokers/*`.
  - Functions: `/entities` (move `/auth/status` to `auth.py`), `/market/quote`, `/fx/rate`.
  - The `market_refresh` job + `market_state.py`.
  - The `google-genai` pin (L3).
- **Tests:** ~18 deleted, 4 rewritten:
  - the observability audit probe moves to a live write route
  - the loop-stays-free race moves to a live provider route
  - the backtest and fees endpoint tests keep their `/recommendations` halves
- **Smoke:** each deleted path → 404; `/health-check`, `/risk/alerts`, `/scenario`, `/research/events`, `/data-status`, `/whatif`, `/war-room` → 200.
- **Effort:** M.

#### PR 113, stage 2: the decision-feed cluster
- **Delete:** `decision_feed.py`, `learning.py`, `pipeline.py`, `learning_engine.py`, `xai_engine.py`, `AllocationAgent`, and the `/decision-feed/weekly` and `/tax/review` functions.
- **Same commit:** `docs/AGENTS.md` (AllocationAgent section, `pipeline.py` anchors) and `tests/test_the_agent_roster_is_what_actually_runs.py`.
- **Tests:** ~13 deleted.
- **Effort:** M.

#### PR 114.0 + 114, stage 3: schema
- **Before:** a read-only `SELECT count(*)` on each table in production; record the counts in STATUS.
- **114.0** (alone): a migration dropping `user_actions`, `decision_items`, `decision_feeds`, `broker_connections`, `tax_profiles`, then `positions.bucket_id` + `buckets`. Postgres locally is mandatory.
- **114:** remove the model classes and relationships.
- **Effort:** M.

### Wave E: settings, wording, polish

#### PR 115: decisions become settings; one minimum trade *(needs D-c, D-d)*
- **P4:** add 13 `thresholds.SPEC` keys:
  - `sleeve_drift_band_pts`
  - `ignore_mute_days`, `done_mute_days`
  - `refire_change_pct`
  - `near_rule_pct`, `near_trail_giveback_pct`, `near_cap_pts`
  - `ideas_shown`, `max_idea_cards`
  - `min_resize_pts`, `material_gap_close_pct`, `material_vol_drop_pts`
  - `concentration_cap_pct`

  Each needs a SPEC line, a `defaults()` line, and `thresholds.get` at the reader. Verify that every reader runs under `use(plan)`; the push job path must too.
- **P2 / D-c:**
  - Effective card floor = max(`min_funded_trade_ils`, pct × NAV); the sheet shows "effective ₪X".
  - `_commodity_recs` returns nothing below the minimum (no un-actable heads-up).
- **Tests:**
  - Per key: override → behaviour flips (the PR 104d pattern).
  - `test_card_floor_never_below_funded_floor`.
  - `test_no_trade_card_or_headsup_below_the_funded_minimum`.
- **Smoke:** `/plan` `thresholds_spec` has 23 keys; defaults are unchanged.
- **QA:** Card-thresholds sheet shows the new rows (Pixel).
- **Effort:** S-M.

#### PR 116: say the true thing *(needs D-g, D-h)*
- **L4:**
  - Rename `cash_floor_pct()/cash_floor_ils()` → `cash_target_*`, keeping the payload key as a deprecated alias.
  - Change the user-facing "floor" → "target" wherever the value is the soft target (Today raise-cash card, plan health, sleeve-funding refusal, war-room how-line).
  - Rename `headsup.MIN_TRADE_ILS` → `MIN_CARD_TRADE_ILS`.
- **L6:** "Cash tab" (`index.html:3052`, `frontier_service.py:2025`) → "Holdings › Set liquid cash"; the "Goal settings" empty state (`:3252`) → "Tap Edit next to Target"; "the Rules tab" → "Holdings › Rules".
- **L5:** the seven stale docstrings with the replacement text from `inv-cleanup.md`, plus the two test docstrings; delete the dead `pyproject.toml` warning filter.
- **L9:** rename the two `money()` helpers (`display_money` / `db_money`).
- **L14 / D-g:** commodity buys are limited to the core basket's commodity tickers when a core is chosen (setting, default on).
- **L3 / D-h:** delete or pin Sentry.
- **W0-5** if delegated: remove `investwise.db`, `frontend/`; CORS default empty.
- **Tests:**
  - `test_no_copy_names_a_tab_that_does_not_exist`.
  - A wording guard (no "floor" beside a value equal to the soft target).
  - The commodity-in-core test.
  - `sw.js` bump.
- **QA:** Pixel: the changed strings (3 checks). **Effort:** M.

#### PR 121: the protection retune searches properly *(added 2026-10-11)*

- **Found 2026-10-11:** the goal card says "this is already the best chance that keeps halving at or under your limit". Live,
  that rested on **two** candidates: guard 15% vs 20%, with the sleeve stops already paused (`_tune_protection`,
  `frontier_service.py:934-975`). It never tries:
  - another guard level (17.5%, 25%), sell fraction or re-arm;
  - the take-profits. The card says they "measured neutral", but nothing in the search re-measures them; QQQ +6% / sells 25% fires about twice a year.
- **Changes:**
  - Widen the grid: guard level × sell fraction × re-arm, plus each sleeve's take-profit on/off. Keep it in budget with
    the `light` run, and cap the candidate count.
  - Every searched axis becomes a setting next to `guard_levels` (the PR 115 pattern).
  - The "already the best" sentence names how many setups were tried. "Take-profits measured neutral" is stated only when
    the search measured it.
  - The rules card can then propose a take-profit change too, through the same Accept and re-arm path.
- **Tests first:**
  - A book where a take-profit costs the goal → the retune proposes pausing it.
  - A book where guard 17.5% beats 15% under the limit → that is proposed.
  - The sentence never claims a setup it didn't try.
- **Smoke:** `/plan/frontier` `rules.tuning.candidates` count ≥ the grid size; defaults unchanged.
- **QA:** the Plan goal card and the Edit-rules preview (Pixel). **Effort:** M.
- **Why Wave E:** it is a recommendation-quality improvement, not a wrong trade or a hidden failure, and it builds on PR 115's
  settings work.

#### PR 117: keyboard and focus *(needs D-i)*
- **L12:**
  - One `.seg`.
  - `:root` gets `--warn`; the undefined `--muted`/`--fg` are fixed (this fixes the bright "Ignored" labels).
  - `:focus-visible` ring.
  - 17 click targets become buttons, or get `role="button" tabindex="0"`.
  - One delegated key handler (Enter/Space; Escape closes the top sheet or modal).
  - Sheet focus save/restore/trap.
- **Test:** a static guard: every `var(--x)` is defined or has a fallback; every `span|div[onclick]` has a role (backdrops allow-listed); `.seg{` appears once.
- **QA:** Pixel + desktop keyboard (4 checks). **Effort:** M.

### Wave F: structure (no behaviour change)

#### PR 118a / 118b: thin routes (L7)
- Move the business logic out of `routes/plan.py` (into `plan_view`, `projection_service`, `book_target.mix_report`, `sleeve_service.sleeves_view`) and out of `routes/intake.py` (into `portfolio_view.holdings`, `intake_service.add_by_amount`, `ledger_service._last_buy`).
- Services stop importing `app.api.routes.*`; three do today.
- **Guard:** `test_services_never_import_routes`.
- **Effort:** M + M.

### Not planned (parked, with reason)

| Item | Why parked |
|---|---|
| L8 float → Decimal | 1,051 `float(` vs 45 `Decimal(`; no observed bug comes from binary rounding (every money bug was FX or scale). All-or-nothing, L effort. Keep the write-boundary rule |
| S6 inbound rate limiting | Single user. PR 110 removes the anonymous LLM spend; optional single-flight on frontier refresh/preview later |
| R1 Railway Config-as-Code migration | **Not parked: dated.** Do it as PR 119 any time before **2026-11-15** (a two-week margin before 2026-12-01): `railway config migrate`, review the generated `.railway/railway.ts` keeps `preDeployCommand`, the healthcheck and restart policy, and update `tests/test_the_deploy_runs_the_migrations.py` + `test_a_broken_deploy_never_takes_traffic.py`, which pin `railway.json` |

## 5. Sequence and calendar

```
Wave 0 (owner, parallel) ─ W0-1 backups · W0-2 redis volume · W0-3 draft · W0-4 plans · W0-6 tokens
PR 105 ─ PR 106 ─ PR 120                 money safety + one goal number (needs D-e)
PR 107 ─ PR 108 ─ PR 109                 truthful instruments  (needs D-a, D-b)
PR 110 ─ PR 111.0 ─ PR 111               doors                 (needs D-f)
PR 112 ─ PR 113 ─ PR 114.0 ─ PR 114      legacy removal        (needs D-j)
PR 115 ─ PR 121 ─ PR 116 ─ PR 117       settings + wording    (needs D-c, D-d, D-g, D-h, D-i)
PR 118a ─ PR 118b                        structure
PR 119 (before 2026-11-15)               Railway config migration
```

- **PR 105 and 106 come first and go alone.** They touch money. Ship each, run its smoke, and wait for one quiet nightly cycle (04:10 solver, 07:00 digest) before the next.
- **Waves B and C may interleave.** They touch different files: jobs/markets/rules vs auth/routes.
- **PR 112–114 go after 110**, so the broker hazard is closed at least once before the code disappears.
- Rough size: 17 PRs (PR 120, 121 added 2026-10-11), about 13–18 Claude session-days. The critical pair (105, 106) is about 2 days.

## 6. Definition of done for the plan

- Every row in §1.2 is closed in BACKLOG.md with its PR, or explicitly parked here.
- Six new class-closing guards are in CI and failing on a reintroduced bug:
  - FX valuation
  - funding modules
  - every job runs
  - write routes are ANALYST+
  - public-route allowlist
  - services never import routes
- STATUS has one entry per PR with a smoke result. HISTORY gets an "Era 14: hardening" line, and DECISIONS gets the D-a…D-j outcomes.
