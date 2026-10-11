# Archive

Superseded plans, reviews and status files, kept **verbatim and under their original file names**, because code
comments and older STATUS entries cite them by name (e.g. `PHASE_W_PLAN.md`, `BEAT_MARKET_TARGET_SOLVER_PLAN.md`).
They were moved here from the repo root on 2026-10-07. **Nothing in this folder is current.** For current truth, read
[`../README.md`](../README.md). Decisions extracted from these files are in [`../DECISIONS.md`](../DECISIONS.md).

## plans/ (oldest first)

| File | Date | What it was | Outcome |
|---|---|---|---|
| `EXECUTION_PLAN.md` | 06-07 | The original build plan: stack, typed state machine, phases | Built; React/Vite later dropped |
| `REMEDIATION_PLAN.md` | 06-08 | Code-review Waves A–D | A–C done; Decimal only partial |
| `SIMPLIFY_PROPOSAL.md` | 06-09 | UX simplification, directions A/B/C | Led to the guided `/app` |
| `AUDIT_AND_FEATURE_PLAN.md` | 06-14 | Three-phase brief (validation, adversary, audit trail, what-if, brokerage scaffold, fees, backtests) | Done |
| `IMPLEMENTATION_SUMMARY.md` | 06-14 | What that three-phase build shipped | Historical |
| `IMPROVEMENT_PLAN.md` | 06-15 | Phases A–H after the app review: auth, real data, honesty, risk, performance, Q&A | Done (F partial) |
| `NOTIFICATION_ALIGNMENT_PLAN.md` | 07-12 | Push vs Today disagreement | Done; finished by PR 69 |
| `PLAN_2026-07-18_alignment.md` | 07-18 | Alignment phases 0–5: first-class cash, funding engine | Done |
| `BEAT_MARKET_NEXT_PLAN.md` | 08-04 | Beat the Market P0–P4; sleeves decisions table | Done |
| `FIX_PLAN_card_claims.md` | 08-15 | The card-claims invariant design | Done; principle lives on in CLAUDE.md / DOMAIN.md |
| `BEAT_MARKET_TARGET_SOLVER_PLAN.md` | 08-15 | Phase T (target solver), N (NAV history), W spec; decisions | Done |
| `FIX_PLAN_2026-08-23_audit.md` | 08-23 | The seven-day audit → PR 1–8 | Done |
| `PHASE_W_PLAN.md` | 08-29 | Walk-forward, regime gate as a setting, deposit-aware projection | Done |
| `PR16_MEASURED_ASSUMPTIONS_PLAN.md` | 08-29 | Measured forward assumptions | Done |
| `EXECUTION_PLAN_2026-08-29_v3.md` | 08-29 | Open list v3 | Superseded by V5 |
| `EXECUTION_PLAN_2026-09-02_v5.md` | 09-02 | Re-validated open list | Superseded by V6 |
| `EXECUTION_PLAN_2026-09-03_v6.md` | 09-03 | Re-validated open list | Superseded by V7 |
| `EXECUTION_PLAN_2026-09-06_v7.md` | 09-06 | The last full open-items sweep (waves, ops track) | Superseded by [`BACKLOG.md`](../BACKLOG.md) |
| `EXECUTION_PLAN_2026-09-14_v8.md` | 09-14 | V8, "the ₪1,395 drop" instruments → PR 67/68 | Done |
| `PLAN_2026-09-28_six_items.md` | 09-28 | The owner's six items → PR 72–77 | Done |
| `PLAN_2026-09-29_advanced_rules.md` | 09-29 | Four advanced rules → PR 80/81 | Done |
| `EXECUTION_PLAN_2026-10-05_cards-truth-and-plan-tab.md` | 10-05 | Execution plan v2.2: true numbers, actionable cards, the Plan tab → PR 99–104 | Done |
| `EXECUTION_PLAN_2026-10-05_frontier.md` | 10-05 | The goal-seeking frontier pipeline → PR 88–98 | Done |
| `BROKER_INTEGRATION_PLAN.md` | 06-14 | Broker abstraction + phased rollout | **Parked** (D41) |

## reviews/

| File | What it was |
|---|---|
| `APP_REVIEW.md` | 06-15 honest review ("financial substance 4/10"), which triggered the "make it real" work |
| `ARCHITECTURE_AUDIT.md` | 06-14 phase 1.1 audit (found the connected folder was a stale skeleton) |

## status/

| File | What it is |
|---|---|
| `STATUS-HISTORY-to-PR87.md` | The old STATUS.md top block (spine, carried backlog with evidence, ops table) + every per-PR entry from PR 87 back to the start, the corrections list, and the changelog |
| `STATUS-ARCHIVE.md` | Detailed STATUS for 2026-08-03 → 08-12 (Phases A/B/C, P0–P4, the CASH-as-NASDAQ incident) |
| `STATUS-V2.md` | The 08-29 cold-start handoff and house rules (superseded) |

## Other

| File | What it was |
|---|---|
| `PRODUCTION.md` | The old production checklist. **Partly wrong:** its start-command advice (`alembic upgrade head && uvicorn`) is the PR 47 outage pattern. Use [`../OPERATIONS.md`](../OPERATIONS.md) |
