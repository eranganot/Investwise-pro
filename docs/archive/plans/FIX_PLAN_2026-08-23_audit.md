# Fix plan — the 2026-08-23 seven-day audit

Five symptoms reported against `e405aec`. Root causes found for all five; three
were reproduced with a failing test before anything was changed.

Audit report: https://claude.ai/code/artifact/e6089dc5-b360-447c-aeac-a9cfe576c5f9

---

## Ranked by value per unit of effort

Effort is XS (one constant / one line), S (one function), M (one subsystem),
L (several subsystems + UI), XL (its own phase).

| | Work | Effort | Value | Why here |
|---|---|---|---|---|
| 1 | **Restore the 11 muted cards** — `POST /recommendations/restore-completed` + `/restore` | XS | **Critical** | Not code. One API call brings back the tax card, the sell-Equities card and the SOXL cold-start card *today*. Nothing else in this table returns value that fast. |
| 2 | **`nav_history` import + own engine** | XS | **Critical** | One wrong name has failed the nightly snapshot 7/7 since it shipped. Fixing it closes symptom 5 outright. Highest ratio in the plan. |
| 3 | **`blockers` reads `JOB_STATE`** | XS | High | The health summary said "nothing obviously broken" while a job sat at 7 failures. A monitor that can't see a dead job is worse than none. |
| 4 | **`_DONE_TTL_DAYS` 90 -> 14** | XS | High | One constant. Caps the blast radius of every future Accept while the real fix (content-bearing ids) is built. |
| 5 | **Honest gain: cost basis vs contributions** | S | **Critical** | The +42.48% headline is wrong by 7,388. Both numbers are already in the same response; this names them. |
| 6 | **Surface degraded agents** | S | High | The open `history` circuit silently kills the war-room, momentum and verdict cards while `degraded: []` claims health. Explains symptoms 1 and 2 without fixing anything else. |
| 7 | **Sleeve held-vs-target labels** | S | High | "64% of the book" is the target printed as an actual. The number is already computed inside `fund_plan`; this exposes it. |
| 8 | **Core choice banner** | S | High | A decision you cannot currently see. `"core": null` today. |
| 9 | **Content-bearing card ids + merged-rebalance id + drop the `[:2]` slice** | M | **Critical** | The actual fix for symptoms 1 and 2. `_rid("tax")` is a constant; one Accept ever mutes the family. |
| 10 | **Stop `push_evaluate` auto-acknowledging rule events** | S | High | Three max-weight rules fire every 30 min and are resolved one second later by a job that notified nobody. |
| 11 | **`Transaction` ledger on accept** | M | High | Zero writers today, so `flow_total_ils` is always 0 and any book change reads as performance. Prerequisite for 12. |
| 12 | **Reconcile the book** | M | High, once | One-off data repair. Needs 11 or the correction goes unrecorded. |
| 13 | **Same-class swap funding** | L | High | Makes 64% reachable at all. Also the riskiest change here: it widens what the app may sell. |
| 14 | **T6b.1 — fix + ship `candidate_search`** | S | Medium | Already written and tested on disk. One real defect (below). Ships an engine nothing calls yet, deliberately. |
| 15 | **Delete the legacy `plan.strategy` columns** | M | **Low** | Nine readers, three writers, ~14 test assertions, a migration — and no user-visible change. Overdue hygiene, but it buys nothing you asked for. **Defer until 1-13 are done.** |
| 16 | **T6b.2 — wiring** | L | Medium | The route and card on top of the engine. |
| 17 | **T5 — the war room reads the solver** | L | Medium | Also where the adversary veto finally gets an owner. |
| 18 | **Phase W** | XL | Medium | Walk-forward sweeps, the `vol_target` regime re-decision, contribution modelling. Last by the spec's own argument. |

**What the ranking changed.** Items 1-4 are four one-liners that between them close
symptom 5 and un-bury symptoms 1 and 2. They were scattered across three PRs in the
first draft. They are now PR 2, and the `Transaction` ledger moved down to sit with the
reconciliation it exists to record. Legacy-column removal dropped from PR 6 to last of
the safe work: it is the only item with real risk and no user-facing payoff.

---

## PR 1 — accepting a card stops creating and destroying money — SHIPPED

`5a6dfef`, hotfixed by `7f7351f`. Four write paths could change NAV with no trade:

- `_rebalance_to` summed `quantity * current_price` with **no FX**, then wrote the
  resulting class factors back into share counts. Measured: **+4.8% of NAV per tap**.
  Two taps ten seconds apart on 2026-08-21.
- `_apply_buy_funded`, `_apply_fee_swap` routed buys through `upsert_positions`, which
  **sets** `row.quantity`. Topping up a held ticker replaced it. Measured: **-4,388**.
- `_apply_redeploy_cash` called `upsert_positions` with six args against a three-arg
  signature.

One `intake_service.buy_into_book()` now serves all of them, looking the ticker up
across the whole book rather than one account — which also closes the duplicate-row
path where a buy landing in a second account was counted twice by NAV.
`tests/test_accept_conserves_nav.py` pins the invariant.

**Lesson recorded:** the hotfix was needed because those tests borrowed the app's
pooled engine across event loops — green on SQLite, red on Postgres. **The
`test-postgres` job IS reproducible locally** (`apt-get install postgresql`, point
`DATABASE_URL` at it); CLAUDE.md says it is not, which is why this shipped untested
against it. PR 2 corrects that line.

**Also observed:** Railway served `5a6dfef` *while CI was red*. "CI is the gate" is
intent, not enforcement — the push deploys independently of the workflow result.
Worth a branch protection rule.

---

## PR 2 — say true things about money and time

The four one-liners plus the gain split. Small, independent, low risk.

- **`app/services/nav_history.py:320`** imports `async_session`; the name is
  `AsyncSessionLocal`. Do not simply rename it — the job runs in an APScheduler thread
  via `asyncio.run()`, so the shared pooled engine hits the cross-loop asyncpg
  rejection that PR 1's hotfix just cured in the tests. Build a throwaway `NullPool`
  engine inside the job's own loop, as `pricing_service.py:17` does (332 runs, 0
  failures). **Add a test that actually calls `run_nav_snapshot_blocking()`** — a
  missing name is caught by any single invocation, and nothing invoked it.
- **`app/services/push_service.py:479`** iterates a hardcoded
  `("push_evaluate", "push_digest")`. `nav_snapshot` failing 7/7 was invisible because
  it is not in that tuple. Iterate every job in `JOB_STATE`.
- **`recommendations.py:214`** `_DONE_TTL_DAYS` 90 -> 14. `_DISMISS_TTL_DAYS` stays 7.
- **`/api/v1/portfolio`** returns `invested_ils: 20000` from a contributions ledger with
  one row, while the positions' own basis sums to `27,387.81` — both in the same
  response, gain overstated by `7,387.81`. Return `cost_basis_ils` and
  `gain_vs_basis_ils` (the real +4.0%) beside `contributed_ils` and
  `gain_vs_contributions_ils`, and lead the UI with gain-vs-basis. A single
  `invested_ils` that can mean either **is** the bug.
- **`CLAUDE.md`** — correct the "cannot be reproduced locally" line about the Postgres
  job, and record the NullPool rule for any test that opens a session beside
  `TestClient`.

---

## PR 3 — the ledger, and the book it lets us repair

- **Write `Transaction`.** It has zero call sites outside the models. Every actionable
  branch of `apply_recommendation()` and `_execute_funded_sleeve()` records ticker,
  side, shares, price, fx, ILS value, tax, source card id. Derive
  `nav_history.flow_total_ils` from it so a book change stops reading as performance.
- **`scripts/reconcile/invert-rebalance.py`.** `_rebalance_to` multiplied every position
  in a class by `f = desired_class / current_class`; with Cash absent from the Grow
  target the Equities factor is exactly
  `f = cur_equities_native / (cur_equities_native - cash_native)` — cleanly invertible.
  Read-only preview first: app-vs-reconstructed per position against the 2026-08-17
  snapshot of `21,168.33`. Only on explicit confirmation does it write, reset the
  contributions ledger, and re-snapshot.
  **Stated honestly:** an estimate. The `sell_losers` accept at 08:51:36 deleted five
  positions mid-sequence and their share counts were never recorded.

---

## PR 4 — cards you can actually see

- Content-bearing ids: `_rid("tax", tuple(sorted(losers)))`, same for `commodity_add`,
  `cashdrag`, `raise_cash`, `rules_suggested`. A constant id means one Accept, ever.
- `_reconcile()` (362-375) merges every `rebalance_to_objective` card into one and keeps
  `rebal[0]`'s id — *SELL Equities*. Accepting the merged card therefore muted a BUY
  Commodities leg whose title you never saw. The merged card gets its own composite id.
- Drop the `rebalance_actions[:2]` slice (771): `_rid("rebal","Fixed Income")` is never
  generated at all.
- Never suppress a HIGH-severity card, or one whose ticker has a rule actively firing.
- **Surface degradation.** `guarded_history` / `guarded_quote` raising `CircuitOpenError`
  inside `_war_room_recs`, `_momentum_recs`, `_holding_verdict_recs`, `_hedge_recs` must
  populate `degraded[]`. All four fail fast today and the response still claims health —
  which breaks the guarantee `smoke-p0` P0.4 exists to enforce.
- `war_room.py:142` never emits `action_type`, so the guard at `recommendations.py:493`
  is dead code and a REBALANCE signal on a name you do not hold falls into the **buy**
  branch.
- `allocation_mix.py:12-20` classifies COW, NIB, USO, DBC, GLD as Equities, so the
  redeploy-cash commodities leg can never be filled.
- `push_service` must stop marking rule events `acknowledged` when it notified nobody.

---

## PR 5 — sleeves that mean 64%

- `sleeve_occupancy(session, user)` from the existing `_held_ils()` (427) and
  `sleeve_service.all_sleeve_targets()` (158). Not `_legs_for()` — it drops tickers
  already at target. The only place true occupancy is computed today is a result string
  at `strategy_service.py:725-726`.
- `/api/v1/plan/sleeves` gains `held_pct` per sleeve, `allocated_held_pct`,
  `core_held_pct`. `index.html:1842` becomes "15% held against a 64% target".
- **Same-class swap funding.** `plan_funding` gives each class one overweight budget, so
  it can raise ~5,077 against the ~13,968 the sleeves need and both abstain. But selling
  V/MSFT/SCHD to buy TQQQ/SOXL is Equities -> Equities: the class weight never moves, so
  the class-overweight budget is the wrong constraint. Bind on the concentration cap and
  an explicit tax/turnover budget instead. This is the swap-vs-rebalance distinction
  CLAUDE.md already insists on, applied to funding.
- `sleeve_coldstart` carries no `apply` key by design, yet Accept still files it as
  completed — which is how the SOXL card got buried. Make an empty sleeve a persistent
  banner that only holding some can resolve.

---

## PR 6 — surface the core choice

There is **no backlog task** for picking a core: it shipped as Phase C6 on 2026-08-13
(`STATUS.md:504`) and was simply never used. `"core": null, "core_is_implicit": true`.
Setting one is reachable only by applying a static family card; the empty state is one
line of grey text (`index.html:1452`).

Make it a persistent banner listing the 14 eligible families from
`strategies.py:12-94` with their target mixes and what each does to measured drawdown.
Rule-based sleeves are correctly refused as cores (`sleeve_service.py:263`).

This matters now: the solver said on 2026-08-16 that the **core alone** breaches the
13.3% ceiling — `-2.88%/yr vs SPY at 31.89% drawdown` (`STATUS.md:185`) — so no sleeve
size can fix it. Its own card says "change the core" (`target_solver.py:733`).
Drawdown-relevant candidates: `bal_all_weather`, `bal_6040`, `pre_low_vol`.

---

## PR 7 — delete the legacy strategy columns

`strategy_service.py:318` says the columns go once C4 removes the readers. C4 shipped
2026-08-13; nine readers remain: `recommendations.py:700-702` and `1087-1090`,
`plan.py:105/115/120` and the `legacy` block at `192-193`,
`strategy_signal_service.py:142-145`, the `load_basket` fallback at
`strategy_service.py:461-464`, `sleeve_service.backfill_once` / `backfill_core_once`.
Plus the frontend `_appliedIds()` fallback (`index.html:1355`), three writes, the
startup DDL (`main.py:56-58`), an alembic drop, ~14 test assertions and three smoke
scripts.

Run the backfill once and confirm it is a no-op before dropping anything.
**Deliberately last of the safe work** — no user-visible change, real regression risk.

---

## PR 8 — the three unfinished phases

All three are marked not started at `STATUS.md:79-81`. Each gets its own plan before
any code, and none is safe to build on a book whose arithmetic was still wrong.

### T5 — the war room reads the solver

`STATUS.md:79`: *"plan-T5 — the war room | **NOT STARTED.** The solver becomes a
constraint the Risk / Decision / Adversary agents read; not an eighth agent, not the
decision maker."*

**The trap in that line: what shipped as "T5" is not this.** `STATUS.md:175`
(`✅ T5 — the card ends in an instruction`) was the recommendation block, which *took
the label from work still outstanding*. Local WIP exists (`scripts/ship-t5.ps1`,
`scripts/smoke/smoke-t5.ps1`, `app/services/target_apply.py` modified, uncommitted).

Scope: the solver's answer — required sleeve size, the drawdown ceiling, whether the
target is reachable at all — becomes an input the existing agents consult, so a card
cannot be approved on the Bulletproof path while the solver says the book cannot get
there. It is also where a defect the audit found independently gets an owner:
`build_war_room()` **never calls `cross_examine()`**, so `adversary_enforce_veto`
(`config.py:121`) is honoured nowhere on the card path — adversary notes are appended
as transcript prose only (`war_room.py:95-131`).

### T6b — the solver varies the core

`STATUS.md:80`: *"T6b | not started — catalog strategies not held, varying the core,
discovered sleeve count, top-N ranking"*. Spec: `BEAT_MARKET_TARGET_SOLVER_PLAN.md:686`.

**T6b.1 is already written and tested, uncommitted** — `app/services/candidate_search.py`
(9KB), `tests/test_candidate_search.py` (16 tests), `scripts/ship-t6b.ps1`. Verified in
the sandbox: 16 passed, ruff clean. The `is_a_limit` design is right — a runtime
backstop can never masquerade as a measured finding — and `forbidden_tickers()` makes
the never-recommend-a-security rule catchable rather than merely true.

**Three findings before it ships:**

1. **The first sleeve is never compared to the core.** `base = score(chosen) if chosen
   else None` (line 82) — with an empty `start` the empty set is *never measured*, so
   `best_score` stays `None`, line 125's floor test is skipped entirely on the first
   addition, and line 124 reports its absolute score as if it were a margin. `grow()`
   therefore returns 1 sleeve for *any* admissible candidate however worthless. On this
   book — where the solver already says the core alone breaches the ceiling — it would
   answer "1 sleeve" where the honest answer is "none helped". **Fix: measure the empty
   set as the baseline and apply the floor to the first addition too.**
2. **A round that ran out of budget still commits its pick.** `score()` returns `None`
   once `evaluated >= max_evaluations`, but the loop-top budget check only fires on the
   *next* iteration — so a round that evaluated 2 of 7 candidates appends the best of
   those 2, then stops with `reason: evaluation_budget`. Discard a round that could not
   evaluate every remaining candidate.
3. **`MIN_GAIN_PCT = 0.25` is the one number the module picked rather than measured** —
   in a module whose whole argument is that a count must be a measurement. Derive it
   from the OOS split's standard error, or mark it in the payload the way `is_a_limit`
   marks the caps.

**T6b.2** is the wiring: `measure(ids)` against `measure_blend` on the OOS split,
varying the core across `strategies.CATALOG`, the route, and the card.

### Phase W — where the return actually comes from

`STATUS.md:81`, spec `BEAT_MARKET_TARGET_SOLVER_PLAN.md:931`:

- **Walk-forward parameter sweeps.** `sweep_param` / `sweep_values` exist on every
  overlay and have never been run walk-forward — "the only untested source of
  *additional* measured edge in the family, and a bigger lever on excess than any sleeve
  size."
- **Re-decide `vol_target`'s regime gate on the P3 numbers** — 8.32 points of drawdown
  for 1.71%/yr. Under a hard ceiling this is what makes a larger sleeve admissible at
  all, which is the constraint the solver keeps hitting.
- **Contribution modelling.** At this NAV the contribution rate moves terminal value
  more than any admissible excess does.

Order is fixed by the spec (`:161`): *"Phase T exists so Phase W can be evaluated
rather than believed."*

**One decision still open** and it blocks the solver's headline: whether the verdict is
computed from the ten-year `strategy_backtest` or the 250-day `holdings_backfill`. The
spec recommends the ten-year; 250 sessions is mostly noise and a single good quarter
would move the required sleeve size more than any real change in edge. To be settled
when T6b.2's plan is written, not inside an implementation PR.

---

## Per-PR deliverables

Every PR ends with three things:

1. **`scripts/deploy/ship-prN-<name>.ps1`** — dot-sources `_common.ps1`, runs the gate,
   commits only named files with `git commit -F`, pushes, then polls `/health` until
   `.commit` matches. Pure ASCII, no backtick continuations.
2. **`scripts/smoke/smoke-<name>.ps1`** — read-only by default, `-Sha` to prove the
   deploy landed, opt-in `-AllowExecute` for anything that writes. House rules from
   `smoke-p0.ps1`: a SKIP is never a PASS, read paired state in one call, print the
   exception type on first failure, `$ApiHeaders` never `$h`.
3. **A "check with your own eyes" block** naming what should visibly change, and what
   should still be broken because a later PR owns it.

**And, from PR 1's mistake: run all three CI jobs before pushing**, including
`test-postgres` against a real Postgres. The SQLite job proves less than it looks like
it does.
