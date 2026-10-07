# History

> How the product got here: 370 commits from 2026-06-07 to 2026-10-07. Per-PR detail for PR 88 onward is in
> [`/STATUS.md`](../STATUS.md); everything earlier is in [`archive/status/`](archive/status/). Decisions are in [DECISIONS.md](DECISIONS.md).

**About numbering.**
- Until mid-August, work was labelled by phase: Phase 0–10, Waves A–D, Phases A–H, P0–P4, C1–C6, T0–T6, N, W.
- Integer **PR N** numbering starts at PR 1 (`5a6dfef`, 2026-08-23) and runs to **PR 104d.1**.
- Plan v2 shipped out of order: 101 → 99 → 100 → 102 → 103 → 104.

## Eras

| Era | Dates | What changed, and why |
|---|---|---|
| **0 · Skeleton + engines** | 06-07 → 06-08 | Phase 0 skeleton and the Tax engine, then in ~36 h: Risk (Monte Carlo + veto), Lag, Decision + Wealth Health Score, Simulation, War Room, Safety, Learning, intake, auth, Celery/APScheduler, JWT RBAC. A code review (`REMEDIATION_PLAN.md`) produced Waves A–C the same day: Decimal money, Alembic, CI, observability. Deployed to Railway from day 2. |
| **1 · Simple UI** | 06-09 | The nine-panel engine-room dashboard was unusable for a non-expert owner. `SIMPLIFY_PROPOSAL.md` → a guided `/app`: dark mode, Today / Holdings / Plan / Agents. |
| **2 · Make it real** | 06-14 → 06-15 | An audit found the UI ran on synthetic data ("financial substance 4/10"). `IMPROVEMENT_PLAN.md` A–H: Google sign-in + `REQUIRE_AUTH`, real Yahoo/Frankfurter data, an honesty clean-up, portfolio risk (VaR/CVaR), performance vs benchmark, grounded Q&A + digest. The Adversary moved to Gemini REST. The legacy dashboard was folded into `/app`. |
| **3 · PWA, push, ILS** | 06-21 → 06-29 | Installable PWA, web push, a screener, **ILS normalisation everywhere**, the Markets tab with a futures regime, 30-minute repricing, trading rules. |
| **4 · Alignment** | 07-12 → 07-18 | Pushes said "do things" while Today said "nothing to do". Fixes: two-way sync, why/how/impact on every card, **first-class cash**, Accept that actually acts, the **funding engine** (every card sized and funded). |
| **5 · Backtests + Beat the Market** | 08-02 → 08-04 | A five-issue bug batch; rules execute and every firing is recorded; persisted nightly backtests; "Beat the Market" becomes a plan family. |
| **6 · Multi-sleeve** | 08-10 → 08-13 | P0 safety (`load_basket` no longer deletes the book; the CASH-as-NASDAQ incident), sleeves as rules, a thread-safe resilience tier. Phase C: **a book runs N sleeves on one budget**, and the core is a visible choice. |
| **7 · Card claims + target solver** | 08-15 → 08-16 | Three cards had bought Equities by selling Equities "toward target" → **the card-claims invariant** and `propose_funded_buy`. Phase T: the target solver (excess CAGR under a drawdown ceiling, the right to abstain). Phase N: real NAV recorded daily. |
| **8 · The seven-day audit** | 08-23 → 08-31 | `FIX_PLAN_2026-08-23_audit.md` → numbered **PR 1–29**: Accept stops minting money (PR 1), the ledger (PR 3), one book target, walk-forward (Phase W: no strategy earns a win), measured assumptions (PR 16), minor units, credentials rotated after the repo had been public (PR 26), the agent map (PR 23). STATUS was split into history and open work. |
| **9 · Re-validated plans; deploy safety** | 09-02 → 09-07 | V5 → V6 → V7, each re-verified against production. Honesty PRs 30–39. **PR 46–52, the deploy/migration saga:** boot-time migrations caused a 2-hour outage, then the revert, then the healthcheck gate, then migrations as preDeploy. PR 57: Alembic is the only schema author, and the container is non-root. Cash on purpose (soft target, ₪150 floor). TASE companies. |
| **10 · Ledger truth** | 09-14 → 09-22 | "The book dropped ₪1,395: did money disappear?" No, but three instruments lied. Trading-day snapshots, cash that is not a bank stock, standing orders, the historical ledger repair (preview, measure, two agreeing records), **the server alone decides which cards show** (PR 69), health = On plan + On track (PR 70), one target mix with 10% Commodities (PR 71). |
| **11 · The owner's six items + rules** | 09-28 → 09-30 | PR 72–77: a triggered rule always has a card, a rebalance is trades, **the goal = money put in**, cash research, one return convention, a holding review that sells only with an edge. PR 78: phantom cash. PR 79–82: editable rules and four advanced rules. PR 83–87: buy-back-hold cash. |
| **12 · Goal-seeking frontier** | 10-05 | PR 88–98 + 101: the frontier engine (Ledoit-Wolf, Kelly), ILS + Black-Litterman inputs, **the goal is the objective** (70% chance, Kelly as a ceiling), fat-tailed paths, a goal-chosen core, protective rules replayed on goal paths, a 1% crash-risk limit, one return basis, dividend-adjusted history. "Every PR ships a smoke test" begins. |
| **13 · Cards truth + Plan tab + settings** | 10-06 → 10-07 | Plan v2.2, PR 99–104d.1: goal cards promise only what Accept delivers; every number says what it measures; every card is actionable or a heads-up; shorter cards; the **Plan tab = Goal · My mix · Explore** with edit sheets and a live odds preview; **every decision is a setting** (crash-risk limit, on-track bar, ten card thresholds). Today warm-up. Plan v2 done. |

## Incidents that became rules

| When | What happened | Rule it produced |
|---|---|---|
| 2026-08-10 | `CASH` re-quoted as NASDAQ:CASH by a second reprice path; NAV ~9× | Find every writer of a field before fixing it (D33). There is now one guarded reprice path |
| 2026-07-18 → 08 | The SW bump iw-v9→v10 dropped the push subscription: two weeks of silent notifications | Re-subscribe on every load if the subscription is missing |
| 2026-08-16 → 23 | `run_nav_snapshot_blocking` imported a non-existent name inside the function: 7 of 7 nights failed, unseen | Every scheduled job has a test that runs it; health blockers read all of `JOB_STATE` (D29) |
| 2026-08-23 | Accept minted +4.8% NAV per tap; an upsert *set* quantity (−₪4,388) | Accept conserves NAV; a single buy path (D22) |
| 2026-08-24 | `5a6dfef` shipped with three cross-loop failures only Postgres CI saw; Railway served it anyway | Cross-loop sessions use NullPool; run Postgres locally; CI is not a gate (D27, D28) |
| 2026-08-30 | Agent key and default password had been published while the repo was public | Repo made private, credentials rotated, a test scans for burned values (D39) |
| 2026-09-04 | PR 47 ran migrations at boot: 2-hour 502 outage, root cause lost with the logs | preDeployCommand migrations, a DB healthcheck gate, verify the SHA (D16, D30) |
| 2026-09 (PR 52 era) | A boot-time ALTER list silently missed `benchmark_ticker` for 10 days | Alembic is the only schema author (D16) |
| CI #182 | `py-vapid` 1.9.4 needed cryptography ≥ 46, which conflicted with the pin, and broke the build | Pins tested for installability (`test_the_pins_are_installable_and_installed.py`) |
| 2026-09-28 (PR 78) | Core realign paid for buys with cash that didn't exist (+41% NAV overnight) | Funding reads real cash only; the hold reserve is pre-committed |
| 2026-10-07 (CI #296) | War-room cache made two card-claim tests skip, and skips passed | A skip is never a pass (D36) |
| PR 82 | A rule row overflowed a 360 px phone; the page went 489 px wide and the bottom nav fell off-screen | Phone-width layout checks in QA |

## Superseded documents

All old plans, reviews and status files are kept unchanged in [`archive/`](archive/README.md), with a one-line description of each.
