# InvestWise Pro — Status

_Last updated: 2026-08-12 by Claude (execution plan, Phases A + B)._
_Seeded from git history + prior transcripts._

## 🚧 PHASE B — #15, thread-pool the provider I/O. COMMITTED, NOT YET RE-MEASURED

Two commits: `fca2718` (resilience, inert) + the offload. 608 passed, ruff
clean. **The after-measurement has not been taken — see "Next" below.**

### The baseline, measured against production first

| | |
|---|---|
| `/plan` alone | 0.38 – 0.52s |
| `/plan` while `/recommendations` was in flight | 2.09 / 2.22 / 3.05s |
| `/recommendations` warm | 3.5 – 7.7s |
| `/portfolio` alone | 0.36s |

Nothing was slow — `/plan` was **queued**, and finished just before the
`/recommendations` call holding the loop in every trial. That is the defect.

**⚠️ The plan's guard rail was already spent.** It said "phase 10 got it from
24.2s to ~1.0s; do not give that back." It was already back: warm is 4–6s.
STATUS contradicted itself (the 08-03 entry says 1.0s; backlog #15 says "~5s
warm"). **The honest number to hold is ~4–6s, not 1.0s.** Phase B does not
improve it — it stops one request being everyone else's problem.

### What changed
- **`app/core/offload.py`** — one `offload()` helper, not `asyncio.to_thread`
  sprinkled per site.
- **Whole agents offloaded, not individual provider calls.** The `guarded_*`
  callers are ordinary sync functions, so awaiting inside them would mean
  turning ten service modules async for the same result.
- Six agents in `build_recommendations`, both blocking calls in the war-room
  route, and both Google OAuth round-trips.
- **`ResilienceTier` is single-flight** (`fca2718`) — the plan said the TTL
  caching would be left as it is, and leaving the *code* as it was would have
  changed the *behaviour*: the cache deduplicated concurrent work only because
  serialization meant request 2 always found request 1's result. Overlapping
  requests would have made it N provider calls per cold key.

### Three corrections to things that were believed
1. **`_gemini_generate`'s docstring was false.** It claimed "runs in a worker
   thread so it never blocks the event loop". It submits to a pool then calls
   `.result()` — a synchronous wait. The pool buys isolation, not concurrency.
   Corrected in place.
2. **The bucket/breaker races are mostly not real.** `CircuitBreaker` lost zero
   of 1.28M concurrent increments across 64 threads. `TokenBucket` *does*
   over-admit (capacity-5 granted 12) but only at ~1000 threads with
   `switchinterval=1e-9`. The locks are insurance; the tests say
   `REGRESSION GUARD` rather than pretending to prove a production bug.
3. **ORM rule for anything passed to `offload`: columns yes, relationships
   never.** Columns are all loaded (`expire_on_commit=False` + `_agent_tx`'s
   savepoint). Relationships are lazy and raise `MissingGreenlet` off-thread —
   proven in `tests/test_offload.py`, which had to `expunge` the Account first
   because SQLAlchemy resolves many-to-one from the identity map with no IO.

### ⚠️ Still to do — the phase is NOT verified
1. **Re-measure after deploy.** Repeat the `/plan`-while-`/recommendations`
   trial. Expect ~0.4s instead of 2.5s. Until then this is unproven live.
2. **`google_auth` is in this commit and touches sign-in.** Chosen deliberately
   (widest blast radius), but it is the one change that can lock you out.
   Test a real Google sign-in after deploy.
3. Warm `/recommendations` should be unchanged (~4–6s). A large *increase*
   would mean thread-pool contention and is the signal to revisit.

## ✅ PHASE A — SHIPPED (2026-08-12). Backlog 16, 17, 18.

593 passed, `ruff check app tests` clean. No app behaviour touched — no engine,
no money path, no route. **Next: Phase B (#15, thread-pool the provider I/O),
which ships alone.**

- **#18 `.gitignore`** — smaller than the plan assumed: `.gitignore` already
  existed and already covered `*.db`, so `investwise.db` was never the problem.
  Added `.claude/` and `scripts/railway-error.log`, the two that actually
  appeared on every `git status`. BOM dropped.
- **#16 lint gate on `tests/`** — the plan said 26 errors; there were **28** by
  the time it ran, which is the argument for a gate rather than a cleanup. 11
  auto-fixable, 17 by hand: 10 `E702` semicolon-joined statements split, 5
  `E741` `l` → `line` (all five are a war-room transcript line), 1 `F841`
  unused `upsert_plan` result, 1 `F811`. Behaviour-preserving throughout — no
  assertion changed. CI is now `ruff check app tests`, and `_common.ps1` was
  updated to match so the local pre-push check can't pass what CI rejects.
- **#17 one `ship-phase.ps1`** — `ship-p0/p1/p2.ps1` deleted. They differed only
  in the file list and the closing checklist. The duplication had already cost
  something: the P2 copy grew a better "nothing staged" message (it names the
  likely cause instead of failing two steps later with "COMMIT_MSG.txt is
  missing") that P0 and P1 never got — that version is the one that survives.
  New `-DryRun` checks the file list without staging. `_common.ps1` stays; 25
  `phaseN-*.ps1` scripts dot-source it.
- Also committed: **`scripts/retire-holding.ps1`**, written for the delisted-COW
  case and never committed. Its hardcoded `IW_AGENT_KEY` fallback was replaced
  with a fail-fast, so backlog #4's blast radius stays at 11 scripts.

**⚠️ `ship-phase.ps1` has never been parsed.** There is no PowerShell in the
sandbox, so it is ASCII-checked and balance-checked only. **First real use
should be `-DryRun`.**

**Two mount findings worth keeping.** The FUSE mount denied `unlink` outright
this session — a stale `.git/index.lock` left by the very first `git status`
then blocked every git write until deletion was explicitly enabled. And
`tests/conftest.py` points at `/tmp/iw_test_app.db`, which a previous session
left owned by `nobody`; `/tmp` is sticky, so conftest's own cleanup cannot
remove it and the whole suite fails with "attempt to write a readonly
database". Set `DATABASE_URL` to a path under `$HOME` to get around it.

## 🔔 FIXED 2026-08-11 — the MSFT notification that arrived every few hours

Reported live: _"I'm still getting this notification every few hours but I have
nothing real (nor important) to do about Microsoft."_ MSFT sat at **20.2% against
a 20% cap it had armed itself** during P1.1.

Three faults, each sufficient on its own:

1. **No hysteresis — I introduced this one in P4.2.** The P4.2 fix cleared the
   latch the moment the condition went false. A position resting on its own
   boundary therefore went latch → push → clear → re-latch → push. The bug
   before it was a rule that never stopped nagging; my fix produced a rule that
   nagged *rhythmically*. Both reach the user as one rule that will not shut up.
   Fixed with a re-arm band: 2.5% of the level, floored at half a point, so a
   20% cap re-arms at 19.5% rather than 19.99%.
2. **`evaluate_user` bypassed every P4.2 rate limit.** It called
   `push_service.send_to_subject` directly with `tag=f"rule:{id}"` — no
   `classify_trigger`, no signature, no `_seen_within`, no ledger row — while
   P4.2 had built exactly that machinery for every other trigger. **Two
   notification paths, one governed and one not: the same shape as the
   duplicated reprice loop that mispriced the cash row on 2026-08-10.** Routed
   through the existing limiter rather than given a second one.
3. **It pushed a firing with no trade behind it.** Backlog #7 had already decided
   a 0.2-point breach is a ~₪43 trim against a ₪250 minimum and made
   `execution_plan` return `None` — so the app declined to propose the trade and
   woke him about it in the same breath. The ₪12-tax-card lesson, unapplied to
   push. Now: RuleEvent yes, card yes, push no.

Plus a floor: `rule_fired` moved from `TRIGGER_IMMEDIATE` (= no limit at all) to
`TRIGGER_RULE_REPEAT = 12h`. Hysteresis stops this flap; the floor means a future
one can interrupt at most twice a day. Per rule id, so a different holding's stop
is unaffected.

**The coupling underneath it.** `triggered_rule_recs` cleared `r.triggered` in the
same line that decided not to render the card. Those are two different questions:
the card must vanish the instant the breach corrects (the screen has to be honest
about *now* — the SOXL fix), but the latch governs *notification*, so clearing it
there re-armed the push on every render of Today. Now separated: card goes
immediately, alert re-arms only on a real move.

New: `tests/test_rule_flap.py` (13). `test_p42_notifications.py`'s
"pushes immediately" assertion was pinning the very behaviour that caused this,
and was rewritten rather than deleted. 593 passed, ruff clean.

## 📋 BACKLOG — everything still open (2026-08-10)

`BEAT_MARKET_NEXT_PLAN.md` is **done**: P0, P1, P2, P3, P4 all shipped. This is
what is left, in the order I would do it.

### ✅ Done (2026-08-10)
1. **Pixel QA across P0-P4** — done.
2. **Notifications on the Pixel + 07:00 digest** — done. P4.2 is now verified end
   to end, cadence included.
3. **`smoke-p0.ps1 -Execute`** — done. The funding write path has run live.
6. **Regime gate on `vol_target`** — decided: leave off. Measured everywhere,
   enabled nowhere. Revisit only if the numbers change.
7. **Sub-minimum trims** — advisory below `MIN_TRADE_ILS`; the card still says
   the cap is breached.
8. **`upsert_positions` cash guard** — the last unguarded door to the cash row.
9. **FMP `timestamp`** — still unexercised in production (Yahoo is primary), so
   the failure mode is loud instead of assumed: quotes with no venue timestamp
   are counted, returned and logged.
10. **Redeploy card vanishing** — concentrates into one leg rather than
    producing no card while cash sits idle.

### Still open, and overdue
4. **Rotate `AGENT_API_KEY`.** Hardcoded in **12 committed smoke scripts**, in
   162 commits of history, and pasted into chat repeatedly. Rotate and edit the
   12 files in ONE change or every smoke breaks. Replace the hardcoded fallback
   with a fail-fast on `$env:IW_AGENT_KEY` rather than a new literal.
5. **Pin `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` in Railway.** DB-generated, so
   a DB reset invalidates every push subscription at once.

---

## 🗺️ EXECUTION PLAN — backlog 12-18

Ordered by what unblocks what, not by size. The one non-obvious dependency:
**#15 must precede #12.**

| # | Value | Effort | Risk | Phase |
|---|---|---|---|---|
| 18 `.gitignore` | low | minutes | none | A |
| 16 lint gate on `tests/` | low | ~1h | none | A |
| 17 one `ship-phase.ps1` | medium | ~2h | none | A |
| 15 thread-pool the provider I/O | high | 1-2 days | medium | B |
| 12 core + N sleeves | **highest** | several sessions | med-high | C |
| 14 regime numbers + per-sleeve toggle | medium | ~half a day | low | D |
| 13 war-room strategy debate | low | 1 day | low | — |

### ✅ Phase A — DONE 2026-08-12 (see the Phase A block at the top)
**18, 16, 17.** All independent, all mechanical, none can break production.

Do them first because they change the *floor* for everything after: #16 means
the next phases cannot introduce test lint (the suite already carries 26
unnoticed errors); #17 means Phase C's many commits use one ship script instead
of a fourth near-copy. This session lost an afternoon to a duplicated reprice
loop — four near-identical ship scripts is the same shape in the tooling.

Ship as one commit. Nothing here touches app behaviour.

### Phase B — make the request path able to carry N (1-2 days, medium risk)
**15 — offload blocking provider I/O to a thread pool.**

**This must come before #12, and that is the whole reason it is Phase B.**
`/recommendations` makes *synchronous* provider calls inside `async def` on a
single uvicorn worker, so one request blocks every other. N sleeves multiplies
those calls — each sleeve needs its own signal evaluation, funding preview and
backtest read. Building #12 first means measuring and fixing the same problem
twice, with more surface the second time.

Method: baseline the warm `/recommendations` latency FIRST (phase 10 got it from
24.2s to ~1.0s; do not give that back), wrap provider calls in
`asyncio.to_thread`, re-measure. The existing TTL caching (quotes 15s, history
1h, fundamentals 6h) stays exactly as it is — this is about not blocking the
loop, not about fetching less.

Risk is medium because it touches every request path, so it ships alone.

### Phase C — core + N sleeves (several sessions)
**12.** Scoped in full above. Sub-phase it the way P3 worked, inert first:

- **C1** `plan_sleeves` table + migration + startup self-heal + backfill the
  existing single strategy. Read-only endpoint. **No behaviour change** — this
  can land and sit inert, like `regime.py` did.
- **C2** add/update/remove a sleeve; `_arm_sleeve_cap` sums per ticker.
- **C3** funding with the wider exclusion set (sleeve A must never sell B).
- **C4** signals, discipline rules and the drift/cold-start cards, per sleeve.
- **C5** the Plan UI: a sleeve list replacing the single-strategy banner.

Settle the core question (strategy row vs implicit remainder) before C1 — it
decides the schema.

### Phase D — finish the regime story (half a day)
**14.** Render the gated-vs-ungated numbers on the card, plus the toggle.

Deliberately AFTER Phase C: the toggle is per-strategy state, which under N
sleeves means per-sleeve state. Doing it first means building it twice, and C5
rewrites that card anyway. `smoke-p3.ps1` prints the table meanwhile, which is
enough to make the enable decision.

### Not scheduled
**13 — war-room strategy debate.** Honestly assessed: low value, and it makes the
slowest agent slower (5.4s cold, worse warm from the per-signal LLM call). The
war room having no concept of a strategy is structural, not a defect. Recommend
leaving it unscheduled unless it turns out to be something you actually want.

### Product, from the plan's own backlog
11. **Per-ticker allocation targets** (P1.1 option b) — only if the max-weight
    cap proves insufficient. Touches the rebalancing every family depends on.
12. **Core + N sleeves — NEXT SESSION, scoped below.**

    **Goal.** Run one core plus any number of sleeves, each with its own share
    and its own rule. E.g. 65% core, 20% `btm_trend_tqqq`, 15% `btm_factor_stack`.
    Not limited to core + 1.

    **Where it stands today.** `plans.strategy VARCHAR(40)` +
    `plans.strategy_sleeve_pct FLOAT` — exactly one strategy. Everything else in
    the book is governed by the plan's *objective* (Grow → 80/10/10), the
    concentration cap and the cash floor. Applying a Beat the Market strategy
    also overwrites objective and risk tolerance for the **whole** book, which is
    why the app currently looks like it has two strategies when it has one.

    **First decision, before any code:** is the core a *strategy row* (a static
    family like `bal_6040`, so sleeves sum to 100% and the objective only sets
    guardrails), or is it *implicit* (the remainder stays objective-managed, as
    today)? The first is cleaner and a bigger change; the second is closer to
    what exists. Everything below assumes a `plan_sleeves` table either way.

    **Schema.** New `plan_sleeves`: `(id, subject, strategy_id, sleeve_pct,
    is_core, created_at)`. Migration 0015 **plus** the startup self-heal DDL in
    `main.py` — hand-running alembic against this deploy has failed twice.
    Backfill the existing `plans.strategy` / `strategy_sleeve_pct` into one row
    so no one's applied strategy is lost. Keep the old columns readable for one
    release rather than dropping them in the same change.

    **Invariants to hold, each of which is a test:**
    - Sleeve percentages sum to ≤ 100. Over-allocating must abstain with a
      reason, the way `_fund_sleeve` already does when it cannot fund.
    - **One cap per ticker = the SUM of that ticker's target weights across
      sleeves.** Two sleeves both wanting TQQQ must not arm two competing
      `max_weight` rules — that is the P1 duplicate bug, back at N scale.
    - **Funding sleeve A must never sell sleeve B.** `_fund_sleeve` currently
      excludes only its own tickers from the trim candidates; it must exclude
      *every* sleeve's.
    - Each sleeve's signal is independent — a flip in one must not touch another.

    **What already supports N, unchanged:**
    - `StrategySignalState` is keyed `(subject, strategy_id)`.
    - `strategy_signal` rules pin `strategy_id` (P4.1).
    - `sleeve_targets(strategy_id, pct)` is pure and composes by merging dicts.

    **What has to change:** `apply_strategy` (add/update/remove a sleeve rather
    than overwrite one), `_arm_sleeve_cap` (sum per ticker), `_fund_sleeve` and
    `load_basket` (per-sleeve or whole-plan, wider exclusion set),
    `active_strategy_id` → returns a list, `discipline_recs` / `signal_rules`
    (per sleeve), the sleeve-drift and cold-start cards (per sleeve), and the
    Plan banner added on 2026-08-10, which currently assumes exactly one.

    **Also settle:** with N sleeves, which one sets the plan's objective and risk
    tolerance? Today the single applied strategy does. Candidates: the core does,
    or they become independent of sleeves entirely. Leaving it implicit is how
    the current confusion started.

13. **War-room strategy debate.** Structural absence, not a bug — the war room
    has no concept of a strategy. Also the slowest agent; would need
    `narrate=False`.
14. **Render the gated-vs-ungated regime numbers on the Plan card**, with a
    per-strategy gate toggle. The data already reaches the client under
    `backtest.robustness.regime`; only `smoke-p3.ps1` prints it.

### Engineering hygiene
15. **Offload blocking provider I/O to a thread pool.** `/recommendations` makes
    synchronous calls inside `async def` on a single uvicorn worker, so one
    request blocks every other. Shaving latency further is the wrong fix.
16. **Widen the lint gate to `tests/`.** CI runs `ruff check app` only; `tests/`
    carries 26 pre-existing errors, 10 auto-fixable.
17. **Fold `ship-p0/p1/p2.ps1` into one `ship-phase.ps1 -Files ...`.** Four
    near-identical scripts now. This session lost an afternoon to a *duplicated
    reprice loop*; the same lesson applies to tooling.
18. **`.gitignore`** `investwise.db`, `.claude/`, `scripts/railway-error.log` —
    noise on every `git status`, and noise is where a real modified file hides.

### The next initiative
19. **Broker integration** — `BROKER_INTEGRATION_PLAN.md`, unstarted and carried
    as "next" for over three weeks. Worth deciding whether it is actually next
    or should be dropped from the top of the list.

## ✅ P4 — COMPLETE (all three parts), tests green, NOT YET SHIPPED (2026-08-10)

Entry/exit rules, the sleeve-drift card, and notifications. **Full suite 570
passed, ruff clean.** This finishes `BEAT_MARKET_NEXT_PLAN.md`.

**Decisions taken this session — do not re-litigate:**

| # | Decision |
|---|---|
| Rule shape | **One strategy-linked rule**, not discrete `ma_above`/`rsi_below` types. Those would put a second copy of SMA/RSI/Donchian in `rules_service` beside the engine's — the duplication that caused the reprice-loop and regime bugs. A test asserts `rules_service` contains no indicator maths. |
| Stats | Rules carry the strategy's **measured** win rate / avg hold / expectancy. No backtest → no rule offered. |
| Entry sizing | Exits execute a full exit; **entries stay advisory** — size is a funding decision. |
| Drift Accept | **Executes when the sleeve is held; refuses a cold start** and routes to "Fund this sleeve". |
| Push limits | **Per trigger, not per message.** Card ids are content hashes, so an id-keyed dedupe lets a re-rounded number push again. |
| `rules_available` | **`None`, not a long window.** A card that must never push live should be *impossible* to push. Digest only. |

**Rules on positions no longer held are now retired** (`active = False`, history
kept). Previously only a rule that had already latched `triggered` was retired,
so TQQQ/META/AMZN each carried up to four "armed" rules for positions not in the
book. A stale AMZN stop at 222.58 would have gone live the instant the position
was re-bought — more dangerous than no stop.

**Still open:** the Pixel pass. Four of P4's checks are phone-only, and three of
them are the notification cadence — the one thing no HTTP call can verify.
`smoke-p4.ps1` lists them.

### Three bugs this batch that only the live screen could show
Worth recording, because 570 green tests found none of them:

1. **Tax harvest offered to sell the strategy sleeve** — SOXL, with a cap armed
   on it to keep it, for a ₪12 saving. Two agents, opposite instructions, same
   position, same screen.
2. **A ₪12 saving was severity CRITICAL** — the level reserved for a firing
   stop-loss — so it sorted above everything with an "Important" badge.
3. **A cap that corrected itself kept nagging** — and `execution_plan` correctly
   returned `None`, so it was guidance with nothing to press. A nag no user
   action could clear.

All three were agents individually correct, disagreeing with each other. A green
suite says every agent works; only the rendered page shows whether they agree.

## ✅ P3 — SHIPPED AND VERIFIED LIVE (2026-08-10)

`smoke-p3.ps1 -Refresh`: **11 passed, 0 failed, 0 skipped.** All seven strategies
recomputed both ways against ten years of real closes.

### The answer: the gate does not earn its place on any strategy

| strategy | CAGR | max DD | verdict |
|---|---|---|---|
| btm_vol_target_tqqq | −1.71 | **−8.32** | cost 1.7%/yr — just over the limit |
| btm_dual_momentum | −2.68 | **−8.88** | cost 2.7%/yr |
| btm_swing_breakout | −3.07 | **−11.57** | cost 3.1%/yr |
| btm_trend_tqqq | −3.01 | −1.69 | cost 3.0%/yr for almost no shelter |
| btm_trend_soxl | −5.67 | −1.06 | cost 5.7%/yr for almost no shelter |
| btm_swing_dip | −0.08 | 0.00 | the gate barely touches it |
| btm_factor_stack | −5.85 | 0.00 | pure cost, zero drawdown benefit |

**Exactly as the tests predicted.** The two trend strategies already gate on a
200-day average, so the regime proxy agrees with them and adds cost without
shelter. The factor stack has no timing rule to agree with, so the gate is pure
drag. That is a real result, not a failure.

**But three of these deserve a human look, and that is why the gate ships off.**
`vol_target` buys **8.3 points of drawdown for 1.71%/yr**, `dual_momentum` 8.9
for 2.68%/yr, `swing_breakout` 11.6 for 3.07%/yr. The −1%/yr limit is a
threshold *chosen*, not discovered — on a 3x leveraged sleeve, 8 points of
drawdown for under 2%/yr is arguably a trade worth taking. The criterion did its
job mechanically; whether `vol_target` in particular should be switched on is
Eran's call, and the numbers to make it are now on the table.

### Cross-check working
Price-derived regime read `risk_on` (trend up, vol 14.4% at the 65th percentile,
breadth 1.0) while the futures cross-check **disagreed** — and the response said
so rather than showing two numbers and leaving it to be noticed. Markets and the
live signal returned the same state, confirming one function feeds both.

### What it is
`app/engines/regime.py`, the gate in `strategy_backtest`, the live read in
`strategy_signal_service`, and the Markets cross-check. **26 P3 tests green**,
full suite **534 passed / 0 failed**, ruff clean.

**Decisions taken this session — do not re-litigate:**

| # | Decision |
|---|---|
| Gate enablement | **Measure both, ship OFF.** Auto-enabling whatever measured better would be selecting on one sample of history — exactly what the overfitting flag exists to catch. |
| "Improves" | **Shallower max drawdown at no worse than −1%/yr CAGR.** CAGR-alone rejects a filter doing its job; drawdown-alone blesses one that refuses to invest. |
| Volatility input | **Realized vol from SPY closes**, not `^VIX`. No new provider, and computable identically in both paths — the whole constraint. |

**Not done:** the Plan card does not *render* the gated-vs-ungated numbers. The
data reaches the client under `backtest.robustness.regime`; `smoke-p3.ps1` prints
the table instead, which is enough to make the enable decision. A small
P2-shaped follow-up — and the natural place to add a per-strategy toggle.

## ✅ P2 — SHIPPED AND VERIFIED LIVE (2026-08-10)

Presentation only — no engine, no money path. Shipped as `7792a06`.

- **Style + Horizon chips** on the measured cards. Horizon was already in the
  catalog and simply never rendered; Style is derived from the basket exactly as
  the static families derive theirs. The derived *return* from the same call is
  deliberately not carried — "Backtested" and "Est. return" are different claims,
  and a number in the payload is a number something eventually renders. A test
  asserts the absence, not just the label.
- **A real classifier gap, found by that test failing** (`1 failed, 506 passed`
  on the first run). `MTUM`, `QUAL` and `AVUV` were in no lookup bucket, so
  `_character` fell through to `single_name`: the Factor Stack was modelled at a
  single stock's 32% volatility and labelled "Concentrated", exactly like a 100%
  TQQQ sleeve. Fixed with a `factor_equity` character, **and** by making the
  concentration rule ask what the big line actually holds — it was
  `top_weight >= 0.30`, which called any three-line basket concentrated; it now
  looks at the largest weight held in a single name or a geared fund.
  This reaches past the chip: `assumptions_for` feeds `compute_snapshot`'s
  volatility fallback (phase 9), so anyone *holding* a factor ETF was being
  risk-scored as if they held one company. **No ticker in the current book is
  affected and no static family holds these**, so no shipped card's numbers move.
- **Goal tab row** — `.stgoal` was `flex-wrap:wrap`, so the fifth tab dropped to
  its own line and read as a separate control. Now scrolls sideways.
- **`.rk` `white-space:nowrap`** — "VERY HIGH RISK" no longer breaks in two.

`smoke-p2.ps1` deliberately does **not** assert the two CSS fixes: they are
rendering, HTTP cannot see rendering, and asserting anyway would be asserting a
guess. They are Pixel-only checks.

SW `iw-v15` → **`iw-v16`**.

Cleanup worth doing: `ship-p0/p1/p2.ps1` are now three near-identical scripts.
Given this session lost an afternoon to a *duplicated reprice loop*, folding them
into one `ship-phase.ps1 -Files ...` is the same lesson applied to tooling.

## ✅ P1 — SHIPPED AND VERIFIED LIVE (2026-08-10)

`smoke-p1.ps1`: **8 passed, 0 failed, 3 skipped.** The headline, straight from
production:

```
PASS  20% and 90% produce different plans (20% vs 90%)
PASS  the cap equals the sleeve you asked for (20%)
PASS  preview names what it would buy: TQQQ ~4281
PASS  every funding leg names ticker, shares, est. CGT and why
PASS  the discipline card no longer offers a competing cap
```

That first line is the entire point of P1 — applying at 20% and at 90% used to
write an identical plan. It no longer does. The last line confirms the old
`sleeve x 1.5` suggestion is really gone rather than merely unreferenced.

Combined chain: **74 passed, 1 failed, 8 skipped** across P1 + P0 + e2e.
The one failure is inside `smoke-all` and is **not yet identified** — see
"Next". Historically the single failing smoke-all check was `subscriptions: 0`
(a Pixel device action, not a defect), but that is a hypothesis, not a finding.

Two of P1's three skips were a **bug in the smoke script, not the app**:
`MaxWeightRules` returned `@()` when no cap was armed, and PowerShell unrolls a
returned empty array into `$null` — indistinguishable from a failed call, so
"no cap armed yet" printed as "/rules unreachable". Fixed with a leading comma
(`return ,@(...)`), which stops the unroll. The remaining skip is honest:
`-Apply` was not run, so the arming path is still unproven live.

What it does (decisions taken this session, do not re-litigate):

- **P1.1** — applying a rule-based strategy arms a `max_weight` on the aggressive
  ticker **at the sleeve size**. Idempotent: a second apply re-levels rather than
  stacking. A 0% sleeve arms nothing (a 0 cap fires the instant it exists).
  **Decided: this replaces the old suggestion** — `discipline_rules` no longer
  emits a `max_weight` at `sleeve_pct × 1.5`, because 30% is not the number a
  slider set to 20% shows, and two caps on one ticker is what the Rules UI
  flags as a duplicate. `discipline_rules` lost its `holdings` argument with it.
- **P1.2** — "What changes?" shows the **funding plan** (what to sell, for how
  much, est. CGT, what gets bought) plus the cap that applying would arm. It
  calls `load_basket(mode="fund", dry_run=True)`, so there is one sizing
  implementation and the preview cannot drift from the button.

Still unproven after the live smoke:

1. **The arming path itself.** Every P1.1 check that needs a cap to exist was
   skipped, because `-Apply` writes the plan and the run was read-only. Run
   `.\scripts\smoke\smoke-p1.ps1 -Apply` — low risk, it re-applies the strategy
   that is already active. Until then, "the cap is armed at the sleeve size" is
   proven only by unit test, not in production.
2. **`_arm_sleeve_cap` re-levels a max_weight the user set by hand**, not just
   one it armed itself. Deliberate (one cap per ticker) and the response reports
   `previous_level`, but it is a write to something the user chose.
3. **The preview now does provider work** — `dry_run` funding prices the sleeve,
   and `/strategies/{id}/preview` used to be cheap. It answered fine in the smoke,
   but its latency has not been measured. Phase 10's history says watch this.
4. **The Plan tab UI is unverified by a human.** `index.html` was edited with the
   sandbox down, so no `node --check` ran; the smoke exercises the API, not the
   rendering. Covered by the Pixel QA pass.

## ✅ P0 SAFETY BATCH — SHIPPED AND VERIFIED LIVE (2026-08-10)

Two commits: the batch itself, then `116a439` for the reprice-path incident found
by running the smoke against production. **`smoke-p0.ps1`: 13 passed, 0 failed,
3 skipped.**

The evidence that P0.3 actually works, from the live refresh:

```
updated: 4          (was 6 - cash is skipped, COW is refused)
stale: 1
stale_tickers: COW @ 2025-06-11T13:56:15, trading_days 303
skipped_cash: 1     repaired_cash: 0
COW: stale=True, not_written=True        <- the frozen price was NOT written
```

and from `/portfolio`: `stale_positions (1)`, `stale_value_ils 2109` — COW still
counted in NAV, flagged, and surfacing on Today as **guidance** with no
executable apply. Every other holding reads `freshness=fresh`.

The three skips are all honest and none hides a defect:
`-Execute` not run (read-only by design); no strategy is currently in the
kept-metrics state, so P0.2's path cannot be exercised; and every rule type on
every holding is already armed, so P0.4's card had nothing to render. To
exercise the last one: delete one rule in Holdings > Rules and reload Today.

Still open from P0: the Pixel 9 pass (`qa/QA-2026-08-10-p0-safety.md`) and one
`-Execute` run to prove the write path.

## ✅ LIVE DATA INCIDENT — RESOLVED same session (2026-08-10)

**Repaired.** `POST /portfolio/cash {"amount_ils": 642.19, "mode": "set"}` →
`nav_ils 21,406.18`, `cash_ils 642.19`, CASH row back to `current_price 1.0 /
ILS`. **`gain_pct` came back at exactly 7.03%**, the same figure the pre-incident
smoke reported, which is the evidence that nothing beyond the cash row was
touched. Only `current_price` and `meta` had been overwritten; `quantity` — the
true shekel balance — was intact throughout.

Kept below because the *cause* is a class of bug this repo has now hit twice.

**What happened: the production CASH row was corrupted and NAV inflated ~9x.**

Cause: `POST /api/v1/portfolio/refresh-prices` kept its **own copy** of the
reprice loop, separate from `pricing_service.refresh_all_positions`. That copy
had neither the cash guard nor the freshness check — so it quoted the synthetic
`CASH` row against the real NASDAQ ticker CASH (Pathward Financial, ~$73) and
stamped `price_currency: USD`. This is **exactly the phase-1 bug** (₪1,934 →
₪521,904), which was only ever fixed in the scheduled job. Two implementations of
one job, so the fix landed on whichever one you happened to call.

Observed: the P0 smoke priced a 20% sleeve at **₪37,650** (NAV ≈ ₪188k) where the
run 20 minutes earlier said **₪4,281** (NAV ≈ ₪21.4k). The refresh reported
`updated: 6` — six positions, and the book only holds five plus cash.

Observed on the row itself: `quantity 642.19, current_price 86.85, currency USD`
— ₪642 of shekels valued as 642 shares of a US bank stock, then FX-multiplied
again. 642.19 x 86.85 x FX = the ~₪167k of phantom NAV.

**The lesson, which is bigger than the bug:** a guard is only as good as the
number of code paths that call it. Phase 1 fixed cash-as-a-stock in
`refresh_all_positions` and *stopped there* — nobody checked whether anything
else repriced. Both duplicates have to be found, or the fix is conditional on
which button the user presses. Same shape as the P0.3 finding itself.

Fixed in the tree: the endpoint now delegates to `refresh_all_positions`
(cash guard + freshness + self-heal) instead of carrying its own loop, and
`tests/test_p0_safety.py::test_the_manual_refresh_endpoint_shares_the_guarded_implementation`
locks it down. Once deployed, any refresh also self-heals a corrupted row via
`repair_cash_row`.

Still worth doing: **grep for any other place that writes `current_price`** —
broker sync, CSV intake, `add_holding`, `update_position`. This audit has not
been done.

## P0 — what shipped, and the bug the suite caught

Suite was `494 passed`, `ruff check app` clean, before each push.
Note for future runs: CI lints **`app` only** (`.github/workflows/ci.yml`), not
`tests` — `ruff check app tests` surfaces 26 pre-existing errors in test files
that have never been gated. Worth cleaning up and widening the gate, separately.

**The bug the suite caught — worth remembering.** `_fund_sleeve` gated its
abstention on `shortfall_ils >= MIN_TRADE_ILS`. On a ₪1,000 book with no cash and
a 90% sleeve, the funding engine raises ₪700 and stops (the ₪200 remainder is
under the minimum worthwhile trade) — ₪200 slid under the gate and a **90% sleeve
quietly installed itself at 70%**, which is exactly the "partial execution" the
plan forbids. Shekels were the wrong unit: a sleeve is chosen in *points of NAV*,
so the shortfall has to be judged there too. Now `SLEEVE_SHORTFALL_TOLERANCE_PCT
= 1.0` — abstain when the achievable sleeve is a full point of NAV below the
chosen one (below that, the integer percentage the user was looking at cannot
change). The refusal now names the sleeve that *would* work
(`achievable_sleeve_pct`), so it is actionable rather than just "no".

Files changed across the two P0 commits:

| File | Phase | What changed |
|---|---|---|
| `app/services/strategy_service.py` | P0.1 | rewritten: `load_basket(mode="fund"\|"replace", dry_run=…)` + `sleeve_targets`, `_fund_sleeve`, `_execute_funded_sleeve`, `_replace_book`, `SLEEVE_SHORTFALL_TOLERANCE_PCT` |
| `app/api/routes/strategy.py` | P0.1 | `LoadBasketRequest` gains `mode` + `dry_run` |
| `app/static_app/index.html` | P0.1/0.2/0.3 | "⚡ Fund this sleeve" vs "↻ Replace book with this basket"; `fundStratSleeve`/`…Confirm`; replace-confirm lists every position + value; `measuredProfile` keeps chips through a failed refresh; Holdings badges a frozen price |
| `app/schemas/market.py` | P0.3 | `Quote.as_of_source` ("market" \| "request") |
| `app/providers/live.py` | P0.3 | Yahoo + FMP stamp `as_of_source`; FMP now reads its real `timestamp` instead of `_now()` |
| `app/services/pricing_service.py` | P0.3 | `STALE_AFTER_TRADING_DAYS=5`, `trading_days_between`, `quote_freshness`; a stale quote no longer overwrites the price |
| `app/api/routes/intake.py` | P0.3 | `/portfolio` per-position `price_stale`/`price_as_of`/`price_freshness` + top-level `stale_positions` / `stale_value_ils` |
| `app/services/recommendations.py` | P0.3/0.4 | stale-price Today card (guidance, `apply` absent); one "N protective rules ready to arm" card wired to `create_rules` |
| `app/static_app/sw.js` | — | cache `iw-v13` → **`iw-v14`** (index.html changed) |
| `tests/test_p0_safety.py` | new | freshness states, weekend safety, stale/fresh/cross-check refresh, sleeve targets, fund-preserves-book, dry-run-writes-nothing, unfundable-abstains (+ the sub-one-point tolerance), replace-still-replaces |
| `tests/test_p0_today_cards.py` | new | suggestions card is one card + arms real rules + stops returning; stale card is guidance; NAV names its untrusted part |
| `scripts/smoke/smoke-p0.ps1` | new | P0's own checks, read-only by default (`-Execute` to really fund), then chains `smoke-e2e.ps1` into one combined verdict |

**Still unverified after the live smoke:**

1. **FMP `timestamp`** — ✅ moot in practice, ⚠️ still untested. The live refresh reported `source: yahoo` for all four updates, so **production is running the Yahoo path**, and the cross-check never had to fire. The FMP `timestamp` field name (taken from the docs, never observed) remains unverified — it only matters if the primary provider is switched back to FMP.
2. **Cash arithmetic in `_execute_funded_sleeve`** — `cash_new = cash − from_cash + leftover` is exercised by the happy path; the **unspent-leg branch** (an unpriceable ticker leaves money over) is not covered by any test.
3. **`upsert_positions` overwrites `meta` wholesale** for an existing row, so buying into a ticker that carried `price_stale` would drop the flag. Funding blends into the existing row directly instead of upserting, so this *should* be avoided — but there's no test proving it.
4. **`tests/test_p0_today_cards.py` matches cards by title**, because `_rid` returns a content hash. Edit the card wording and those tests go red for the wrong reason.
5. **`/recommendations` latency** — `suggest_rules_for_holdings` fetches 200 days per ticker, but `_momentum_recs` already does exactly that against a 1h cache, so it should be warm. Confirm the warm call is still ~1s (phase 10 got it from 24.2s to ~1.0s; don't give that back).
6. **No UI change for the abstention** — `achievable_sleeve_pct` is returned by the API but the Plan page just renders the `reason` string, which already contains "Lower the sleeve to about N%". Wiring a one-tap "set it to N%" button is a small, obvious follow-up.

Deploy: `ship-it` (commit with `git commit -F COMMIT_MSG.txt`, never a here-string;
never `git add -A`). P0 ships **alone** — the plan is explicit that nothing else
goes in this deploy. Then run `.\scripts\smoke\smoke-p0.ps1`.

## Now (on main, CI green)
- **Alignment plan Phases 0–5 COMPLETE and committed** (`defe8ec`…`9e2cd01` = HEAD, all 2026-07-18; CI fully green incl. gated ruff lint): first-class **cash** (set/adjust API + Holdings UI + Cash slice in mix); **funding engine** — every buy card sized and funded (cash first → worst-fit holdings, per-objective cash floor), executable `buy_funded`, "How it's funded" legs on Today; **grounded war-room signals** unified with Today (only `grounded` + DISPLAYED promote; demo signals opt-in); **Done ≠ Ignore** (separate completed/ignored buckets + restore); **Accept honesty** (actionable vs guidance cards, no fake "applied"); `_reconcile` pass kills contradictory cards; stale-PWA-shell fix (no-cache shell + network-first SW); **strategy profiles** (risk/return/concentration chips + "What changes?" before/after preview). *(Shipped at SW `iw-v10`; the app has since moved to `iw-v11`.)*
- **Trading rules engine**: stop-loss / take-profit / trailing-stop / price alerts / buy-the-dip / max-weight, each raising alerts + to-dos, with a management UI. Triggered rules surface in the daily digest and as a Today-screen alert banner.
- **Actionable Accept**: the Today-view "Accept" on a recommendation now really executes it — sells credit net-of-CGT proceeds to a visible CASH holding, fee-swaps replace the fund at a live price, trims credit the sold portion; Accept returns a "what changed" summary the UI shows.
- **Actionable trend cards + suggested rules**: the momentum downtrend/uptrend cards (previously `apply: none`, so Accept did nothing) now arm a concrete, one-click discipline rule — downtrend → stop-loss at a volatility-derived price; uptrend → trailing stop (+ a max-weight cap on already-large positions) — which becomes a real alert + Today to-do; Accept returns an "Armed …" summary. New per-holding **Suggested rules** panel (`GET /api/v1/rules/suggestions`) proposes a stop-loss / trailing-stop / take-profit / max-weight set with concrete, vol-derived levels; add each or "arm all".
- **Price freshness**: scheduled 30-min auto-reprice of all holdings (FMP → Yahoo fallback) with a truthful data-source/status label (no more silently-stale prices).
- **Markets + AI layer**: Yahoo futures with a risk-on/off regime feeding the agents; Gemini portfolio / holding / macro summaries + grounded deep-research-per-holding; new Markets tab and AI cards.
- **PWA + push**: installable app (manifest, service worker, mobile bottom-nav); web push for recommendations, risk alerts, price moves and daily digest; server-side recommendation dismissals with 7-day TTL so push and Today stay in sync.
- ILS currency normalization across valuation + display; goal target, projections, and allocation mix all derived from live FX-normalized NAV (kept aligned).
- Opportunity screener agent + fundamentals layer + expanded holdings recommendations; expanded commodity catalog (advisor reasons grounded, never invents numbers, "Not financial advice").
- Legacy "Advanced" dashboard restored behind the auth gate.
- Test suite ≈259 passing as of 07-14, plus ~75 alignment-batch tests (Phases 0–5: cash 7, honesty 5, reconcile/shell 10, signals+done 27, funding 17, profiles 9); lint (ruff, now gated) + test + test-postgres CI jobs green.
- HEAD `86760ff` (2026-08-03). SW at **`iw-v11`**. Working tree clean.

## Last shipped — bug batch phases 1–11, ALL COMMITTED (2026-08-02 → 08-03)
✅ **The whole batch is on `main`** — 11 commits `d42f38c`…`86760ff` (HEAD, 2026-08-03), and **verified live** by `smoke-all.ps1` (29/30, see below). This section was carried as "Uncommitted / not yet tested" last week; that flag is now resolved. SW at `iw-v11`.

| Phase | Commit | |
|---|---|---|
| 1 cash-as-stock | `d42f38c` | 2026-08-02 |
| 2 rules execute + logged | `5ed4973` | 2026-08-02 |
| 3 health-score ceiling | `e75de4e` | 2026-08-02 |
| 4–5 push silent failure | `8eeba7c`, `9eeba42` | 2026-08-02 |
| 6 rules clear / cash untradeable | `108cdbf` | 2026-08-02 |
| 8 banner deadlock | `4f23ffb` | 2026-08-03 |
| 9 missing metadata | `3bc28d7` | 2026-08-03 |
| 10 `/recommendations` latency | `5d9d779` | 2026-08-03 |
| 11 `pool_pre_ping` | `c9894c6` | 2026-08-03 |
| smoke fix (`$h`/`$H`) | `86760ff` | 2026-08-03 |

What each phase did:
1. **Cash quoted as a stock (worst one).** `refresh_all_positions` quoted the synthetic `CASH` row against the real NASDAQ ticker CASH (Pathward Financial ~$73) and stamped `price_currency: USD`; FX doubled it again → ₪1,934.52 shown as ₪521,904, and a fictional +2153% portfolio gain. Guard + idempotent self-heal (`is_cash_position`, `repair_cash_row`).
2. **Rules now execute + are logged.** `triggered_rule_recs` had no `apply` key at all, so every firing was guidance-only. New `execution_plan` (stops/take-profit → full exit; max-weight → exact trim; price alerts/buy-dip stay advisory), new `sell_position` apply-kind, new `RuleEvent` table + `GET /rules/events`. One-tap, never automatic; tracked book only, stated on the card.
3. **Health score ceiling removed.** `thematic=60.0` hardcoded at 15% weight (invisible, capped everything at 94), tax base 85, and risk = `100 − vol×2` (only 100 at zero volatility). Now four measured components 30/25/25/20, tax tops at 100, risk scored against the plan's own volatility budget. Legacy `/whs` endpoint unchanged.
4. **Notification outage — ROOT CAUSE FOUND (2026-08-02, from live `/push/status`).** The dedupe ledger's newest entry is `2026-07-18T18:47` — the exact day the alignment batch shipped (SW `iw-v9`→`iw-v10`). Replacing the service worker dropped the browser push subscription; the server pruned the dead row (`subscriptions: 0`); and `initNotifyState` only re-registered when a subscription *already existed* — with permission still `granted` and `sub === null` it fell through to `_setNotifyState("off")` and never re-subscribed. The app looked switched off rather than broken, so the outage was permanent and invisible. Fixed in phase 5: re-create the subscription when permission is granted but it's missing. (Scheduler/403/watchdog hardening below was real but was *not* the cause.)
5. **Push silent-failure modes.** 403 added to `DEAD_CODES` (a rotated VAPID key meant permanent silence with no pruning), misfire grace raised from APScheduler's 1 s default, job watchdog so a hung run can't wedge `max_instances=1` forever. New `GET /api/v1/push/status`.

6. **Phase 6 — triggered rules clear; cash isn't tradeable.** Reported after phase 2 went live ("4 trading rules triggered: CASH, MSFT, META, META — I already took actions"). (a) `triggered` latched True and only price alerts reset it, so acting never cleared the rule and the banner counted finished work. `resolve_rule` now stamps the event *and* clears the flag — one-shot exits (stop/take-profit/trailing/dip) are consumed, standing conditions (max-weight, price alerts) re-arm; wired into apply + dismiss + complete. (b) CASH sat in the rule position index, so the suggester offered stops on a cash balance; the phase 1 repair reset that row from ~72.9 to its true 1.0, which those stops read as a 98% crash and fired. Cash excluded, and rules whose ticker is no longer a tradeable holding are retired rather than skipped.

7. **Phase 8 — banner deadlock.** A rule can latch `triggered` while its card is independently hidden by a 7-day dismissal, so the banner counted work with nothing left to click, unresolvable by any user action. `build_recommendations` now resolves any triggered rule whose card is in the dismissed/completed set. Self-heals legacy state on the next Today load.
8. **Phase 9 — missing metadata no longer defaults to zero.** Confirmed live: every equity (COW, MSFT, AMZN, V, SCHD) has **both** `expected_return_pct` and `volatility_pct` blank; only CASH carries values. `_portfolio_stats` did `or 0.0`, so the app reported "~0%/yr vs your 10% target — behind" for a book that had gained 7.79% (expected vs realized are different quantities, but ~0%/yr was still an artefact). `compute_snapshot` had the same hole with a flat 15% volatility, so the Risk score was a placeholder too. Both now fall back to `strategy_profile.assumptions_for()` — the existing per-instrument-character table, already labelled planning assumptions. **Result: score 78 → 92, risk 87 from a real 21.8% vol, expected ROI 7.47%/yr.** Also: redeploy weights derived from rows rather than `snap["exposure_ticker"]`, and the redeploy card now displaces the objective rebalance + any competing sized buy (Today had shown three cards spending the same cash).
9. **Phase 10 — `/recommendations` latency.** Measured: plan 0.2s, portfolio 0.1s, adversary/diagnostics 0.8s, **recommendations 24.2s**. Not the LLM (diagnostics makes a real Gemini call in 0.8s), not rate limiting (bucket raises, doesn't sleep; 20/sec vs 18 calls), not retry backoff (`base_delay=0`). It's provider fan-out: ~18 sequential calls per request — fundamentals per ticker for holding verdicts, *the same fundamentals again* for sector hedging, plus 200 days of bars each. All three shared a 15s TTL, shorter than the request itself, so nothing was ever warm. Split: quotes 15s, history 1h, fundamentals 6h. First request after a restart still pays (in-process cache); prewarming in the 30-min price job is the next step if that proves painful.

## Verified live (2026-08-03, `smoke-all.ps1` — 29 passed / 1 failed / 0 skipped)
All five originally-reported issues confirmed fixed against production: cash card == modal (₪6,473 both), gain 7.79% (was +2153%), health 92 with every component measured, rules execute and clear, audit trail stamped. The single failure is `subscriptions: 0` — a device-side action (enable notifications on the Pixel), not a code defect.

**Re-verified after phases 12–13 (2026-08-03, 24 passed / 0 failed / 1 skipped):** notifications live (1 subscription, push arriving on the Pixel), AI features working, banner matches cards, audit trail carries executed outcomes. **`/recommendations` warm call 6.7s → 1.0s** after skipping the war-room LLM narrative on the Today path.

### Known limitation — redeploy card goes silent when every leg is sub-minimum
Phase 13's second pass reallocates an *unfillable class's* budget, but it is gated on `if remaining >= MIN_TRADE_ILS and legs:` — with zero legs there is nothing to reallocate into, so the card vanishes rather than under-deploying. Seen live: NAV 22,006, cash 2,589 (11.8%) vs a 3% floor → spendable ₪1,929; the Equities share of ₪551 split across four holdings = ₪138 each, all under the ₪250 minimum → no legs → no card, while ₪1,929 sits idle. **Fix:** when no leg clears the minimum, concentrate the budget into the single best candidate instead of splitting it thin.

## Corrections worth remembering
- **A guard protects the path it is on, not the behaviour.** Phase 1 fixed cash-being-quoted-as-NASDAQ:CASH in `refresh_all_positions` and stopped there. `POST /portfolio/refresh-prices` had its own copy of the same loop with no guard, so on 2026-08-10 the identical bug fired again from the manual button and inflated NAV ~9x. When fixing a data-integrity bug, **find every writer of that field first** — the fix is otherwise conditional on which code path the user happens to hit. (Still unaudited: broker sync, CSV intake, `add_holding`, `update_position`.)
- **The AMZN "cap breach" was not a bug.** `risk_tolerance: High` sets `concentration_cap` to 0.40, so AMZN at 29% is within limits — `diversification_score: 100` proves it (that score can only be 100 when `max_weight <= cap`). A smoke test hardcoding 0.25 manufactured the false alarm. Read caps from `/api/v1/plan`, never assume 0.25.
- **Gemini outages were billing, not code**: HTTP 429 "prepayment credits are depleted". `gemini_generate` swallows every exception identically, so a two-minute billing fix looked like a permanent outage. Surfacing the error class is still an open improvement.
- **Smoke tests must not pass on missing data.** A check reported "no competing cash cards" when the API had timed out and returned null. Every check now SKIPs or FAILs on a null payload.
- **PowerShell variable names are CASE-INSENSITIVE.** `$h = Api GET '/api/v1/health-check'` overwrote `$H` (the auth headers), so every subsequent call died at parameter binding in 0.00s — the request never left the machine. The handler printed "NO RESPONSE", which reads as a network fault, and that one hidden error message produced **four** wrong hypotheses (keep-alive reuse, deploy drain, server saturation, DB pool) and a production DB change shipped on false evidence. Headers are now `$ApiHeaders` and `Api` throws if it is ever not a hashtable. **Print the exception type and message on the FIRST failure — never collapse errors into a generic string.**
- **Phase 11 (`pool_pre_ping`) was justified by that false evidence.** Keep it — Railway does close idle connections and `push_service`/`pricing_service` already guard against it — but it fixed nothing that was being observed.

ℹ️ **(historical, now satisfied)** The pre-ship warning on this batch — run suite + ruff, expect `test_*health*` to shift, `rule_events` relies on `auto_create_tables` — was cleared when the phases landed and the smoke run passed. Keep the `auto_create_tables` note in mind if that setting is ever turned off in production.

## Next (confirm priority)
0. **Identify the one failing check inside `smoke-all`.** The chain reports 74 passed / **1 failed** / 8 skipped; the failure is in the smoke-all section (24/1/0) and was never read this session. Run `.\scripts\smoke\smoke-all.ps1` alone. Likely `subscriptions: 0` — do not assume it. ← blocks calling the chain green
0b. **`smoke-p1.ps1 -Apply`** — proves the cap is really armed at the sleeve size. Every P1.1 live check is currently skipped.
0c. **Pixel QA for P0** (`qa/QA-2026-08-10-p0-safety.md`) + a Plan-tab pass for P1 (preview shows funding legs; Apply shows the armed cap; SW is `iw-v15` so close/reopen the installed app once).
0d. **Then P2** (`BEAT_MARKET_NEXT_PLAN.md`): presentation only — Style + Horizon chips, the tab row wrapping at five tabs (`.stgoal`, `flex-wrap:nowrap; overflow-x:auto`), and "VERY HIGH RISK" breaking across two lines (`.rk` needs `white-space:nowrap`). Then P3 (regime proxy) and P4 (rules + notifications).
1. **Enable notifications on the Pixel 9** and confirm `subscriptions: 1`, then that the 07:00 digest arrives. This is the *only* open item from the 5-issue batch and the single failing smoke check — a device action, not code. ← next up
2. **Offload blocking provider I/O to a thread pool.** `/recommendations` is ~5s warm on a **single uvicorn worker** making *synchronous* calls inside `async def` handlers, so one request blocks every other. Shaving latency further is the wrong fix.
3. **Rotate `AGENT_API_KEY`** (it was pasted into a chat transcript).
4. Pin `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` in Railway — currently DB-generated, so a DB reset would invalidate every push subscription at once.
5. Broker integration — see `BROKER_INTEGRATION_PLAN.md` (unstarted; confirm it's still the next initiative — it has been "next" and unstarted for 3 weeks).

## Pending QA / open questions
- **P0 safety batch (2026-08-10):** `qa/QA-2026-08-10-p0-safety.md` on the Pixel 9 — 8 sections, ~10 min. Pre-flight matters here: the SW went `iw-v13`→`iw-v14`, so close/reopen the installed app once, and force `POST /api/v1/portfolio/refresh-prices` before section 4 or no holding will carry `price_freshness` yet. Automated half: `.\scripts\smoke\smoke-p0.ps1` (read-only; `-Execute` writes).
- **5-issue batch (phases 1–11):** ✅ verified live 2026-08-03 via `smoke-all.ps1` — see "Verified live" above. Only the notification subscription remains.
- **Alignment batch (Phases 0–5, 2026-07-18):** ⚠️ **still unrun on the Pixel 9 after two weeks**, and the SW has since moved `iw-v10`→`iw-v11`, so the update path in the original checklist is stale. Check: cash set/adjust + pinned Cash row + Cash slice in donut; a buy card shows sizing + "How it's funded" legs and executes; Mark-as-done vs Ignore land in separate restore lists; war-room cards match Today; strategy "What changes?" preview renders; no stale shell after SW `iw-v11`. **Decide whether this is still worth running standalone or is subsumed by the 08-03 smoke pass.**
- **Notification ↔ Today alignment (2026-07-12):** `qa/QA-2026-07-12-notification-alignment.md` on the Pixel 9 — blocked on the same "enable notifications on the device" step as Next #1; do them together.
- Confirm on live: accepting a tax-loss/sell rec removes the holding and adds a CASH position for the net proceeds; fee-swap replaces the fund; trim credits cash. (Accept-executes fix.)
- Confirm the 30-min reprice + data-source label shows fresh prices (FMP primary, Yahoo fallback) in production.
- Confirm the live advisor answer for "should I add commodities?" reads as a balanced for/against.

## Known sharp edges
Postgres per-test isolation fixture (throwaway NullPool engine, own event loop); ruff strictness. Windows mount can serve truncated views of file-tool edits — verify large writes on the mount (see `safe-windows-edits`). Commit with `git commit -F COMMIT_MSG.txt`, never a PowerShell here-string (parses as pathspecs, commit silently doesn't happen). Never `git add -A` (`frontend/node_modules` is tracked, CRLF-noisy). See CLAUDE.md.

## Changelog (newest first)
- 2026-08-10 — **Beat the Market P0 (safety) written, not yet run.** All four items from `BEAT_MARKET_NEXT_PLAN.md` P0 are in the working tree: (0.1) `load_basket` no longer deletes the book — `mode="fund"` is the new default for the rule-based family and raises only the sleeve's shortfall through the existing funding engine (spendable cash to the objective floor, then worst-fit holdings), abstaining with a stated reason rather than half-executing; `mode="replace"` survives for the four static families behind a confirm that now lists every position and its value; both modes gained `dry_run`. (0.2) `measuredProfile` reads `metrics.cagr_pct` before `ok`, so a card that kept its numbers through a failed refresh shows chips plus "refresh has been failing since X" instead of "Couldn't measure". (0.3) freshness became **three** states, not two — the original bug was that FMP stamps `_now()` on every quote, so a delisted holding looked freshly traded; `Quote.as_of_source` now distinguishes a venue timestamp from a request timestamp, FMP reads its real `timestamp`, and where the primary can't say, Yahoo is asked purely for the trade time. A quote older than 5 **trading** days no longer overwrites the price; the position is flagged, badged in Holdings, reported in `/portfolio.stale_positions`, and surfaced on Today as guidance — never an automatic write-off. (0.4) suggested protective rules reach Today as one card wired to `create_rules`. Plus `smoke-p0.ps1` and two new test files. **The sandbox VM died before any of it could be executed** — see the ⛔ block at the top of this file.
- 2026-08-03 — Weekly status review: 🎉 **busiest week of the four repos — 11 commits, `d42f38c`→`86760ff`.** The entire 5-issue bug batch (phases 1–11) is committed AND verified live (`smoke-all.ps1` 29/30); rewrote the "Uncommitted (2026-08-02)" section as "Last shipped" with a phase→commit table, resolving last week's uncommitted-work flag. Working tree **clean**. Merged the duplicated "Open items" list into "Next" and gave Next a priority order. ⚠️ Flagged: the **Phases 0–5 alignment-batch QA has now gone two weeks unrun** and its SW update path (`iw-v10`) is stale — the app is on `iw-v11`; decide whether the 08-03 smoke pass subsumes it. Broker integration still unstarted after 3 weeks as the "next initiative". The one failing smoke check (`subscriptions: 0`) is a device action, not a defect.
- 2026-07-27 — Weekly status review: quiet week — no commits since `9e2cd01` (2026-07-18); tree clean; STATUS in sync. Pending QA flagged: Phases 0–5 alignment batch + notification alignment on the Pixel 9.
- 2026-07-20 — Weekly status review: ✅ **the whole 2026-07-18 alignment batch is now committed** — 8 commits `defe8ec`→`9e2cd01` (HEAD), resolving the "Phase 1+ uncommitted" flag; working tree clean apart from doc edits. Note: two near-duplicate commit pairs in history (`5acab4c`/`9e2cd01`, `2c02d07`/`fe9ffc6`) — harmless, both phases landed once in the tree. Folded the commit-hygiene notes (no here-strings, no `git add -A`) into Known sharp edges; collapsed old review entries. (This STATUS was also rewritten whole via bash after an Edit-tool write truncated it on the mount.)
- 2026-07-18 (alignment batch, Phases 0–5 + two live fixes — consolidated) — **Recommendations became grounded, sized, funded and honest.** *Phase 0*: FX bug in `market_impact.annotate` (exposure computed without an FX rate); the four bare `except: pass` blocks instrumented with a `degraded` list; honest empty state + `POST /recommendations/restore`; portfolio totals gained invested / gain / cash. *Phase 1*: **cash as a first-class holding** — `GET|POST /portfolio/cash`, pinned Cash row, a Cash slice always in the donut, `CASH_META` liquidity, and a latent `credit_cash` cost-basis bug (₪2,500 reporting as ₪6.25M invested) fixed with self-heal. *Phase 2*: `signal_service` builds observations from **real price history** (`DEFAULT_OBSERVATIONS` demoted to demo-only behind `DEMO_SIGNALS`), one war-room→Today pipeline promoting only `grounded` + DISPLAYED decisions, and **Done ≠ Ignore** split into two buckets with separate restores. *Phase 3*: **funding engine** — every buy card sized against the plan's target mix and caps, then funded (cash to a per-objective floor, then worst-fit holdings with ₪ amount / shares / est. CGT), executable `buy_funded`, and a green "How it's funded" block on Today. *Phase 4*: `strategy_profile` derives expected return, volatility, drawdown, concentration and leverage **from each basket**, plus a "What changes?" before/after preview — deliberately NOT fabricating asset-class differences between all-equity strategies. *Phase 5*: green CI — the cross-event-loop asyncpg test rewritten to the throwaway-`NullPool` conftest pattern, 7 ruff errors fixed, `ruff==0.15.22` pinned and the lint job **gated**, README rewritten from the stale "Phase 0 skeleton". Also landed that day: the **stale-shell fix** (the SW cached the old `index.html` under a new key — `cache.add` fetches through the browser HTTP cache; the shell is now `no-cache` + network-first — **this was the real cause of the reported "Ignore does nothing" bugs**), the `_reconcile` pass killing four mutually-impossible cards, and **Accept no longer pretending to act** (10 card types were `apply: none` yet said "Done — applied."; `_ACTIONABLE_KINDS` is now the source of truth and the UI labels ⚡ actionable vs 💡 guidance). SW `iw-v3`→`iw-v10`; ~75 tests added. Commits `defe8ec`…`9e2cd01`.
- 2026-07-14 — **War-room timestamps + benchmark-lag & commodity recommendations.** (1) The Agents war room now stamps each run: an 'Analyzed at <date · time>' header plus a per-decision time on every session card (`build_war_room` returns `generated_at`; each session carries `decided_at`). (2) New performance-driven card in Today's What-to-do-now: when the portfolio trails its benchmark by >3% the engine surfaces a grounded 'You're trailing <benchmark>' improvement rec (real excess-return number, points at laggards/fees/drift). (3) Commodities now surface as holdings advice — an 'Add a commodities sleeve' card fires when under-allocated vs the objective's commodity target, naming concrete screener-ranked picks, and `buy_ideas` now includes commodity picks alongside equities. +5 tests (`test_recs_extras.py`).
- 2026-07-14 — **Trading-rules UI redesign (grouped by holding).** The rules list was a flat stack of fat cards — ticker repeated on every row, same-holding rules scattered, no colour coding, duplicates unflagged. Rebuilt as a `Positions | Rules` segmented sub-tab inside Holdings (no new bottom-nav tab): rules now group under one card per holding (ticker + live price header), each rule a compact colour-coded row (stop-loss red / take-profit green / trailing blue / max-weight amber) with a distance-to-trigger bar. Adds filter chips (all/triggered/armed/paused), sort (closest/holding/type), duplicate detection with a one-click remove, and triggered rules pinned + highlighted on top of their group (and their group floated first). Count badge on the Rules tab; Today's rule-alert banner deep-links straight into the Rules pane. Client-side only (regroups the existing `/api/v1/rules` payload) — no backend change.
- 2026-07-14 — **Actionable trend cards + per-holding suggested rules.** The downtrend/uptrend momentum cards were advice-only (`apply: none`) — Accept did nothing user-visible. They now arm a concrete rule: downtrend → stop-loss at a volatility-derived price (≈8–15% below today); uptrend → trailing stop (10–20%) plus a max-weight cap when the name is already a large slice. Accept returns an "Armed …" summary; the rule then fires the normal alert + Today to-do. Added `stop_buffer_pct` (levels grounded in each holding's own realized volatility, never invented), a `create_rules` apply-kind, `rules_service.suggest_rules_for_holdings` + `GET /api/v1/rules/suggestions`, and a "Suggested rules" UI panel (add each / arm-all). +5 tests (`test_trading_rule_suggestions.py`); changed surfaces green locally, full suite gated by CI on push.
- 2026-07-13 — Weekly status review: the Accept-executes + notification-alignment work landed as `67f7da3` (2026-07-12), resolving that week's uncommitted-work flag.
- 2026-07-06 — **Accept now executes recommendations.** The Today-view "Accept" applied nothing user-visible: sells just deleted holdings (value vanished) and fee/other cards were `apply:none`. Now sells credit net-of-CGT proceeds to a visible **CASH** holding (liquidity you can see/redeploy), `trim` credits the sold portion, and fee-swap cards actually sell the high-fee fund and buy the cheaper equivalent at a live price (falls back to cash if unpriceable). Accept returns a "what changed" summary the UI now shows. Tests: sell→cash added.
- 2026-07-12 — **Notification ↔ Today alignment + why/impact recommendations.** Root cause of "push says do X, app says nothing to do": the app hid cards via a *permanent* `localStorage` list while server dismissals (which gate push) expire after 7 days — so a resurfaced item re-notified while the app hid it forever. Replaced the permanent list with a TTL-matched (7-day) local store keyed by `iw_snoozed_v2`; the server dismissal is now the single source of truth. Notifications are now categorised `action` (maps 1:1 to a Today card) vs `info` (price moves + weekly digest, reworded as FYI, `silent`/no-nag in the SW). Added actionable region/currency/liquidity diversification cards so every pushed alert maps to a card. Every recommendation now carries plain-language `why` + `impact` (rendered in the card); the goal-gap contribution card, the digest and the home "Where this could end up" panel now share one Monte-Carlo projection instead of diverging. SW cache bumped `iw-v2`→`iw-v3`. Ships alongside the previously-uncommitted Accept-executes work.
- 2026-06-22→06-29 (consolidated) — **Foundations.** Installable PWA (manifest, SW, mobile bottom-nav) + web push (recs, risk alerts, price moves, daily digest) with server-side 7-day-TTL dismissals; price-provider hardening (FMP stable + keyless Yahoo fallback). Markets tab: Yahoo futures risk-on/off regime feeding the agents; Gemini portfolio / holding / macro summaries + grounded deep-research-per-holding. Trading-rules engine (stop-loss / take-profit / trailing / price-alert / buy-dip / max-weight → alerts + to-dos + management UI). Scheduled 30-min reprice with a truthful data-source label. Earlier still: NAV/goal alignment, FX normalization, opportunity screener + fundamentals, commodities advisor context, advanced dashboard restore, Postgres test-isolation fix. STATUS.md + CLAUDE.md seeded.
