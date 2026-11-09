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
> **Closed in W0-5:** L2 (`investwise.db` and `frontend/` removed; CORS is off unless `FRONTEND_ORIGIN` is set). Owner follow-up **done 2026-11-09**: `FRONTEND_ORIGIN` was deleted from the production service (the live API sends no `Access-Control-Allow-Origin`) and its `preserve()` line removed from `.railway/railway.ts`.
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
| O6 | ~~GitHub Actions was not running~~ (Free-plan minutes ran out 2026-10-13). Back since 2026-11-02, every run on main green. Closed by O6 (D143, 2026-11-08): CI is one Postgres job + ruff on pushes to main and on PRs; the SQLite job is gone (~9 of ~24 runner-minutes per run). CI still does not block a deploy (D28). | Closed | Done |
| O2 | **Rotate the GitHub PAT and the Railway token** (one project-scoped token). Never rotated since first flagged on 2026-08-03. | Open (plan W0-6) | P1 |
| O3 | ~~Database backups and a restore procedure~~ | Owner confirmed 2026-10-23 (plan A3) | Done |
| R2 | ~~Orphan `redis-volume`~~ | Owner deleted it 2026-10-23 (plan A5) | Done |
| O4 | ~~Install the `pre-push` hook on every machine~~ | Installed in the cloud sandbox clone 2026-10-23 and on the owner's Mac 2026-10-24 (self-test: a rewrite of main is refused) | Done |
| O5 | Rotate the Postgres password | Parked deliberately: only for a reason (D40) | Parked |

## Product (owner decisions)

| # | Item | Source | Pri |
|---|---|---|---|
| P21 | **Revisit the goal (D122).** The comparison is built (P1 PR: "Compare goals" on the Plan tab, priced nightly). Open: the owner reads it and picks a goal. Decide with P1. | plan v4 §4.3, D122 | P1 (owner) |
| P1 | **Review the goal definition.** Should deposits or extra years raise the "30% on every shekel" target? Plan v2.2 promised this backlog entry but it never reached STATUS. | plan v2.2, PR 99 "Decided 2026-10-05" | ? |
| P5 | Broker integration (real orders). | `archive/plans/BROKER_INTEGRATION_PLAN.md` | Parked (D41) |
| P6 | Israeli index funds on TASE. | Provider can't price numeric symbols | Parked |
| P7 | B1: what `/plan` shows for volatility (store in-market vol nightly; match windows). | STATUS backlog B1 | Parked |
| P8 | B-margin-A (cash research: any name may replace a top-up), B-sells-trend (sell on trend alone), B-review-ratio (revisit the measured-edge bar). | STATUS backlog | Parked |
| P9 | ~~Per-holding day attribution on the NAV card; intraday "today" point~~. Closed by PR 161 (D145): "What moved" under the Today chart, per holding, each on its own named session; the intraday point already existed (PR 138). | `archive/plans/EXECUTION_PLAN_2026-09-14_v8.md` | Done |
| P10 | Promote engines to agents ("Tier 4") + a synthesising "Chief" agent (decided 2026-08-30, not started). Or drop it. | [AGENTS.md](AGENTS.md) | ? |
| P12 | **"Best trend right now" commodity pick** (owner, 2026-10-11, D80): a way to catch the strongest commodity trend when the core basket is the default. Today `commodity_buys_core_only = 0` falls back to the plan-blind screen leader; a real design would size it as a separate, labelled bet. | D80 | Parked |
| P13 | **Revisit the stock universe for the Beat the Market stock strategies** (plan D6): 44 US names is thin for a top-10 ranking. Expand to the ~100 largest S&P names if MH1 or H1 pass the gate. | `plans/BEAT_MARKET_EXPANSION_2026-10-13.md` | Later |
| P14 | ~~Sleeve plan follow-ups~~. Closed 2026-10-23: the six sleeve-plan smokes run on `47e9ebe` (134d 17/0/0, 135a 8/0/0, 135b 5/0/1, 135c 5/0/1 after fixing its pinned `iw-v69` check, 136a 7/0/0, 136b 6/0/0); same-bet now also measured on what sleeves hold (D123). | sleeve plan | Done |
| P16 | ~~The halving limit~~. Decided 2026-10-23 (D122): keep 30%; revisit the goal instead (P1). | STATUS PR 136a | Done |
| P17 | **Live stock rotation.** Parked (D133): measured 2026-10-24 on production, no stock ranking beats SPY (15.35%/yr over 10 years): S2 13.75, R2 10.96, R1 9.58, R3 9.33, S1 9.24, S3 8.54; every one picks worse than an equal-weight hold of its universe and lowers the book. Re-open only for a ranking that passes the gate and beats SPY. | D101, D133 | Parked |
| P18 | **Fixed-amount goals in the goal model.** Only a %/yr goal gets odds, sleeve moves, the optimiser and a starter book; a plan with an amount and no yearly ROI gets none. | D105 | P3 (owner, 2026-10-24) |
| P19 | **Objective default core mixes as settings** (used only when no core is chosen; code constants today -- "every decision is a setting"). | owner, 2026-10-18 | Parked (owner, 2026-10-24) |
| P20 | **Relative value across two prices of one exposure** (dual listing, fund vs NAV). Parked with P6 (D133): the price provider cannot price TASE numeric symbols and no dual-listed name is held; Globes publishes live TASE/NY gaps but not their history. | owner | Parked (owner, 2026-10-24: "Park with P6") |
| P15 | **Single-stock short selling.** The book, funding and backtest engine are long-only end to end; it needs negative quantities, margin, borrow fees and forced exits through every layer, and the research in the sleeve plan finds the edge mostly gone after fees. Inverse-ETF legs (PSQ/SH) are the in-plan route. | sleeve plan, research | Parked |
| P11 | The objective tilt for the screener (`OBJECTIVE_WEIGHTS` is empty and `OWNER_REVIEWED = False`). | AGENTS.md | ? |


## Correctness (verified 2026-10-08)

Verdicts: **P** = PROVEN by a reproducing probe or a live read; **I** = INFERRED (the plan says how to prove it). Evidence is in
[`plans/evidence-2026-10-08/`](plans/evidence-2026-10-08/).

| # | Item | Where | Verdict | Sev | PR |
|---|---|---|---|---|---|
| C12 | `broker_sync.py:33` raw price fallback; `intake_service` whole-dict `meta` assignments | V7 | — | P3 | 112 (broker deleted) |
| C13 | ~~The unspent-leg refund (`unspent_ils`) has no test~~. Closed (2026-10-24): `test_c13_c17_c28_small_fixes.py` -- an unpriceable leg is skipped and credited back to cash. | V7 | — | P3 | C13 |
| C14 | Israeli CGT inflation-indexed basis not modelled (disclosed only); needs accountant sign-off | `tax_engine` | — | P3 | — |
| C15 | ~~Core realign keeps selling TQQQ right after sleeve top-ups~~. Closed (D119): the trim was sold pro-rata across every sleeve, short ones included; now only over-size sleeves, from their own holdings, never a basket ticker. | `core_realign` | P | P2 | C15 |
| C16b | ~~Why were the stops OFF?~~ Closed by PR 140: the protection card's all-or-nothing `retire_sleeve_exits` paused every stop on the book; it is gone, Accept refuses a stop, and every pause now writes a rule event. | `frontier_service` | I | P1 | PR 140 |
| C16 | ~~The `fund_plan` card variant has no funder preview~~. Closed by C16 (owner 2026-10-24: "ship it as a guard"): the drift top-up previews `fund_plan(dry_run=True, honour_holds=True)` too. Inferred, not observed live (no ticker two sleeves want on the live book). | `recommendations.py` | I | P2 | C16 |
| C17 | ~~The refusal alert on a failed Accept shows only the short error~~. Closed (2026-10-24): the Accept route's 400 detail carries the error, the reason, the funding sentence and the legs (`refusal_text`); every Today alert shows `detail`. | `index.html`, accept route | P | P3 | C17 |
| C19 | ~~A stop on SOXL did not stop the app buying TQQQ~~ (2026-10-22 review G3/G6). Closed by PR 141 (D116): a stop hold on a 2x/3x fund blocks every 2x/3x fund for every buyer; the held-cash card offers non-blocked places only; a blocked sleeve gets no top-up card (so no daily drift push). | `buyback_hold` | P | P1 | PR 141 |
| C20 | ~~Ignoring a fired stop switched it off~~ (2026-10-22 review). Closed by PR 142 (D117): Ignore re-arms it below today. | `rules_service` | P | P1 | PR 142 |
| C21 | ~~Rule-check failures were silent; job history was lost on every deploy~~ (2026-10-22 review). Closed by PR 143 (D118). | `pricing_service`, `scheduler` | P | P1 | PR 143 |
| C22 | ~~The ledger wrote 140 of 440 trades at price 0 / ₪0~~. New trades fixed by PR 147; the owner applied PR 147.1's repair 2026-10-23 (87 measured rows repriced; 27 estimates left as they are, by choice). | `ledger_service` | P | P2 | PR 147 / 147.1 |
| C23 | ~~A broken build or job was silent~~ (2026-10-22 review). Closed by PR 144 (D121): boot check, job-health heads-up, app-health pushes. Part 3 (plan D-1) done: the read-only smoke key shipped (D131) and the scheduled "Daily production smoke" runs every morning (its 11-07 run led to PR 158). | `scheduler`, `push_service` | P | P1 | PR 144 |
| C24 | ~~The book target still sized the 2x/3x sleeves past the ceiling~~. Closed by C24 (D125): `sleeve_service.all_sleeve_targets` caps 2x/3x tickers pro rata at `leveraged_ceiling_pct`; the excess is managed in the core; the Plan tab says so. | `sleeve_service`, `book_target` | P | P2 | C24 |
| C26 | ~~The goal note's "lower your target" lever could fall below the S&P~~. Closed by C26 (D126, 2026-10-23): the nightly solve measures an index book on the same paths; below it, no target card and no lower-target lever. Smoked 5/0/1 after the 10-24 solve. | `frontier_service`, `book_optimizer` | P | P1 | C26 |
| C25 | ~~No ceiling on the 2x/3x share of the book~~ (2026-10-22 review). Closed by PR 148 (D125): `leveraged_ceiling_pct` 44; planners clip, executors refuse, the writer backstops, Today filters and offers a trim when over. | `leveraged_ceiling` | P | P1 | PR 148 |
| C27 | ~~Two rebalance bands fired with "nothing to execute"~~ (owner, 2026-10-23). Closed by C27 (D127): the core card no longer reserves off-basket holdings for a sleeve shortfall a stop hold blocks. | `recommendations._core_plan` | P | P1 | C27 |
| C28 | ~~A rebalance band's note keeps the target from when it was armed~~. Closed (2026-10-24): live, all four bands were stale (QQQ 7.2% vs 12.3%, VTI 26.6% vs 19.7%, VXUS 8.9% vs 9.8%, SMH 3.9% vs 7.4%); the generated sentence is re-worded from the live target whenever it is read; a hand-written note is kept. | `rules_service` | P | P3 | C28 |
| A1 | ~~The goal model read a fixed stop as a trailing stop at today's distance~~ (2026-10-24 rules review). Closed by A1 (D128), with SPY as the benchmark. | `frontier_service.rules_spec`, `paths.simulate_rules` | P | P1 | A1 |
| A2r | ~~The protection retune never tried stop widths~~. Closed by A2r (D129): x1.5, x2, x3 (never off), ranked by the amount at the bar. | `frontier_service` tuning | P | P1 | A2r |
| C29 | ~~No card said what to change in a book that trails SPY~~ (owner, 2026-10-24). Closed by C29 (D130): the optimiser ranks by the amount at the bar; one "Your book vs the S&P 500" card lists the measured changes best first, one Accept each. | `book_optimizer` | P | P1 | C29 |
| C30 | ~~The core realign and the cash redeploy undo each other while sleeves are unfunded~~ (found 2026-10-25). Closed by PR 150 (D135): while a buyable sleeve is unfunded, the realign swaps off-basket names into the basket and keeps the core's size; nothing is left in cash for the cash card. Edge re-measured 2026-11-09 (PR 159): not live (book at target, every sleeve funded); on 11-06 the band trim cash funded the QQQ sleeve 16 min later, not a round trip. The churn found instead (buy ideas and the cash pick buying names the realign sells) is fixed by PR 159 (D144); the band-funded buy is C36. | `core_realign`, `recommendations._core_plan` | P | P1 | PR 150 |
| C31 | ~~A stop on SOXL also held 2x gold and the Treasury funds~~ (found 2026-10-26 reviewing the SOXL hold). Closed by PR 154 (D138): the stop group is the geared equity funds. | `buyback_hold.with_group`, `strategy_profile.STOP_GROUP` | C | P1 | PR 154 |
| C32 | ~~Today's "history_stale" banner stayed up for hours after one provider timeout~~ (owner, 2026-10-26). Closed by PR 153 (D136): it shows for a stale read a failure forced in the last 30 minutes. | `resilience.ResilienceTier`, `recommendations._provider_degraded` | C | P1 | PR 153 |
| C33 | ~~The cash card filled an unfunded sleeve's slot with a screener pick~~ (owner, 2026-11-02). Closed by PR 155 (D139): an unfunded sleeve leg's share is taken out of the cash card's gaps and its cash kept back for "Fund this sleeve"; core cards quote the managed core (64%, not 30%). Edge closed by PR 159 (D144): reproduced (Equities 67.5% -> 72% when the under class was capped); the plan-weight pass now skips classes over target. | `recommendations._redeploy_cash_recs`, `card_scope` | P | P1 | PR 155 |
| C34 | ~~A new card could reach Today with no push~~ (owner, 2026-11-02). Closed by PR 156 (D140): every new card id is pushed, at any severity, ready-to-arm included, with an overflow summary past 5 per run and a 30-minute check. Edge: a card that lives under 30 minutes can still be missed. Since PR 160/162 every Today build stamps its cards and `/cards/sightings` counts `never_pushed_short_lived`; read it after two weeks, and push from the build only if it is not zero. | `push_service.evaluate_and_notify` | P | P1 | PR 156 |
| C35 | ~~The protection card said "remove 0 rules" and "several on one fund" with no exits, and put a 5% stop on BIL~~ (owner, 2026-11-03). Closed by PR 157 (D141): cash-like funds get no exit; the title and first sentence are counted; a bounded width says so. | `protection_manager.card`, `role_of` | C | P2 | PR 157 |
| C36 | **A band rule tops up a sleeve ticker by selling another sleeve at its target** (inferred, 2026-11-09). 11-06 06:32: the QQQ band bought 912 ILS of QQQ funded by BIL while BIL was about at its 35% target, then the sleeve funding bought QQQ again and 750 was trimmed at 16:02. Not reproduced: the book at that minute cannot be rebuilt from the API. Next: a test with an unfunded sleeve and a band on its ticker. | `advanced_rules._band_buy_card`, `funding_service` | P | P2 | -- |
| A3r | ~~**One exit per holding.**~~ Closed by A3r (D132): core funds no exit (bands + book guard), sleeve / 2x-3x holdings one trailing stop at k x measured monthly volatility (k on the goal replay), no take-profits; the protection manager's one card; generators no longer arm exits. | `protection_manager` | P | P1 | A3r |
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
| V1 | ~~Pixel QA for PR 104a~~. Voided by PR 145: its successors 104b-104d passed on the same screens. The file stays in `qa/` for the record; re-open if wanted. |
| V2 | ~~`qa/QA-2026-07-12-notification-alignment.md` and `qa/QA-2026-08-10-p0-safety.md` never run~~. Voided by PR 145: both screens were rebuilt since (PR 102 heads-ups, PR 139-144 safety); the files stay in `qa/`; re-open if wanted. |
| V3 | ~~`test_portfolio_totals::test_dismissed_recommendations_are_counted_and_restorable` was flaky~~. Closed by PR 145: it dismisses the concentration trim the one-holding book always gets (it took the first card, which could come from live market data, and returned early on an empty Today). |

## Recently closed (do not re-open)

- **Daily-smoke false FAIL on the redeploy card: closed 2026-11-07 (PR 158).** The smoke ignored `cash_redeploy_at_ils`; the app was right. The trigger is now also a Plan-page setting (D142).
- **O1 VAPID pin: closed 2026-10-08, live.** `/api/v1/push/status`: `vapid_pinned_by_env: true`, `vapid_env_matches_db: true`; daily 07:00 digests delivered.
- **C2c `redeploy_cash` claim: holds** (probe: drift vs book target 0.50 → 0.23).
- **"$ amount" commodity copy: correct**; the amount is in the instrument's currency (USD).
- B2 overlay retirement: executed in PR 59 (`btm_qqq_hold`).
- `cash_target_pct`: superseded by PR 64/68.1 (₪150 floor, ₪1,500 trigger).
- `alembic upgrade head` from nothing: fixed in PR 46 and tested.
- Agent key + default password rotation: done (PR 26 + 08-31).
- Repo hygiene of root plan files and old scripts: done in the 2026-10-07 docs overhaul (PR #1).
