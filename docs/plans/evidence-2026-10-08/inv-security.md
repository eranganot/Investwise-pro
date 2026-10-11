# InvestWise Pro -- security / legacy investigation (C6-C8, S1-S6, L1)

Repo: C:\dev\Investwise-pro\.claude\worktrees\docs-overhaul @ 93732f9 (read-only).
Scratch tests: C:\Users\erang\.claude\jobs\a36f9c6f\tmp\scratch_sec\ (sqlite, REQUIRE_AUTH as noted, ENABLE_SCHEDULER=false).

Context fact used throughout: Google OAuth callback ALWAYS mints SUPERADMIN (app/api/routes/google_auth.py:142).
Password login (/auth/token) only exists for the SuperAdmin credential. The only path to a READ_ONLY token in prod is
POST /auth/m2m (SUPERADMIN-only; default role READ_ONLY). So the role ladder is mostly vestigial in production.

## C6. /broker/connect + /broker/sync write mock holdings into the real book

- SYMPTOM: two bodyless POSTs add TEVA/GOLD/BOND to the user's tracked book.
- EVIDENCE (PROVEN) -- `scratch_sec/test_c6_broker.py`, REQUIRE_AUTH=true, READ_ONLY JWT for the allowlisted email:
  ```
  BEFORE 200 [["VOO", 10.0]]
  CONNECT (READ_ONLY token, no body) 200 {"provider":"mock","institution":"Mock Brokerage",...,"status":"CONNECTED"}
  SYNC    (READ_ONLY token, no body) 200 {"ok":true,"provider":"mock","synced_positions":3,"added":["BOND","GOLD","TEVA"]}
  AFTER  200 [["VOO",10.0],["TEVA",300.0],["GOLD",60.0],["BOND",100.0]]
  ```
- RULED OUT: (a) "needs BROKER_ENABLED" -- registry.py:12-14 returns MockAggregator *before* the broker_enabled check; mock is
  the config default (config.py:199). (b) "needs ANALYST" -- both routes depend only on acting_user (broker.py:26-28,37-39).
  (c) "lands in a separate sandbox book" -- sync uses ensure_entity(user,"Personal") + account "Mock (synced)"
  (broker_sync.py:105-107); /portfolio aggregates all of the user's accounts, so they are in NAV.
- ROOT CAUSE: app/brokers/registry.py:12-14 (mock always allowed) + app/services/broker_sync.py:107 upsert_positions into the
  user's Personal entity + app/api/routes/broker.py:28,38 (acting_user only, bodyless defaults).
- WHY SILENT: not called by index.html (grep: 0 hits for /broker); tests/test_broker_sync.py asserts the mock rows ARE
  added (it treats this as the feature). No ledger transaction is written (upsert_positions has no ledger call), so
  the NAV jump looks like unexplained gain -- nothing flags it.
- BLAST RADIUS: anyone holding any valid token (incl. a READ_ONLY m2m) or a stray script/curl can inject ~3 fake holdings
  into the only real book; every downstream number (NAV, mix, frontier, Today cards, goal odds) moves. Reversible by
  deleting the 3 positions, but contributions/gain stay skewed if NAV snapshots ran in between.
- CONFIDENCE: PROVEN.
- FIX (S): preferred -- unmount broker router (unused scaffold; see L1). Minimal -- in registry/get_aggregator refuse
  "mock" unless `environment != "production"` or an explicit `BROKER_ALLOW_MOCK=true`, and require ANALYST on both
  routes. Test: with REQUIRE_AUTH=true + ENVIRONMENT=production, POST /broker/connect then /broker/sync -> non-200 or
  ok:false, and GET /portfolio count unchanged.
- EFFORT: S. RISK: low (no UI caller; test_broker_sync.py must be updated/deleted).

## C7. /decision-feed/weekly silently falls back to demo observations

- SYMPTOM: an empty (or meta-less) book gets BUY/REBALANCE items for tickers the user does not own, unlabelled.
- EVIDENCE (PROVEN) -- `scratch_sec/test_c7_c8_s2_s3.py::test_c7_weekly_feed_demo_fallback`:
  ```
  C7 EMPTY BOOK 200 keys= ['feed_max','count','supported_categories','items']
  C7 tickers ['TEVA','GOLD']                                   <- from demo_data.DEFAULT_OBSERVATIONS
  C7 any 'demo'/'sample'/'illustrative'/'fallback' in body: False
  C7 seed /portfolio/add 200 (VOO)        -> C7 REAL BOOK tickers []      (real observations used, no lag -> no items)
  C7 BOOK WITH INTAKE ROW (spot==listing) -> tickers []
  ```
- RULED OUT: (a) "fires on every real book" -- NO. It is `[...] or DEFAULT_OBSERVATIONS` (workflows.py:162-163): only when
  *no* position yields an observation. Rows written by /portfolio/add and /intake carry spot/listing meta, so a populated
  book uses real data (observed: VOO -> []). (b) "labelled somewhere" -- no `demo`/`illustrative` key or word in the body.
- ROOT CAUSE: app/api/routes/workflows.py:162-163 `... or DEFAULT_OBSERVATIONS` (app/services/demo_data.py:7-19); a book is
  "empty" for this purpose whenever every Position.meta lacks spot_price/listing_price (intake_service.py:439-445).
- WHY SILENT: response shape identical; route unused by index.html; no test asserts a label.
- BLAST RADIUS: practically nil in production today -- Eran's book is populated and the PWA never calls this route. Real
  hazard only for an API/script consumer or an empty book (fake TEVA "BUY" with impact/confidence numbers).
- CONFIDENCE: PROVEN (behaviour). INFERRED that the prod book takes the real path -- confirm with one prod GET returning
  no TEVA/HYPE/GOLD/NOISE that Eran does not hold.
- FIX (S): delete with the legacy decision-feed group (L1). If kept: return `{"items": [], "reason": "no observations"}`
  instead of demo, or add `"illustrative": true`. Test: empty book -> items==[] (or illustrative is True).
- EFFORT: S. RISK: none (no UI caller). Verdict: real defect, LOW severity.
- **SIBLING FOUND IN A LIVE PATH (C7b, PROVEN):** app/services/whatif.py:53 has the identical unconditional
  `... or DEFAULT_OBSERVATIONS`, and /whatif IS called by index.html:3833, which renders `recommended_titles` and
  `vetoed_tickers`. `scratch_sec/test_c7b_whatif.py`, empty book:
  `risk_profile = {'evaluated': 3, 'recommended': 2, 'vetoed': 1, 'vetoed_tickers': ['HYPE'],
  'recommended_titles': ['Buy TEVA (NYSE)', 'Rebalance GOLD (SPOT)']}` -- no label. The war room does this correctly:
  it only falls back when `settings.demo_signals` is true (war_room.py:53-54; config.py:116-118 "Off in production:
  sample prices must never be presented as real advice") and sets `grounded=False`. whatif ignores that flag.
  Same trigger condition (empty or meta-less book), so low reach for Eran today, but it is user-facing.
  FIX (S): gate whatif's fallback on `get_settings().demo_signals` and return `grounded`; test: empty book ->
  recommended_titles == [] with demo_signals False.

## C8. /api/v1/ask: "never invent numbers" and "Not financial advice." are prompt-only

- SYMPTOM: whatever the model returns is shown verbatim in the Ask box.
- EVIDENCE (PROVEN) -- `test_c8_ask_returns_llm_text_unmodified` stubs ask_service.gemini_generate_ex to return
  "Your portfolio returned 37.4% last year and TEVA will hit 412 by March. Buy more.":
  ```
  C8 /ask 200 {"llm": true, "answer": "Your portfolio returned 37.4% last year and TEVA will hit 412 by March. Buy more."}
  C8 answer == stub: True | has disclaimer: False
  C8 digest_service.unsupported_numbers(same text, same ctx) -> [37.4, 412.0]
  ```
  i.e. the checker that already exists would have caught both invented figures; /ask never calls it.
- RULED OUT: (a) "UI appends the disclaimer" -- index.html:640-641 sets `o.textContent=r.answer` only (the footer at :525
  is a page-level line elsewhere). (b) "context_service.for_prompt prevents it" -- it only labels the input.
- ROOT CAUSE: app/services/ask_service.py:37-45 returns `out` unchecked; compare digest_service.py:224-234 which runs
  `unsupported_numbers(out, ctx)` and discards to the deterministic fallback.
- WHY SILENT: tests/test_assistant.py covers the non-LLM paths; Gemini usually complies, so no bad answer has been seen.
- BLAST RADIUS: /ask (UI). Same pattern in app/services/ai_service.py:86,113,130,159 (portfolio-summary, holding summary,
  macro summary, deep research) -- none verify numbers or the disclaimer. The digest's LLM path also does not enforce
  "Not financial advice." (only its fallback text appends it, digest_service.py:188).
- CONFIDENCE: PROVEN.
- FIX: in ask_service.answer after `out`: `bad = unsupported_numbers(out, ctx)`; if bad -> return
  `{"llm": False, "verified": False, "unsupported_numbers": bad, "answer": <deterministic "can't answer that from your
  data" + grounded_on>}`; append "Not financial advice." when missing. Numbers the user typed in `question` must be
  added to the allowed set (else "should I buy 50 shares?" is rejected). Test: stub invented text -> verified False,
  37.4 absent, answer ends with disclaimer; a grounded number passes. Then the same guard for ai_service.
- EFFORT: S (ask), M (all ai_service paths). RISK: false rejects when the model rounds/derives; the digest already
  accepts this trade-off -- log reject rate.

## S2. Logout does not revoke the session JWT

- EVIDENCE (PROVEN) -- `test_s2_token_survives_logout`:
  ```
  S2 before 200 {'sub':'eran.ganot@gmail.com','role':'SUPERADMIN'} | logout 307 /login  set-cookie iw_session=""; Max-Age=0
  S2 same bearer after logout 200 {'sub':'eran.ganot@gmail.com','role':'SUPERADMIN'}
  S2 replayed cookie after logout 200   (GET /api/v1/portfolio)
  ```
- RULED OUT: "revoked_tokens covers it" -- RevokedToken is only written/read by rotate_refresh (auth_service.py:142-157)
  for single-use refresh tokens. require_role (core/auth.py:106-134) validates signature/exp/allowlist/role only.
- ROOT CAUSE: google_auth.py:146-150 `/auth/logout` only deletes the cookie; core/auth.py:126 `_principal_from_token`
  never consults revoked_tokens. Session = 30-day SUPERADMIN JWT (config.py:168, google_auth.py:142).
- WHY SILENT: the browser behaves correctly (cookie gone); only a copied cookie shows it.
- BLAST RADIUS: a leaked iw_session = 30 days of SUPERADMIN that survives logout. Only kill switch today: rotate
  JWT_SECRET (logs everything out). Low likelihood (httpOnly, single user), high impact.
- CONFIDENCE: PROVEN.
- FIX: on /auth/logout decode the cookie and insert its jti into revoked_tokens; in require_role reject revoked jti
  (needs a DB read -> cache). Cheaper and more useful: per-user `sessions_valid_after` (reject iat < it) = "log out
  everywhere". Test: mint cookie, GET /auth/logout with it, replay -> 401; X-Agent-Key unaffected.
- EFFORT: S-M. RISK: low; every route passes through require_role, so test cookie, bearer and agent-key paths.

## S3. /push/unsubscribe is not caller-scoped

- EVIDENCE (PROVEN) -- `test_s3_unsubscribe_not_scoped` (victim = allowlisted email; attacker = READ_ONLY m2m subject
  `m2m:other-client`):
  ```
  S3 victim subscribe 200 | victim status subs: {"subscriptions": 1}
  S3 attacker (other subject) unsubscribe 200 {"ok":true}
  S3 victim status after: {"subscriptions": 0}
  ```
- ROOT CAUSE: app/api/routes/push.py:62 passes only `req.endpoint`; push_service.py:157-159 deletes `WHERE endpoint=:e`
  with no subject predicate. Sibling: save_subscription (push_service.py:149-151) re-assigns an existing endpoint's
  `subject` to whoever posts it (write-side twin: subscribe can hijack a known endpoint).
- WHY SILENT: single user; endpoint URLs are long unguessable capability URLs.
- BLAST RADIUS: another authenticated principal who knows an endpoint can silence or redirect a device's alerts.
  Practical risk very low with one human user + allowlist.
- CONFIDENCE: PROVEN.
- FIX (S): `.where(endpoint==e, subject==user.email)`; save_subscription refuses to move a row between subjects. Test:
  the scratch test inverted (victim still has 1).
- EFFORT: S. RISK: legacy rows saved in open mode carry subject=superadmin_name, not the email -- allow SUPERADMIN to
  match either, or the owner cannot unsubscribe an old device.

## S1. Mutating endpoints gated only by acting_user (READ_ONLY)

- METHOD: `scratch_sec/test_s1_s4_routes.py` walks every APIRoute's dependant tree (route + router deps), records the
  highest `require_role(...)` found, then fires each POST/PUT/PATCH/DELETE with a READ_ONLY JWT (allowlisted email) and
  with no credentials. 401/403 = gate rejected; anything else (200/404/422) = the gate let it through (404/422 are raised
  *after* auth). Raw table: `scratch_sec/routes.json` (146 API routes + 5 non-API: /openapi.json, /docs, /docs/oauth2-redirect,
  /redoc, /app mount).
- COUNTS: 67 mutating routes: ANALYST 33, **READ_ONLY 25**, SUPERADMIN 2, **NONE 7**.
- EVIDENCE (PROVEN) -- mutating routes a READ_ONLY token passes (status with READ_ONLY / anonymous):

  | Route | RO | anon | What it mutates |
  |---|---|---|---|
  | POST /recommendations/{id}/accept | 404* | 401 | **executes the card on the book** (sells/buys) |
  | POST /recommendations/{id}/dismiss, /{id}/restore, /restore, /restore-completed, /headsups/{id}/dismiss | 200 | 401 | card mute state |
  | PUT /portfolio/position/{id} | 404* | 401 | **edits a holding** |
  | DELETE /portfolio/position?ticker=&market= | 422* | 401 | **deletes a holding** |
  | POST /broker/connect, /broker/sync | 200 | 401 | **injects mock holdings (C6)** |
  | POST /rules, PATCH/DELETE /rules/{id}, POST /rules/{id}/toggle, POST /rules/evaluate | 422*/404*/200/200/200 | 401 | stop-loss/take-profit rules + rule events |
  | POST /push/subscribe, /unsubscribe, /test, /check | 422*/422*/200/200 | 401 | device subscriptions, sends pushes |
  | POST /ask, /ai/holding/{t}/research | 200 | 401 | none (LLM spend) |
  | POST /plan/frontier/preview, /whatif, /scenario, /backtest | 200/200/422*/200 | 401 | none (compute) |

  `*` = request reached the handler (auth passed) and failed on the dummy id/body.
- ANALYST-gated (correctly rejected READ_ONLY with 403): PUT /plan, sleeves fund/delete/swap, core set/clear,
  target apply/undo, frontier refresh, holding-review refresh, intake (json/csv), /portfolio/add, refresh-prices,
  cash, contributions, ledger repair x2, standing-orders CRUD/run, reconcile apply/baseline, portfolio risk/performance,
  nav-history snapshot, strategies apply/load-basket/signal-ack/backtests-refresh, decision-feed/generate, /actions.
  SUPERADMIN: /auth/m2m, /push/run-all (and GET /audit).
- Mutating with NO auth (anon 200/422): POST /auth/token, /auth/refresh (fine, they are login), /lag/scan,
  /safety/check, /allocation/analyze, /jobs/monte-carlo, /jobs/simulation (pure calculators, no DB writes -- see S4/S6).
- INCONSISTENCY that matters: setting cash needs ANALYST but deleting a holding needs only READ_ONLY; adding a holding
  needs ANALYST but accepting a card that buys/sells needs READ_ONLY.
- RULED OUT / PRACTICAL REACH: Google sign-in always mints SUPERADMIN (google_auth.py:142); password login exists only
  for the SuperAdmin credential; X-Agent-Key is SUPERADMIN. **The only way a READ_ONLY principal exists in prod is a
  /auth/m2m token minted with the default role** (auth.py:39-46). So today nothing human is READ_ONLY; this is a latent
  defect that turns any future "read-only share/script token" into a write token.
- ROOT CAUSE: app/api/deps.py:16-18 `acting_user` = require_role(READ_ONLY); routes that write rely on it alone
  (recommendations.py, intake.py position PUT/DELETE, rules.py, push.py, broker.py). No convention ties "writes" to ANALYST.
- WHY SILENT: every real session is SUPERADMIN; no test asserts READ_ONLY is refused on writes.
- BLAST RADIUS: any READ_ONLY m2m token = full write over holdings (via accept/PUT/DELETE), rules, push.
- CONFIDENCE: PROVEN (gate behaviour); INFERRED that no READ_ONLY m2m token exists in prod (check audit_log for
  POST /api/v1/auth/m2m).
- FIX (S-M): add `dependencies=[Depends(require_role(Role.ANALYST))]` to the 12 writing READ_ONLY routes above (leave
  compute-only POSTs at READ_ONLY). Guard test: introspect app.routes (same walker as the scratch test) and assert every
  POST/PUT/PATCH/DELETE is ANALYST+ unless in an explicit COMPUTE_ONLY allowlist -- this catches the next new route too.
- EFFORT: S (code) + S (test). RISK: low; UI sessions are SUPERADMIN so nothing in the PWA changes.

## S4. Unauthenticated endpoints with REQUIRE_AUTH=true

- EVIDENCE (PROVEN) -- 38 API routes + 5 non-API routes have no auth dependency. Anonymous probes
  (`test_s1_s4_routes.py`, `test_s4_public.py`, `test_s4_leak.py`):

  | Route | anon | Class | Note |
  |---|---|---|---|
  | /health, /health/ready, / | 200 | fine-public | commit SHA, env, schema revision (needed by smoke) |
  | /login, /auth/google/login, /auth/google/callback, /auth/logout, /auth/status | 200/307 | fine-public | |
  | POST /auth/token, /auth/refresh | 422 | fine-public | login |
  | /app/ (static shell), /dashboard -> 307 /app | 200/307 | fine-public | shell only; data is behind APIs. `/dashboard` now just redirects (main.py:333-336), so the CLAUDE.md "Advanced dashboard behind REQUIRE_AUTH" rule is satisfied trivially |
  | /push/public-key, /intake/template.csv, /commodities, /strategies, /strategies/backtests, /scenario/supported | 200 | fine-public | catalogs / public key |
  | /docs, /redoc, /openapi.json | 200 | info-disclosure | full map of 136 paths + schemas; no data |
  | /metrics | 200 | info-disclosure (minor) | labels are only method+status (core/metrics.py:6-7); no tickers/emails (checked) |
  | /data-status | 200 | fine-public (UI calls it pre-login) | provider names only |
  | **/providers/health** | 200 | **LEAKS DATA** | `failing_keys`, `no_data_keys`, `last_error_key` contain `quote:<TICKER>` of held positions whose quote failed. Observed: `"failing_keys":["quote:SECRETHOLDING"],"last_error_key":"quote:SECRETHOLDING"` anonymously after a failed reprice. Leaks *which* tickers are held (not amounts). |
  | /adversary/diagnostics | 200 | cost + config disclosure | reveals `google_key_present`, model; **when ADVERSARY_LLM_ENABLED it makes a live Gemini call per request** (agents/adversary.py:232) |
  | **/markets/summary** | 200 | **cost** | uncached Gemini call per anonymous request when GOOGLE_API_KEY set (ai_service.py:119-131; only the futures snapshot is cached 5 min) |
  | /markets/futures | 200 | fine-public | cached 5 min |
  | /market/quote?ticker=, /fx/rate | 422/200 | quota | anonymous proxy to the configured market provider (FMP quota) for arbitrary tickers |
  | /decision-feed/demo, /whs, /simulation, /tax/preview, /risk/preview, POST /lag/scan, /safety/check, /allocation/analyze, /jobs, POST /jobs/monte-carlo, /jobs/simulation, GET /jobs/{id} | 200/422 | legacy calculators, no user data | inputs come from the query/body or demo data; jobs run eager in-process (10k-path MC, bounded) |
- VERDICT: no endpoint returns holdings/amounts/NAV anonymously. One real leak (held tickers via /providers/health),
  two cost-amplification paths (/markets/summary, /adversary/diagnostics when enabled), API map disclosure (/docs).
- ROOT CAUSE: routers without dependencies (market.py:72-74, gfinance.py:69-71, adversary.py:9-11, FastAPI default
  docs_url/openapi_url in main.py:240).
- FIX (S): put /providers/health and /adversary/diagnostics behind require_role(SUPERADMIN) (smoke uses X-Agent-Key, so
  scripts keep working -- verify the smoke scripts send the header for these); gate /markets/summary with acting_user
  (UI is authenticated anyway) or cache its LLM text 5-15 min; `FastAPI(docs_url=None, redoc_url=None,
  openapi_url=None)` when environment=production (or serve them behind auth). Test: anon GET of each -> 401 with
  REQUIRE_AUTH=true; a parametrized "public allowlist" test over app.routes so a new unauthenticated route fails CI.
- EFFORT: S. RISK: low -- checked: scripts/smoke/_smoke-common.ps1:31-37 and smoke-all.ps1:35 send `x-agent-key` on every
  `Api` call (smoke-all.ps1:274 reads /adversary/diagnostics), so SUPERADMIN-gating these does not break smoke.
  index.html calls /markets/summary and /data-status; the former is fine behind acting_user (session cookie),
  /data-status is read at boot right after /auth/me so it could also be gated.

## S5. API_KEY / require_api_key is dead code; the production warning is false

- EVIDENCE (PROVEN, grep over app/ tests/ scripts/): `require_api_key` is defined in app/core/security.py:13 and
  referenced only by tests/test_auth_entities.py:8,32-42 (unit test of the function itself). **Zero route/dependency
  references.** `settings.api_key` (config.py:123) is read only by security.py:15-17 and main.py:200.
- SYMPTOM: main.py:200-201 logs `"API_KEY is not set in production - write endpoints are unauthenticated."` on every prod
  boot. With REQUIRE_AUTH=true that is false (S1 shows writes return 401 anonymously) -- and setting API_KEY would change
  nothing, because no route reads it.
- WHY SILENT: it is a WARNING at boot that has been true-looking noise forever; nobody can act on it.
- BLAST RADIUS: misleading ops signal only; nothing enforces with it.
- CONFIDENCE: PROVEN.
- FIX (S): delete security.py, `api_key` from config, the main.py:200-201 warning, and the 2 tests in
  test_auth_entities.py; optionally replace the warning with `if not settings.require_auth: logger.error("REQUIRE_AUTH
  is off in production")` -- that is the condition that actually means "unauthenticated". Test: boot with
  environment=production, require_auth=False -> error logged; require_auth=True -> no auth warning.
- EFFORT: S. RISK: none.

## S6. No inbound rate limiting

- EVIDENCE (PROVEN by absence): no slowapi/limiter/middleware in app/ or requirements; the only rate limiter is
  `TokenBucket` on OUTBOUND provider calls (providers/registry.py:29, `provider_rate_limit_per_sec=20`). main.py
  middleware stack = CORS + `_observe` (metrics/request-id/audit). Single uvicorn process, no `--workers`
  (railway.json:8, Dockerfile:59).
- WHY IT MATTERS MOSTLY FOR THE PUBLIC ROUTES: with REQUIRE_AUTH=true and a one-person allowlist, authenticated abuse
  requires a stolen session (S2). The anonymous surface is what an internet scanner can hit:
  - **/markets/summary** -- 1 Gemini call per anonymous request, uncached (cost; S4).
  - **/adversary/diagnostics** -- 1 Gemini call per request when ADVERSARY_LLM_ENABLED (cost).
  - /market/quote?ticker= -- arbitrary tickers through the paid provider; the outbound TokenBucket protects the
    provider but means anonymous traffic can starve Eran's own repricing (RateLimitedError, market.py:32).
  - POST /jobs/monte-carlo, /jobs/simulation, GET /simulation -- 10k-path MC, eager, run inline on the event loop
    (jobs.py:33-48) -> CPU DoS of the single worker.
- EXPENSIVE AUTHENTICATED ENDPOINTS (for the posture decision; none has an in-flight lock or cooldown):
  | Endpoint | Role | Cost |
  |---|---|---|
  | POST /plan/frontier/refresh | ANALYST | ~10-20 s, ~10y closes + resampled solve (plan.py:796-805) |
  | POST /plan/frontier/preview | READ_ONLY | one frontier evaluation per call; sheets call it per slider change |
  | GET /recommendations | READ_ONLY | builds Today; 22 s measured cold (main.py warm-loop comment); has write side effects |
  | GET /plan/target/cores | READ_ONLY | ~54 s measured (plan.py docstring) |
  | GET /plan/target, /target/split, /sleeve-count, /walk-forward | READ_ONLY | solver / 6x5x2 simulations |
  | POST /strategies/backtests/refresh | ANALYST | full catalog backtest |
  | POST /portfolio/performance, /portfolio/risk | ANALYST | history backfill per holding |
  | POST /ask, /ai/holding/{t}/research, GET /ai/* | READ_ONLY | Gemini $ (research = grounded search) |
- VERDICT: confirmed, no rate limiting. Severity LOW for the authenticated surface (single user), MEDIUM-LOW for the
  anonymous LLM paths (direct billing exposure).
- FIX (posture options, cheapest first): (1) gate/cache the 2 anonymous LLM routes (S4 fix) -- removes the billing
  exposure without a limiter; (2) a per-route single-flight lock + short result cache on frontier refresh/preview,
  target/cores, backtests refresh (also prevents double-tap duplicate work); (3) slowapi with a small per-IP limit on
  anonymous routes only. A global limiter is not worth it for a one-user app. Test for (1): anon GET /markets/summary
  -> 401, or two calls within TTL invoke gemini once (stub counter).
- EFFORT: S for (1), S-M for (2), M for (3). RISK: (2)/(3) can surface 429s to the PWA during the frontier sheet's
  slider drags -- exempt preview or debounce client-side.

## L1. Legacy subsystem deletion -- blast radius and staged order

Method: grep of importers in app/ and tests/ (module names + class names), grep of each path in
app/static_app/index.html, route-to-module map from the introspection (`routes.json`). **index.html calls none of
the legacy routes below (0 hits each, including `decision-feed`).** Test counts are `def test` functions that would
have to be deleted (D) or rewritten to another route/fixture (R).

### Modules -- keep vs delete

| Module | Importers in app/ (besides itself) | Tests | Deletable? |
|---|---|---|---|
| services/pipeline.py (PipelineOrchestrator) | routes/decision_feed.py only | test_a_cap_you_set_bounds_the_trade (docstring + 1 via AllocationAgent), roster test (anchors) | YES, with decision_feed |
| agents/allocation_agent.py (AllocationAgent) | services/pipeline.py only | test_allocation::test_agent_vetoes_buy_into_overweight_class (D1); test_a_cap...::test_the_agents_veto_is_the_weaker_check (D1); roster: ROSTER dict + test_the_allocation_agent_still_holds_the_only_pre_card_veto + test_the_allocation_veto_is_still_unreachable_from_the_app (D2) + docs/AGENTS.md §AllocationAgent + any line-anchor claims parametrized in test_every_line_anchor_in_the_map_points_where_it_says | YES, with pipeline -- **must edit docs/AGENTS.md and the roster test in the same commit** (the roster test walks app/agents/ and fails if the doc and disk disagree) |
| engines/safety_engine.py | routes/safety.py, pipeline.py | test_safety_engine.py (D5), test_api_integration::test_safety_check_via_api (D1), **test_observability::test_mutations_are_audited_and_viewable uses POST /safety/check as its audit probe (R1)** | YES, after pipeline |
| engines/xai_engine.py | routes/decision_feed.py, routes/workflows.py (weekly only), pipeline.py | test_xai.py (D3) | YES, after decision_feed + weekly |
| engines/learning_engine.py | routes/learning.py, pipeline.py | test_learning_engine.py (D2) | YES, with learning + pipeline |
| **engines/state_machine.py** | decision_feed, workflows(weekly), pipeline, **services/war_room.py:15, services/whatif.py:24 (both LIVE)** | test_state_machine (7), test_risk_engine, test_adversary_routing, test_war_room_reads_the_solver, test_xai | **NO** -- the Agents tab and What-if run on it. Same for lag_engine, decision_engine, risk_engine, tax_engine (all imported by war_room.py:12-16 / whatif.py:21-25) |
| schemas/state_machine.py | 55 files (Market/ActionType enums) | many | **NO** -- different file, do not confuse with the engine |
| services/demo_data.py | decision_feed, workflows, pipeline, **war_room route (behind demo_signals)**, **whatif.py:53 (live, ungated -- C7b)** | test_war_room*, test_adversary_routing, test_signal_service | Not until whatif is fixed; then only war_room's flagged path keeps it |
| worker/celery_app.py + worker/tasks.py | routes/jobs.py only | test_jobs.py (D4) | YES. No worker process exists (railway.json:8 runs uvicorn only); jobs are eager in-request. Drop `celery`, `redis` from requirements.txt:11-12 and `redis_url` from config |
| market_refresh job (scheduler.py:101,107-109) + services/market_state.py | scheduler only; `market_state.status()` has no reader in app/ | test_sims_and_refresh::test_market_refresh_state (D1) | YES. It calls ResearchAgent().scan() and throws the result away (market_state.py:22-33) -- a no-op that stamps a time nobody reads. Mentioned in docs/ARCHITECTURE.md + BACKLOG.md only |
| services/broker_sync.py + app/brokers/* | routes/broker.py only | test_broker_sync.py (D5), test_the_universe_reaches_tel_aviv (1 test imports broker_sync._to_intake -> D1) | YES (also closes C6) |
| Engines used by LIVE code -- route goes, engine stays | simulation_engine (plan.py, recommendations, target_solver), allocation_engine (plan, strategy, recommendations, book_target), backtest_engine (strategy, portfolio_risk_service, recommendations), fee_engine (recommendations), screener_engine/agent (recommendations, redeploy_research, objective_weights), whs_engine (portfolio_analytics -> /health-check), tax/risk/lag/decision engines (war_room, whatif) | -- | Only the HTTP route is legacy |

### Routes

| Route(s) | Router file | Other routes in the same file? | Tests touching the route | Delete? |
|---|---|---|---|---|
| /decision-feed/demo, /generate, /latest | decision_feed.py | no | test_api_integration::test_allocation_veto_via_api (D1), test_workflows_api::test_latest_items_carry_expiry (D1) | YES (stage 2) |
| /decision-feed/weekly | **workflows.py** | **yes: /health-check, /risk/alerts, /scenario (all UI ✅), /tax/review, /scenario/supported** | test_workflows_api::test_weekly_feed_uses_intaken_portfolio (D1) | delete the function only, NOT the router |
| /actions, /learning/profile | learning.py | no | via test_learning_engine (D2) | YES (stage 2) |
| /lag/scan | lag.py | no | none | YES (stage 1) |
| /whs | whs.py | no | none (test_health_score_ceiling tests the engine's legacy 5-component path -- keep or drop the `thematic` arg) | YES (stage 1) |
| /simulation | simulation.py | no | none | YES (stage 1) |
| /safety/check | safety.py | no | test_api_integration (D1), test_observability (R1) | YES (stage 1, rewrite the audit probe) |
| /tax/preview | tax.py | no | none | YES (stage 1) |
| /tax/review | workflows.py | yes (see above) | none | delete function only |
| /risk/preview | risk.py | no | none | YES (stage 1) |
| /allocation/analyze | allocation.py | no | none | YES (stage 1) |
| /backtest | backtest.py | no | test_backtest::test_backtest_endpoint_and_recommendations_validation (R1 -- it also checks /recommendations) | YES (stage 1) |
| /fees | fees.py | no | test_fee_optimizer::test_fees_endpoint_and_feed_integration (R1 -- keep the /recommendations half) | YES (stage 1) |
| /screener | screener.py | no | test_screener::test_screener_endpoint (D1) | YES (stage 1) |
| /entities | entities.py | **yes: /auth/status** (public, used by nothing in UI but harmless) | test_auth_entities::test_entities_listed... tests the service, not the route (keep) | delete /entities; move /auth/status to auth.py |
| /market/quote, /fx/rate | market.py | **yes: /research/events, /data-status (UI ✅), /providers/health** | **test_the_loop_stays_free_while_a_provider_answers::test_a_slow_quote_does_not_stall_every_other_request races /market/quote (R1)**; test_cash_is_never_sent... mentions it in a docstring only | delete functions only; rewrite the loop test to race a live provider-calling route |
| /adversary/diagnostics | adversary.py | no | test_llm_error_surfacing mentions it in a docstring only | **KEEP but SUPERADMIN-gate** -- scripts/smoke/smoke-all.ps1:274 reads it |
| /jobs, /jobs/monte-carlo, /jobs/simulation, /jobs/{id} | jobs.py | no | test_jobs.py (D4) | YES (stage 1, with Celery) |
| /broker/connect, /broker/sync | broker.py | no | test_broker_sync.py (D5) | YES (stage 1 -- also the C6 fix) |

### Tables

| Table | Live references | Constraint | Delete? |
|---|---|---|---|
| tax_profiles | none in app/ or tests/ | none | YES (migration) |
| buckets | none in app/ code, but **positions.bucket_id FK -> buckets.id (tables.py:127)** and `Entity.buckets` relationship (tables.py:89-90, 117) | must drop positions.bucket_id (a LIVE table) in the same migration | YES, carefully (stage 3) |
| decision_feeds, decision_items, user_actions | decision_feed.py, learning.py, pipeline.py, learning_engine.py; `User.decision_feeds` relationship (tables.py:72-73) cascades on user delete | FK chain items->feeds, actions->items (tables.py:228,273) | YES after stage 2 code is gone; test_persistence::test_persist_feed_with_items (D1), test_learning_engine (already D) |
| broker_connections | broker_sync.py only | none | YES after broker code is gone |
| revoked_tokens (not legacy) | auth_service.rotate_refresh | -- | KEEP (S2 fix will use it more) |

Whether prod has rows in any of these is INFERRED-empty: run `SELECT count(*)` on each against prod (read-only) before the
migration; decision_feeds will be non-empty if /decision-feed/generate was ever called.

### Proposed staged order (each stage = one PR + smoke; nothing live touched until stage 4)

0. **Hazard fixes, no deletion** (can ship today): C6 (unmount /broker or refuse mock), C7b (whatif honours
   demo_signals), S4 gates (/providers/health, /adversary/diagnostics -> SUPERADMIN; /markets/summary -> acting_user),
   S5 (delete API_KEY + false warning). Smoke: anon GETs -> 401; /whatif on prod returns no demo titles.
1. **Leaf routes + Celery + no-op job** (no shared code with live paths): delete routers lag, whs, simulation, safety
   (+ safety_engine), tax, risk, allocation, backtest, fees, screener, jobs (+ worker/celery_app.py, worker/tasks.py,
   celery/redis deps), broker (+ broker_sync, app/brokers/*); delete functions /entities (move /auth/status),
   /market/quote, /fx/rate; delete market_refresh job + market_state.py. Tests: D ~= 5 safety_engine + 1 safety api +
   4 jobs + 5 broker + 1 tel_aviv broker + 1 screener + 1 market_refresh = **18 deleted**; R = observability audit
   probe, loop-stays-free race, backtest endpoint test, fees endpoint test = **4 rewritten**. Smoke: each deleted path
   -> 404; /health-check, /risk/alerts, /scenario, /research/events, /data-status, /whatif, /war-room still 200.
2. **Decision-feed cluster**: decision_feed.py, learning.py, pipeline.py, learning_engine.py, xai_engine.py,
   AllocationAgent, the /decision-feed/weekly and /tax/review functions in workflows.py, demo route. **Same commit:**
   docs/AGENTS.md (remove AllocationAgent section and every `pipeline.py:N` anchor) + roster test (ROSTER dict, the 2
   AllocationAgent veto tests, anchor params). Tests D ~= 3 xai + 2 learning + 1 persistence + 1 allocation-agent +
   1 a_cap agent-veto + 2 roster + 1 api_integration veto + 2 workflows_api = **13 deleted**, plus roster/anchor edits.
   Keep engines/state_machine.py and lag/decision/risk/tax engines (war_room, whatif).
3. **Schema**: one Alembic migration dropping user_actions, decision_items, decision_feeds, broker_connections,
   tax_profiles, then positions.bucket_id + buckets; remove the model classes and the User/Entity relationships.
   Run locally on Postgres (CLAUDE.md) -- this is the stage where test-postgres matters. Pre-check prod row counts.
4. **Optional, touches live code**: retire DEFAULT_OBSERVATIONS entirely (war_room demo_signals path), and decide
   whether engines/state_machine's Detected->Vetted->... lifecycle should stay as the war room's internal model.

- CONFIDENCE: importer lists PROVEN by grep (dynamic imports via strings would not show -- none seen; the roster
  test's importlib walk is the one dynamic consumer and is covered). Test counts are from `def test` names and could
  be off by 1-2 for parametrized tests. Prod table contents INFERRED.
- RISK: stage 1/2 low (no UI callers; the shared router files workflows.py and market.py are the trap -- delete
  functions, not files). Stage 3 medium (migration on a live table, positions).

## Summary

| Item | Verdict | Severity | Effort |
|---|---|---|---|
| C6 broker mock sync | **DEFECT, PROVEN** -- 2 bodyless POSTs with a READ_ONLY token added TEVA/GOLD/BOND (300/60/100) to the book, no ledger row | Medium (corrupts the real book; needs any valid token; no UI caller) | S (unmount, or refuse mock in prod + ANALYST) |
| C7 weekly feed demo fallback | **DEFECT, PROVEN**, narrower than claimed: only when no position yields an observation (empty book). Returns TEVA/GOLD unlabelled. Route unused by UI | Low | S (delete in L1 stage 2) |
| C7b /whatif demo fallback (new) | **DEFECT, PROVEN** -- live UI route, same ungated fallback: empty book -> "Buy TEVA (NYSE)", "Rebalance GOLD (SPOT)", vetoed HYPE; ignores `demo_signals` which the war room honours | Low (only empty/meta-less book) | S |
| C8 /ask unverified LLM text | **DEFECT, PROVEN** -- stubbed reply with 37.4% / 412 and no disclaimer returned verbatim (`llm: true`); digest's `unsupported_numbers` flags both. Same gap in ai_service summaries/research | Medium (violates a CLAUDE.md domain rule on a UI path) | S for /ask, M for all ai_service paths |
| S1 writes at READ_ONLY | **DEFECT, PROVEN** -- 25 of 67 mutating routes accept READ_ONLY, incl. card accept, position PUT/DELETE, rules CRUD, broker sync. But every human session is SUPERADMIN; only a READ_ONLY m2m token would reach this | Low today (latent Medium) | S + route-introspection guard test |
| S2 logout doesn't revoke | **DEFECT, PROVEN** -- bearer and replayed cookie both 200 after /auth/logout; require_role never reads revoked_tokens | Medium-Low (30-day SUPERADMIN cookie; only kill switch is rotating JWT_SECRET) | S-M |
| S3 unsubscribe unscoped | **DEFECT, PROVEN** -- a different subject deleted the victim's subscription (1 -> 0). The subscribe path can also re-own an endpoint | Low (endpoint URLs are unguessable; single user) | S |
| S4 unauthenticated routes | **PARTIAL.** No route returns holdings/NAV/amounts anonymously. Real issues: /providers/health leaks held tickers (`quote:SECRETHOLDING` observed); /markets/summary = uncached Gemini call per anonymous hit; /adversary/diagnostics calls Gemini when enabled; /docs+/openapi.json expose the API map; /market/quote spends provider quota | Low-Medium | S (gate 3 routes, disable docs in prod) |
| S5 API_KEY dead code | **CONFIRMED** -- zero route references; only its own unit test; the "write endpoints are unauthenticated" boot warning is false with REQUIRE_AUTH=true | Low (misleading ops signal) | S |
| S6 no rate limiting | **CONFIRMED** -- only an outbound provider TokenBucket. Main exposure is the anonymous LLM routes (fixed by S4); expensive authenticated routes (frontier refresh/preview, target/cores ~54 s, recommendations 22 s cold) have no single-flight lock | Low (Med-Low for anonymous LLM spend) | S (via S4) to M (slowapi) |
| L1 legacy deletion | **ANALYSIS.** Safe in stages; ~18 tests deleted + 4 rewritten (stage 1), ~13 deleted + roster/AGENTS.md edits (stage 2), 1 migration (stage 3). **Do NOT delete** engines/state_machine.py (or the lag/decision/risk/tax engines): war_room.py and whatif.py use them. workflows.py and market.py hold live UI routes: delete functions, not files. Keep /adversary/diagnostics (smoke-all.ps1 reads it) | n/a | M overall (S-M, M, M per stage) |

Scratch evidence: C:\Users\erang\.claude\jobs\a36f9c6f\tmp\scratch_sec\ -- conftest.py, test_c6_broker.py,
test_c7_c8_s2_s3.py, test_c7b_whatif.py, test_s1_s4_routes.py (+ routes.json), test_s4_public.py, test_s4_leak.py.
Run from the repo: `python -m pytest -q -s -p no:cacheprovider --rootdir=. -c pyproject.toml <file>`.

