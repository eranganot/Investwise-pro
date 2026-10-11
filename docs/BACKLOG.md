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
>
> Before working an item: confirm it is still open (several older items turned out to be closed). When it ships, delete
> the row, note the PR, and add a STATUS entry.
>
> Priority: **P1** do next · **P2** soon · **P3** when convenient · **Parked** = waiting on a decision · **?** = owner to decide.

---

## Ops (owner-only)

| # | Item | Status | Pri |
|---|---|---|---|
| O2 | **Rotate the GitHub PAT and the Railway token** (one project-scoped token). Never rotated since first flagged on 2026-08-03. | Open (plan W0-6) | P1 |
| O3 | **Database backups and a restore procedure.** Nothing in the repo describes them; the Railway CLI can't show backup status. | Confirmed gap 10-08. Check the Railway dashboard → Postgres → Backups (plan W0-1) | P1 |
| R1 | **Railway Config-as-Code (`railway.json`) is deprecated and stops working on 2026-12-01** (CLI warning, observed 10-08). It carries the preDeploy migration and the healthcheck. | Plan PR 119, **before 2026-11-15** | P1 (dated) |
| R2 | Orphan `redis-volume` (150 MB, attached to nothing) in the Railway project | Plan W0-2 | P3 |
| O4 | Install the `pre-push` hook on every machine (the branch-protection substitute) | Check with `ops-check.ps1` | P2 |
| O5 | Rotate the Postgres password | Parked deliberately: only for a reason (D40) | Parked |

## Product (owner decisions)

| # | Item | Source | Pri |
|---|---|---|---|
| P1 | **Review the goal definition.** Should deposits or extra years raise the "30% on every shekel" target? Plan v2.2 promised this backlog entry but it never reached STATUS. | plan v2.2, PR 99 "Decided 2026-10-05" | ? |
| P2 | **Align the two minimum-trade numbers:** ₪100 card minimum (`min_card_trade_ils`) vs ₪250 funded-leg minimum (`min_funded_trade_ils`). Both are settings now, but not aligned. **Verified 10-08:** below NAV ≈ ₪50k the ₪100 setting is inert, and the commodity card degrades into an un-actable "Buy ₪180 of X" heads-up. | STATUS PR 104c/104d | PR 115 (D-c) |
| P3 | The goal model doesn't simulate stops on BND/GOOGL/VTI/UGA/USO (only sleeve tickers + the guard). It is disclosed in the UI but not modelled. | STATUS PR 104b/104c.1 | P2 |
| P4 | Remaining hard-coded decisions → settings ("every decision is a setting"): `RISK_CAPS`, `CASH_FLOOR_PCT`, `OBJ_TARGET`, the 5-pt sleeve drift band, mute/done TTLs and re-fire deltas, near-rule/near-cap constants, card-priority weights, frontier draws/bands, Kelly-by-objective. **Inventoried 10-08:** 35 constants, 13 recommended (no migration needed). | Code review 10-07; `plans/evidence-2026-10-08/inv-cleanup.md` §P4 | PR 115 (D-d) |
| P5 | Broker integration (real orders). | `archive/plans/BROKER_INTEGRATION_PLAN.md` | Parked (D41) |
| P6 | Israeli index funds on TASE. | Provider can't price numeric symbols | Parked |
| P7 | B1: what `/plan` shows for volatility (store in-market vol nightly; match windows). | STATUS backlog B1 | Parked |
| P8 | B-margin-A (cash research: any name may replace a top-up), B-sells-trend (sell on trend alone), B-review-ratio (revisit the measured-edge bar). | STATUS backlog | Parked |
| P9 | V8 P3: per-holding day attribution on the NAV card; intraday "today" point on the chart. | `archive/plans/EXECUTION_PLAN_2026-09-14_v8.md` | ? |
| P10 | Promote engines to agents ("Tier 4") + a synthesising "Chief" agent (decided 2026-08-30, not started). Or drop it. | [AGENTS.md](AGENTS.md) | ? |
| P11 | The objective tilt for the screener (`OBJECTIVE_WEIGHTS` is empty and `OWNER_REVIEWED = False`). | AGENTS.md | ? |


## Correctness (verified 2026-10-08)

Verdicts: **P** = PROVEN by a reproducing probe or a live read; **I** = INFERRED (the plan says how to prove it). Evidence is in
[`plans/evidence-2026-10-08/`](plans/evidence-2026-10-08/).

| # | Item | Where | Verdict | Sev | PR |
|---|---|---|---|---|---|
| C6 | `/broker/connect` + `/broker/sync` inject mock TEVA/GOLD/BOND into the real book (READ_ONLY suffices, no ledger row) | `brokers/registry.py:12-14`, `broker_sync.py:107` | P | Med | 110 / 112 |
| C7b | `/whatif` (a **UI** route) falls back to demo TEVA/GOLD/HYPE on an empty book, ignoring `demo_signals` | `whatif.py:53` | P | Low | 110 |
| C7 | `/decision-feed/weekly` same fallback (empty book only; no UI caller) | `workflows.py:162-163` | P | Low | 113 |
| C8 | `/ask` and the AI summaries return model text unverified; an invented 37.4% / 412 came back verbatim, no disclaimer | `ask_service.py:37-45`, `ai_service.py` | P | Med | 110 |
| C12 | `broker_sync.py:33` raw price fallback; `intake_service` whole-dict `meta` assignments | V7 | — | P3 | 112 (broker deleted) |
| C13 | The unspent-leg refund (`unspent_ils`) has no test | V7 | — | P3 | — |
| C14 | Israeli CGT inflation-indexed basis not modelled (disclosed only); needs accountant sign-off | `tax_engine` | — | P3 | — |

## Security posture (verified 2026-10-08)

| # | Item | Verdict | Sev | PR |
|---|---|---|---|---|
| S1 | 25 of 67 write routes accept READ_ONLY (card accept, position PUT/DELETE, rules, push, broker). Reachable only via an m2m token today | P | Low (latent Med) | 110 |
| S2 | Logout doesn't end the session: bearer and replayed cookie both 200 after `/auth/logout` | P | Med-Low | 111.0 + 111 (D-f) |
| S3 | `/push/unsubscribe` and `/subscribe` aren't caller-scoped | P | Low | 110 |
| S4 | Anonymous: `/providers/health` leaks held tickers; `/markets/summary` does an uncached Gemini call per hit; `/adversary/diagnostics` too when enabled; `/docs` maps the API. No anonymous route returns holdings or NAV | P | Low-Med | 110 |
| S5 | `API_KEY` / `require_api_key` dead; the boot warning is false | P | Low | 110 |
| S6 | No inbound rate limiting | P | Low | parked (S4 removes the anonymous LLM spend) |

## Cleanup and tech debt (verified 2026-10-08)

| # | Item | Verdict | PR |
|---|---|---|---|
| L1 | Delete the legacy subsystem in 3 stages (routes/Celery/broker/no-op job → decision-feed cluster → 6 tables). **Keep** `engines/state_machine.py` and the lag/decision/risk/tax engines (war room, what-if use them) | Analysis | 112–114 (D-j) |
| L2 | `investwise.db` (Phase-2 snapshot, no `alembic_version`) and `frontend/` are unused; the CORS default is a ghost | P | W0-5 / 116 |
| L3 | `google-genai` pinned, never imported; `sentry-sdk` imported, unpinned (SENTRY_DSN does nothing) | P | 112 / 116 (D-h) |
| L4 | Naming: `cash_floor_*()` returns the soft target (₪3,000) while the setting `cash_floor_ils` is the hard floor (₪150); the UI says "floor" for the target; two `MIN_TRADE_ILS` | P | 116 |
| L5 | Stale docstrings: 7 in `app/` + 2 in tests + a dead `pyproject.toml` warning filter (suite: 2291 passed, 0 warnings without it) | P | 116 |
| L6 | Stale copy: "Cash tab" (`index.html:3052` **and** `frontier_service.py:2025` on a Today card), "Goal settings" (`:3252`), "the Rules tab". *"$ amount" is correct: the server sizes in USD* | P | 116 |
| L7 | Fat routes; **3 services import from `app.api.routes.plan`** (layering inversion) | P | 118a/b |
| L8 | float vs Decimal: 1,051 vs 45 in `app/`; no bug traced to it | P (count) | parked |
| L9 | Two `money()` helpers (2-dp float display vs 4-dp Decimal DB), same name | P | 116 |
| L12 | `.seg` defined twice; `--muted` undefined (bright "Ignored" labels); 17 non-focusable click targets; 0 `:focus` rules; no Escape/focus handling | P | 117 (D-i) |
| L14 | The commodity screen is plan-blind: it buys CANE (sugar) while the goal model prices commodities as IAU/DBC | P | 116 (D-g) |
| L15 | Gemini errors: done except grounded per-holding research (a 429 shows "temporarily unavailable" and is retried) | P | 110 |

## Verification debt

| # | Item |
|---|---|
| V1 | Pixel QA for PR 104a (`qa/QA-2026-10-06-pr104a-plan-tabs.md`). Its successors 104b–104d passed, so it is probably covered; record it or void it. |
| V2 | `qa/QA-2026-07-12-notification-alignment.md` and `qa/QA-2026-08-10-p0-safety.md` were never run. Void them or re-scope. |

## Recently closed (do not re-open)

- **O1 VAPID pin: closed 2026-10-08, live.** `/api/v1/push/status`: `vapid_pinned_by_env: true`, `vapid_env_matches_db: true`; daily 07:00 digests delivered.
- **C2c `redeploy_cash` claim: holds** (probe: drift vs book target 0.50 → 0.23).
- **"$ amount" commodity copy: correct**; the amount is in the instrument's currency (USD).
- B2 overlay retirement: executed in PR 59 (`btm_qqq_hold`).
- `cash_target_pct`: superseded by PR 64/68.1 (₪150 floor, ₪1,500 trigger).
- `alembic upgrade head` from nothing: fixed in PR 46 and tested.
- Agent key + default password rotation: done (PR 26 + 08-31).
- Repo hygiene of root plan files and old scripts: done in the 2026-10-07 docs overhaul (PR #1).
