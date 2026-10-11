# InvestWise Pro — Execution Plan V8

_Written 2026-09-14 by Claude, against HEAD `84be80b` (PR 67.1). Supersedes nothing: V7's
rows are not re-litigated here. This plan exists because of one question Eran asked today —
"the book dropped ₪1,395 overnight, make sure no money disappeared" — and because answering
it took an hour that the app should have answered in one line._

**The answer to the question: no money disappeared.** 93.2% of the drop is SOXL. The work
below is not a fix for that. It is the three instruments that made a two-minute question
take an hour, two of which were actively wrong.

---

## 0. Cold start

| | |
|---|---|
| Repo | `C:\dev\Investwise-pro`, branch `main`, HEAD `84be80b` (PR 67.1) |
| Production | `/health` → `84be80b`, v22.1, `schema_revision: 0022_rule_trim_fraction`, `builds_own_schema: false` |
| Tree | 1 modified (`STATUS.md`), 2 untracked: `candidate_search.py` + its test. **Third week unchanged.** |
| Book | NAV **₪20,978.51**, cash **₪150.02**, 6 positions: SOXL, QQQ, GOOGL, BND, USO, CASH. Objective Grow. |
| Sleeves | `btm_trend_soxl` 38.0 target / 38.16 held · `btm_qqq_hold` 37.0 / 37.06 held · core `bal_6040` 25% (implicit) |
| Contributions | ₪20,000, one entry, 2026-08-03. **No second flow has ever existed.** |

### House rules (unchanged, and they held today)

- Never `git add -A`; `git commit -F`, no BOM; PowerShell stays ASCII, no backticks.
- Read-only git from a sandbox uses `git --no-optional-locks`. Used today; no lock left behind.
- Run the Postgres job locally before pushing. Drop and recreate the database first.
- Every new test confirmed **failing against current HEAD** before it is accepted.
- **Validate the instrument before believing the number.** Applied twice today, and both times
  the instrument was the thing that was broken. See P0 and P1.

---

## What today's investigation established

```
Total drop 2026-09-13 -> now            -₪1,394.74

  price effect, today's book, Fri FX    -₪1,312.85   of which SOXL -₪1,367.11
  FX effect (shekel -0.73%)              +₪150.75
  residual (trades, swap, rounding)      -₪238.27    1.06% of book -- NOT decomposed
```

SOXL fell **-14.6%** (121.82 → 104.05) on a day **SOXX (1x semis) fell -4.96%** — "chip stocks
tumble after AI leaders call for slowdown". SOXL is 38% of the book. Everything else was flat
to up: GOOGL +3.1%, USO +0.8%, BND +0.0%, QQQ -0.4%.

Ruled out, each by an observation: money leaving the account (one contribution ever, zero
withdrawals, `flow_total_ils: 0.0`, `unexplained_flows: []`); a stale, zero or redenominated
price (all six fresh at 18:05Z, FX 3.0534 consistent across every row, each within intraday
drift of an independent quote); the ~20 card accepts destroying value (they account for at
most the ₪238 residual, not ₪1,395).

**Nothing in P0–P3 below is a fix for the drop. There is no defect in the drop.**

---

## P0 — `markets_service` reports a multi-day change as a day change. PROVEN.

`app/services/markets_service.py:38-62`. `_fetch_one` requests Yahoo's chart at
`interval=1d&range=5d` and takes `meta.chartPreviousClose` as the previous close. For that
range Yahoo returns the close **before the window**, not the previous session.

Observed today, directly against the same URL the service calls:

| | window start (09-09) | true prev close (09-11) | now | app says | truth |
|---|---|---|---|---|---|
| `CL=F` | 96.05 | 100.05 | 101.33 | **+5.50%** | **+1.28%** |
| `ES=F` | 7643.75 | 7659.50 | 7703.75 | **+0.78%** | **+0.58%** |

Crude is wrong by **4.2 percentage points**. This is why `/markets/summary` said crude was up
5.61% on a day the book's own USO moved +0.90% — a contradiction between two numbers the app
prints on the same screen.

**WHY SILENT** — the number is always plausible, it is never compared against a holding the
book prices itself, and on a quiet 5-day stretch it happens to be nearly right (ES today).

**BLAST RADIUS** — `/api/v1/markets/summary`; `_regime()` at `:66-105`, which derives
`regime` / `tone` / `equity_avg_change_pct` from these same figures and feeds them into the
recommendation engine and the LLM market banner; and through that, the 07:00 push digest.
Today the banner read *"Equity markets are broadly higher today"*.

**FIX** — take the previous close from the last two entries of `quote[0].close`, not from
`chartPreviousClose`. Keep `chartPreviousClose` only as a fallback when the series is short.

**THE CHECK** — a test the *data* can fail, not one the code can pass: assert the `CL=F` day
change agrees with USO's own day change within tolerance. That is a cross-instrument
reconciliation; it is the only kind that would have caught this.

**CORRECTION TO MY OWN CLAIM.** I told Eran this morning that the futures feed was "telling
you the opposite of reality". That is proven for crude. It is **not** true for equities: ES=F
genuinely is above Friday's futures settle, so the equity line was overstated by ~0.2 points,
not inverted. My sentence was wider than the evidence behind it.

---

## P1 — the performance backfill prices a phantom Pathward Financial position. PROVEN in part.

`POST /api/v1/portfolio/performance` returns `end_value_ils: 32,611.42` for a book whose NAV
is **₪20,978.51** — a 55% overstatement, in a field labelled ILS and shown in the UI.

`app/services/performance_service.py:63` builds `holdings` straight from `list_positions`,
including the synthetic cash row, and `:107` sets `qty["CASH"] = 150.02`. The response
confirms it: `holdings_analyzed: ["USO","GOOGL","CASH","BND","SOXL","QQQ"]`.

**`CASH` is a real listed ticker.** Yahoo resolves it to **Pathward Financial, Inc.**
(NasdaqGS, $78.94). The backfill is pricing ₪150 of shekels as 150 shares of a US bank.

**NOT YET ESTABLISHED** — the exact arithmetic. Substituting $78.94 gives ₪56,983, not
₪32,611, so either the series is adjusted-close or something else is wrong alongside it.
**First observation to take:** return (or log) the per-ticker series the backfill used and
compare each ticker's last point against `/portfolio`. Do that before writing the fix.

**WHAT IT COST TODAY** — this endpoint's `portfolio_index` said the book moved **-3.93%**
since Friday. Direct pricing says **-5.93%**. I built a decomposition on the -3.93% first,
concluded ₪537 had leaked out of the book through the accepts, and was wrong. The per-ticker
recomputation is what killed it.

**FIX** — exclude non-market rows from the backfill and carry cash as cash.

**THE CHECK** — the backfill's last point must reconcile to NAV within tolerance. A single
assertion, and it fails today.

---

## P2 — the trade ledger has no read path

`transactions` records every leg with `value_ils` and `tax_ils`, written by `ledger_service`.
No route exposes it, and `DATABASE_URL` names a private host that does not resolve from
outside (`scripts/ops/OPS_RUNBOOK.md:80`). So the **₪238.27 residual above cannot be
decomposed** — not from production, not from here.

Candidates for that ₪238, none observed: the 25% CGT that
`strategy_service._execute_funded_sleeve` (`:1043-1052`) deducts from the proceeds of every
sell of an appreciated holding and credits to nothing; the VTI → GOOGL swap; ordinary
rounding across ~20 accepts. **It is 1.06% of the book and it is not evidence of a leak** —
it is evidence that nobody can currently tell.

**FIX** — `GET /api/v1/portfolio/ledger?since=&until=` (ANALYST role): the legs, plus a daily
roll-up of sells, buys and tax. Then answer the residual from the roll-up.

This is the generic instrument. Every future "where did my money go" is one call instead of
an hour.

---

## P3 — nothing in the app attributes a day's move. PRODUCT DECISION, not a defect.

The app showed a -6.2% day and said nothing about why. The digest offered to rebalance the
core. The answer was one sentence: **SOXL -14.6%, 93% of the move, because semis fell 5%.**

Proposal: a per-holding contribution line on the NAV card — Δvalue since the last snapshot,
split into price / FX / trades. Honest only once P1 and P2 are done.

Second, smaller: there is no `nav_snapshots` row for 2026-09-14 yet (the job runs nightly), so
the chart still ends on Friday's peak while the headline shows today's number. The gap between
those two is exactly what "money disappeared" felt like. A live "today" point on the chart,
clearly marked as intraday, would close it.

**Eran's call whether P3 belongs in this pass at all.**

---

## P4 — carried, with age. Unchanged by this plan.

| age | item |
|---|---|
| 3 weeks | `app/services/candidate_search.py` + test still untracked. Land it or delete it. |
| 6 weeks | Pin `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` in Railway — pin the pair the DB already minted. |
| 6 weeks | Rotate the other three secrets (GitHub, Railway, Postgres). |
| 9 weeks | Decide on broker integration, or move it to the backlog. |
| 5 weeks | Pixel QA for the P0 safety batch, or void it. |

---

## Sequencing

| PR | contents | why together |
|---|---|---|
| **A** | P0 + P1 | Both are "an instrument lied, and I believed it". Same test discipline: each gets a cross-instrument reconciliation the data can fail. |
| **B** | P2 | Independent. New route + roll-up. Closes the ₪238 the same day it ships. |
| **C** | P3 | Only after A and B. Product decision first. |

Per PR: suite green locally including the Postgres job, ruff on `app` and `tests`, then push
and watch CI — remembering that **CI is not a gate** and Railway deploys on red. `STATUS.md`
gets the root cause, what was ruled out and why it was silent, not just what changed.

**Not in scope: anything that "fixes" the ₪1,395.** The market did that.
