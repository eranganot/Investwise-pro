# Advanced trading rules: APPROVED 2026-09-29, built as PR 80 (migration) + PR 81 (rules)

_2026-09-29. Eran asked for "a few more trading rules (more advanced ones)" and picked all four below.
Builds on PR 79 (rule edit and plan-target conflicts). Nothing here gets coded until he approves it._

## What exists today (checked in code)
- `RULE_TYPES`: stop_loss, take_profit, trailing_stop, price_above, price_below, buy_dip, max_weight,
  strategy_signal. `_evaluate()` fires on **price / weight vs one `level`**. `execution_plan()` turns
  a firing into a trim or sell_position. Rules are evaluated every 30 minutes (`evaluate_user`).
- `trading_rules` stores one number per rule (`level`), plus `mode`, `peak_price`, `trim_fraction`
  and `strategy_id`. **Three of the four new rules need a second parameter**, so they need somewhere
  to keep it (see Decision 1).
- The pieces these rules can reuse, so nothing gets a second implementation:
  `plan_ticker_targets()` (PR 79), `holding_review` metrics and backtests (PR 77: 20/50/200-day
  state, measured edge vs holding), `stop_buffer_pct` (volatility-sized buffers), `nav_snapshots`
  (daily NAV), `funding_service.propose_funded_buy()` (the only allowed way to fund a buy).

## The four rules

### 1. Rebalance band (`rebalance_band`)
- **Parameter:** band width in points (default 3). **Target:** read live from `plan_ticker_targets`,
  never stored, so it follows the plan when the plan changes.
- **Fires:** weight > target + band → a trim card back to the target. Weight < target − band → a buy
  card up to the target, funded through `propose_funded_buy` (sells only what the plan says is over).
- **Why:** it can't fight the plan by construction. It's the replacement for fixed caps like the
  15% on VTI.
- **Suggestion:** after PR 80, the rule suggestions offer a band for every plan ticker instead of a
  max-weight cap.

### 2. Profit-protect trail (`profit_protect`)
- **Parameters:** activation gain % (default +20% vs cost basis) and trail % (default
  volatility-sized, `stop_buffer_pct(k=2, 10–20%)`).
- **Behaviour:** dormant until price ≥ cost × (1 + activation). It then tracks the peak from that
  moment and fires a sell card when price ≤ peak × (1 − trail). The Rules row shows "dormant: +12% of
  +20%" and then "armed: peak X, stop Y".
- **Why:** it locks in real gains without the early exits a plain trailing stop causes on a position
  that's still underwater.

### 3. Trend-break exit (`trend_break`)
- **Parameter:** which trend: 50-day or 200-day.
- **Fires:** when `holding_review`'s stored state for the ticker moves from above to below that
  average. It reads the stored measurement and never fetches during evaluation.
- **Action (keeps your PR 77 decision):** a **sell** card only if the holding review measured that
  exiting on this signal beat holding **on this ticker's own history**. Otherwise it gives an alert
  plus a suggestion to arm a stop, labelled "unmeasured".

### 4. Portfolio drawdown guard (`drawdown_guard`, whole book, ticker `__BOOK__`)
- **Parameter:** drawdown % from the book's peak NAV (default 10%).
- **Peak:** the highest `nav_snapshots.nav_ils`, **adjusted for deposits and withdrawals** (a
  withdrawal isn't a drawdown). It uses the same flow-aware logic as the NAV chart.
- **Fires:** an alert (push) plus a Today card. See Decision 3 for what the card offers.

## Decisions (Eran, 2026-09-29 13:38)
1a (migration), 2a (one-time card), 3a (alert + actionable de-risk), 4 defaults approved **plus**: "if they need to be changed a recommendation card should appear" (tuning cards).

## Decisions as they were asked
1. **Where the second parameter lives.** (a) *Recommended:* an additive migration `0025` with a
   nullable `params` JSON column on `trading_rules`. The PR 47 history is why this is flagged: the
   migration is additive only and ships alone, and `/health.schema_revision` is verified before the
   feature code. (b) No migration: keep params in `kv_settings`, keyed by rule id. That works but
   splits a rule across two tables.
2. **Rebalance band vs existing caps.** (a) *Recommended:* after PR 80, a one-time card offers to
   convert each max-weight cap on a plan ticker into a band. Nothing converts silently.
   (b) Leave caps alone.
3. **Drawdown guard action.** (a) *Recommended:* alert plus a card that **proposes** moving a slice
   of the most volatile sleeve to cash/bonds, sized to bring the book's modelled drawdown back inside
   your ceiling. Accept executes it. (b) Alert only.
4. **Defaults** (band 3 pts, activation +20%, trail volatility-sized, trend 200-day, drawdown 10%):
   are these OK?

## Tests (each rule is applied to a real book and re-measured, per CLAUDE.md)
- A band trim lands on the target, and a band buy is funded only from over-target classes.
- Profit-protect never fires before activation. After activation it fires at peak × (1 − trail).
- Trend-break offers sell only with a measured edge. Without one it's an alert and a stop suggestion.
- The drawdown peak ignores a deposit and a withdrawal. The guard fires at exactly the threshold.
- Edit (PR 79) works on all four. A band is never flagged by the PR 79 conflict pass.

## Order
Migration PR (if 1a), then rules 1+2, then rules 3+4. Each step deploys and is verified live.
