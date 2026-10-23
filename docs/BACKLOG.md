# Backlog

> **The single list of open work.** Built 2026-10-07 from the old STATUS.md backlog blocks, execution plans V7/V8/v2.2,
> and a full code review done for the docs overhaul. It replaces the "Next", "Backlog" and "Ops actions" tables that
> used to sit at the top of STATUS.md (archived verbatim in `archive/status/STATUS-HISTORY-to-PR87.md`, with their evidence).
>
> **Active plan (2026-10-23):** [`plans/EXECUTION_PLAN_2026-10-23_after-review.md`](plans/EXECUTION_PLAN_2026-10-23_after-review.md)
> -- the prioritised order of everything below, with approach, tests, smoke and the decisions each item needs.
>
> **Previous plan (complete):** [`plans/EXECUTION_PLAN_2026-10-08_hardening.md`](plans/EXECUTION_PLAN_2026-10-08_hardening.md)
> (PR 105–119) sequences the rows below. Every C/S/L row was re-investigated on 2026-10-08 and is now PROVEN or
> INFERRED with a reproducing probe ([`plans/evidence-2026-10-08/`](plans/evidence-2026-10-08/)). The **PR** column says
> which PR closes each row. Plans v2.2 and frontier are complete.
>
> **Closed in PR 105 (2026-10-11):** C2b (sleeve-drift trim oversold by the USD/ILS rate), C3+ ("Replace book"
> multiplied NAV by the rate), C3, C3r and C15 (`/portfolio/add` replaced the holding). See STATUS.md PR 105.
> **Closed in PR 106:** C1, C2 (sleeve funding sold an under-target class; no post-trade drift gate).
> **Closed in PR 107:** C4, C5, L10, L11 (jobs that reported success when they failed; untested jobs; futures outage cache; boot waiting on Yahoo).
> **Closed in PR 108:** C10, C11, C16 (one regime acts; futures day change; `_OBJ_RETURN` investigated: labelled and gated, no change).
> **Closed in PR 109:** C9 (a read of Today deleted watch alerts and could switch off a triggered stop; D-b).
> **Closed in PR 110:** C6, C7b, C8, S1, S3, S4, S5, L15 (write routes ANALYST+, public allowlist, broker mock off in production, whatif demo gated, /ask and AI summaries number-checked + disclaimed, push caller-scoped, research 429 classified).
> **Closed in PR 111:** S2 (logout revokes this browser's token; D73).
> **Closed in PR 113:** C7 (`/decision-feed/weekly` deleted with the decision-feed cluster).
> **Closed in PR 112–114b:** L1 (legacy subsystem deleted in 3 stages, D76; tables dropped by migration 0029).
> **Closed in PR 115:** P2 (one card minimum, D-c), P4 (13 decisions became settings, D-d).
> **Closed in PR 119:** R1 (`railway.json` replaced by `.railway/railway.ts`, every setting kept).
> **Closed in PR 116:** L3 (Sentry deleted, D81), L4 (cash target naming), L5 (stale docstrings + the dead pytest filter), L6 (copy names real places), L9 (`display_money` / `db_money`), L14 (commodity buys use the core's funds, D80).
> **Closed in PR 117:** L12 (one `.seg`, D84; theme variables defined; focus ring; 17 click targets keyboard-reachable; Escape, Enter/Space and the sheet's focus handling).
> **Closed in W0-5:** L2 (`investwise.db` and `frontend/` removed; CORS is off unless `FRONTEND_ORIGIN` is set). Owner follow-up: production still sets `FRONTEND_ORIGIN=http://localhost:5173`; delete its line from `.railway/railway.ts`, then `railway config apply` removes it.
> **Closed in PR 123:** P3 (held stocks with sale rules are simulated as their own columns; every rule shows its goal impact, D85).
> **Closed in PR 118a/118b:** L7 (routes are thin; no service imports a route, guarded by `test_services_never_import_routes`).
>
> Before working an item: confirm it is still open (several older items turned out to be closed). When it ships, delete
> the row, note the PR, and add a STATUS entry.
>
> Priority: **P1** do next · **P2** soon · **P3** when convenient · **Parked** = waiting on a decision · **?** = owner to decide.

---

## Ops (owner-only)

| # | Item | Status | Pri |
|---|---|---|---|
| O6 | **GitHub Actions is not running** -- every job refused since 2026-10-13 10:40 UTC (the last green run was PR 127's branch at 10:25). Cause: the Free plan's Actions minutes ran out (owner, 2026-10-23); they reset on 2026-11-01. Each merge cost ~46 minutes (two runs of ~23: SQLite ~10, Postgres ~10, lint <1). Until then every PR is gated on full local SQLite + Postgres runs on Python 3.12, ruff, and its smoke against a locally served build. After the reset: run CI on push to main only, or drop the duplicate SQLite job, to halve the minutes. | Open | P1 | **Revisit the goal (D122).** The comparison is built (P1 PR: "Compare goals" on the Plan tab, priced nightly). Open: the owner reads it and picks a goal. | plan v4 §4.3, D122 | P1 (owner) |
| O2 | **Rotate the GitHub PAT and the Railway token** (one project-scoped token). Never rotated since first flagged on 2026-08-03. | Open (plan W0-6) | P1 |
| O3 | ~~Database backups and a restore procedure~~ | Owner confirmed 2026-10-23 (plan A3) | Done |
| R2 | ~~Orphan `redis-volume`~~ | Owner deleted it 2026-10-23 (plan A5) | Done |
| O4 | Install the `pre-push` hook on every machine | Installed in the cloud sandbox clone 2026-10-23 (self-test: a rewrite of main is refused); the owner's Mac: `~/iw-install-hook.sh` given 2026-10-23 | P2 |
| O5 | Rotate the Postgres password | Parked deliberately: only for a reason (D40) | Parked |

## Product (owner decisions)

| # | Item | Source | Pri |
|---|---|---|---|
| P1 | **Review the goal definition.** Should deposits or extra years raise the "30% on every shekel" target? Plan v2.2 promised this backlog entry but it never reached STATUS. | plan v2.2, PR 99 "Decided 2026-10-05" | ? |
| P5 | Broker integration (real orders). | `archive/plans/BROKER_INTEGRATION_PLAN.md` | Parked (D41) |
| P6 | Israeli index funds on TASE. | Provider can't price numeric symbols | Parked |
| P7 | B1: what `/plan` shows for volatility (store in-market vol nightly; match windows). | STATUS backlog B1 | Parked |
| P8 | B-margin-A (cash research: any name may replace a top-up), B-sells-trend (sell on trend alone), B-review-ratio (revisit the measured-edge bar). | STATUS backlog | Parked |
| P9 | V8 P3: per-holding day attribution on the NAV card; intraday "today" point on the chart. | `archive/plans/EXECUTION_PLAN_2026-09-14_v8.md` | P3 (owner, 2026-10-24) |
| P10 | Promote engines to agents ("Tier 4") + a synthesising "Chief" agent (decided 2026-08-30, not started). Or drop it. | [AGENTS.md](AGENTS.md) | ? |
| P12 | **"Best trend right now" commodity pick** (owner, 2026-10-11, D80): a way to catch the strongest commodity trend when the core basket is the default. Today `commodity_buys_core_only = 0` falls back to the plan-blind screen leader; a real design would size it as a separate, labelled bet. | D80 | Parked |
| P13 | **Revisit the stock universe for the Beat the Market stock strategies** (plan D6): 44 US names is thin for a top-10 ranking. Expand to the ~100 largest S&P names if MH1 or H1 pass the gate. | `plans/BEAT_MARKET_EXPANSION_2026-10-13.md` | Later |
| P14 | ~~Sleeve plan follow-ups~~. Closed 2026-10-23: the six sleeve-plan smokes run on `47e9ebe` (134d 17/0/0, 135a 8/0/0, 135b 5/0/1, 135c 5/0/1 after fixing its pinned `iw-v69` check, 136a 7/0/0, 136b 6/0/0); same-bet now also measured on what sleeves hold (D123). | sleeve plan | Done |
| P16 | ~~The halving limit~~. Decided 2026-10-23 (D122): keep 30%; revisit the goal instead (P1). | STATUS PR 136a | Done |
| P17 | **Live stock rotation** -- hold and rotate a selection strategy's monthly picks, so Explore-only strategies (Quality Momentum 5) can be added as sleeves. | D101 | P2 (owner, 2026-10-24: after C16 and PR 145) |
| P18 | **Fixed-amount goals in the goal model.** Only a %/yr goal gets odds, sleeve moves, the optimiser and a starter book; a plan with an amount and no yearly ROI gets none. | D105 | P3 (owner, 2026-10-24) |
| P19 | **Objective default core mixes as settings** (used only when no core is chosen; code constants today -- "every decision is a setting"). | owner, 2026-10-18 | P2 (owner, 2026-10-24: a quick item) |
| P15 | **Single-stock short selling.** The book, funding and backtest engine are long-only end to end; it needs negative quantities, margin, borrow fees and forced exits through every layer, and the research in the sleeve plan finds the edge mostly gone after fees. Inverse-ETF legs (PSQ/SH) are the in-plan route. | sleeve plan, research | Parked |
| P11 | The objective tilt for the screener (`OBJECTIVE_WEIGHTS` is empty and `OWNER_REVIEWED = False`). | AGENTS.md | ? |


## Correctness (verified 2026-10-08)

Verdicts: **P** = PROVEN by a reproducing probe or a live read; **I** = INFERRED (the plan says how to prove it). Evidence is in
[`plans/evidence-2026-10-08/`](plans/evidence-2026-10-08/).

| # | Item | Where | Verdict | Sev | PR |
|---|---|---|---|---|---|
| C12 | `broker_sync.py:33` raw price fallback; `intake_service` whole-dict `meta` assignments | V7 | — | P3 | 112 (broker deleted) |
| C13 | The unspent-leg refund (`unspent_ils`) has no test | V7 | — | P3 | — |
| C14 | Israeli CGT inflation-indexed basis not modelled (disclosed only); needs accountant sign-off | `tax_engine` | — | P3 | — |
| C15 | ~~Core realign keeps selling TQQQ right after sleeve top-ups~~. Closed (D119): the trim was sold pro-rata across every sleeve, short ones included; now only over-size sleeves, from their own holdings, never a basket ticker. | `core_realign` | P | P2 | C15 |
| C16b | ~~Why were the stops OFF?~~ Closed by PR 140: the protection card's all-or-nothing `retire_sleeve_exits` paused every stop on the book; it is gone, Accept refuses a stop, and every pause now writes a rule event. | `frontier_service` | I | P1 | PR 140 |
| C16 | The `fund_plan` card variant has no funder preview, so it can still offer an Accept the funder refuses (same bug as PR 137) | `recommendations.py` | I | P2 | — |
| C17 | The refusal alert on a failed Accept shows only the short error, not the reason and legs the Plan tab shows | `index.html` | P | P3 | — |
| C19 | ~~A stop on SOXL did not stop the app buying TQQQ~~ (2026-10-22 review G3/G6). Closed by PR 141 (D116): a stop hold on a 2x/3x fund blocks every 2x/3x fund for every buyer; the held-cash card offers non-blocked places only; a blocked sleeve gets no top-up card (so no daily drift push). | `buyback_hold` | P | P1 | PR 141 |
| C20 | ~~Ignoring a fired stop switched it off~~ (2026-10-22 review). Closed by PR 142 (D117): Ignore re-arms it below today. | `rules_service` | P | P1 | PR 142 |
| C21 | ~~Rule-check failures were silent; job history was lost on every deploy~~ (2026-10-22 review). Closed by PR 143 (D118). | `pricing_service`, `scheduler` | P | P1 | PR 143 |
| C22 | ~~The ledger wrote 140 of 440 trades at price 0 / ₪0~~. New trades fixed by PR 147; the owner applied PR 147.1's repair 2026-10-23 (87 measured rows repriced; 27 estimates left as they are, by choice). | `ledger_service` | P | P2 | PR 147 / 147.1 |
| C23 | ~~A broken build or job was silent~~ (2026-10-22 review). Closed by PR 144 (D121): boot check, job-health heads-up, app-health pushes. Open: a daily production smoke as a scheduled task (plan D-1). | `scheduler`, `push_service` | P | P1 | PR 144 |
| C24 | ~~The book target still sized the 2x/3x sleeves past the ceiling~~. Closed by C24 (D125): `sleeve_service.all_sleeve_targets` caps 2x/3x tickers pro rata at `leveraged_ceiling_pct`; the excess is managed in the core; the Plan tab says so. | `sleeve_service`, `book_target` | P | P2 | C24 |
| C26 | **The goal note's "lower your target" lever can fall below the S&P** (owner, 2026-10-23: "once I choose the beat the market strategies the goal is to get higher return than the s&p so a return of 3.7/yr would not be good enough"). Decided: **compare to the S&P**. The nightly goal solve also measures an S&P fund on the same model and confidence bar; the note never offers a target below it and says instead whether the book beats the S&P at your bar (whether the sleeves earn their risk). Live 10-23: median 15.6%/yr, volatility 43%, 3.7%/yr at the 65% bar with the rules armed. Same fix for the actionable "Set a target this book can reach: N%/yr" card (`frontier_service` ~2688), whose Accept would set the target to 3.7%. Approach: run the solve's history-basis tail (`paths.simulate`/`goal_stats`, same p_star, deposits and deadline) on an all-SPY book; store `benchmark` {amount/roi at confidence, median CAGR, basis}. | `frontier_service`, `book_optimizer` | P | P1 | — |
| C25 | ~~No ceiling on the 2x/3x share of the book~~ (2026-10-22 review). Closed by PR 148 (D125): `leveraged_ceiling_pct` 44; planners clip, executors refuse, the writer backstops, Today filters and offers a trim when over. | `leveraged_ceiling` | P | P1 | PR 148 |
| C27 | ~~Two rebalance bands fired with "nothing to execute"~~ (owner, 2026-10-23). Closed by C27 (D127): the core card no longer reserves off-basket holdings for a sleeve shortfall a stop hold blocks. | `recommendations._core_plan` | P | P1 | C27 |
| C28 | A rebalance band's note keeps the target from when it was armed ("Your plan targets 7.2% of the book in QQQ") while the band now measures against the live plan target (26.1% after the core grew). Re-word the note on every evaluation, or drop the number from it. | `rules_service`, `advanced_rules` | P | P3 | — |
| C18 | ~~Auto re-arm for paused protective rules~~. Closed (D124): after `rearm_after_days` (3), never one already crossed. | `rules_service` | P | P2 | C18 |

## Security posture (verified 2026-10-08)

| # | Item | Verdict | Sev | PR |
|---|---|---|---|---|
| S6 | No inbound rate limiting | P | Low | parked (S4 removes the anonymous LLM spend) |

## Cleanup and tech debt (verified 2026-10-08)

| # | Item | Verdict | PR |
|---|---|---|---|
| L8 | float vs Decimal: 1,051 vs 45 in `app/`; no bug traced to it | P (count) | parked |

## Verification debt

| # | Item |
|---|---|
| V1 | Pixel QA for PR 104a (`qa/QA-2026-10-06-pr104a-plan-tabs.md`). Its successors 104b–104d passed, so it is probably covered; record it or void it. |
| V2 | `qa/QA-2026-07-12-notification-alignment.md` and `qa/QA-2026-08-10-p0-safety.md` were never run. Void them or re-scope. |
| V3 | `test_portfolio_totals::test_dismissed_recommendations_are_counted_and_restorable` fails locally on untouched main (live market data is rate-limited, so the second Today build does not rebuild the dismissed card); make it deterministic. |

## Recently closed (do not re-open)

- **O1 VAPID pin: closed 2026-10-08, live.** `/api/v1/push/status`: `vapid_pinned_by_env: true`, `vapid_env_matches_db: true`; daily 07:00 digests delivered.
- **C2c `redeploy_cash` claim: holds** (probe: drift vs book target 0.50 → 0.23).
- **"$ amount" commodity copy: correct**; the amount is in the instrument's currency (USD).
- B2 overlay retirement: executed in PR 59 (`btm_qqq_hold`).
- `cash_target_pct`: superseded by PR 64/68.1 (₪150 floor, ₪1,500 trigger).
- `alembic upgrade head` from nothing: fixed in PR 46 and tested.
- Agent key + default password rotation: done (PR 26 + 08-31).
- Repo hygiene of root plan files and old scripts: done in the 2026-10-07 docs overhaul (PR #1).
