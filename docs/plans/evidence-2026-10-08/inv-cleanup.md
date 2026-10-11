# Cleanup investigation (L2-L15, P2, P4)

Repo: worktree docs-overhaul @ 93732f9. All line numbers from that tree.

## L2. investwise.db, frontend/, FRONTEND_ORIGIN

- SYMPTOM: `investwise.db` (155,648 B) and `frontend/package-lock.json` (62 KB) are tracked; nothing seems to use them.
- EVIDENCE:
  - `git log -- investwise.db` -> last touched `3a77c4e 2026-06-08 Phase 2: Risk Engine`. Contents (read-only sqlite open): 11 tables `users, entities, decision_feeds, accounts, buckets, tax_profiles, decision_items, whs_snapshots, positions, user_actions, transactions`, **no `alembic_version` table**. It is a Phase-2 schema snapshot, ~28 migrations stale.
  - grep `investwise.db` over app/ tests/ scripts/ Dockerfile docker-compose.yml .github railway.json: **0 hits in app/tests/CI/Dockerfile**. Only hits are `scripts/archive/deploy/*.ps1` and `scripts/archive/root/ship-flap-fix.ps1`, all of which are *workarounds for it being tracked* (`git checkout -- investwise.db` after local pytest dirtied it). `docker-compose.yml:21` matches only on the Postgres DB *name* `investwise`.
  - DB default: `app/core/config.py:56` `database_url = "postgresql+asyncpg://...localhost:5432/investwise"`; tests use `tests/conftest.py:3` `sqlite+aiosqlite:////tmp/iw_test_app.db`. No path resolves to `./investwise.db`.
  - `.gitignore:3` already has `*.db` -> the file is tracked only because it predates the rule.
  - frontend/: only `package-lock.json`. `git log -- frontend` -> `6edebc5 chore: untrack frontend/node_modules`. No package.json, no build step. References: `app/services/recommendations.py:4871` and `tests/test_buy_ideas_reach_today.py:6` mention `frontend/` only inside docstrings as a grep path (`grep buy_ideas app/static_app/ frontend/`); `tests/test_no_secret_is_committed.py:74` `SKIP_DIRS = ("frontend/node_modules/", ...)` (harmless after deletion). Dockerfile/CI: 0 hits.
  - CORS: `app/main.py:245-251` `CORSMiddleware(allow_origins=[settings.frontend_origin], allow_credentials=True, ...)`; `config.py:61` default `http://localhost:5173`; `docker-compose.yml:22` sets the same. The PWA is served by the same FastAPI app and every call is same-origin relative (`index.html:640 fetch("/api/v1/ask"...)`, `:646`, `:649`). Same-origin requests never consult CORS.
- ROOT CAUSE: leftovers from the Vite/React era (5173 is Vite's dev port) and Phase-2 local dev DB.
- WHY SILENT: nothing reads them; only cost is noise (archived ship scripts had to special-case the db file).
- BLAST RADIUS of deleting: none at runtime. Changing/removing FRONTEND_ORIGIN: no effect on the PWA (same origin). Only a *cross-origin* browser client (none exists) would be affected. With credentials=True and a single localhost origin, the current setting is actually a tiny attack-surface item, not protection.
- CONFIDENCE: PROVEN.
- FIX: `git rm investwise.db frontend/package-lock.json`; drop the `frontend/` path from the two docstrings (optional); either remove CORSMiddleware entirely or keep it with default `""` -> empty allow list (and drop `FRONTEND_ORIGIN` from docker-compose). Leave `SKIP_DIRS` alone (harmless) or trim.
- EFFORT S. RISK very low. TEST: none needed for the files; for CORS optionally a test that `OPTIONS /api/v1/health` with `Origin: http://evil` gets no `access-control-allow-origin`.

## L3. requirements: google-genai / sentry-sdk

- EVIDENCE:
  - `requirements.txt:18 google-genai==2.8.0`. grep `^(import|from) google` / `genai` in app tests scripts: **0 imports**. Gemini is called by raw HTTP: `app/services/llm.py:65,109` and `app/agents/adversary.py:93` build `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` and post with httpx. `adversary.py:73` docstring mentions the SDK only to explain why it is NOT used.
  - `sentry-sdk`: `app/main.py:29-35` `if settings.sentry_dsn: try: import sentry_sdk ... except Exception: logger.warning("SENTRY_DSN set but sentry-sdk not installed")`. Not in requirements.txt or requirements-dev.txt. So setting SENTRY_DSN in Railway would silently (one warning) do nothing.
  - Also noted: `redis==5.2.1` has 0 direct imports but is needed by Celery's redis broker (`app/worker/celery_app.py`, `REDIS_URL`) -> keep. `python-multipart` 0 imports but FastAPI forms need it -> keep.
  - `tests/test_the_pins_are_installable_and_installed.py`: parametrizes `test_the_installed_version_is_the_pinned_version` over `_pins()` parsed from requirements.txt (line ~19 of the test body). Removing a pin simply removes a parameter; adding `sentry-sdk==X` forces it to be installed in CI (no optional-package escape hatch, by design, comment ~line 52). `test_the_pin_list_is_not_empty` unaffected.
- VERDICT: PROVEN (google-genai dead pin; sentry unpinned => feature unreachable).
- FIX: delete `google-genai==2.8.0` (removes ~a dozen transitive deps from the image). For sentry, owner decision: either (a) delete the sentry block in main.py + `sentry_dsn` in config (nothing uses it today), or (b) pin `sentry-sdk==<ver>` and make the except clause `ImportError` only. Recommend (a) unless Eran wants Sentry; (b) also needs a cryptography/urllib3 compatibility check against the pinned set.
- EFFORT S. RISK low (verify `pip install -r requirements.txt` in a clean venv still resolves; CI does this). TEST: existing pins test covers it; for (b) add `test_sentry_init_runs_when_dsn_set` with a dummy DSN.

## L5. Stale comments / docstrings (each verified)

| # | Location | Current text | Truth (evidence) | Replacement |
|---|---|---|---|---|
| a | `app/models/tables.py:1` | `"""All 11 persisted tables (Section 4 of the spec).` | `grep -c __tablename__ app/models/tables.py` = **29** | `"""Every persisted table (29 at migration 0028; see docs/DATA_MODEL.md).` -- better: drop the count, `"""All persisted tables.` so it cannot rot again. |
| b | `tables.py:619-622` (NavSnapshot) | "Past NAV cannot be recovered: the `transactions` table has never been written (grep for `Transaction(` outside the models returns nothing), so there is no trade ledger to reconstruct from. History can only start." | `Transaction(` IS written: `app/services/ledger_service.py:54` (`record_leg`), `ledger_repair.py:368`, `:750`. | "Past NAV before this table cannot be recovered: until PR <ledger> the `transactions` ledger was never written, so there is no trade record before that date to reconstruct from. Since then `ledger_service.record_leg` writes every book change, but NAV history still only starts with this table." Same fix in `app/services/nav_history.py:7-10` (also says `whs_snapshots` "is never written" -- false, `nav_history.py:619 session.add(WhsSnapshot(`). `ledger_service.py:11` quotes the old sentence explicitly as history -> leave. |
| c | `app/services/buyback_hold.py:27` (module docstring last line) | "Stored in `kv_settings` as JSON, one key per user, so no migration." | Same file `:42-49` (PR 83): holds are now `trading_rules` rows of type `buy_back`; kv blob is only migrated on first read (`_migrate_kv`, `:62`). | "Stored as `trading_rules` rows of type `buy_back` (PR 83; level = buy-back price), so the Rules tab lists, edits and ends them. The legacy `kv_settings` blob (`buyback_holds:<email>`) is migrated into rows on first read." |
| d | `tables.py:588-592` (PlanSleeve docstring) and `:608-610` (column comment) | "``is_core`` ships unused, reserved for the other model ..." / "RESERVED, always False in C1 ... Nothing reads this yet, and nothing should start reading it until the explicit-core model is actually chosen." | Read and written: `sleeve_service.py:396-454` (C6: one `is_core=True` row names the core strategy; `get_core`, `core_strategy_id`), `sleeve_service.py:82` filters it out of sleeves, `card_scope.py:171` skips it. | docstring: "``is_core=True`` marks the single core row (C6): it names the static strategy family that manages the remainder; its ``sleeve_pct`` is always 0 because the core's size is the computed remainder." Column comment: "# True on the one core row per subject (sleeve_service.get_core). Sleeve readers must filter it out." |
| e | `app/services/market_state.py:1-7` | "...With the built-in deterministic provider the values are stable; when a live vendor is configured this re-pulls fresh quotes/events each hour. We track when the last refresh ran so the UI can show 'updated hourly / last refreshed N min ago'." | `registry.economic_provider()` (`registry.py:63-75`) has no alternative and returns **no events** since PR 58; `refresh_market_data` discards `ResearchAgent().scan()`'s result; `routes/market.py:52-57` says `last_refreshed` was **removed from the UI** in PR 58. No quotes are pulled here (price refresh is `pricing_service`). Only caller: `worker/scheduler.py:101-109`. | "Hourly `market_refresh` job: re-runs ResearchAgent.scan() and stamps a counter for job_state. Since PR 58 there is no economic-data provider (scan returns nothing) and the UI no longer shows this timestamp; this is a legacy job (BACKLOG L1) kept only as a scheduler heartbeat." Better fix: delete the job with L1 (also removes the synchronous startup scan, L11). |
| f | `app/engines/frontier.py:8-12` | `inputs ...(PR 89) / propose THIS (PR 88) / stabilise resampled weights, a no-trade band (PR 90) / validate whole-book mean-CVaR on real history (PR 91) / apply book_target, behind Accept (PR 92)` | PR 90 = `frontier_service.py` card ("PR 90 - the frontier acts", resample `DRAWS=100`, `BAND_PCT=(10,90)`) + migration 0026; PR 91 = card on user's dials; PR 92 = goal is the objective (goal_probability); **mean-CVaR validation was never built** (`cvar` appears only in this docstring and in `portfolio_risk` 1-day CVaR); historical validation became PR 93 (filtered historical paths). Accept executes a sleeve resize in `frontier_service`, not via `book_target`. | `inputs  frontier_inputs: ILS returns, shrunk cov, Black-Litterman (PR 89)` / `propose THIS (PR 88)` / `stabilise frontier_service: block-bootstrap resample + 10-90% band (PR 90/91)` / `steer goal_probability vs the plan's ROI target (PR 92); fat-tail check on filtered history (PR 93)` / `apply frontier_resize card, Accept resizes sleeves (PR 91)`. Also line 3 "Until this module the plan's targets were constants" -> prefix "Before PR 88". |
| g | `pyproject.toml:16-26` | "APScheduler's price-refresh job fires during TestClient startup and calls asyncio.run() on a thread that already has a running loop..." + `"ignore:coroutine '_refresh_all' was never awaited:RuntimeWarning"` | `tests/conftest.py:41` sets `ENABLE_SCHEDULER=false`, and `scheduler.py` (comment ~:115-140) now primes price refresh through `_guarded` in a worker thread, so the described path no longer exists. Measured below (suite run with `-o filterwarnings=`). | see result line below |

Also listed in BACKLOG L5 but not re-verified here: `test_startup_ddl_covers_migrations.py` docstring, `conftest.py:61`.

- VERDICT: a-f PROVEN stale. EFFORT S (comment-only PR, ~8 hunks). RISK nil (ruff only). TEST: none; optionally a tiny test asserting the tables.py count matches if the count is kept (recommend dropping the number instead).
- (g) result: see "pyproject filter" line appended after the suite run.

## L6. Stale UI copy

Tabs today (`index.html:284-291`, bottom nav `:593-600`): Today, Holdings, Plan, Markets, Agents, Explore. Holdings has segments Positions / Rules (`:370-371`). Cash + standing orders live in the **Liquid cash modal** (`openCash()`, `:1926`; modal `:530-548` "Monthly standing order"), opened from Holdings > Positions "💵 Set liquid cash" (`:378`), the ✎ on the cash row (`:1893`), and "+ Add a monthly standing order" (`:1877`).

| # | Where | Current | Verdict | Replacement |
|---|---|---|---|---|
| a | `index.html:3052` (Edit-goal sheet) | `${act.length} standing orders: change them on the Cash tab.` | PROVEN stale: no Cash tab. | `${act.length} standing orders: <a href="#" onclick="closeSheet();openCash();return false">change them in Liquid cash</a>.` (or plain text "under Holdings › Set liquid cash"). |
| b | `index.html:3252` (`goalSummary` empty state) | `No goal yet. Set a target return and a deadline under Goal settings below.` | PROVEN stale: form removed in PR 104c (`:3793` comment says so). The row with the Edit button is `#goalRows`, rendered directly below `#goalSum` (`:453-454`). | `No goal yet. Tap <b>Edit</b> next to Target below to set a target return and a deadline.` (or a button calling `openGoalSheet()`). |
| c | `index.html:1492` commodity input | `placeholder="$ amount"` | **NOT an issue.** Server: `routes/intake.py:347` `amount: float = Field(gt=0)  # budget in the instrument's price currency`; `:383` `qty = req.amount / price` where price is the live quote in its own currency via `to_major_units`; front-end posts `market:"NYSE"` (`:1501`) and confirms `@ $${r.price} (~$${value})` (`:1505`). All commodity-screen tickers are US ETFs -> USD. The copy is correct. Optional polish: "US$ amount" to contrast with ₪ elsewhere. |
| d | **server copy** `app/services/frontier_service.py:2025` (goal-deposit card "how") | `"Change or pause it any time on the Cash tab"` | PROVEN stale, same cause; shows on a Today card. | `"Change or pause it any time under Holdings › Set liquid cash"` |
| e | `recommendations.py:2659`, `:4334`, `frontier_service.py:2306` | "the Rules tab ..." | Minor: Rules is a segment of Holdings, not a tab. | "Holdings › Rules". Low priority. |

Searched and clean: "Advanced", "/dashboard", "Settings tab", "Goal settings" (only the PR 104c comment at `:3793`), "Cash tab" (only a and d).

BONUS (out of scope, flag for owner): `POST /portfolio/add` (`intake.py:352-398`) creates/tops-up a position via `upsert_positions` but calls neither `ledger.record_leg` nor `record_contribution` and does not debit cash. Per `ledger_service.py:16-19` ("If a write path can move a position and does not call `record_leg`, that path is a hole in the ledger"), this is a ledger hole and the added value would read as performance in NAV history. INFERRED (not reproduced); worth its own investigate-issue.

- EFFORT S (3 string edits + 1 server string). RISK nil. TEST: a grep-style test `test_no_copy_names_a_tab_that_does_not_exist` scanning index.html + app/services for "Cash tab" / "Goal settings" / "Settings tab" (cheap, prevents recurrence). Card copy change (d): run `tests/test_card_claims.py` + any test asserting the "how" text (grep `Cash tab` in tests: 0 hits).

## L12. Frontend debt (index.html, 4,094 lines). Counts from scratch_cleanup/a11y_count.py

- `.seg` defined twice: `:67-69` (`.seg{display:flex;gap:4px;background:var(--soft);...padding:4px}`, `button{flex:1;...padding:9px}`, `.on{background:var(--card)}`) and `:128-130` (`.seg{display:inline-flex;...overflow:hidden}`, `button{background:var(--card2);padding:6px 15px;font-size:12px}`, `.on{background:var(--blue);color:#fff}`). Same specificity, so the later block wins property by property and the result is a **hybrid of both** (gap + padding + soft bg + flex:1 from the first; inline-flex, overflow, blue "on" from the second; `#planSeg` patches it back with inline `display:flex`). 5 users: `#holdSeg :369`, `#planSeg :446`, `:487` (quarterly/yearly), `#h_mode :554`, `#h_rng :557`. PROVEN.
- CSS vars: `:root` (`:21-22`) defines bg, card, card2, ink, mut, line, blue, green, amber, red, soft. Used but undefined: `--warn` x7 (all with fallback `#e5b857`, so they render, but bypass the theme and differ from `--amber:#fbbf24`); `--fg` x1 (`:1168`, fallback `#e5e7eb`, renders); `--muted` x2 (`:1550-1551`, **no fallback**) -> `color:var(--muted)` at `:1570` is invalid at computed-value time, so "Ignored" / "Marked done" outcomes render in inherited bright ink instead of muted gray. PROVEN (minor visible bug).
- Clickable non-focusable elements: `onclick` on `<button>` 131, `<a>` 5 (focusable), **`<span>` 14, `<div>` 8**. Of the 22, 5 are overlay backdrops (`#sheetBg :471`, `.ov` at `:529,552,565,588`; correct to leave). The 17 real targets: 4 modal close `<span class="x">&times;</span>` (`:530,553,566,589`), 8 `span.link` (`:325,523,1079,1084,1092,1151,1173,2764`), chips (`:1013,1021`, rendered N times), `#rulesAlert :321`, `#rating :324`, agent line `:3818`. `tabindex`: 0 occurrences; `role="button"`: 0; `:focus` rules: **0**; plus `outline:0` on `.lvl input :168`.
- Sheets/modals: `#sheet` has `role="dialog" aria-modal="true"` (`:473`) but `openSheet` (`:2997`) does not move focus and `closeSheet` (`:2999`) does not restore it; no Escape handling anywhere (the only key handler in the file is Enter on `#askq :358`); the 4 `.ov` modals have no role/aria. No focus trap.
- Also counted: `alert(` 20, `confirm(` 8, `prompt(` 2, inline `style="` 485.

Minimal fix set (one PR):
1. Delete one `.seg` block (`:128-130` or `:67-69`) after the owner picks the look; move `#planSeg`'s inline `display:flex` into the class.
2. `:root`: add `--warn:var(--amber)` (or `#e5b857` if that hue is wanted); replace `var(--fg,...)` with `var(--ink)` and `var(--muted)` with `var(--mut)`.
3. CSS `:focus-visible{outline:2px solid var(--blue);outline-offset:2px}`; replace `outline:0` on `.lvl input` with a `.lvl:focus-within` ring.
4. Convert the 4 `span.x` to `<button class="x" aria-label="Close">` and the `span.link`s to `<button class="link">` with a reset (`background:none;border:0;padding:0;color:inherit;font:inherit;cursor:pointer`). Chips / `#rating` / `#rulesAlert` / agent line: `role="button" tabindex="0"` plus one delegated keydown handler (Enter/Space on `[role=button]` -> `click()`).
5. Same delegated handler: Escape closes the topmost layer (`#sheetBg` visible -> `closeSheet()`, else the open `.ov` -> its close fn). `openSheet` saves `document.activeElement` and focuses the first input/button in `#sheet`; `closeSheet` restores it; Tab wraps first<->last focusable while the sheet is open. Add `role="dialog" aria-modal="true" aria-labelledby` to the 4 `.ov .modal`.
- Leave native alert/confirm and inline styles for a later pass.
- VERDICT PROVEN. EFFORT M (items 1-2 alone: S). RISK low-moderate: the `.seg` merge changes the look of 5 controls (check on the Pixel). TEST: a static test over index.html: every `var(--x)` is defined in `:root` or has a fallback; every `<span|div ... onclick>` has `role="button"` (allowlist the 5 backdrops); `.seg{` appears once. Plus a QA line for Escape/Tab on desktop.

## L13. candidate_search.py + test (untracked, main checkout)

- WHAT IT IS: `C:\dev\Investwise-pro\app\services\candidate_search.py` (196 lines, mtime 2026-08-25 10:58), "T6b - discover how many sleeves are worth running, and which". Pure function `grow(measure, *, candidates, start, min_gain_pct=0.25, max_sleeves=6, max_evaluations=240)`: greedy forward selection over strategy ids, memoised, stop reasons `sleeve_cap / evaluation_budget / candidates_exhausted / nothing_admissible / no_further_gain`, `auto_apply_forbidden: True`; plus `forbidden_tickers(result, allowed_ids)`. **No imports beyond `logging`**, so it cannot fail against HEAD.
- RUN: copied both to `scratch_cleanup/cs/` (test import rewritten to `import candidate_search as cs`): `16 passed in 0.08s`; `ruff check`: All checks passed.
- CALLERS: `grep candidate_search` over app/ tests/ scripts/ (non-archive) docs/: **0**. Only mentions: `scripts/archive/deploy/ship-pr51-the-working-tree.ps1:18,111-114` ("Deliberately left untracked ... an open land-or-discard decision and it is Eran's") and `ship-pr67-1-...ps1:242`.
- WHY IT EXISTS: it is the **superseded first draft** of the same feature that landed the same day as tracked code: `app/services/sleeve_count.py` (`git log --diff-filter=A`: `2b83595 2026-08-25 T6b second half: discover the sleeve count, and say how the search ended`), version `t6b2` vs the draft's `t6b`, same plan quote, same greedy method and 0.25 noise floor (`NOISE_FLOOR_PCT = 0.25`), richer stop reasons (`BELOW_NOISE_FLOOR, NO_ADMISSIBLE_ADDITION, BASELINE_BREACHES_CEILING, MAX_SLEEVES, RUNTIME_BACKSTOP, NOTHING_MEASURABLE`), wired to `GET /plan/target/sleeve-count` via `routes/plan.py:851` -> `target_solver.solve_sleeve_count_for`, tested by `tests/test_how_many_sleeves.py`.
- VERDICT: PROVEN dead duplicate. Only thing it has that sleeve_count lacks is `forbidden_tickers` (the "ids only, never a security" firewall check).
- RECOMMENDATION: **delete both files** (main checkout: `Remove-Item app\services\candidate_search.py, tests\test_candidate_search.py`; nothing to commit since untracked). If the owner wants the firewall, port the 10-line `forbidden_tickers` + its 2 tests into `sleeve_count.py` / `test_how_many_sleeves.py` instead of landing a second implementation.
- EFFORT S. RISK nil (untracked, zero callers). TEST: none for delete; port case = the 2 existing firewall tests.

## L14. CANE (commodity screen) vs IAU/DBC (core)

- WHERE EACH COMES FROM:
  - CANE: `app/services/commodities.py:33` `{"ticker": "CANE", "name": "Sugar", "category": "Agriculture", "expense_ratio_pct": 1.00}` -> `universe.commodity_candidates()` (`universe.py:147-154`, the whole catalog) -> `OpportunityAgent.screen_commodities` (`screener_agent.py:152-192`) ranks every live candidate "2:1 on trend against cost" with **no input about the plan or core**.
  - IAU/DBC: core baskets in `strategies.py:50` (All-Weather `VTI .35, BND .40, IAU .15, DBC .10`), `:56`, `:88`; frontier model's Commodities proxy `frontier_inputs.py:78` `{"IAU": 0.6, "DBC": 0.4}, "assumption_ticker": "IAU"`; `frontier_service.py:1104` default `"Commodities": "IAU"`; `recommendations.py:34` `CLASS_ETF["Commodities"] = "DBC"`.
- CONSUMERS of the screen: `_commodity_recs` (`recommendations.py:5045`, called at `:2136`) picks `picks[0]` of `screen_commodities(top_n=3)` (`:5072`) and builds "Buy X of <pick>" funded via `propose_funded_buy` (`:5093`); `redeploy_research.live_scores` (`:108`) feeds the idle-cash redeploy card; `:5186` buy-ideas list. None reads the core basket (grep of `_commodity_recs` body for core/basket: 0 hits).
- PRIOR OBSERVATION (live): `docs/archive/status/STATUS-HISTORY-to-PR87.md:6596-6599` and `:6855-6870` (2026-08-25): "with All-Weather chosen (IAU 15% + DBC 10%) the redeploy card offered **CANE** -- sugar ... the screener has no idea a core was chosen". Tracked as OPEN/"cosmetic" in v7 plan (`EXECUTION_PLAN_2026-09-06_v7.md:78`). No code change since: `commodities.py:33` unchanged, `screen_commodities` still plan-blind.
- WHAT IT PRODUCES: (1) a Today/redeploy card buying the momentum leader (CANE/USO/...) to fill a Commodities gap the core defines as gold + broad basket; (2) the goal/frontier odds model the Commodities slice as IAU/DBC (`frontier_inputs.py:78`), so the card's purchase is modelled as an instrument it is not (sugar vol and drift != gold); (3) possible two cards naming different commodity tickers for the same gap (core-basket card `core_to_basket` `:1656-1790` buys IAU/DBC; `_commodity_recs` buys the screen pick). The shared funding `_ledger` stops double *spending*, not the contradictory naming.
- VERDICT: design inconsistency PROVEN from code + the archived live observation; a fresh live repro not done (no production calls). Severity P3 (mis-labelled exposure, not a money-loss bug). CLAUDE.md "never invent numbers" is not violated; "may not claim a portfolio change it does not produce" is borderline: the card's projected effect on goal odds uses the IAU/DBC proxy.
- FIX (mirror PR 10's bond mandate): in `_commodity_recs` and the redeploy Commodities lane, if `core_weights(core_id)` names Commodities tickers, restrict candidates to those (rank them by the same screen; keep the trend-dead filter); fall back to the full screen only when no core is chosen. Optionally make it a setting ("commodity buys: core basket only / best trend") per D64. Leave CANE in the catalog (the Holdings commodity picker legitimately offers it).
- EFFORT S-M. RISK low (narrows a candidate list). TEST: `test_commodity_card_buys_the_core_basket_when_a_core_is_chosen` (All-Weather core, commodities underweight, monkeypatched screen ranking CANE first -> card ticker in {IAU, DBC}); and the no-core case still picks the screen leader.

## L15. Gemini error surfacing

- DONE ALREADY: `app/services/llm.py:23-42` `_classify` maps no-key / 429-quota-credit / 401-403 / 404 / timeout / other to six distinct plain-language messages (429 -> "AI credits are exhausted — top up billing in Google AI Studio."); `gemini_generate_ex` (`:45-60`) returns `(text, err)`. Callers that pass `err` through: `ai_service.py` portfolio/holding/macro summaries (`:86`, `:113`, `:130`: `"summary": out or err or ..., "error": ...`), `ask_service.py:37-49` (`"answer": err or ...`). The UI renders `summary` / `answer` as text, so these show the real reason. `tests/test_llm_error_surfacing.py` (4 tests) covers `_classify` + `gemini_generate_ex`.
- LEFT TO DO (PROVEN):
  1. **Per-holding deep research** (`ai_service.py:159-163`) uses `gemini_generate_grounded` (`llm.py:97-151`), which returns `None` on any failure (`:150 except Exception: return None`); the route then says "Research is temporarily unavailable. Please try again." and the UI shows it (`index.html:713-719`). Scratch test `scratch_cleanup/test_l15_grounded_429.py` (monkeypatched `urlopen` raising HTTPError 429 "prepayment credits are depleted"): `gemini_generate_grounded` -> `None`, and it made **2 calls** (`:141-144`: any HTTPError, including 429/403, is treated as "grounding unsupported" and retried ungrounded). 2 passed. This is exactly the original "billing looks like an outage" symptom, on the one AI surface that was not converted.
  2. **Digest** (`digest_service.py:220`) uses the reason-dropping `gemini_generate`; on failure it returns the deterministic fallback with `llm: False` and no reason. Acceptable product-wise (fallback is correct text) but the reader cannot tell "AI off/billing" from "AI chose fallback". Low priority.
  3. **Adversary narrative** (`adversary.py:203-206`) swallows into `None`; reason only visible via `/api/v1/adversary/diagnostics`. Low priority (narrative is optional).
- FIX: add `gemini_generate_grounded_ex -> (dict|None, err|None)` using `_classify`; only fall back to ungrounded on HTTP 400 (tool unsupported), not on 401/403/404/429; in `ai_service` research route return `"research": err or "...", "error": err`. Optionally add `"llm_error"` to the digest payload and a muted line "AI summary off: <reason>" in the digest card.
- EFFORT S. RISK low. TEST: extend `tests/test_llm_error_surfacing.py`: `test_grounded_research_429_says_billing` (assert message contains "credit"/"billing" and `urlopen` called once), `test_grounded_falls_back_only_when_the_tool_is_rejected` (HTTP 400 -> 2 calls).

## L7. Fat routes (routes/plan.py 1,123 lines; routes/intake.py 944 lines)

**Strongest evidence it matters: services import from routes (layering inversion), PROVEN:**
- `app/services/recommendations.py`: `from app.api.routes.plan import _measured, _portfolio_stats`
- `app/services/plan_health.py`: `from app.api.routes.plan import goal_projection, mix_check` (calls route handlers as functions)
- `app/services/context_service.py`: `from app.api.routes.plan import goal_projection`
- tests also reach in: `test_the_numbers_come_from_measurement.py`, `test_the_book_volatility_is_not_rescaled_by_exposure.py` (`_portfolio_stats`, `_plan_dict`), `test_one_basis.py` (`_frontier_basis_text`), `test_the_projection_sees_your_deposits.py`, `test_intake.py` (`_row_to_position`), `test_performance_range.py` (`PERF_RANGES`).

Business-logic blocks to move (line ranges at 93732f9):

| File:lines | Block | What it does | Move to |
|---|---|---|---|
| plan.py:39-72 | `_measured` | loads measured book assumptions | `services/plan_view.py` (new) or `measured_assumptions` |
| plan.py:73-80 | `_frontier_basis_text` | copy for frontier basis | `plan_view` |
| plan.py:81-152 | `_portfolio_stats` | FX-normalised NAV + forward return/vol of the book (used by Today) | `plan_view` / `portfolio_analytics` |
| plan.py:153-194 | `_goal` | goal target/progress math | `goal_service` (or plan_view) |
| plan.py:195-269 | `_plan_dict` | assembles the plan payload incl. goal | `plan_view` |
| plan.py:304-392 | `get_my_sleeves` body | sleeve table + core remainder assembly | `sleeve_service.sleeves_view` |
| plan.py:466-559 | `core_options` body | per-family mix, est vs measured drawdown | `core_service.options` / `strategy_service` |
| plan.py:914-1096 | `goal_projection` (~180 lines) | three Monte Carlo runs, contribution rate, excess comparison | `projection_service.project(session,user,contribution_per_year)` -- this is the one services already call |
| plan.py:1097-1123 | `mix_check` | book_target + AllocationEngine compare | `book_target.mix_report` |
| intake.py:42-67 | `_row_to_position`, `_persist` | CSV row parsing + persistence | `intake_service` |
| intake.py:106-343 (~240 lines) | `get_portfolio` | per-holding FX valuation (`fx_rate_with_status`), classification, gains vs basis/vs invested, last priced buy from the ledger, headline numbers | `portfolio_view.holdings(session,user)` -- biggest single win |
| intake.py:352-398 | `add_holding` | live quote, agorot->ILS (`to_major_units`), sizing, upsert (and the missing ledger leg, see L6 bonus) | `intake_service.add_by_amount` |
| intake.py:640-656 | `_last_buy` | ledger lookup | `ledger_service` |
| intake.py:772-781 | `_orders_payload` | standing-order summary | `standing_orders` service |

- VERDICT PROVEN (debt, not a bug). FIX: pure moves, route keeps validation + `Depends` + response shaping; services stop importing `app.api.routes.*`. Do it as 2 PRs (plan.py, intake.py), no behaviour change. EFFORT M-L. RISK moderate only through import cycles / monkeypatch targets in tests (several tests monkeypatch `plan_routes.*`; update their targets). TEST: an import-lint test `test_services_never_import_routes` (AST scan of app/services + app/engines for `app.api`), which locks the result in.

## L8. float vs Decimal in money paths (counts now)

`grep -o '\bfloat(' / '\bDecimal('`:

| Scope | `float(` | `Decimal(` |
|---|---|---|
| app/services | 889 | 25 |
| app/engines | 107 | 1 |
| all of app/ | 1,051 | 45 |

(BACKLOG said 553 vs 38 at 09-06; the float count roughly doubled.) Storage is Decimal (`MONEY = Numeric(18,4)`, `tables.py:30`) and writers quantise via `tables.money` (ROUND_HALF_EVEN, matches Postgres); arithmetic is float everywhere in between. VERDICT: PROVEN as a count; no observed bug attributable to float arithmetic in this pass (the known money bugs were FX/scale, not binary rounding). RECOMMENDATION: do NOT start a partial conversion ("all or nothing" stands); keep the boundary rule (every MONEY write through `tables.money`) and add a guard test that every assignment to a MONEY column goes through it. EFFORT of a full conversion: L, RISK high, value low for a single-user ILS book. Severity P3/park.

## L9. Two money() helpers

| Helper | Returns | Precision / rounding | Purpose | Callers (AST scan) |
|---|---|---|---|---|
| `app/core/money.py:18` `money(x) -> float` | float | 2 dp, ROUND_HALF_UP | display / serialisation | `engines/allocation_engine.py:11`, `engines/scenario_engine.py:10`, `engines/tax_engine.py:12`, `tests/test_money.py:4` |
| `app/models/tables.py:36` `money(value) -> Decimal` | Decimal | 4 dp (from `MONEY.scale`), ROUND_HALF_EVEN | DB write quantisation | `services/intake_service.py:17`, `ledger_repair.py:350,366,745`, `pricing_service.py:20`, `reconcile_service.py:296`, `strategy_service.py:25`; tests `test_a_quote_of_zero_is_not_a_quote.py:215`, `test_minor_unit_currencies.py:222`, `test_rebalance_leaves_cash_alone.py:162` |

They are not duplicates in intent (display vs storage); the defect is the shared name, which invites importing the wrong one (a 2dp float written to a 4dp share-quantity column would lose share precision). FIX: rename `core.money.money` -> `money2` / `display_money` (3 engine files + 1 test) and `tables.money` -> `db_money` (or `quantize_money`) (7 service sites + 3 tests); keep a deprecated alias for one release if wanted. EFFORT S. RISK low (mechanical; ruff catches misses). TEST: existing `test_money.py` + Postgres job.

## P2. ₪100 card minimum vs ₪250 funded-leg minimum

- THE TWO NUMBERS:
  - Card floor: `headsup.py:22-23` `MIN_TRADE_ILS = 100.0`, `MIN_TRADE_PCT_OF_BOOK = 0.5`; `min_trade_ils(nav)` (`:36-42`) = **max(₪100, 0.5% of NAV)**, settings `min_card_trade_ils` / `min_card_trade_pct`. Applied in `split()` (`:68-93`): a trade-kind card (`_TRADE_KINDS`, `:27`) whose `est_amount` is under the floor becomes a heads-up; rule cards are exempt.
  - Funded floor: `funding_service.py:35` `MIN_TRADE_DEFAULT_ILS = 250.0`, `min_trade_ils()` (`:50-52`), setting `min_funded_trade_ils`; `MIN_TRADE_ILS` resolves to it via module `__getattr__` (`:61`). `propose_funded_buy` returns **None** when `gap < 250` (`:839`), `want < 250` (`:846`), funded `amount < 250` (`:857`, `:867`); `plan_funding` drops sell legs under 250 (`:328`, `:579`, `:613`). ~45 other call sites in recommendations/strategy/frontier/rules gate on the 250 before a card is built.
- REPRO (scratch `test_p2_minimums.py`, synthetic ILS book, 4 passed):
  ```
  card floor at NAV 20k: 100.0  at NAV 35k: 175.0  at NAV 60k: 300.0  funded floor: 250.0
  gap 180 -> None                       (propose_funded_buy refuses)
  gap 400 -> 397.44                     (control: funded)
  cards: ['buy_x']  heads-ups: ['commodity_add_x']
  ```
- WHAT ACTUALLY HAPPENS for a trade of ₪100-250 (NAV < ₪50k, where the card floor is below 250):
  1. Any producer that goes through funding (funded buys, redeploy, fund sleeve, core-to-basket, frontier resize legs, cap trims via `rules_service.py:144-145`) **never builds the card**: it returns None / skips the leg silently. The ₪100 card floor is never reached, so for these kinds it is effectively dead below NAV ₪50k.
  2. `_commodity_recs` (`recommendations.py:5095`) is the visible exception: when `gap_ils < 250` it skips funding, leaves `apply = {"kind": "none"}` and still appends "Add a commodities sleeve — ₪180 of X" (`:5116-5127`). Not actionable -> `split` turns it into a **heads-up** whose one line is an instruction ("Buy ₪180 of X ...") with no act button. That is the PR 102 anti-pattern (advice with no Accept), just moved into the heads-up list.
  3. Above NAV ₪50k the pct term exceeds 250, the card floor binds, and the two agree in practice.
- So: no card is shown that funding then refuses (good; no broken Accept), but the "Smallest trade card ₪100" setting in the Card-thresholds sheet promises something the app never does below 250, and one card degrades into an un-actionable heads-up.
- VERDICT: PROVEN (code + scratch repro). Severity P2-low (setting is misleading; one heads-up is an instruction without an act).
- FIX (owner picks, per D64): (a) **one number**: card floor = max(`min_funded_trade_ils`, pct x NAV); drop `min_card_trade_ils` from SPEC (or keep it but validate `min_card_trade_ils >= min_funded_trade_ils` in `thresholds.clean`), and show "effective minimum ₪X" in the sheet; (b) in `_commodity_recs`, when `gap_ils < MIN_TRADE_ILS` return nothing (like every other producer) instead of a no-apply card. Recommend (a)+(b).
- EFFORT S. RISK low. TEST: `test_no_trade_card_or_headsup_below_the_funded_minimum` (commodity gap 180 -> neither card nor heads-up), `test_card_floor_never_below_funded_floor` (thresholds.effective / `headsup.min_trade_ils(nav) >= funding.min_trade_ils()`).

## P4. Hard-coded decision constants -> settings inventory

### How `thresholds.SPEC` works (why a new key is cheap)
`app/services/thresholds.py`: `SPEC[key] = (label, what it decides, lo, hi)` (`:20-42`); `defaults()` (`:45-61`) reads each default from the constant that owns it (no second copy); overrides live in `plans.thresholds` JSON (migration 0028), validated by `clean()` (`:64-83`, bounds + unknown-key rejection); `use(plan)` puts the overrides in a ContextVar once per request (`api/deps.py:29`) / job (`recommendations.py:1295`, `frontier_service.py:1138,1297`, `rules_service.py:513`) so worker threads inherit them; readers call `thresholds.get(key, CONSTANT)`. `routes/plan.py:260-266` ships `thresholds`, `_overrides`, `_defaults`, `_spec` with `GET /plan`, and the **Card-thresholds sheet renders generically from `thresholds_spec`** (`index.html:3161-3192`). So a new scalar key = 1 SPEC line + 1 `defaults()` line + wrap the reader in `thresholds.get(...)` + 1 test; **no migration, no UI code**. Dict-valued settings (per-objective tables like `RISK_CAPS`, `OBJ_TARGET`) need either flattening into scalar keys (`cash_floor_pct_grow`, ...) or a `clean()` branch like `guard_levels`; the sheet renders one numeric input per key, so a dict needs a small UI addition.

Already settings (for reference): `min_card_tax_ils, min_card_trade_ils, min_card_trade_pct, min_funded_trade_ils, cash_floor_ils, rebalance_band_pts, take_profit_fraction_pct, gain_line_pct, material_odds_pts, guard_levels` (SPEC); plan columns `kelly_fraction` (defaults from `KELLY_FRACTION_BY_OBJECTIVE`), `return_confidence`, `halve_limit_pct` (default `HALVE_FLOOR_PCT`), `on_track_pct` (default `GOAL_CONFIDENCE`), `cash_target_pct/_ils`, `cash_redeploy_at_ils`, `risk_tolerance`, `objective`.

### Inventory

| Name | file:line | Value | What it decides | User-facing? | Recommend |
|---|---|---|---|---|---|
| `RISK_CAPS` | plan_service.py:11-15 | Low/Med/High -> concentration .15/.25/.40, vol .10/.15/.25, ruin .10/.20/.35 | max weight per name (every buy is capped by it, `effective_name_cap`), vol + ruin vetoes | Yes: decides card sizes and "too concentrated" cards | **Setting**: `concentration_cap_pct` (scalar override of the risk_tolerance row) first; vol/ruin caps keep as constants (no card shows them directly) |
| `CASH_FLOOR_PCT` | funding_service.py:31 | Preserve .10, Income .07, Balanced .05, Grow .03 | default soft cash target when the plan has none | Yes (cash reserve / redeploy cards) | **Keep constant**: already overridden per plan by `cash_target_pct/_ils` (Liquid-cash modal). Just show "default for Grow: 3%" in that modal |
| `OBJ_TARGET` | allocation_mix.py:4-9 | Grow 80/10/10, Balanced 60/30/10, Preserve 30/60/0+10 cash, Income 40/50/10 | class targets when no core/sleeves set; fallback in `targets_for`, `_commodity_recs` | Yes (rebalance cards on a book with no core) | **Setting (later, M)**: low priority now that a core basket or frontier defines the book target; expose read-only first ("your book target comes from: core X") |
| `SLEEVE_DRIFT_BAND_PCT` | recommendations.py:42 | 5.0 pts | when Today says a sleeve drifted from its size | Yes | **Setting** `sleeve_drift_band_pts` (S). Note it disagrees with `rebalance_band_pts` default 3.0 -- owner should decide if one band or two |
| `_DRIFT_HIGH_X` / `_DRIFT_CRITICAL_X` | recommendations.py:150-151 | 3x / 6x band | severity escalation of drift cards | Indirect | Keep constant (card_priority now ranks) |
| `_DISMISS_TTL_DAYS` | recommendations.py:372 | 7 | how long "Ignore" mutes a card | Yes | **Setting** `ignore_mute_days` (S) |
| `_DONE_TTL_DAYS` | recommendations.py:386 | 14 | how long "Done" mutes a card | Yes | **Setting** `done_mute_days` (S) |
| `_DONE_SETTLE_DAYS` | recommendations.py:405 | 3 | grace before the book may contradict a "Done" | Yes (card reappears) | Setting `done_settle_days` (S) or keep; low value |
| `_REFIRE_DELTA` | recommendations.py:392 | 0.25 (25%) | how much a muted card's number must change to re-fire | Yes | **Setting** `refire_change_pct` (S) |
| `_REFIRE_FLOOR_ILS` | recommendations.py:395 | ₪100 | below this the ratio is ignored | Indirect | Tie to `min_funded_trade_ils` (see P2) instead of a separate number |
| `_NEAR_RULE_FRAC` | recommendations.py:3498 | 0.10 | "close to your take-profit" card distance | Yes | **Setting** `near_rule_pct` (S) |
| `_TRAIL_GIVEBACK` | recommendations.py:3514 | 0.60 | "closing on trailing stop" = 60% of trail given back | Yes | **Setting** `near_trail_giveback_pct` (S) |
| `_NEAR_CAP_PCT_POINTS` | recommendations.py:3522 | 2.0 pts | "drifting into max-weight cap" | Yes | **Setting** `near_cap_pts` (S) |
| `_GAIN_CARD_PCT` | recommendations.py:3518 | 25 | already a setting (`gain_line_pct`) | -- | done |
| `TAKE_PROFIT_LADDER_STEP` | rules_service.py:171 | 0.10 | spacing of the take-profit ladder | Yes (rule levels) | Setting `tp_ladder_step_pct` (S) |
| `DERISK_FRACTION` | advanced_rules.py:53 | 0.25 | what a de-risk sells of the most volatile holding | Yes | Setting, or bind to `take_profit_fraction_pct` (comment says "same 25%") |
| advanced `DEFAULTS` | advanced_rules.py:49-50 | profit_protect 15, trend_break 200, drawdown_guard 10, activation 20 | defaults for new advanced rules | Yes but each rule's own level overrides | Keep (per-rule values are already editable) |
| `MONEY_FULL_FRAC`, `TIE_POINTS`, `_SEV_FOR` | card_priority.py:41-43 | 0.05, 5 pts, 70/50/30 | card score -> label and order | Yes (order + label) | **Keep constant** (ranking internals; making them settings invites tuning noise). Expose only if Eran asks |
| `IDEAS_SHOWN` | recommendations.py:4186 | 5 | how many buy ideas are listed | Yes | Setting `ideas_shown` (S) -- Eran picked it ("Top 5") so D64 applies |
| `_MAX_BUY_IDEA_CARDS` | recommendations.py:4860 | 2 | screener names that become Today cards | Yes | Setting `max_idea_cards` (S) |
| `KELLY_FRACTION_BY_OBJECTIVE` | engines/frontier.py:60-65 | Grow .5, Balanced 1/3, Preserve/Income .25 | default Kelly dial | Yes | **Keep as default**: plan column `kelly_fraction` already overrides (Risk dials). Show "default for Grow: 0.5x" in the sheet |
| `KELLY_MIN/MAX` | frontier.py:87-88 | 0.25 / 1.0 | dial bounds | Indirect | Keep (safety bound) |
| `DRAWS`, `BLOCK_DAYS`, `SEED` | frontier_service.py:61-63 | 100, 21, 7 | resampling size/blocking | No | Keep (computational) |
| `BAND_PCT` | frontier_service.py:64 | (10, 90) | resampled no-trade band | Indirect (whether a resize card exists) | Keep; maybe expose later |
| `MIN_MOVE_PCT` | frontier_service.py:67 | 2.0 pts | smallest sleeve resize that is a card | Yes | **Setting** `min_resize_pts` (S) |
| `GRID_PCT` | frontier_service.py:70 | 0.5 | proposal rounding | No | Keep |
| `MAX_AGE_H` | frontier_service.py:72 | 36 h | stale verdict not acted on | No | Keep |
| `MATERIAL_GAP_CLOSE`, `MATERIAL_VOL_DROP_PCT` | frontier_service.py:85-86 | 1/3, 3 pts | when a resize "earns its card" | Yes | **Setting** (2 keys, S) -- same nature as `material_odds_pts`, which is already one |
| `GOAL_CONFIDENCE` / `HALVE_FLOOR_PCT` | frontier_service.py:342,345 | 0.70 / 1.0% | on-track bar, crash-risk limit | Yes | done (plan columns `on_track_pct`, `halve_limit_pct`) |
| `TAIL_TOLERANCE_PTS` | frontier_service.py:356 | 1.0 | fat-tail check tolerance | Indirect | Keep |
| `GUARD_LEVELS` | frontier_service.py:914 | (15, 20) | done (`guard_levels`) | -- | done |
| `RECENT_WEIGHT`, `RECENT_HALFLIFE_DAYS` | frontier_inputs.py:437-439 | 0.4/0.5/0.7, 63 d | how much recent markets weigh in return estimates | Indirect, large effect on odds | Keep constant but **document on the dials sheet**; candidate setting only if Eran wants it |
| `_OBJ_RETURN` | recommendations.py:3153 | Grow 8.5, Balanced 6.5, Preserve 4.0, Income 5.0 (+ fallbacks `rate 6.0`, `vol 12.0` at `:1918-1919`) | "behind goal" lever sizing (`:1922`, `:3246`, `:3371`) | Yes: numbers on cards | **Flag**: hand-typed return assumptions on a card path, after PR 98 "one return basis". Not a setting -- replace with the measured/frontier basis or label as assumption. Separate investigate-issue |
| `MIN_CLAIMABLE_MOVE` | funding_service.py:780 | 0.005 | below this a buy is a "swap" | Indirect (card label) | Keep (claim-honesty invariant) |
| `REFRESH_INTERVAL_MINUTES` | market_state.py:15 | 60 | legacy job cadence | No | Delete with L1 |

**Recommended first batch (one PR, S-M, no migration):** `sleeve_drift_band_pts`, `ignore_mute_days`, `done_mute_days`, `refire_change_pct`, `near_rule_pct`, `near_trail_giveback_pct`, `near_cap_pts`, `ideas_shown`, `max_idea_cards`, `min_resize_pts`, `material_gap_close_pct`, `material_vol_drop_pts`, `concentration_cap_pct`. Each: SPEC line + defaults line + `thresholds.get` at the reader. Some readers run in code that has no `use(plan)` in scope (e.g. mute TTLs read inside `_filter_dismissed` during push jobs) -- verify each reader is under a `use()` context (push job path: `rules_service.py:513` sets it; check `push_service` too). TEST per key: set override in `plans.thresholds`, assert behaviour flips (pattern already in PR 104d tests). Keep constant: card-priority weights, frontier DRAWS/SEED/GRID/MAX_AGE/KELLY bounds, MIN_CLAIMABLE_MOVE.

## L5(g) pyproject filter -- measured

Full suite with the filter removed (`pytest -q -o "filterwarnings=" -p no:cacheprovider`, worktree @ 93732f9, SQLite): **`2291 passed in 1017.92s`, 0 warnings**. The `_refresh_all` never-awaited warning no longer occurs (scheduler is off in tests, `conftest.py:41`, and the prime now runs through `_guarded`). PROVEN dead: delete `pyproject.toml:15-27` (the comment and the filter). Side note: CLAUDE.md says "~790 tests"; the suite is 2,291.

## Summary

| Item | Verdict | Severity | Effort |
|---|---|---|---|
| L2 investwise.db / frontend/ / FRONTEND_ORIGIN | PROVEN unused (0 refs outside archived ship scripts that only *work around* the tracked db). CORS: the PWA is same-origin, so nothing breaks if the default changes | P3 | S |
| L3 google-genai / sentry-sdk | PROVEN: genai has 0 imports (Gemini is raw REST); sentry imported behind `try`, unpinned, so SENTRY_DSN would do nothing. Pins test just loses a parameter | P3 | S |
| L5 stale comments (7 checked) | PROVEN stale a-f (29 tables not 11; transactions + whs_snapshots ARE written; buy-back holds are trading_rules; is_core is read; market_state describes a removed UI; frontier PR map wrong, mean-CVaR never built); pyproject filter: see last line | P3 | S |
| L6 stale UI copy | PROVEN: "Cash tab" (index.html:3052 **and** server copy frontier_service.py:2025), "Goal settings" (:3252). "$ amount" is **NOT an issue** (server sizes in the instrument's currency, USD). Bonus: `/portfolio/add` writes no ledger leg (INFERRED hole) | P2 | S |
| L12 frontend a11y/CSS debt | PROVEN: .seg twice (hybrid result); --muted undefined with no fallback (visible); 17 non-focusable click targets; 0 :focus rules; no Escape/focus mgmt | P3 | M (S for CSS bits) |
| L13 candidate_search.py | PROVEN dead draft of tracked `sleeve_count.py` (same day, same feature); 16/16 tests pass in scratch, 0 callers. **Delete** (optionally port `forbidden_tickers`) | P3 | S |
| L14 CANE vs IAU/DBC | PROVEN from code + archived live observation: commodity screen is plan-blind; cards can buy sugar while the goal model prices commodities as IAU/DBC | P3 | S-M |
| L15 Gemini errors | Mostly DONE. Left: grounded per-holding research still shows "temporarily unavailable" on a 429 and retries the billing error ungrounded (scratch repro: 2 calls, None); digest/adversary drop the reason | P3 | S |
| L7 fat routes | PROVEN, plus a layering inversion: 3 services import from `app.api.routes.plan` | P3 | M-L |
| L8 float vs Decimal | Count now 889/25 (services), 107/1 (engines), 1,051/45 (app). No bug found; park, keep the write-boundary rule | P3 (park) | L |
| L9 two money() | PROVEN: different purposes (2dp float display vs 4dp Decimal DB), same name; 3 engines vs 7 service sites | P3 | S |
| P4 constants -> settings | 35 constants inventoried; 13 recommended for a first SPEC batch (no migration, sheet renders generically). Flag `_OBJ_RETURN` hand-typed returns on a card path | P2 | S-M |
| P2 ₪100 vs ₪250 | PROVEN + repro: below NAV ₪50k no funded card between 100 and 250 is ever produced (refused before it exists), so the ₪100 setting is inert; the commodity card degrades into an un-actable "Buy ₪180 of X" heads-up | P2-low | S |
