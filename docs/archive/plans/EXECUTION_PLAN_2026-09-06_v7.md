# InvestWise Pro — Execution Plan V7

_Written 2026-09-06 by Claude, against HEAD `446ceaa` (PR 61) with PR 61.1 staged.
Supersedes `EXECUTION_PLAN_2026-09-03_v6.md`._

V6's standing rule — *for every open task, make sure it is still open* — applied to V6 itself.
Every row below carries an observation taken today against **production or the working tree**,
not a citation of V6. **Twenty-two PRs have shipped since V6 was written** (PR 40 → PR 61), and
they closed more of it than they left.

---

## 0. Cold start

| | |
|---|---|
| Repo | `C:\dev\Investwise-pro`, branch `main`, HEAD `446ceaa` (PR 61). PR 61.1 staged, not committed. |
| Production | `/health` → `446ceaa`, v22.1, `schema_revision: 0021_plan_cash_target`, **`builds_own_schema: false`** |
| Tree | 4 modified (PR 61.1), 3 untracked: `candidate_search.py` + its test, and PR 61.1's ship script. **Down from 20.** |
| Book | NAV **₪21,874.63**, cash **₪1,820.48**, objective Grow, risk tolerance High (`concentration_cap` 0.40), `cash_target_pct` **7.5** |
| Sleeves | `btm_trend_soxl` 33.91% target / 33.93% held · `btm_qqq_hold` 33.09% target / **0% held** |
| Measured coverage | 97 |

**House rules — one is new and it is new because I broke it.**

- Never `git add -A`; `git commit -F`, no BOM; PowerShell stays ASCII, no backticks.
- **Read-only git from a sandbox uses `git --no-optional-locks`.** V6 already said this. On
  2026-09-06 I ran a plain `git status --short` through the mount, the unlink of
  `.git/index.lock` was refused, the lock survived, and Eran's ship script failed on it
  twenty-three minutes later. Second occurrence — the first is in the 08-31 hygiene row.
- Run the Postgres job locally before pushing. Drop and recreate the database first.
- Every new test confirmed **failing against current HEAD** before it is accepted.
- **A reproduction is not a reproduction until the setting that decides the branch is in it.**
  PR 61.1: my fixture ran the Medium `concentration_cap` (0.25) against a live plan on High
  (0.40), and that one number was the difference between a ranking that picked the right leg
  by luck and one that did not.
- A card may never claim a portfolio change it does not produce (`tests/test_card_claims.py`).

---

## What V6 opened, and where it stands

### The six PRs V6 sequenced

| | V6 said | today |
|---|---|---|
| **A** | `_apply_buy_funded` sells before it knows it can buy — two live cards on that path | **CLOSED, PR 40.** It prices every leg first and returns `{"aborted": True, "unpriced": ticker}` untouched. The docstring records the old order. |
| **B** | The tax card over-states the saving; Israeli CGT indexes basis to CPI | **CLOSED by disclosure, PR 41** — the recommended option. `recommendations.py:1337` appends *"Estimated on the nominal gain, with no inflation-linked basis adjustment"*. `tax_engine.py:19` still lists the assumption. Indexing the basis remains undone and unlisted — it needs a CPI source. |
| **C** | The veto and the cap that nothing reads | **STILL FULLY OPEN.** `grep -rn review_buy app/` → `allocation_agent.py:26` (the definition) and `pipeline.py:102` (the only caller), reached solely by `POST /decision-feed/generate`. `rank_trim_candidates` still takes one scalar `cap`; it gained `book_target` and now `prefer`, but no per-ticker cap. |
| **D** | Four price writers, three `meta` wipes | **HALF CLOSED.** `intake.py:344` now goes through `to_major_units` (PR 45) and `_intake_meta()` merges instead of replacing. Still open: `broker_sync.py:33` takes `bp.current_price or bp.cost_basis or 1.0` raw, and `intake_service.py` still assigns a whole dict at `:281` and `:323`. |
| **E** | The deploy you cannot repeat | **CLOSED across PR 46/47/48/49/52/57.** `0018` now carries guards; `auto_create_tables: bool = False`; production reports `builds_own_schema: false`; the Dockerfile is pinned, non-root, has a `HEALTHCHECK` and a 60-line `.dockerignore`. Ordering held — migrations first, then the flag, then the image — and PR 48 is the record of what happened when it did not. |
| **F** | Three routes block the event loop | **CLOSED.** `market.py` runs all three through `await offload(...)`; the module docstring now says why. |

### P0 — the tree

**Done.** 20 untracked → 3, and the plan files are tracked (`git ls-files` lists V3, V5, V6 and
`EXECUTION_PLAN.md`). `candidate_search` is the only real code still untracked, now a deliberate
backlog row rather than an accident.

### Ops track

| | today |
|---|---|
| **Pin the VAPID keypair** | **DONE.** `vapid_pinned_by_env: true`, `vapid_env_matches_db: true`, `subscriptions: 1` — the live subscription survived, which was the whole risk. |
| **Branch protection on `main`** | **CANNOT BE ENFORCED**, and this is settled rather than pending: GitHub does not apply rulesets to a private repo on this plan, and said so in its own banner. `scripts/ops/pre-push` is the working substitute. GitHub Pro (~$4/mo) is the alternative if it is ever worth it. |
| **Rotate GitHub / Railway tokens** | **ERAN TO CONFIRM.** Three Railway tokens exist, one Account-scoped named "claude"; the recommendation is one project-scoped token, CLI pointed at it, all three deleted. |
| **Rotate the Postgres password** | **Backlog, deliberately** — the only ops item whose failure mode is an outage rather than exposure. |

### Honesty track

| | today |
|---|---|
| "What's moving" is four fixture rows under a live clock | **CLOSED, PR 58.** `BuiltinEconomicDataProvider.get_events()` returns `[]`, the caption is gone, and the payload carries `source` / `live` / `source_note`. |
| Seven agents declared, five exist | **CLOSED, PR 58 — and my claim was wrong.** All seven names *are* emitted; the war room speaks for each. The test pins the sequence, and it caught me nearly re-introducing a silent Research line. |
| Dead settings | **CLOSED, PR 58.** `settings.objective` removed. `OBJECTIVE_WEIGHTS` stays `{}` with the rationale — no tilt survived measurement. |
| The equity universe never got the liveness gate | **CLOSED.** `_fundamentals_for` counts unreachable candidates and drops rather than scores them; the live log says *"equity candidates UNREACHABLE this scan… THIS RANKING IS OVER A SUBSET"*. |
| A rule note quotes the price on the day it was armed | **Not found at HEAD** — no note in `rules_service.py` quotes a price. Treat as closed or re-derive; it is cosmetic either way. |
| The commodity screen offers CANE while the core names IAU and DBC | **OPEN.** `commodities.py:33` still lists CANE at a 1.00% expense ratio. Cosmetic. |

### Wave 7 — honest but not right

| | today |
|---|---|
| Money is a float everywhere | **OPEN and growing**: 553 `float(` against 38 `Decimal(` in `app/` (V6: 467 / 36). DB columns are still `Numeric(18,4)`, so nothing is corrupted at rest. Large, low-drama, deliberate or not at all. |
| Risk is per-position, never portfolio-level | **LARGELY CLOSED, PR 53.** `app/engines/portfolio_risk.py::analyze` measures real covariance over the actual holdings, in shekels. What is left is B1 — reconciling the strategy-blend volatility with the realised one, which is a window problem, and it is in the backlog with its evidence. |

### Rows V5 carried that V6 dropped — checked anyway

| | today |
|---|---|
| A rule for an unfunded sleeve is retired on the next read | **CLOSED, PR 59/59.1.** `retire_strategy_rules` records the owner and returns `retired` plus `left armed - no owner recorded`; retiring a rule and retiring an allocation are now different buttons. |
| The unspent-leg refund has no test | **STILL OPEN.** `grep -rc unspent_ils tests/` → nothing. |
| The secondary provider is stubbable but not governed | **Believed closed by PR 50** (one slow provider call was an outage for every other request) — not re-verified line by line. |
| No rate limiting on our own endpoints | **STILL OPEN.** `main.py` registers CORS plus three `@app.middleware("http")` — `_observe`, `_security_headers`, `_audit`. None of them counts. |

---

## What is actually open, in order

```
61.1  the funder sells the position the move converts   staged, gated, one command
62    Israeli companies into the candidate universe     next
63    the sleeve target follows its signal              approved (option a), invasive
C     review_buy on the live path + per-ticker cap      half a day, unchanged since V5
D.2   broker_sync price + two meta assignments          half a day, latent
      unspent_ils has no test                           one test
      no request rate limiting                          posture decision, then an hour
      CANE vs IAU/DBC                                   cosmetic
      floats -> Decimal                                 all or nothing
```

**C is the oldest untouched item on the board.** It was Wave 2 in V5 on 09-02 and PR C in V6 on
09-03, and nothing has moved it since. Nothing on the live path stops a card buying further into
an overweight class.

---

## Investigations — each with a defined first step

| item | state today | first step |
|---|---|---|
| **The +₪739 mint** in `rebalance_to_objective` | ROOT CAUSE UNKNOWN, confidence *nothing*. Unchanged since V6: three sightings, PR 20's currency flip ruled out in CI #180. | Loop `test_accept_conserves_nav.py` on both backends until it fires and capture the `NAV CONSERVATION BREACH` line. Fixed exit condition. |
| **`unexplained_basis_ils`** | **1,423.59** today, against 1,536.70 on 09-03 and 1,557.18 on 09-02. Third consecutive move, second downward. `basis_baseline_ils` still `null`. A number that falls while NAV rises is arithmetic, not an accumulating leak. | Sample `/portfolio` across a day against `price_refresh` timestamps before reading any code. |
| **Missing snapshot dates** | **Unchanged and now with a fresh instance.** `/portfolio/nav-history` still runs 08-16, 08-17, then 08-24 — 08-18 → 08-23 absent. And the identical-value pattern repeated this week: `pct` is `3.7135` on **both 09-04 and 09-05**, as it was `0.1281` on 08-28 and 08-30. | V6's hypothesis stands and is worth one hour: the scheduler is in-process, so every Railway restart loses that day's fire. Check restart history against those dates. |
| The TEVA agorot minor-unit test | Overtaken in part — PR 45 fixed the writer and `_safe_pb` rejects a `.TA` P/B. The original flaky test is unverified off the sandbox. | One run on the Windows box. |
| Postgres `StaleDataError` on `trading_rules` | root cause unknown, not seen since | — |
| The T5 seed window | INFERRED, never verified against production | — |
| Health score 92 → 82 | INFERRED | needs the four component chips rendered |

---

## Decisions only Eran can make — three, down from eight

| decision | why it is stuck | age |
|---|---|---|
| **Switch on `vol_target`** | buys 8.32 points of drawdown for 1.71%/yr and fails only because the threshold is 1.0 — a threshold you set. | 4 weeks |
| **The ceiling re-seed** | whether the solver's ceiling should ever auto-refresh | 3 weeks |
| **Walk-forward geometry** | one geometry was tested. Run the 5y/1y rolling and the expanding window, or accept the single result and say so. | 4 weeks |

**Closed since V6, with what closed them:**

- **The ₪618.65 in cash** — `cash_target_pct` is **7.5** and cash is ₪1,820 against a ₪1,641
  target. It is a decision now, not drift. (PR 54, and Eran setting the number.)
- **49.13% volatility** — answered by measurement: the blend reports 26.06% volatility and
  36.78% max drawdown. PR 53/55/56 moved the reporting onto measured numbers and made the
  remaining gap a stated window problem rather than an argument.
- **Broker integration** — parked in the product backlog, 2026-09-05, with its un-park
  condition. It was assumed to block the actionable-cards work; investigated, and it does not.
- **Land or discard `candidate_search`** — parked, un-parks on a caller.
- **Run or void the P0 Pixel QA** — parked, un-parks on a re-scope.
- **Israeli index funds** — parked today, un-parks on a provider that serves TASE fund symbols.

---

## Evidence appendix — 2026-09-06

```
/health                     446ceaa, 0021_plan_cash_target, builds_own_schema false
/api/v1/portfolio           nav 21874.63, cash 1820.48, unexplained_basis_ils 1423.59,
                            basis_baseline_ils null
/api/v1/plan                objective Grow, caps {concentration 0.40, volatility 0.25,
                            ruin 0.35}, cash_target_pct 7.5
/api/v1/plan/sleeves        btm_trend_soxl 33.91 / held 33.93 ; btm_qqq_hold 33.09 / held 0
/api/v1/mix                 Equities .8148, Fixed Income .1020, Cash .0832,
                            rebalance_required false
/api/v1/push/status         vapid_pinned_by_env true, vapid_env_matches_db true, subs 1
/api/v1/jobs                {"async_enabled": false, "mode": "eager (synchronous)"}
/portfolio/nav-history      08-16, 08-17, then 08-24..09-05. 08-18..08-23 absent.
                            pct 3.7135 on both 09-04 and 09-05.
load-basket btm_qqq_hold    pre-61.1: SELL VTI 2925.31 + TQQQ 4145.14, from_cash 179.88

grep -rn review_buy app/          -> allocation_agent.py:26 (def), pipeline.py:102 (only caller)
grep -n "current_price or bp"     -> broker_sync.py:33, unnormalised
grep -n "\.meta = meta" intake_service -> 281, 323
grep -c "if not|has_column|inspector" 0018 -> 2  (was 0)
grep -n auto_create_tables config.py   -> 53: False
grep -rho "float(" app | wc -l    -> 553 ; "Decimal(" -> 38
grep -rc unspent_ils tests/       -> nothing
grep -n add_middleware app/main.py -> CORS + _observe + _security_headers + _audit; none counts
Yahoo .TA probe                   -> 16 of 18 TASE shares complete; every numeric fund symbol
                                     Not Found; TA35.TA is the index, not a fund
```

_Not financial advice._
