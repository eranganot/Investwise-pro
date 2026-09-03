# InvestWise Pro — Execution Plan V6

_Written 2026-09-03 by Claude, against HEAD `4076ef1` (PR 39). Supersedes
`EXECUTION_PLAN_2026-09-02_v5.md`._

V5's standing rule — *for every open task, make sure it is still open* — was applied to V5
itself. Every row below carries an observation taken this morning (05:26–05:50 UTC,
2026-09-03) against **production or the working tree**, not a citation of V5. Six rows
changed state overnight, one of them the headline. Nothing here is scheduled from a
document alone.

---

## 0. Cold start — read this before doing anything

| | |
|---|---|
| Repo | `C:\dev\Investwise-pro`, branch `main`, HEAD `4076ef1` (PR 39), **in sync with origin, tree NOT clean** |
| Tree | `STATUS.md` and `STATUS-V2.md` modified; **20 untracked files** (see P0). Both were edited at 19:22 on 09-02, one minute after V5 was written at 19:21 — the V5 session left its own STATUS edits uncommitted. |
| Production | `investwise-pro-production.up.railway.app` — `/health` serves `4076ef1`, v22.1. Nothing waiting to be pushed. |
| Auth | every `/api/v1` route needs `X-Agent-Key`. Working key is `scripts/.agent-key` (git-ignored). Never into a file, a screenshot or a chat window. |
| Book | NAV ₪20,950.86 across 5 rows: TQQQ ₪7,240.18 · SOXL ₪7,437.04 · VTI ₪3,391.73 · BND ₪2,263.26 · CASH ₪618.65 |
| Measured coverage | **`measured_pct_of_book: 97`** — was 78.9 in V5. The core is measured, not abstaining. |

**This file is what is open. STATUS.md is the history.** `STATUS-ARCHIVE.md` holds
08-03 → 08-12. `STATUS-V2.md` is superseded and must not be read as an open-items list —
it still carries the NAV-leak description that STATUS.md later corrected (see below).

**House rules that must survive the handover.**

- Never `git add -A` — `node_modules` is tracked.
- `git commit -F`, no BOM.
- PowerShell stays ASCII, no backticks; parse-check every `.ps1` with `pwsh` before running it.
- Never run a git write from a sandbox against the Windows mount. **Even `git status` is a
  write**: it takes `.git/index.lock`, and the mount refuses the unlink, so the lock
  survives and blocks your next commit. One was created and moved to `_to_delete/` at
  05:27 today. Use `git --no-optional-locks status` from a sandbox.
- `New-Commit` is path-limited by design: list every modified tracked file, or PR 33b repeats.
- Run the Postgres job locally before pushing. A green SQLite run proves less than it looks.
- Every new test is confirmed **failing against current HEAD** before it is accepted.
- A card may never claim a portfolio change it does not produce (`tests/test_card_claims.py`).
- Evidence discipline: Evidence Block, PROVEN vs INFERRED, run the command before writing
  the sentence.

---

## The headline: V5's "one action" has already been done

V5 opened with *"Press the core card"* and called it worth more than any PR on the board.
It was pressed. **PROVEN**, four independent observations:

```
/api/v1/plan              measured_pct_of_book: 97          (V5: 78.9)
/api/v1/portfolio         holds VTI 2.9716 and BND 10.404;  V is gone   (V5: "holds none of VTI, plus V")
/api/v1/mix               drift_percentage["Fixed Income"]: 0.0
                          rebalance_required: false
                          core_pct_of_book 0.27, target_from_core {Eq .162, FI .108}
                          current_allocation {Eq .8625, FI .108, Cash .0295}
/api/v1/recommendations    zero occurrences of "core_to_basket"; no core card on Today,
                           and none in hidden_cards either — it retired because it was done
```

Fixed Income sits at **exactly** its target. The realign landed in full, so:

- **The ₪565 funding decision is closed.** It was V5's newest "decision only Eran can make";
  it no longer has a question in it.
- **Three of V5's eight decisions and its entire lead section are spent.** The 27% core is
  now a measured `bal_6040`, which also means the 49.13% volatility assumption is testable
  against a number rather than argued (see Decisions).
- The only drift left is **cash at 2.95% against a 0% target** — ₪618.65 sitting out of the
  market. That is a smaller, real, and different question from the one V5 asked.

---

## What re-verification changed

| V5 said | production or the tree says today |
|---|---|
| "Press the core card" is the one action | **Done.** `measured_pct_of_book` 78.9 → 97; FI drift 0.0000. |
| Fund the ₪565 the realign cannot raise | **Closed** with it. |
| The NAV leak *loses* ₪739.21, *Postgres-only*, so the SQLite gate does not see it | **Wrong twice.** It **mints** +₪739 (`leaked +739.21 ILS`, NAV goes up), and `STATUS.md:1658-1662` records CI #180 failing it on the **SQLite** job with PR 20 applied. The SQLite gate *can* see it. Root cause still UNKNOWN, confidence *nothing*, and the leading candidate (the PR 20 currency flip) was **ruled out** in #180. Not a fix item — an investigation with a defined first step. |
| `unexplained_basis_ils` is *growing* — 1,460.39 → 1,557.18, "a gap that moves is a live discrepancy" | It moves **both ways**: **1,536.70** this morning, down 20.48 overnight while NAV rose. A number that tracks valuation in both directions is price/FX-linked arithmetic, not an accumulating leak. Same investigation, different first question. |
| Five nights with no snapshot (18–22 Aug) | **Six dates** absent: 08-18 … 08-23. One of them (08-22) was deleted deliberately as corrupt, so five are unexplained — but 08-23 is not in V5's window at all. |
| `_apply_buy_funded` sells before it knows it can buy — Wave 2, last bullet | **Live right now.** Today carries two cards on that exact path: *Consider USO — ₪1,308* and *Consider COMT — ₪775*, both `apply.kind = buy_funded`. This is the only remaining tap that can move real money wrongly. It leads this plan. |
| Money is a float everywhere — 482 `float(` vs 38 `Decimal(` | 467 vs 36 at HEAD. The shape holds; the numbers drift with the grep. |
| `upsert_positions` overwrites `meta` at `:105` and `:241` | Confirmed — **and a third at `intake_service.py:283`** (`position.meta = meta`) that V5 missed. |
| Clean tree, in sync with origin | In sync; **not clean** — 2 modified, 20 untracked. True when written (19:21); the same session then edited `STATUS.md` and `STATUS-V2.md` at 19:22 and did not commit them. |

Everything else in V5 — Waves 2 through 8, the twelve struck rows, the ops track — was
re-checked and **still holds**, at the file and line V5 named. Details in the appendix.

---

## P0 — the tree, before any PR (15 min, no code)

The next session's cold start is wrong until this is done, and PR 33b is what two sessions
in one dirty tree looks like.

1. `STATUS.md` (+45/-19) and `STATUS-V2.md` (+6) are modified and uncommitted. Commit them
   or restore them — decide which, do not leave them.
2. **20 untracked files**: `candidate_search.py` + `test_candidate_search.py` (a standing
   decision, below), 10 `ship-*.ps1` / `fix-*.ps1` / `commit-*.ps1` from PRs 26–30, 4
   `pr2N-commit-msg.txt`, 2 tarballs (`iw-src.tgz`, `plans.tgz`), and the two plan files.
   The commit-msgs and the tarballs are spent — delete.
3. `_to_delete/` now holds five stale `.git/index.lock` files. Safe to delete.
4. Add `EXECUTION_PLAN_*.md` to git, or to `.gitignore`, deliberately — right now the plan
   that governs the work is not in the repo's history.

---

## The sequence, ordered by what can still go wrong with money

### PR A — the buy that sells before it knows it can buy  ·  Tier 1  ·  live today

`_apply_buy_funded` (`app/services/recommendations.py:3736`, dispatched at `:3557`) sells
first and holds the proceeds as cash. PR 39 named it — *"survivable for one swap and not
for three positions"* — and `recommendations.py:3902` already carries the comment
describing the correct shape. `core_to_basket` prices every buy leg first and aborts
untouched; this path does not.

It is not theoretical. **Two cards on Today use it**, for ₪1,308 and ₪775. If a leg fails
to price after the sell, the book is left in cash and the card has already claimed the buy.

- **Fix**: price all legs before the first write; abort untouched on any failure. Lift the
  pattern from `core_to_basket`, do not re-derive it.
- **Gate**: a test that makes the buy leg unpriceable and asserts (a) no position changed,
  (b) cash unchanged, (c) the card reports a refusal, not a partial. Must fail at `4076ef1`.
- **Blast radius**: every `buy_funded` card. `tests/test_card_claims.py` is the invariant
  it must not break.

### PR B — the tax card over-states what it saves  ·  Tier 1  ·  live today

`app/engines/tax_engine.py:16-21` lists its own assumptions, and the third is
*"no inflation-linked cost basis adjustment"*. Israeli CGT indexes the basis to CPI, so
every tax-loss-harvesting card over-estimates the saving. **The *Harvest a tax loss* card
is on Today right now**, with `apply.kind = sell_losers`, quoting a number.

Two ways to close it, and this is a call, not a coin flip:

- **Disclose** (~1 hour): the card says the figure is nominal and an upper bound. Honest
  immediately, wrong by the same amount.
- **Index** (~half a day): pull a CPI series, index the basis, and the number becomes right.
  Needs a data source decision and a provider that can be unavailable.

Recommendation: **disclose in PR B, index later** — a card that names its own limit does not
violate the claim invariant; a card quoting a confident wrong number does.

### PR C — the veto and the cap that nothing reads  ·  Tier 2

Two halves of the same hole: the app has controls and the live path does not call them.

- `AllocationAgent.review_buy()`'s only caller is `pipeline.py:102`, reached solely by
  `POST /decision-feed/generate`. `recommendations.py` — which built every card on Today —
  never imports it. Nothing stops a card buying further into an overweight class.
- `funding_service.rank_trim_candidates(rows, snap, objective, cap, exclude)` takes **one
  scalar `cap`**, always the plan-wide `concentration_cap`. A per-ticker cap has nowhere to
  be read from.

- **Gate**: extend `tests/test_the_agent_roster_is_what_actually_runs.py` — it already pins
  `review_buy`'s source; make it pin the *call site on the live path* too. Failing first.

### PR D — four writers that put a price on a holding without normalising it  ·  Tier 2  ·  latent

Confirmed open at HEAD, all four:

- `app/api/routes/intake.py:344` — `float(guarded_quote(req.ticker).price)` raw. No
  `major_unit()`, no `price_source`. Add a `.TA` ticker by hand and it books in agorot.
- `app/services/broker_sync.py:33` — `bp.current_price or bp.cost_basis or 1.0`, same gap.
- `app/api/routes/intake.py:39-54` — the CSV path builds `IntakePosition` straight from
  strings.
- `app/services/intake_service.py` — and `row.meta = meta` at **`:105`, `:241` and `:283`**
  wipes `price_source`, `price_currency`, `price_as_of` on every re-intake.

`pricing_service.py` is the only writer that repairs minor units and stamps provenance, and
it shows the correct `meta` merge. Ship the four writers and the three merges as one PR —
same family, one test file.

- **Gate**: (1) add a `.TA` ticker through `add_holding`, assert the stored price is in
  shekels; (2) re-run intake over a priced position, assert `price_source` survived. Both
  failing at `4076ef1` first.
- **Why it ranks below A and B**: no broker sync runs today and no CSV import is scheduled,
  so this is a loaded gun, not a firing one. It is still Tier 2 because it writes money.

### PR E — the deploy you cannot repeat  ·  Tier 2  ·  ordering matters

Confirmed: `alembic/versions/0018_transaction_ledger.py` has **7 `op.add_column` and 0
guards** (`grep -cE "if not|has_column|inspector"` → 0). `app/core/config.py:27` sets
`auto_create_tables: bool = True`; `main.py:41` runs `create_all` on boot and `:161-162`
only *warns* in production. The 16-line Dockerfile has no `USER`, no `HEALTHCHECK`, no
`.dockerignore`, no digest pin, single stage.

**Do them in this order, and not in one commit**: guards in 0018 (and audit 0017/0019/0020
for the same) → *then* flip the `auto_create_tables` default → *then* the image. Flipping
the config while the migrations are still unguarded takes production down on the next boot;
right now `create_all` is the only reason a from-scratch deploy works at all.

### PR F — three routes that block the event loop  ·  Tier 3

`app/api/routes/market.py` — `market_quote` calls `guarded_quote(...)` unawaited inside
`async def`; `fx_rate` calls `guarded_fx(...)`; `research_events` calls
`ResearchAgent().scan()`. The fix already exists in the codebase: `await offload(...)`,
used 12 times, and `war_room.py` imports it three lines from the same style of call.
Mechanical, low risk, real latency.

Then a posture decision, not code: `/api/v1/jobs` reports
`{"async_enabled": false, "mode": "eager (synchronous)"}`. Add Redis or **declare eager the
accepted posture in `PRODUCTION.md`** and stop listing it as an open item.

---

## Ops track — ~40 minutes, no code, runs parallel to any PR

The agent key and the SuperAdmin password are done and were re-confirmed. What is left:

1. **Pin the VAPID keypair.** Live this morning: `vapid_pinned_by_env: false`,
   `vapid_env_matches_db: null`, `subscriptions: 1`. Runbook `scripts/pin-vapid-from-db.sql`.
   **Pin the pair the database already minted** — generating a fresh pair kills the one live
   subscription silently, and the digests that fired 08-30 → 09-02 went to that subscription.
2. **Rotate the three secrets that never have been** — GitHub token, Railway token, Postgres
   password (`PRODUCTION.md` §1). Now the oldest open item on the board.
3. **Branch protection on `main`.** PR 33b is the argument. *Could not be verified from
   here* — no `gh` CLI on the box; check it in the GitHub UI while you are in there.

---

## Honesty track — cheap, visible, no money at risk

- **"What's moving" is four fixture rows under a live clock.** `registry.py:63-64` returns
  `BuiltinEconomicDataProvider()` unconditionally; `builtin.py:113-130` returns the same
  four events forever; `index.html:2791` captions them *"Updates hourly · last refreshed
  {n} minutes ago"* from the **price-refresh** clock. Same failure PR 22 fixed on the
  history chart. Drop the caption or label the fixture — one line either way.
- **Seven agents declared, five exist.** `app/services/war_room.py:18` —
  `AGENTS = ["Research","Alpha","Risk","Tax","Decision","Adversary","UX"]`; `app/agents/`
  holds `adversary`, `allocation_agent`, `fee_agent`, `research_agent`, `screener_agent`.
  Option 2 (promote the engines) was decided 30 Aug and deferred. Pinned by
  `test_the_agent_roster_is_what_actually_runs.py`.
- **Dead settings.** `OBJECTIVE_WEIGHTS` is `{}` at `objective_weights.py:109` (deliberately
  — no tilt survived measurement, and the file says so); `settings.objective` is written at
  `plan_service.py:55` and read by nobody. Delete the reader-less one, keep the documented
  empty one.
- **Three smaller ones**, unchanged from V5: a rule note quotes the price on the day it was
  armed; the commodity screen offers CANE (`commodities.py:33`) while the core names IAU and
  DBC; `universe.py:140`'s `equity_universe()` never got the liveness gate the commodity
  universe got.

---

## Wave 7 carried forward — honest but not right

- **Money is a float everywhere it is used**: 467 `float(` against 36 `Decimal(` in `app/`.
  DB columns are `Numeric(18,4)`, so nothing is corrupted at rest; every calculation in
  between is binary floating point. Large, low-drama, deliberate or not at all.
- **Risk is per-position, never portfolio-level** — no covariance matrix, no VaR from real
  returns, no measured beta, on a book that is 70% two leveraged sleeves. PR 37 built the
  measurement machinery; this is the portfolio-wide version. *Partly overtaken*: the solver's
  `measured` block now reports a blended `volatility_pct: 26.06`, `max_drawdown_pct: 36.78`
  over 2,512 observations — that is a portfolio-level number, arrived at by backtest rather
  than by covariance.

---

## Investigations — not schedulable as fixes, each with a defined first step

| item | state | first step |
|---|---|---|
| **The +₪739 mint** in `rebalance_to_objective` | ROOT CAUSE UNKNOWN, CONFIDENCE nothing. Three sightings: CI #169, PR 16's gate (both Postgres), CI #180 (**SQLite**, PR 20 applied). PR 20's currency flip was the leading candidate and is **ruled out**. PR 21 shipped the `NAV CONSERVATION BREACH` line that names every position that moved. | Loop `test_accept_conserves_nav.py` on both backends until it fires and **capture the BREACH line**. Fixed exit condition: N runs or a captured breach — not "look at the code again". |
| **`unexplained_basis_ils`** | 1,460.39 → 1,557.18 → **1,536.70**; `basis_baseline_ils: null`. Moves both directions with valuation. | Sample `/portfolio` across a full day against `price_refresh` timestamps before reading any code. If it tracks price, it is arithmetic, not a leak. |
| **Six missing snapshot dates** (08-18 → 08-23) and two identical-NAV pairs (`21168.33` on 08-16/08-17; `21333.59` on 08-29/08-30) | Cause unknown. | **INFERRED hypothesis worth one hour**: the scheduler is in-process (`/push/status` shows APScheduler with `nav_snapshot` next-run and `runs: 1` since this process started). Every Railway restart loses that day's missed fire. Check deploy/restart history for 08-18 → 08-23 before writing code. The `unmeasured_days` the chart reports remain **correct behaviour**. |
| The TEVA agorot minor-unit test | root cause unknown, *confidence: nothing* | one run on the Windows box, to learn whether it reproduces off the sandbox |
| Postgres `StaleDataError` on `trading_rules` | root cause unknown | — |
| The T5 seed window | INFERRED, never verified against production | — |
| Health score 92 → 82 | INFERRED | needs the four component chips rendered |

---

## Decisions only Eran can make — six, down from eight

| decision | why it is stuck | age |
|---|---|---|
| **The ₪618.65 in cash** | 2.95% of the book against a 0% target, and the only drift left after the realign. Deploy it, or set a deliberate cash floor and stop calling it drift. | new |
| **Broker integration** | named "next" since 13 July while 45+ commits shipped past it. Start it or move it to the backlog. | 7 weeks |
| **Switch on `vol_target`** | buys 8.32 points of drawdown for 1.71%/yr and fails only because the threshold is 1.0 — a threshold you set. | 3 weeks |
| **49.13% volatility** | now answerable rather than arguable: the measured 10-year blend reports **26.06% volatility and 36.78% max drawdown** against a 34.1% benchmark drawdown. Keep 49.13 as a stress assumption, or move projections onto the measured number. | new |
| **The ceiling re-seed** | whether the solver's ceiling should ever auto-refresh | 2 weeks |
| **Land or discard `candidate_search`** | has tests, has no commit, still untracked. P0 forces the answer. | 3 days |
| **Run or void the P0 Pixel QA** | never run; 19 PRs have shipped on top, its pre-flight notes are already wrong | 3 weeks |
| **Walk-forward geometry** | one geometry was tested. Run the 5y/1y rolling and the expanding window, or accept the single result and say so. | 3 weeks |

*(Closed since V5: the ₪565 funding gap, and the core realign itself.)*

---

## Suggested order of work

```
P0   tree reconciliation                        15 min, no code
A    _apply_buy_funded prices before it sells    half a day   <- live money, two cards
B    tax card discloses nominal basis            1 hour       <- live number, on screen
ops  VAPID pin + three rotations + protection    40 min, no code, any time
C    review_buy on the live path + per-ticker cap  half a day
D    four price writers + three meta merges      half a day
E    migration guards -> config flip -> image     1 day, three commits, in that order
F    three routes onto offload                   1 hour
     honesty track                               1-2 hours total, pick them off
     investigations                              time-boxed, exit conditions above
```

A and B first because both are one tap away from a user today. The ops track is unblocked
by everything and can be done while waiting on a CI gate.

---

## Evidence appendix — commands run 2026-09-03, 05:26–05:50 UTC

```
/health                          {"commit":"4076ef1","version":"22.1","environment":"production"}
/api/v1/portfolio                nav_ils 20950.86, cash 618.65, contributed 20000,
                                 unexplained_basis_ils 1536.70, basis_baseline_ils null,
                                 5 positions: TQQQ SOXL VTI CASH BND
/api/v1/plan                     measured_pct_of_book: 97
/api/v1/mix                      FI drift 0.0, rebalance_required false, core_pct 0.27
/api/v1/recommendations          count 3: sell_losers, buy_funded x2. No core_to_basket.
/api/v1/push/status              vapid_pinned_by_env false, subscriptions 1, scheduler running
/api/v1/jobs                     {"async_enabled": false, "mode": "eager (synchronous)"}
/api/v1/portfolio/nav-history    12 dates; 08-18..08-23 absent; 21168.33 x2, 21333.59 x2
/api/v1/plan/target              measured: vol 26.06, maxDD 36.78, cagr 16.60, 2512 obs

grep -rn review_buy app/               -> only pipeline.py:102
sed -n 344p app/api/routes/intake.py   -> float(guarded_quote(req.ticker).price)
grep -n "\.meta = meta" intake_service -> 105, 241, 283
grep -rn unspent_ils tests/            -> 0
grep -cE "if not|has_column|inspector" 0018_transaction_ledger.py -> 0  (7 add_column)
grep -n auto_create_tables config.py   -> 27: True
wc -l Dockerfile                       -> 16; USER/HEALTHCHECK -> 0; .dockerignore -> absent
grep -c "float(" app/                  -> 467 ; "Decimal(" -> 36
sed -n 18p app/services/war_room.py    -> AGENTS = [7 names]; ls app/agents/ -> 5 modules
STATUS.md:1658-1662                    -> CI #180, SQLite job, +739.22, PR 20 applied
git --no-optional-locks status         -> 2 modified, 20 untracked
```

_Not financial advice._
