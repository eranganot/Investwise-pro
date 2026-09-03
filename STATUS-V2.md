# InvestWise Pro — STATUS V2: the open work

> **SUPERSEDED.** The open-items list now lives in `EXECUTION_PLAN_2026-09-03_v6.md`,
> rebuilt by re-validating every item against production. Twelve items listed below were
> closed on 09-02 and more on 09-03. Kept for its cold-start notes and house rules only.
>
> **Do not read its NAV-leak section as current.** It says the `rebalance_to_objective`
> leak is Postgres-only and loses ₪739.21. Both halves are wrong: it *mints* +₪739, and
> CI #180 caught it on the **SQLite** job with PR 20 applied (`STATUS.md`, "The +739.2 ILS
> leak is NOT fixed by this").


_Written 2026-08-29 at commit `a06a182` (PR 16). This file is a **cold-start
handoff**: it assumes the reader has none of the conversation that produced it._

`STATUS.md` remains the history — what shipped and why. **This file is only what
is left.** Read the house rules first; they are not optional and two of them
exist because they were broken.

---

## 0. House rules — read before touching anything

**Shipping**

- The local gate mirrors `.github/workflows/ci.yml` **exactly**: plain
  `pytest -q` with **no extra flags**, plus `python -m ruff check app tests`
  (both directories). Adding a flag to a command whose purpose is to mirror CI
  defeats the command.
- **All three CI jobs run before pushing**, including `test-postgres`. It is the
  one job that cannot be reproduced by the SQLite run and it has caught real
  defects. See §5 for how to run Postgres locally.
- **Never `git add -A`.** Name every file explicitly.
- Commit messages via `git commit -F <file>`.
- **Never `git commit --amend` a commit that has been pushed.** This rewrote a
  published commit once and diverged `main`. The recipe is: `git am --3way` →
  if it says *"Patch already applied"*, **stop** → otherwise copy `STATUS.md`
  into place and commit it as **its own commit**.
- PowerShell scripts are **pure ASCII with no backticks anywhere**. A backtick is
  an escape character inside a double-quoted PowerShell string; a line
  continuation written with one has broken a script here before.

**Per PR, three deliverables, not one**

1. `scripts/deploy/ship-prN-<name>.ps1` — dot-sources `_common.ps1`, runs the
   gate, commits only named files with `git commit -F`, pushes, then polls
   `/health` until `.commit` matches the pushed SHA before declaring anything.
2. `scripts/smoke/smoke-<name>.ps1` — read-only by default, `-Sha` to prove the
   deploy landed. House rules from `smoke-p0.ps1`: **a SKIP is never a PASS**;
   read paired state in one call; print the exception *type* on first failure;
   the headers variable is `$ApiHeaders`, never `$h` (PowerShell variable names
   are case-insensitive and `$h` once silently clobbered the auth headers).
3. A **"check with your own eyes"** block naming what should visibly change and
   what is still broken because a later PR owns it.

**Domain rules** (`investing-discipline` skill — load it for any strategy,
projection, recommendation or scenario work)

- **Ground or abstain.** Never invent a number. If something cannot be computed,
  say *why*, by name, with a fix hint.
- A quote that succeeds is not a quote that is current — always check
  `price_as_of`.
- Money-touching operations: dry-run by default, print before/after NAV, and say
  plainly when something is bookkeeping rather than a trade.
- Projections in real terms, ILS-normalised, from the same FX-normalised NAV
  every other card sizes against.
- Reproducibility pins: snapshot id + engine version + assumption version. A
  version change **invalidates** a pin rather than softening it.
- **The advice/execution firewall**: never place orders, never recommend
  specific securities outside the catalog, `execution_plan` always `None`.
- End investment answers with **"Not financial advice."**

**Two invariants the codebase enforces and you should not weaken**

- **Card-claim invariant:** a card may not claim a portfolio change it does not
  produce, and two cards may not disagree about one book.
- **A SKIP is never a PASS** — extended to tests: a silent `return` is a false
  green. Use `pytest.skip(...)`.

---

## 1. Tier 1 — money safety. Do this first.

### The NAV leak — `rebalance_to_objective` created ₪739.21

**Two sightings, both Postgres-only, both intermittent, both money-conservation.**

Most recent (2026-08-29, during PR 16's gate):

```
tests/test_accept_conserves_nav.py::test_every_actionable_card_on_a_live_book_conserves_nav
FAILED: rebalance_to_objective card 'Rebalance toward your target mix'
        leaked +739.21 ILS
pinned_prices = {'MSFT': 483.24, 'SCHD': 35.11, 'TQQQ': 71.17, 'V': 371.04}
```

First sighting was CI run #169 — same shape, different test
(`test_cash.py::test_accepting_a_sell_credits_cash_with_a_sane_cost_basis`,
`assert 192360.0 <= (41000.0 + 1.0)`).

**What has already been ruled out**, so nobody repeats it:

- Not caused by PR 16. Unmodified `HEAD` ran the full Postgres suite green as a
  control; PR 16's code ran it green on a repeat; and the failure landed at
  **test #4 of the run**, before PR 16's own test file is reached.
- Not fresh-DB schema, not `pytest-randomly` (not installed, so ordering is
  deterministic), not the `conftest` override, and the file passes 4/4 when run
  alone.

**The hypothesis to test first.** The set of cards `build_recommendations`
emits depends on **process-global circuit-breaker state**, so *which* cards
exist varies between runs. If that is right, the leak is **deterministic given
that card** and the intermittency is only about whether the card gets generated
at all — which means it is reachable in production whenever that card shape
appears, and the defect is in `_rebalance_to`
(`app/services/recommendations.py:2691`).

**Suggested approach:** build the leaking card directly from a fixture rather
than waiting for `build_recommendations` to emit it, then assert conservation on
the handler. If it reproduces deterministically, the intermittency question
disappears and it becomes an ordinary bug.

> This is the only remaining item where the app can still silently change what
> the book is worth. It outranks everything below.

---

## 2. Tier 2 — the 2026-08-29 live review

Eran raised eight items against the deployed app. Four were one defect and are
fixed by **PR 16** (the expected-return banner, the health score, the projection
and the year-6 chart all read a static per-ticker table instead of the measured
backtests). **Three remain.**

### 2a. Two cards contradict each other about the same book

One card sells ~₪1,071 of Equities (*"Rebalance toward your target mix"*, BOOK
scope); another buys ~₪3,139 of SOXL (SLEEVE scope) — and SOXL **is** Equities.
Accepting both moves the mix the wrong way.

`contradictions.py` rule 2 (`:101`) exists to catch exactly this and has **two
holes, both of which apply here**:

- `:111` — the sell side requires `str(r.get("title","")).lower().startswith("sell")`.
  The merged card is titled *"Rebalance toward your target mix"*, so its sell leg
  is invisible to the check.
- `:109` — `if not cls: continue`. The SOXL sleeve top-up is ticker-scoped and
  carries no `asset_class`, so its buy leg is invisible too.

**Fix:** read `apply.legs[]` and classify each leg through
`allocation_mix.classify`, instead of matching on title text and one
`asset_class` field. Title-matching is a failure mode `STATUS.md` already
records once ("every test in the area asserted on strings").

Smallest of the three. Card-claim invariant, so it is not cosmetic.

### 2b. The account history reads an added holding as +37% performance

The chart shows a +35% spike on 22 Aug and prints *"No deposits or withdrawals
in this window, so this is simply what your money did"*
(`app/static_app/index.html:820`). Both halves are wrong: ₪8,000 was added to a
~₪21.5k book, and then removed.

`nav_history.time_weighted` takes flows **only** from the contributions ledger
(`app/services/nav_history.py:304-305`, via `net_flow_between` at `:45`). A
holding added through intake moves NAV without moving `invested_ils`, so the
time-weighted return books it as performance. The round trip nets out
(+1.08% total), but the *shape* of the line is a fiction and the caption is an
active false claim.

**The signal already exists and nothing reads it:** `NavSnapshot` stores a
`positions` count on every row alongside `invested_ils`, precisely so a NAV move
with no matching deposit is detectable. When the position count changes between
two snapshots and `invested_ils` does not, the app must not claim the step was
performance.

### 2c. "Find the best split" ignores the sleeve total you accepted

The Target card says *"set your sleeves to 75%"*. Accept it, then run the split
search and it returns **40 / 40 = 80%**.

`max_total` for the search is `100 − cash_floor × 100` — i.e. **97%**
(`app/services/target_solver.py:102` and `:921`). The accepted verdict is never
an input, so the search answers *"the largest excess your ceiling allows
anywhere"* and stops at the 40% concentration cap on QQQ.

The two cards genuinely ask different questions and PR 14b made that visible,
but that is not the whole answer: **once a target has been accepted it is a
decision, and the split search should be constrained by it.** Feed the accepted
sleeve total in as `max_total`.

---

## 3. Tier 3 — older backlog, none of it blocking

| item | detail |
|---|---|
| **Rotate `AGENT_API_KEY`** | Hardcoded in **34 committed scripts**, 160+ commits of history, and pasted into chat transcripts repeatedly. Oldest open item; deferred four times. It has not become less true. |
| **Walk-forward geometry spread** | W1 ran one geometry (3y fit / 260-session test, rolling). Run the 5y/1y rolling and the expanding window too and report whether "no strategy reaches `FITTING_HELPS`" survives all three. Fold geometry is itself a researcher choice that can manufacture a result. ~3× compute. |
| **Ceiling re-seed — a decision, not a bug** | `index.html` seeds the drawdown ceiling from `performance(range=MAX).max_drawdown_pct` **only when null**, and a stale 13.3 then survived into every nightly verdict. PR 14 measures the mismatch and deliberately **never re-seeds**, because silently moving a risk constraint the user owns is worse than leaving it wrong. Whether it should ever auto-refresh is Eran's call. |
| **PR 16 follow-up: the health-score seam** | `nav_snapshots` stores a daily health score and PR 16 changes what that score *means*, so the stored series has a seam at deploy. Recording the assumption basis on the row needs a migration; deliberately not smuggled into PR 16. |
| `alembic upgrade head` from scratch | Fails — `alembic/versions/0018_transaction_ledger.py` adds 7 columns unguarded. |
| Equity universe liveness | No liveness gate: COW (a delisted iPath ETN) returned a 14-month-stale price that fed NAV, gain %, allocation and every recommendation sized against NAV. The commodity path was fixed; equities were not audited. |
| Rule notes freeze their creation-day price | `app/services/rules_service.py:844` renders *"Caps downside ~15% below today's 77.30"* using the price at rule-creation time. Shows 77.30 while TQQQ trades at 69.01. |
| Commodity screen ignores the core's basket | Offers CANE when the core (All-Weather) already names IAU + DBC. |
| `_mean` drops `None` | `app/engines/screener_engine.py:66-68` filters `None` and averages what is left, so a metric that could not be fetched silently changes the denominator. Fixed for commodities, **still live for equities** (`rank_equities`, `:93-96`). |
| Device QA, weeks overdue | Enable notifications on the Pixel 9 and confirm `subscriptions: 1` — this is the single failing check in `smoke-all` (74 passed / 1 failed / 8 skipped) and it is a device action, not code. Then the P0 safety pass (`qa/QA-2026-08-10-p0-safety.md`, ~10 min). |
| Blocking provider I/O | `/recommendations` is ~5s warm on a single uvicorn worker making synchronous calls inside `async def` handlers, so one request blocks every other. Offload to a thread pool rather than shaving latency. |
| Pin VAPID keys in Railway | Currently DB-generated, so a DB reset invalidates every push subscription at once. |
| Broker integration | `BROKER_INTEGRATION_PLAN.md`, unstarted. Has been "next" for weeks — confirm it is still wanted before starting. |

---

## 4. What is already done — do not redo it

The seven-day audit (PRs 1–14b), **Phase T** (T5, T6a, T6b, sleeve count) and
**Phase W** (W1 walk-forward, W2 gate threshold, W3 contributions) are all
shipped, plus **PR 16**. `STATUS.md` carries the full entry for each.

Findings from that work that constrain new work:

- **`_MIN_OBSERVATIONS = 260`** (`app/engines/strategy_backtest.py`) — *"~1
  trading year; below this, CAGR is noise."* A calendar year is ~252 sessions,
  so **any window expressed in years fails this floor**. It has now decided
  **four** designs. Express windows in trading sessions.
- **W1's result:** zero of six strategies reach `FITTING_HELPS`. Three would have
  been reported as wins **on the mean alone** while winning only 3–4 folds of 6.
  Nothing here shows a stable out-of-sample edge — which is why PR 16 defaults
  the forward return to the *out-of-sample* CAGR, not the full window.
- **W3's result:** at this book's NAV, ₪1,000/month of deposits moves terminal
  value by **+₪171,404** against **+₪4,256** for a full extra point of annual
  return. Break-even is **₪26/month**. Every search in Phase T has been arguing
  over the ₪4,256.
- **Per-blend date axes:** `align()` intersects across everything in the feed, so
  one short-history ticker (JEPI, listed 2020-05) truncates every candidate.
  Scope feeds per blend. This mistake has been made twice.
- **Rate-limited ≠ delisted.** `_fetch` retries and classifies; before that, 18
  of 28 tickers came back "no history" and every candidate was excluded.

---

## 5. Environment notes

**Local Postgres** (required — `test-postgres` is the job only CI otherwise proves):

```bash
# superuser is 'investwise', NOT 'postgres'; port 55432
su postgres -c '/usr/lib/postgresql/16/bin/pg_ctl -D /tmp/pgdata -o "-p 55432" -l /tmp/pg.log start'
psql -h 127.0.0.1 -p 55432 -U investwise -d postgres -c "CREATE DATABASE iw_run_$(date +%s);"
DATABASE_URL="postgresql+asyncpg://investwise@127.0.0.1:55432/<db>" python -m pytest -q
```

CI gets a **fresh empty database each run**, so reproducing a CI failure means
creating a new database per run. `conftest.py` uses `os.environ.setdefault` for
`DATABASE_URL`, so an explicit env var wins and a Postgres run is genuinely
Postgres.

**Cross-loop trap.** Reaching for `AsyncSessionLocal` beside a `TestClient`
passes on SQLite and fails on Postgres — the client has already driven the app's
shared pooled engine in its own loop, so asyncpg rejects the connection. Use the
`own_session` fixture, which builds a throwaway `NullPool` engine in the test's
own loop. This has gone red twice, in two different files, *after* the rule was
written down — which is why it is a fixture and not a comment.

**Synthetic provider hazard.** `market_data_provider` defaults to `"builtin"`,
which is deterministic and seeded off the ticker. Any verification against real
tickers **must** set `MARKET_DATA_PROVIDER=yahoo` plus
`PROVIDER_RATE_LIMIT_PER_SEC=1..2`, or the numbers are fiction that looks real.

**`smoke-c3.ps1 -AllowExecute` places real trades.** Never run it unattended.

**Gate baseline at `a06a182`:** SQLite 1324 passed / 4 skipped; Postgres 16 1320
passed / 12 skipped; `ruff check app tests` clean.

---

Not financial advice.
