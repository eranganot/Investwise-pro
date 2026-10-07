# InvestWise Pro — Execution Plan V5

_Written 2026-09-02 by Claude, against HEAD `4076ef1` (PR 39). Supersedes
`EXECUTION_PLAN_2026-08-29_v3.md`, `STATUS-V2.md` and the V4/V5 artifacts._

Every row was re-validated against **production or the current tree** before it earned a
place here — the standing rule is: *for every open task, make sure it is still open.*
Twelve candidates did not survive that test and are listed at the bottom rather than
deleted, because an item you fix twice costs more than one you never listed.

---

## 0. Cold start — read this before doing anything

| | |
|---|---|
| Repo | `C:\dev\Investwise-pro`, branch `main`, HEAD `4076ef1` (PR 39), clean tree, in sync with origin |
| Production | `investwise-pro-production.up.railway.app` — `/health` serves `4076ef1`, v22.1. **Nothing is waiting to be pushed.** |
| Auth | every `/api/v1` route needs `X-Agent-Key`. The working key is `scripts/.agent-key` (git-ignored). It never goes into a file, a screenshot or a chat window. |
| Book | NAV ₪20,945.79 across 5 positions; cash ₪618.65; contributed ₪20,000; +4.73% |
| Measured coverage | `measured_pct_of_book: 78.9` — the core abstains by name, `CORE_NOT_HELD`. See "the one action" below. |

**This file is what is open. STATUS.md is the history** — its PR entries are accurate and
are the best record of *why* each thing was done. `STATUS-ARCHIVE.md` holds 08-03 → 08-12.
`STATUS-V2.md` is superseded by this file and should not be used as an open-items list.

**Check the tree before you start.** `git status`. If tracked files are modified, another
session is mid-PR — two sessions in one working tree is the PR 33b failure with a second
author added.

**House rules that must survive the handover.**

- Never `git add -A` — `node_modules` is tracked.
- `git commit -F`, no BOM.
- PowerShell stays ASCII, no backticks; parse-check every `.ps1` with `pwsh` before running it.
- Never run a git write from a sandbox against the Windows mount — it leaves a
  `.git/index.lock` that blocks your own next commit.
- `New-Commit` is path-limited by design: list every modified tracked file, or PR 33b repeats.
- Run the Postgres job locally before pushing. A green SQLite run proves less than it looks.
- Every new test is confirmed **failing against current HEAD** before it is accepted.
- A card may never claim a portfolio change it does not produce (`tests/test_card_claims.py`).
- Evidence discipline: Evidence Block, PROVEN vs INFERRED, run the command before writing
  the sentence.

---

## The one action that is worth more than any PR here

**Press the core card.** Today carries *"Bring your core back to Classic 60/40"* with
`apply.kind = core_to_basket` (PR 39). Your core is `bal_6040` at 27% of the book and it
holds none of VTI, plus V which is not in the basket at all.

That single button does three things at once: it makes the book match the strategy you
chose; it takes `measured_pct_of_book` from **78.9% toward 100%**, because PR 37 measures
the core nightly and only abstains because the holdings are not the basket; and it removes
the reason three of the last four PRs existed.

Two things to know before pressing it. The card lists every leg before the button and the
executor runs the same plan, so it cannot promise a trade Apply will not make. And after
CGT (≈₪143) and the cash floor, **₪565 of the buy stays unfunded** — the card says so on
its face rather than quietly buying less than it said. That residual is a funding decision,
not a bug.

---

## Wave 1 — money the app can still get wrong

PR 31 fixed a bond filed as a stock; PR 32 fixed a quote of zero written into the book.
Same family, not finished. Verified open at `4076ef1`.

1. **The NAV leak.** `rebalance_to_objective` loses ₪739.21 on Postgres. Tier 1 — the only
   remaining place the app can silently change what the book is worth. Postgres-only, so
   the SQLite gate does not see it. It has been flagged and deferred four times.
2. **Four writers put a price on a holding without normalising it.** `add_holding`
   (`app/api/routes/intake.py:344`) takes `float(guarded_quote(req.ticker).price)` raw —
   no `major_unit()`, no `price_source`. Add a Tel Aviv ticker by hand and it books in
   agorot. Same gap in `broker_sync.py:33`, the CSV path (`intake.py:39-54`) and
   `intake_service.py`. Only `pricing_service.py` repairs minor units and stamps provenance.
3. **`upsert_positions` overwrites `meta` wholesale** — `intake_service.py:105` and `:241`,
   `row.meta = meta`, a fresh dict of intake fields that wipes `price_source`,
   `price_currency` and `price_as_of`. `pricing_service.py` shows the correct merge.
   Two lines; ships with #2.

**Gate:** a test that adds a `.TA` ticker through `add_holding` and asserts the stored
price is in shekels, plus one that re-runs intake over a priced position and asserts
`price_source` survived. Both must fail against `4076ef1` first.

---

## Wave 2 — caps and vetoes nobody reads

- **The Allocation Agent's veto never runs.** `review_buy()`'s only caller is
  `pipeline.py:102`, reached solely by `POST /decision-feed/generate`.
  `recommendations.py` never imports it, so nothing on the live path stops a card buying
  further into an overweight class. Pinned by
  `tests/test_the_agent_roster_is_what_actually_runs.py`.
- **A hand-set cap plays no part in what gets trimmed.** `funding_service.py:111` takes one
  scalar `cap`, always the plan-wide `concentration_cap`. No per-ticker lookup exists.
- **A rule for an unfunded sleeve is retired on the next read.** `rules_service.py:368` and
  `:736` — *"retired: … is no longer in your portfolio"*. "Not held" and "not held *yet*"
  are the same test.
- **The unspent-leg refund has no test.** `strategy_service.py` defines `unspent_ils`;
  `grep -rn unspent_ils tests/` returns nothing.
- **`_apply_buy_funded` sells before it knows it can buy.** Named by PR 39: it sells first
  and holds the proceeds as cash — *"survivable for one swap and not for three
  positions."* `core_to_basket` prices every buy leg first and aborts untouched; the older
  path does not.

---

## Wave 3 — finish the rotation (ops, ~40 min, no code)

The agent key and the SuperAdmin password are **done** — measured, not assumed.

- **Pin the VAPID keypair.** Live: `/push/status` → `vapid_pinned_by_env: false`,
  `vapid_env_matches_db: null`, `subscriptions: 1`. Runbook:
  `scripts/pin-vapid-from-db.sql`. **Pin the pair the database already minted** — a newly
  generated pair kills the live subscription silently.
- **The other three secrets have never been rotated** — GitHub token, Railway token,
  Postgres password (`PRODUCTION.md` §1). Now the oldest open item on the board.
- **Branch protection on `main`.** PR 33b is the argument.

---

## Wave 4 — the history that still has holes

- **Five nights with no snapshot** (18–22 Aug) and two pairs of identical NAVs
  (`21168.33` on 08-16 and 08-17; `21333.59` on 08-29 and 08-30). Cause unknown; one hour.
- **The unexplained cost basis is growing.** `/portfolio` → `unexplained_basis_ils`
  **1,557.18**, up from 1,460.39 earlier the same day, with `basis_baseline_ils: null`.
  A gap that moves is a live discrepancy, not a historical artifact.

The `unmeasured_days` the chart reports are **correct behaviour** — 24 Aug replaced half
the book and the reconstruction refuses to price it. A drawn line there would be a fiction.

---

## Wave 5 — latency and the request thread

- **Three market routes still block the loop.** `market.py:22`, `:31` and the research
  route call `guarded_quote` / `guarded_fx` / `ResearchAgent().scan()` unawaited inside
  `async def`. The recommendations path was already fixed with `await offload(...)`; apply
  the same pattern.
- **Jobs run synchronously.** `/api/v1/jobs` → `{"async_enabled": false, "mode": "eager
  (synchronous)"}`. Add Redis or declare eager the accepted posture.
- **The secondary provider is stubbable but not governed** — bypasses the rate limiter,
  breaker and cache. Carried from PR 28 and PR 29.
- **No rate limiting on our own endpoints.** `main.py` registers three middlewares, none of
  which counts.

---

## Wave 6 — the deploy you cannot repeat

- **`alembic upgrade head` fails from scratch** — `0018_transaction_ledger.py` adds seven
  columns with **zero** guards (`grep -c "if not|has_column|inspector"` → 0).
- **`AUTO_CREATE_TABLES=true` in production** — `config.py:27`; `main.py` runs `create_all`
  on boot and warns in production without disabling. It is what makes the item above
  survivable and also what makes it invisible. Fix the migrations first.
- **The image runs as root** — 16-line Dockerfile, no `USER`, no digest pin, no
  `HEALTHCHECK`, no `.dockerignore`, single stage.

---

## Wave 7 — numbers that are honest but not right

- **Money is a float everywhere it is used** — 482 `float(` against 38 `Decimal(`. DB
  columns are `Numeric(18,4)` so nothing is corrupted at rest; every calculation in between
  is binary floating point. Large, low-drama; deliberate or not at all.
- **Capital gains are computed on the nominal gain.** `tax_engine.py:16-21` —
  *"no inflation-linked cost basis adjustment"*. Israeli tax indexes the basis to CPI, so
  every tax-loss-harvesting card over-estimates the tax saved. The card on Today right now
  claims ₪20.
- **Risk is per-position, never portfolio-level** — no covariance matrix, no VaR from real
  returns, no measured beta, on a book that is 79% two leveraged sleeves. PR 37 built the
  measurement machinery for baskets; this is the portfolio-wide version.

---

## Wave 8 — things that look live and are not

- **Seven agents declared, five classes, four of them engines.** `war_room.py:18` declares
  seven; `app/agents/` holds five. Option 2 (promote the engines) was **decided 30 Aug** and
  deferred to Tier 4. Pinned by `test_the_agent_roster_is_what_actually_runs.py`.
- **"What's moving" is four fixture rows under a live clock** (`docs/AGENTS.md:77-93`) — it
  cannot show a fifth item no matter what the market does.
- **The objective tilt table is empty and one setting is dead.** `OBJECTIVE_WEIGHTS` is
  deliberately empty (no tilt survived measurement); `settings.objective` is written by
  `plan_service.py:55` and read by nobody.
- **Three smaller ones:** a rule note quotes the price on the day it was armed; the
  commodity screen offers CANE while the core names IAU and DBC; the equity universe never
  got the liveness gate the commodity universe got.

---

## Not schedulable until something new is known

| item | state |
|---|---|
| The TEVA agorot minor-unit test | root cause unknown, *confidence: nothing*. First step: one run on the Windows box to learn whether it reproduces off the sandbox. |
| Postgres `StaleDataError` on `trading_rules` | root cause unknown |
| The T5 seed window | INFERRED, never verified against production |
| Health score 92 → 82 | INFERRED; needs the four component chips rendered |

---

## Decisions only Eran can make

| decision | why it is stuck | age |
|---|---|---|
| **Fund the ₪565 the core realign cannot raise** | after CGT and the cash floor the buy is short. Add cash, accept a partial realign, or trim a sleeve below its target — the app will not choose. | new |
| **Broker integration** | named "next" since 13 July while 45+ commits shipped past it. Start it or move it to the backlog. | 7 weeks |
| **Switch on `vol_target`** | buys 8.32 points of drawdown for 1.71%/yr and fails only because the threshold is 1.0 — a threshold you set. On a 3× sleeve that trade is arguably worth taking. | 3 weeks |
| **49.13% volatility** | the right assumption for a book that is 79% leveraged sleeves? Every projection rests on it — and PR 37 means the core half is now measurable. | new |
| **The ceiling re-seed** | whether the solver's ceiling should ever auto-refresh | 2 weeks |
| **Land or discard `candidate_search`** | has tests, has no commit, sits among 20 untracked files | 2 days |
| **Run or void the P0 Pixel QA** | never run; 18 PRs have shipped on top, its pre-flight notes are already wrong | 3 weeks |
| **Walk-forward geometry** | one geometry was tested. Run the 5y/1y rolling and the expanding window, or accept the single result and say so. | 3 weeks |

---

## Struck from the list — validated, then removed

Twelve rows that other documents still call open.

| was on the list | what production or the tree actually says |
|---|---|
| Only 78.8% of the book is measured; the core is priced ticker-by-ticker | **Fixed.** PR 37 measures fourteen candidate cores nightly on the same `StrategyBacktest` table. It abstains with `CORE_NOT_HELD` because the book does not hold the basket — the number moves when the core card is pressed, not when more code is written. |
| The target card forgets what you accepted | **Fixed.** PR 36: `last_accepted()` reads the newest `PlanApplication` row; `_seedTarget` asks before it measures. An undo withdraws the acceptance. |
| "Buy Equities" never names an instrument | **Fixed.** PR 38: the core leg target is `core_pct × weight × NAV`, left un-aggregated, so the card names VTI / BND / V and the amounts. |
| A card marked Done stays hidden even if the book never moved | **Fixed.** PR 38: for `done` (never `ignored`), after 3 settle days, an unchanged number is evidence the trade was not made. A book that did move is never accused. |
| The core cards only give advice | **Fixed.** PR 39: `apply.kind = core_to_basket`, live on Today now. One plan sizes both the card and the button. |
| Rotate `AGENT_API_KEY` / `AUTH_PASSWORD` — 4 weeks old | **Done.** The local key returns `200`; the published default password returns `401` on `/auth/token`. One env var holds one value, so the published key cannot also be live. |
| Enable notifications on the Pixel 9 | **Done.** `subscriptions: 1`; digests fired 08-30 → 09-02 at 07:00, plus rule triggers. |
| Delete the corrupt 08-22 snapshot | **Done.** The date is absent from `/portfolio/nav-history`. |
| `scripts/.agent-key.previous` one `git add -A` from being committed | **Fixed.** `.gitignore:31` — `scripts/.agent-key*`. |
| Cash-mix uses two different denominators | **Never true.** `allocation_mix.py`, `recommendations.py:2563`, `:2604`, `contradictions.py:253` and the front end all divide by NAV inclusive of cash. |
| `gemini_generate` swallows every error class | **Fixed.** `_classify()` separates quota, auth, 404, timeout and transport. Only `digest_service` still calls the bare form. |
| Two dashboards; consolidate to one | **Not true now.** `frontend/` holds a lockfile and no source; `/dashboard` redirects to `/app`. |

---

## Housekeeping

`_to_delete/` holds stale `.git/index.lock` files moved out of `.git/` so they cannot block
a commit. Two tarballs (`iw-src.tgz`, `plans.tgz`) are untracked in the repo root. All are
safe to delete.

_Not financial advice._
