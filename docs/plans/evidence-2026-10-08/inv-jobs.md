# Investigation: scheduled jobs, markets day-change, startup I/O, provider bypasses, backups

Repo: C:\dev\Investwise-pro\.claude\worktrees\docs-overhaul @ 93732f9 (read-only).
Scratch tests: C:\Users\erang\.claude\jobs\a36f9c6f\tmp\scratch_jobs\

(Items appended below as completed.)

---
## C4. Jobs that swallow exceptions and record success

**SYMPTOM:** claim that backtest_refresh, strategy_signals, push_evaluate, push_digest record `last_ok: True` in JOB_STATE when their work fails.

**EVIDENCE** (`python -m pytest -q -p no:cacheprovider -s C:\Users\erang\.claude\jobs\a36f9c6f\tmp\scratch_jobs\test_c4_swallow.py` -> 13 passed). Each job run through `scheduler._guarded(job_id, fn)()` exactly as APScheduler does, with the inner coroutine monkeypatched to `raise RuntimeError("INJECTED")`:
```
[backtest_refresh] {'runs': 1, 'failures': 0, 'last_ok': True, 'last_result': {'computed': 0, 'abstained': 0}, ...}
[strategy_signals] {'runs': 1, 'failures': 0, 'last_ok': True, 'last_result': {'checked': 0, 'flipped': 0}, ...}
[push_evaluate]    {'runs': 1, 'failures': 0, 'last_ok': True, 'last_result': {'sent': 0}, ...}
[push_digest]      {'runs': 1, 'failures': 0, 'last_ok': True, 'last_result': {'sent': 0}, ...}
```
Also with NO app-code monkeypatch, only pointing the job at a DB with no tables (a real OperationalError, e.g. a missing migration / DB down):
```
[push_evaluate]    {'failures': 0, 'last_ok': True, 'last_result': {'sent': 0}}
[backtest_refresh] {'failures': 0, 'last_ok': True, 'last_result': {'computed': 0, 'abstained': 0}}
```
Two MORE jobs found doing the same (not in the original list):
```
[futures_warm]   {'failures': 0, 'last_ok': True, 'last_result': None}      # futures_snapshot raised
[market_refresh] {'failures': 0, 'last_ok': True, 'last_result': {...refresh_count: 1}}  # ResearchAgent.scan raised
```
Controls (same harness) record the failure correctly:
```
[price_refresh]   {'failures': 1, 'last_ok': False, 'last_error': 'RuntimeError: INJECTED'}
[nav_snapshot]    {'failures': 1, 'last_ok': False, 'last_error': "OperationalError: ... no such table: users"}
[solver_refresh]  {'failures': 1, 'last_ok': False, 'last_error': 'RuntimeError: INJECTED'}
[holding_review]  {'failures': 1, 'last_ok': False, 'last_error': 'RuntimeError: INJECTED'}
[standing_orders] {'failures': 1, 'last_ok': False, 'last_error': 'OperationalError: ... unable to open database file'}
```

**RULED OUT:**
- "_guarded itself loses errors" -- no: the five controls through the same `_guarded` record `last_ok: False`.
- "the failure is caught deeper and the job legitimately did nothing" -- no: the injected raise is at the inner coroutine, and the real-DB case fails on the first SELECT; the catch is the outer `try/except Exception` in the sync entry function.
- "only monkeypatch artefact" -- no: the no-tables DB variant reproduces with zero app-code patching.

**ROOT CAUSE:** sync entrypoints catch everything and return the healthy-empty shape:
- `app/services/backtest_service.py:595-601` -> `return {"computed": 0, "abstained": 0}`
- `app/services/strategy_signal_service.py:449-455` -> `return {"checked": 0, "flipped": 0}`
- `app/services/push_service.py:595-599` and `604-608` -> `return {"sent": 0}`
- `app/worker/scheduler.py:151-155` (`_warm_futures` closure: `except Exception: pass`)
- `app/services/market_state.py:26-29` (`refresh_market_data`: logs warning, returns status)
Chain: exception -> caught in entry fn -> returns a dict -> `_guarded._target` stores `box["result"]` -> `_record(ok=True)` -> `job_state()` / `push_service.blockers` (push_service.py:524-530 loops all history, flags only `not last_ok`) report "nothing broken".
This is precisely the pattern PR 12 removed from pricing_service and PR 11 avoided in solver_store (their docstrings say so); these four were never converted.

**WHY SILENT:** the only signal is a `logger.warning(... exc_info=True)` line in Railway logs. `/api/v1/push/status` blockers read JOB_STATE, which says ok. The returned shape is identical to a legitimate quiet run (no subscribers / no strategies / no flips). No test executes these entry functions (see C5).

**BLAST RADIUS:**
- push_evaluate / push_digest: notifications stop entirely (any DB error, a bad subscriber, a single exception in `evaluate_and_notify` for ONE subscriber aborts the whole fan-out at push_service.py:574-580 since there is no per-subscriber try) while status says healthy. This is the exact "notifications went silent for two weeks" scenario the scheduler docstring (scheduler.py:3-7) was written to make diagnosable. Partial mitigation: `KV_LAST_PUSH_RUN` heartbeat is written only on success, so a stale heartbeat is an indirect tell -- but blockers do not compare its age.
- backtest_refresh: `strategy_backtests` rows go stale; downstream solver_refresh (04:10) re-solves from stale measurements and stamps today's date.
- strategy_signals: rule-based sleeve flips are never detected -> no signal card, no push.
- futures_warm: regime/futures cache goes stale; Today and AI summaries read whatever the cache holds (markets_service caches only successes?) -- every 5 min reports ok.
- market_refresh: vestigial (no economic source), so low impact.

**CONFIDENCE:** PROVEN (observed for all six).

**FIX:** delete the try/except in the four entry functions (match `run_solver_refresh_blocking`'s comment), let `_guarded` record; drop the `except: pass` in `_warm_futures`; make `refresh_market_data` re-raise after stamping (or remove the vestigial job). Optional: per-subscriber try in `_for_each_subscriber` that counts `failed` and raises at the end if any failed, so one bad subscriber does not silence all. Test: a parametrized test over every job id registered by `start_scheduler` (fake BackgroundScheduler as in the scratch test) that injects a raise into each job's inner coroutine and asserts `JOB_STATE[id]["last_ok"] is False` -- this also enforces the rule for future jobs.
**EFFORT:** S. **RISK:** low -- the only behavioural change is JOB_STATE/blockers turning red when something is actually broken; check nothing asserts on the returned zero-dicts (grep found no test calling these functions).

---
## C5. Which jobs have a test that actually runs them; per-job engine audit

**SYMPTOM:** claim that backtest_refresh, strategy_signals, push_evaluate, push_digest, futures_warm have no test that runs them.

**EVIDENCE:** `grep -rln "\b<name>\b" tests` for every entry fn AND inner coroutine (command in transcript; zero hits = empty line):
```
run_backtest_refresh_blocking  -> (none)      _refresh_all_job      -> (none)
run_strategy_signals_blocking  -> (none)      _evaluate_all_job     -> (none)
run_evaluations_blocking       -> (none)      run_digests_blocking  -> (none)
_for_each_subscriber           -> (none)      futures_warm/_warm_futures/futures_snapshot -> (none)
```
Indirect paths checked and ruled out:
- `backtest_service.refresh_all`: only `inspect.getsource(B.refresh_all)` in test_a_core_is_a_strategy_too.py:290 (text, not execution). The route that calls it (`POST /api/v1/strategies/backtests/refresh`, strategy.py:83) is hit by no test.
- `strategy_signal_service.evaluate_all` / `evaluate_user`: every `evaluate_all(`/`evaluate_user(` call in tests is `rules_service` (same names, different module). Tests use only `sigs.evaluate`, `_state`, `pending_signal_recs`, `resolve_signal`, `active_strategy_id(s)`, `signal_card_id`.
- `push_service.evaluate_and_notify` / `send_digest`: only `inspect.getsource` (test_p42_notifications.py:90,97). The routes that call them (`POST /api/v1/push/check`, `/push/run-all`) are hit by no test.
- `markets_service`: no test imports it at all; `/api/v1/markets/futures` and `/markets/summary` untested.

**True coverage per job** (job id -> entry fn -> test that EXECUTES it):

| job id | entry fn | executing test | inner coroutine executed? |
|---|---|---|---|
| market_refresh | market_state.refresh_market_data | tests/test_sims_and_refresh.py::test_market_refresh_state | yes (no-op scan) |
| price_refresh | pricing_service.run_price_refresh_blocking | test_the_card_says_how_bad_it_is.py:75,96 | **PARTIAL**: one test raises before `asyncio.run`, the other patches `_refresh_all` away. The real `_refresh_all` (engine + refresh + rules eval) is never run as a job; `refresh_all_positions` is tested directly elsewhere |
| futures_warm | closure `_warm_futures` in start_scheduler | **NONE** | NONE (markets_service entirely untested) |
| push_evaluate | push_service.run_evaluations_blocking | **NONE** | NONE |
| push_digest | push_service.run_digests_blocking | **NONE** | NONE |
| standing_orders | standing_order_service.run_standing_orders_blocking | test_standing_orders.py::test_the_scheduled_job_actually_runs | yes |
| nav_snapshot | nav_history.run_nav_snapshot_blocking | test_job_health_and_gain.py:61 | yes (calendar pinned) |
| backtest_refresh | backtest_service.run_backtest_refresh_blocking | **NONE** | NONE |
| solver_refresh | solver_store.run_solver_refresh_blocking | test_frontier_resize.py::test_the_nightly_job_runs_the_frontier_pass | yes (frontier pass stubbed, solver real) |
| holding_review | holding_review.run_holding_review_blocking | test_reviewer_card_claims.py:167 | yes |
| strategy_signals | strategy_signal_service.run_strategy_signals_blocking | **NONE** | NONE |

Claim CONFIRMED: 5 jobs with zero execution coverage (+ price_refresh partial). Note also that even if these had tests on SQLite, the swallow in C4 means a test asserting only "it returns a dict" would pass on a crash -- tests must assert `last_ok`/raise.

**Engine audit** (all 11 jobs; cross-loop rule = build engine in the job's own loop, never borrow `app.core.database.engine`/`AsyncSessionLocal`):

| job | engine | where | verdict |
|---|---|---|---|
| market_refresh | no DB | market_state.py | n/a |
| futures_warm | no DB | markets_service.py | n/a |
| price_refresh | own, **pooled** (`pool_pre_ping=True`) | pricing_service.py:494 | OK (created+disposed in its loop) |
| push_evaluate / push_digest | own, **pooled** | push_service.py:567 | OK |
| backtest_refresh | own, **pooled** | backtest_service.py:584 | OK |
| strategy_signals | own, **pooled** | strategy_signal_service.py:438 | OK |
| nav_snapshot | own, NullPool | nav_history.py:1083 | OK |
| solver_refresh | own, NullPool | solver_store.py:427 | OK |
| holding_review | own, NullPool (`_own_session`) | holding_review.py:233 | OK |
| standing_orders | own, NullPool | standing_order_service.py:232 | OK |

No job touches the shared engine: `grep AsyncSessionLocal|database import engine` outside app/api finds only main.py (audit middleware, main loop) and warmup.py (runs as `asyncio.create_task` on the main loop -- same loop, OK; it accepts a `session_factory` for other loops). No violation. Minor doc drift: ARCHITECTURE.md §7 says every job builds a *NullPool* engine; four use a pooled engine. That is still loop-safe because the engine is created and disposed inside the same `asyncio.run` loop, so not a defect -- but NullPool would be the consistent choice.

**CONFIDENCE:** PROVEN (grep + reading every hit).
**FIX:** one parametrized "every registered job runs" test (fake BackgroundScheduler captures `add_job` callables from `start_scheduler`, run each through `_guarded` on a seeded SQLite DB with providers stubbed, assert `last_ok is True`), plus the failure-injection twin from C4. Run under test-postgres too (that is where the cross-loop class shows). **EFFORT:** M (seeding for push/strategy). **RISK:** low.

---
## C11. markets_service "day change" is a ~5-session change

**SYMPTOM:** futures tiles show a "today" change that does not match the day's move (reported: crude +5.5% vs +1.28%).

**EVIDENCE:** code `app/services/markets_service.py:39-56`: URL `?interval=1d&range=5d`, `prev = meta.get("chartPreviousClose") or meta.get("previousClose")`, `chg = (regularMarketPrice - prev)/prev`.
Live Yahoo, read-only (`python -I C:\Users\erang\.claude\jobs\a36f9c6f\tmp\scratch_jobs\c11_yahoo.py`, 2026-10-07 ~21:00 UTC, same URL/UA as the app):
```
CL=F price 88.94 | chartPreviousClose 91.11 | previousClose None
   bars [('10-02', 91.11), ('10-05', 89.43), ('10-06', 89.44), ('10-07', 88.94)]
   app formula (chartPreviousClose) -2.38%   vs previous daily bar -0.56%
ES=F price 7850.25 | chartPreviousClose 7777.25 | previousClose None
   app formula +0.94%   vs previous daily bar -0.30%      <-- SIGN FLIP
NQ=F app formula +1.10%   vs previous daily bar -0.25%     <-- SIGN FLIP
GC=F app formula -0.62%   vs previous daily bar -1.20%
^VIX price 15.08 | chartPreviousClose 16.39
   app formula -7.99%   vs previous daily bar +0.47%       <-- SIGN FLIP
```
`chartPreviousClose` is the close at the START of the 5-day window (it equals the first bar's close, 4 sessions back), not yesterday's close. `previousClose` is absent from the v8 chart meta for these symbols, so the fallback never fires.

**RULED OUT:**
- "Yahoo meta is just stale intraday" -- no: the gap equals exactly the first bar of the 5d window on every symbol.
- "the `previousClose` fallback corrects it" -- no: `previousClose: None` in all 5 responses (and `or` only reaches it when cpc is falsy).
- "only the display is wrong" -- no: the same `change_pct` feeds `_regime` (see blast radius).

**ROOT CAUSE:** markets_service.py:50 uses `chartPreviousClose` with `range=5d`. Chain: wrong `change_pct` -> `_regime()` averages ES/NQ/YM `change_pct` (lines 69-89) into `equity_avg_change_pct` and the score -> `cached_regime()` -> recommendations.py:2169-2181 emits the "Markets look risk-off ... consider raising cash by ~5-10%" card when score <= -2. Also `/api/v1/markets/futures` tiles (index.html:698), and `ai_service.macro_summary` sends the whole snapshot JSON to Gemini as the "what's moving" note (ai_service.py:119-128); `market_rationale` also goes into the portfolio-summary context.

**Other users of chartPreviousClose:** grep over *.py/*.js/*.ps1 for `chartPreviousClose|previousClose|regularMarketPreviousClose` -> only markets_service.py:50 and `live.py:370` (FMP `price` falls back to `previousClose` when `price` is missing; a different, legitimate use). Pricing (`YahooMarketDataProvider.get_quote`) uses the last price only. Push price-move (push_service.py:417-430) compares against a per-ticker last-notified baseline in KV. Performance/position_history compute from stored prices. **Not affected.**

**WHY SILENT:** no test imports markets_service; the number looks plausible; the regime still produces a label.

**BLAST RADIUS:** Markets tab tiles (wrong magnitude, sometimes wrong sign); the regime score tracks the direction of equity futures over ~a week instead of today, so it can raise or suppress the risk-off cash card on Today; the Gemini macro note narrates wrong "today" moves (grounded data, but grounded on the wrong quantity, which works against the "never invent numbers" rule). The VIX level component is correct (it uses price, not change).

**CONFIDENCE:** PROVEN (live response + code).

**FIX:** compute the change from the bars: last non-null close in `indicators.quote[0].close[:-1]` (prior session) vs `regularMarketPrice`. Alternatively request `range=1d` or `2d`, but the bar method is robust to holidays and is what the comparison above used. Test: feed `_fetch_one` a recorded fixture of this exact CL=F response and assert `change_pct == -0.56`, plus a sign test with ES (+0.94 old vs -0.30 new). **EFFORT:** S. **RISK:** low; the regime label may change after deploy (expected).

---
## L11. Synchronous network I/O on the boot path

**SYMPTOM:** `start_scheduler()` runs inside the FastAPI lifespan before the server accepts requests, and makes network calls synchronously.

**EVIDENCE:** code: main.py:203-205 calls `start_scheduler()` (sync, on the event loop, inside lifespan). scheduler.py:107 `refresh_market_data()` and scheduler.py:156 `_warm_futures()` -> `futures_snapshot(force=True)` -> 8 parallel `_fetch_one` calls via ThreadPoolExecutor, each `_http_text(timeout=10)`.
Measured (`python -m pytest -q -p no:cacheprovider -s ...\scratch_jobs\test_l11_l10.py -k boot`, real lifespan + real APScheduler, ENABLE_SCHEDULER=true, `markets_service._http_text` stubbed to sleep N s then raise):
```
[delay=0.0s]  lifespan->first request: 1.78s   futures fetches during boot: 8  /health/ready=200
[delay=5.0s]  lifespan->first request: 5.15s   futures fetches during boot: 8
[delay=12.0s] lifespan->first request: 12.10s  futures fetches during boot: 8
```
Boot time grows 1:1 with the slowest Yahoo fetch. `refresh_market_data()` is NOT network-bound (the economic provider returns [] since PR 58: registry.py:63-73), so the docs' "a market refresh" part is a no-op; the cost is the 8 futures fetches.
Extra finding: `_http_text`'s `result(timeout=timeout+5)` is not a cap (`...\scratch_jobs\test_http_cap.py`). With urlopen hung 10 s and timeout=1 (nominal cap 6 s) it returned after **10.00 s** with TimeoutError, because the `with ThreadPoolExecutor` block's `shutdown(wait=True)` joins the hung thread. The only real bound is urllib's per-socket-op timeout (10 s), which does not cover DNS resolution or a slow-drip body.

**RULED OUT:** "it runs in a thread so boot isn't blocked" -- no: `_warm_futures()` is called directly at scheduler.py:156 before `add_job`; only price_refresh/standing_orders are primed via `next_run_time`. "Parallel, so negligible" -- parallel bounds it by the slowest of the 8, not by zero.

**ROOT CAUSE:** the direct call at scheduler.py:156, inside a lifespan that blocks. Uvicorn does not accept connections until lifespan startup returns.

**WHY SILENT / IMPACT QUANTIFIED:** normal Yahoo latency is sub-second, so boot gains ~1 s. Worst realistic case (Yahoo blackholing): ~10 s per socket op, in parallel -> ~10-20 s added. Railway healthcheckTimeout is 300 s (railway.json:12), and Railway keeps the old deployment serving until /health/ready passes, so a rolling deploy has **no user-visible impact**. Impact exists only on a crash/restart (restartPolicy ON_FAILURE, no old container), where downtime grows by the fetch time. Exceeding 300 s would need a DNS hang (unbounded) -- INFERRED, not observed. The Dockerfile HEALTHCHECK start-period of 20 s is local-only.

**CONFIDENCE:** PROVEN for the mechanism and the 1:1 delay; INFERRED for the >300 s pathological case (would need a DNS-hang reproduction).
**FIX:** replace `_warm_futures()` at :156 with `next_run_time=datetime.now(timezone.utc)` on the futures_warm job (the pattern PR 12 used for price_refresh), and drop the boot `refresh_market_data()` (vestigial). Separately, make `_http_text` actually time out (`ex.shutdown(wait=False, cancel_futures=True)` or a module-level executor). Test: a lifespan-timing test like the scratch one, asserting boot < 2 s with a 10 s stubbed provider. **EFFORT:** S. **RISK:** low (Today reads `cached_regime()`, which already tolerates an empty cache for the first seconds).

---
## L10. Provider resilience-tier bypasses

**SYMPTOM:** `markets_service._fetch_one` and `pricing_service.fallback_provider` call Yahoo without the registry's cache/breaker/rate-limit/single-flight.

**EVIDENCE:**
- markets_service.py:15,42 calls `app.providers.live._http_text` directly, with no `_tiers()`/guarded_* call. Bypass confirmed.
- pricing_service.py:27-62 `fallback_provider()` returns a bare `YahooMarketDataProvider()`; used at :155, :258 (`offload(yahoo.get_quote, tk)`) and :265 (`yahoo.get_quote(tk)` -- **not offloaded**, a blocking call inside an async function). Its own docstring (:54-60) admits it bypasses the tier. Bypass confirmed.
- BUT `yahoo = None if primary.name == "yahoo" else fallback_provider()` (:155), and production runs `MARKET_DATA_PROVIDER=yahoo` (docs/OPERATIONS.md:47). So **in production the pricing bypass is dormant**; it only runs with builtin (dev/tests) or fmp.
- Failure mode of the futures bypass, observed (`...\scratch_jobs\test_l11_l10.py::test_outage_overwrites_good_cache_with_empty`):
```
[before outage] items: 8 regime: {'regime': 'neutral', ..., 'vix': 30.0, 'rationale': 'VIX 30.0, equity futures +50.00%'}
[after outage]  items: 0 regime: {'regime': 'neutral', 'tone': 'warn', 'score': 0, 'vix': None, 'rationale': 'limited data'}
[cached_regime after outage] {'regime': 'neutral', ... 'rationale': 'limited data'}
```
A Yahoo outage or 429 **overwrites a good cached snapshot with an empty one**, and the regime silently becomes "neutral" (no stale-copy fallback, unlike the tier's stale cache). Combined with C4, the futures_warm job still records ok. Note `futures_snapshot` itself never raises (every `_fetch_one` error becomes None), so even without the closure's `except: pass`, futures_warm could not report an outage.

**RULED OUT:** "a breaker is needed to avoid hammering Yahoo" -- the load is small: 8 requests / 5 min from the job, plus cold-cache route/AI calls that can duplicate fetches since there is no single-flight (at most a few x 8). Rate-limit ban risk: LOW. The real risk is semantic (silent neutral regime, blank tiles "Markets unavailable right now"), not load.

**ROOT CAUSE:** markets_service.py:108-123 caches whatever `items` it got, including `[]`; `_fetch_one` swallows every error into `None`.
**BLAST RADIUS:** the risk-off card can disappear during an outage exactly when markets are stressed and Yahoo is most likely to throttle; the AI macro note says "limited data". Pricing bypass: none in prod. In fmp mode, the unoffloaded `yahoo.get_quote` at :265 would block the event loop of the manual `/portfolio/refresh-prices` route (intake.py:401-416) for up to ~10 s per ticker whose FMP quote lacks a timestamp -- INFERRED (not run; would need MARKET_DATA_PROVIDER=fmp and a slow stub).
**CONFIDENCE:** PROVEN (bypasses + cache overwrite); INFERRED (fmp-mode loop block).
**FIX:** route futures through a registry tier (`_tiers()["market"].call("futures:"+sym, ...)`) to get stale-on-error + single-flight. Minimal version: in `futures_snapshot`, keep the previous snapshot when `items` is empty (mark it stale) and raise so futures_warm records the failure. Pricing: wrap the fallback in the tier and `offload` the :265 call. Test: the outage test above, inverted (assert the prior regime survives and the job reports failure). **EFFORT:** S (minimal) / M (tier). **RISK:** low.

---
## O3. Backups

**EVIDENCE:** case-insensitive search of the whole worktree (excluding .html) for `pg_dump|pg_restore|backup|restore|point-in-time|PITR`:
- No `pg_dump`/`pg_restore` anywhere. No backup script in scripts/, scripts/ops/, scripts/repair/; no backup job in scheduler.py; nothing in railway.json (only build/startCommand/preDeployCommand/healthcheck/restart policy) or Dockerfile.
- Every "restore" hit is the recommendations mute/done API or prose. "backup" appears only as warnings: scripts/pin-vapid-from-db.sql:26-27 and scripts/ops/verify-vapid-pin.ps1:22 ("a secret with no backup" -- the VAPID private key lives only in the DB), OPS_RUNBOOK.md:65.
- docs/OPERATIONS.md:98-100 already states it: "Nothing in the repo describes database backups or a restore procedure"; BACKLOG.md:24 lists O3 as open, P1.
- Repo evidence about the DB host: OPERATIONS.md:14 "the Railway Postgres plugin", :46 `DATABASE_URL=${{Postgres.DATABASE_URL}}`, :93 / OPS_RUNBOOK.md:74-76: whether Railway exposes `DATABASE_PUBLIC_URL` is **unverified** ("the internal host doesn't resolve from a laptop"). So the repo has no proven path even to run an ad-hoc `pg_dump` from outside Railway.

**What Railway's Postgres provides by default:** NOT determinable from repo evidence. Nothing in the repo records whether volume backups/snapshots are enabled on the service, their schedule, or their retention. Status: UNKNOWN (INFERRED that nothing is configured from the repo side; the observation that settles it is the Railway dashboard -> Postgres service -> Backups tab).

**Irrecoverable data at stake:** `nav_snapshots` (OPERATIONS.md: missed days "gone permanently", pre-2026-08-16 already lost), the `transactions` ledger, `kv_settings` state (mutes, holding review, settings objects), and the VAPID keypair (losing it silently kills every push subscription).
**VERDICT:** confirmed gap (not a code defect). **FIX:** check/enable Railway Postgres backups in the dashboard and record schedule + retention in OPERATIONS.md §7; add a read-only `scripts/ops/backup-db.ps1` (`pg_dump` via `DATABASE_PUBLIC_URL`) and do one restore drill into a scratch DB, written up. **EFFORT:** S-M (mostly ops). **RISK:** none to the app; the drill must never point at prod as the target.

---
## L5. Stale test docstrings

1. **tests/conftest.py:61-62** (`_drop_stale_sqlite_file`): "Postgres runs (CI's test-postgres job) are untouched: they set DATABASE_URL explicitly and **get their schema from migrations**." STALE. .github/workflows/ci.yml:26-46 test-postgres runs only `pip install` + `pytest -q`; there is no alembic step. The schema comes from `create_all` in conftest's `_isolate_db` (conftest.py:~90) plus AUTO_CREATE_TABLES (`setdefault("AUTO_CREATE_TABLES","true")`, conftest.py:4). The first half of the sentence ("untouched") is still true.
2. **tests/test_startup_ddl_covers_migrations.py:3-8**: "This app does not run `alembic upgrade head` against production ... that list is what actually runs ... **it is the mechanism**." STALE / inverted. railway.json:9 `"preDeployCommand": "alembic upgrade head && alembic current"` now owns the production schema, and main.py:190-199 logs an ERROR if AUTO_CREATE_TABLES is on in production (default False). The startup ALTER list now runs only in tests/docker-compose. The test still has some value (it keeps the SQLite/compose DDL in step with migrations), but its stated premise is wrong and would mislead someone into believing prod depends on that list.

**CONFIDENCE:** PROVEN (file reads). **FIX:** rewrite both docstrings. **EFFORT:** S. **RISK:** none.

---
## Summary

| item | verdict | severity | effort |
|---|---|---|---|
| C4 jobs swallow exceptions -> JOB_STATE ok | **CONFIRMED (PROVEN)** for backtest_refresh, strategy_signals, push_evaluate, push_digest; **plus futures_warm and market_refresh** found doing the same. Controls price_refresh/nav_snapshot/solver_refresh/holding_review/standing_orders record failures correctly | High (push silence is the exact incident class the scheduler was hardened for) | S |
| C5 jobs without an executing test | **CONFIRMED**: 5 with zero execution coverage (backtest_refresh, strategy_signals, push_evaluate, push_digest, futures_warm); price_refresh partial (inner `_refresh_all` never run as a job). Engine audit: all 9 DB jobs build their own engine in-loop (4 pooled, 5 NullPool); no cross-loop violation; doc says "NullPool" for all (minor drift) | Medium | M |
| C11 futures day-change uses chartPreviousClose | **CONFIRMED (PROVEN live)**: with range=5d it is a ~4-session change; signs flipped live for ES, NQ, VIX; it feeds the regime -> risk-off card and the Gemini macro note. No other code uses it (pricing, push price-move and performance are unaffected) | Medium | S |
| L11 sync network I/O at boot | **CONFIRMED (PROVEN)**: boot delay = slowest of 8 Yahoo fetches (measured 1:1, 12 s stub -> 12.1 s). Also `_http_text` timeout+5 cap is ineffective (measured 10 s vs nominal 6 s). Within the 300 s healthcheck; no impact on rolling deploys, only on restarts | Low | S |
| L10 resilience-tier bypasses | **CONFIRMED**. Futures: an outage overwrites a good cache with empty -> silent "neutral" regime (PROVEN). Pricing fallback: dormant in prod (primary=yahoo); an unoffloaded blocking call at pricing_service.py:265 matters only in fmp mode (INFERRED) | Low-Medium | S (minimal) / M |
| O3 backups | **CONFIRMED gap**: no pg_dump/backup/restore anywhere in the repo; Railway default backup status not determinable from the repo (UNKNOWN); public DB URL unverified | High (ops; irrecoverable NAV history + VAPID key) | S-M |
| L5 stale docstrings | **CONFIRMED stale**: conftest.py:61-62 (CI postgres does not use migrations) and test_startup_ddl_covers_migrations.py:3-8 (prod does run alembic via preDeploy) | Low | S |
