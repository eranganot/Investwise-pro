# Backlog

> **The single list of open work.** Built 2026-10-07 from the old STATUS.md backlog blocks, execution plans V7/V8/v2.2,
> and a full code review done for the docs overhaul. It replaces the "Next", "Backlog" and "Ops actions" tables that
> used to sit at the top of STATUS.md (archived verbatim in `archive/status/STATUS-HISTORY-to-PR87.md`, with their evidence).
>
> **Active plan:** [`plans/EXECUTION_PLAN_2026-10-08_hardening.md`](plans/EXECUTION_PLAN_2026-10-08_hardening.md)
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
| O6 | **GitHub Actions is not running** -- every job refused since 2026-10-15 ("recent account payments have failed or your spending limit needs to be increased"). PR 131-136b merged on full local SQLite + Postgres runs instead. Fix in GitHub -> Settings -> Billing & plans. | Open | P1 |
| O2 | **Rotate the GitHub PAT and the Railway token** (one project-scoped token). Never rotated since first flagged on 2026-08-03. | Open (plan W0-6) | P1 |
| O3 | **Database backups and a restore procedure.** Nothing in the repo describes them; the Railway CLI can't show backup status. | Confirmed gap 10-08. Check the Railway dashboard → Postgres → Backups (plan W0-1) | P1 |
| R2 | Orphan `redis-volume` (150 MB, attached to nothing) in the Railway project | Plan W0-2 | P3 |
| O4 | Install the `pre-push` hook on every machine (the branch-protection substitute) | Check with `ops-check.ps1` | P2 |
| O5 | Rotate the Postgres password | Parked deliberately: only for a reason (D40) | Parked |

## Product (owner decisions)

| # | Item | Source | Pri |
|---|---|---|---|
| P1 | **Review the goal definition.** Should deposits or extra years raise the "30% on every shekel" target? Plan v2.2 promised this backlog entry but it never reached STATUS. | plan v2.2, PR 99 "Decided 2026-10-05" | ? |
| P5 | Broker integration (real orders). | `archive/plans/BROKER_INTEGRATION_PLAN.md` | Parked (D41) |
| P6 | Israeli index funds on TASE. | Provider can't price numeric symbols | Parked |
| P7 | B1: what `/plan` shows for volatility (store in-market vol nightly; match windows). | STATUS backlog B1 | Parked |
| P8 | B-margin-A (cash research: any name may replace a top-up), B-sells-trend (sell on trend alone), B-review-ratio (revisit the measured-edge bar). | STATUS backlog | Parked |
| P9 | V8 P3: per-holding day attribution on the NAV card; intraday "today" point on the chart. | `archive/plans/EXECUTION_PLAN_2026-09-14_v8.md` | ? |
| P10 | Promote engines to agents ("Tier 4") + a synthesising "Chief" agent (decided 2026-08-30, not started). Or drop it. | [AGENTS.md](AGENTS.md) | ? |
| P12 | **"Best trend right now" commodity pick** (owner, 2026-10-11, D80): a way to catch the strongest commodity trend when the core basket is the default. Today `commodity_buys_core_only = 0` falls back to the plan-blind screen leader; a real design would size it as a separate, labelled bet. | D80 | Parked |
| P13 | **Revisit the stock universe for the Beat the Market stock strategies** (plan D6): 44 US names is thin for a top-10 ranking. Expand to the ~100 largest S&P names if MH1 or H1 pass the gate. | `plans/BEAT_MARKET_EXPANSION_2026-10-13.md` | Later |
| P14 | **Sleeve plan follow-ups** (plan complete 2026-10-18, PR 131-136b): run `scripts/smoke/smoke-sleeve-plan-all.ps1` and record 134d-136b in STATUS; revisit the same-bet correlation limit (0.80 -> 0.75?) now that the catalogue has different engines. | [`plans/SLEEVE_OVERLAP_PLAN_2026-10-14.md`](plans/SLEEVE_OVERLAP_PLAN_2026-10-14.md) | P1 |
| P16 | **The halving limit (owner decision).** The optimiser (PR 136a) finds no measurable change that lifts the goal chance past ~9-10% within the 30% limit; without the protective rules ~26%, with ~67% halving. The limit is the lever left. | STATUS PR 136a | P1 |
| P17 | **Live stock rotation** -- hold and rotate a selection strategy's monthly picks, so Explore-only strategies (Quality Momentum 5) can be added as sleeves. | D101 | P2 |
| P18 | **Fixed-amount goals in the goal model.** Only a %/yr goal gets odds, sleeve moves, the optimiser and a starter book; a plan with an amount and no yearly ROI gets none. | D105 | P2 |
| P19 | **Objective default core mixes as settings** (used only when no core is chosen; code constants today -- "every decision is a setting"). | owner, 2026-10-18 | P3 |
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
| C15 | Core realign (`core_to_basket`, rec_f6f017) keeps selling TQQQ right after sleeve top-ups (net TQQQ ledger -991 since 10-14). Cause unexplained; `trim_sleeves` leg is sized from sleeve overshoot. | `core_realign` | I | P2 | — |
| C16 | The `fund_plan` card variant has no funder preview, so it can still offer an Accept the funder refuses (same bug as PR 137) | `recommendations.py` | I | P2 | — |
| C17 | The refusal alert on a failed Accept shows only the short error, not the reason and legs the Plan tab shows | `index.html` | P | P3 | — |

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
