# Domain logic: how holdings and a plan become Today's cards

> The business logic of InvestWise Pro. Written 2026-10-07 at PR 104d.1. "RC" =
> `app/services/recommendations.py` (6,888 lines). Line numbers drift, so search for the function
> name if a line has moved. Words in **bold** on first use are defined in [GLOSSARY.md](GLOSSARY.md).

---

## 1. The model in one paragraph

The user owns a **book**: holdings plus cash, valued in ILS. They set a **plan**: an objective
(Grow / Balanced / Income / Preserve), a risk tolerance, and a **goal**, which is a target ROI per
year on the money they put in, by a deadline. The plan resolves to one **book target**: the
**sleeves** (rule-based strategies with a % of the book each), the **core** (a static basket
holding the remainder), a 10% Commodities carve-out, and a cash reserve. All of it sums to 100%.
Every few minutes the app compares the book to the book target, the user's **trading rules**,
the market, and the odds of reaching the goal. It then emits **cards**. A card is a single,
sized, funded proposal. Pressing **Accept** executes it on the *tracked* book and writes the
ledger. No brokerage order is ever placed: the user mirrors the trade at their broker. Anything
the app knows that can't be acted on in one tap is a **heads-up**, not a card.

## 2. The non-negotiable rules

These are the invariants. Each one exists because it was broken in production, and each is
guarded by tests ([§8](#8-invariants-and-the-tests-that-guard-them)).

1. **A card may not claim a portfolio change it does not produce.**
   - Any "moves X toward target" is computed from the **simulated post-trade mix**, never from trade size.
   - Selling a class to buy the same class is a **swap** and is labelled one.
2. **Funding may only sell what the plan says you have too much of.**
   - There is one overweight budget per asset class, spent down as the loop sells.
   - A class at or under target is not a funding source while anything else is over.
   - Sleeves and the core basket are never raided to fund a card.
3. **Every funded buy goes through `funding_service.propose_funded_buy()`**, never `plan_funding` with hand-written prose.
   `describe_funding` requires `buying_class`. *(Two paths still call `plan_funding` directly: multi-leg sleeve funding,
   which passes `book_target` and runs a post-trade drift gate since PR 106, and cash redeploy. See [§10](#10-known-gaps-and-naming-debt).)*
4. **Every card can be acted on, or it is a heads-up** (PR 102).
5. **Accept conserves NAV** (only the card's tax leaves the book), and every share change is a ledger row.
6. **The server alone decides which cards show.** There is no per-device snooze (PR 69).
7. **ILS-normalised everywhere.** NAV, goal, projections and the mix all derive from the FX-normalised snapshot.
8. **Every number says what it measures** (PR 100). "Est." vs "measured" is explicit, and no card gets a new sentence.
9. **Every decision is a setting.** A limit or bar the owner decides is a plan setting with a default,
   visible where it acts and editable in-app (PR 104c/d).
10. **The advisor never invents numbers** and ends with "Not financial advice."

## 3. Core concepts

### 3.1 The book and NAV
- **Positions** (`positions` table) carry quantity, native price and currency. **Cash is a position** (`CASH`, ILS, price 1).
- `portfolio_analytics.compute_snapshot` converts each holding with `fx.fx_rate(...)`, giving `snap["nav"]` and the ILS
  weights. Downstream code must use **snapshot weights × NAV**, never `qty * price` (that was the VXUS $-vs-₪ bug).
- Minor units: `ILA` (agorot) and LSE pence are 1/100 of the major unit. Getting this wrong produces 100× errors (PR 18, 45).

### 3.2 The plan
One `plans` row per user. For every column see [DATA_MODEL.md](DATA_MODEL.md#the-plan).
- `objective` picks the default mix (`allocation_mix.OBJ_TARGET`):

  | Objective | Equities | Fixed income | Commodities | Cash |
  |---|---|---|---|---|
  | Grow | 80 | 10 | 10 | |
  | Balanced | 60 | 30 | 10 | |
  | Income | 40 | 50 | 10 | |
  | Preserve | 30 | 60 | | 10 |

  It also picks the Kelly fraction and the soft cash target.
- `risk_tolerance` (Low / Medium / High) picks `RISK_CAPS`: the concentration cap, plus the vol/ruin caps the war-room Risk engine reads.

### 3.3 Sleeves, the core and the book target
- A **sleeve** (`plan_sleeves`) is a rule-based strategy from `strategy_catalog.py` (the "Beat the Market" family,
  e.g. `btm_trend_soxl`, `btm_qqq_hold`). It governs `sleeve_pct` of NAV and has its own signals and discipline.
  Sleeve holdings are never a funding source.
- The **core** is the remainder. Since C6 it is a named static basket from `strategies.py` (e.g. `bal_6040` = 60 VTI / 40 BND),
  chosen on Plan › My mix or proposed by the goal solver (PR 94). "A core is a strategy too" (PR 37): its basket is also excluded from funding.
- **Two catalogs, one API:** `strategies.py` holds static presets/baskets (core candidates); `strategy_catalog.py` holds
  backtestable rule specs (sleeves). `/api/v1/strategies` serves both.
- The **book target** (`app/services/book_target.py`, `compose` / `book_target`) is *the* target mix:

  ```
  sleeves' per-ticker targets (classified)
  + core mix × core's remaining share
  − cash reserve
  − 10% Commodities carve-out
  = 1.0  (asserted)
  ```

  It carries provenance. `targets_for` *replaces* rather than merges (PR 71: the old merge summed to 110% and made cards fight).
- `plan_ticker_targets` gives per-ticker plan targets. A sell rule may not trim through them (PR 79).

### 3.4 Cash
- **Soft target** (default by objective, or `cash_target_ils`): routine cards stop spending here.
- **Hard floor** (default ₪150, setting `cash_floor_ils`): only a card carrying a `reserve_reason` may spend below the soft
  target, and never below the floor. `plan_funding(allow_reserve=True)` without a reason is a TypeError.
- The **redeploy card** fires above `cash_redeploy_at_ils` (default ₪1,500) and invests down to the target (PR 64/68.1).
- **Buy-back holds** (PR 75/83/87): after a take-profit sale the sleeve target stays.
  - While the price is ≥ the sale price, the hold is active: buyers treat the sleeve as met and the proceeds stay reserved in cash.
  - The first close below the sale price releases it for good.
  - Holds are `trading_rules` rows of type `buy_back`. The reserve is pre-committed in the funding ledger, so no other card spends it.
- **Stop holds** (PR 139, D114): a protective exit (stop-loss, trailing stop, profit-protect, trend break) records a
  `buy_back` row with `params.kind = "stop"` and the owning sleeve.
  - No price ends it. The 06:15 signal job marks it `signal_out` when the sleeve's target for the ticker goes to zero, and
    ends it at the next reading that is in. No signal reading yet counts as NOT out.
  - Every buyer honours it: card executors call `buyback_hold.assert_buyable` before their first write (writers commit as
    they go); `_fund_sleeve` / `fund_plan` drop the ticker; `buy_into_book` refuses it as a backstop unless `recording=True`
    (your own Holdings > Add). `tests/test_a_stop_out_is_not_bought_back.py` walks app/ and fails on a buy path with no check.
  - **One bet, one hold** (PR 141, D116): a stop hold on a 2x/3x fund (`strategy_profile.GEARED`: the leveraged set
    without the -1x PSQ and SH) blocks EVERY 2x/3x fund. `buyback_hold.with_group` derives the extra entries on each read
    (`via` = the stopped ticker, same hold rule id), so `stop_held` and `active` both return them and every buyer above
    honours them; ending the source hold ends the block. The held-cash card still names only the real hold (and "Buy back"
    buys only it) but offers no blocked fund as a place for the money; a blocked sleeve gets no top-up or cold-start card.
  - `buy_back` is an ENTRY type: a hold exists for a ticker you no longer hold, so it is never purged or hidden as an orphan.
- **Standing orders**: monthly deposits credited by the hourly job (a contribution plus cash). They feed the goal.

### 3.5 The goal (PR 74, 92–99)
- `goal_service.compute_goal`: **target = Σ contributions × (1 + ROI)^(years to deadline)**. It moves *only* on deposits,
  withdrawals or edits, never on market moves, and **no card moves it** (PR 74). The deadline governs.
- **The goal is the objective** (PR 92). The **frontier** and **Kelly** are tools for reaching it.
- **On track** means P(reach goal) ≥ the on-track bar (default **70%**, `plans.on_track_pct`), checked on *both* the model and
  fat-tailed filtered historical paths (PR 93, `engines/paths.py`).
- **Crash-risk limit:** P(book halves peak-to-trough) ≤ **1%** by default (`plans.halve_limit_pct`, PR 97).
- The goal solve (`frontier_service`, nightly 04:10 and on demand) also does three things:
  - picks risk, capped at 1.0× Kelly (the Kelly dial is an optional ceiling)
  - proposes the core (PR 94)
  - **replays the protective rules on the goal's paths** (PR 95), tuning them if they cost too many odds points (PR 96).
    PR 121: the retune crosses each sleeve's take-profits and the guard's level × sell size × re-arm gap. PR 140 (D115)
    removed the sleeve-exit switch: the retune never pauses a stop-loss, trailing stop, profit-protect or trend break,
    `apply_rules_tuning` refuses a spec that names one (`STOPS_STAY_ON`), and a verdict still proposing one shows no card.
    Each pause writes a `paused` rule event (`action.by` = card or you); downside rules that are off sit on one
    "N protective rules are switched off" heads-up whose act is `rearm_rules`. It screens on 250 paths in two passes, capped at 120 setups, and re-measures the best 3 on every path. The card
    and the Plan sentence say how many setups were measured, and mention take-profits only when they were.
    **A3r (D132): one exit per holding.** `protection_manager` is the one place that proposes an exit: core funds
    none (bands + book guard), sleeve / 2x-3x holdings one trailing stop at k x measured monthly volatility (k = 2/3/4
    on the goal preview), no take-profits. Measured nightly 05:40 (`kv_settings` `protection_manager:<email>`), one
    card (`rec_protection_one_exit`, apply `protection_plan`) that deletes, re-levels and adds; generated cards and
    rule suggestions never arm an exit (`filter_generated`, `filter_suggestions`). Beside it the retune card is hidden.
    PR 123 (D85): a held stock with a sale rule is its own column in the replay (its history, re-centred on the
    core's drift), and every rule's effect is measured by switching it off alone on 500 paths
    (`rules.per_rule`); GET /rules carries it as `goal_impact`.
    PR 124 (D86): `POST /plan/frontier/rule-impact` replays a card's proposed rules (`apply.rules`) on and off on the
    same paths (light preview, `rules.compare`); Today shows it under each rule-proposing card.
    PR 121.1 (D83): the halving limit is hard. Over it, the card ("Bring your crash risk inside your limit") offers the
    best setup inside it even at a cost in goal odds, and says the cost.
- Verdicts: `ON_TRACK`, `LESS_RISK`, `REBALANCE`, `IMPROVE`, `OUT_OF_REACH`, `NO_TARGET`, `TAIL_DISAGREES`, `TAIL_SHORT`.
  They are stored in `solver_verdicts`; Today only *reads* them.
- **A stale measurement silences the goal** (PR 99.1). Goal cards compute the goal *after* Accept, and only promise what Accept delivers (PR 99).

### 3.6 Forward numbers: how returns and risk are measured
- **Measured assumptions** (PR 16, `measured_assumptions.py`): expected return and volatility come from stored backtests of
  the sleeves and the core, not from a static table. `measured_pct_of_book` reports how much of the book is measured.
- **One return convention** (PR 76, `engines/return_convention.py`): every stated rate is compound (CAGR), converted once
  to arithmetic drift for simulation.
- **One return basis** (PR 98) and **total return** (PR 101): backtests use dividend-adjusted history.
- **Frontier inputs** (`frontier_inputs.py`, PR 89): ILS returns (USD/ILS compounded in), Ledoit-Wolf shrunk covariance, and
  Black-Litterman μ. `return_confidence` defaults to **blended** (τ = 0.2, the prior worth 5 years, PR 95); the shrunk estimate is shown as a stress line.
  Volatility is regime-weighted recent vol (PR 97).
- **Blend** (`engines/blend.py`): a core+sleeves book is measured by blending daily target vectors and simulating once,
  **never by averaging CAGRs or drawdowns**.

### 3.7 Rules
`trading_rules` (`rules_service.RULE_TYPES` plus `advanced_rules`):

| Group | Types |
|---|---|
| Classic | stop_loss, take_profit (a ladder that re-arms, house fraction 25%), trailing_stop, price_above/below, buy_dip, max_weight (per-name cap, bounds every buy, PR 65), strategy_signal |
| Advanced (PR 81) | **rebalance_band** (±3 pts), **profit_protect** (activates at +20%, gives back 15%), **trend_break** (200-day), **drawdown_guard** (whole book, 10%, sells 25% of the most volatile holding, re-arms 2.5 pts under; the goal solve may tune all three, PR 121) |
| Holds | buy_back (PR 83) |

- **A firing never trades.** `evaluate_user` latches `triggered`, writes a `rule_event`, and `triggered_rule_recs` turns it into
  a `rule_<id>` card with an executable apply spec. **A triggered rule always has a card** (PR 72).
- If a rule fights the plan (it would sell an on-target sleeve, or trim below plan target), the card becomes a **decision card**:
  "Sell & lower the sleeve" vs "Keep & move the rule". It never just retires (PR 72/79).

### 3.8 Settings and thresholds
`app/services/thresholds.py` (PR 104d) holds `SPEC`: ten card thresholds with defaults read from their owning constants:

| Key | Default | Decides |
|---|---|---|
| `min_card_tax_ils` | 50 | Smallest tax-harvest card |
| `min_card_trade_ils` / `min_card_trade_pct` | 100 / 0.5% | Smallest trade card; anything smaller is a heads-up |
| `min_funded_trade_ils` | 250 | Smallest funded leg |
| `cash_floor_ils` | 150 | Hard cash floor |
| `rebalance_band_pts` | 3 | Band width |
| `take_profit_fraction_pct` | 25 | House take-profit fraction |
| `gain_line_pct` | 25 | "Ran up" card line |
| `material_odds_pts` | 5 | Odds gain that justifies a goal card |
| `guard_levels` | (15, 17.5, 20, 25) | Drawdown-guard levels the retune tries (PR 121 widened it) |
| `guard_sell_fraction_pct` / `guard_rearm_gap_pts` | 25 / 2.5 | What the live guard sells; how far under its level it re-arms (PR 121) |
| `guard_fractions_tried` / `guard_rearm_gaps_tried` | (25, 50) / (2.5, 5) | The sell sizes and re-arm gaps the retune tries (PR 121) |
| `commodity_buys_core_only` | 1 | With a core holding commodities, commodity cards buy only its funds (PR 116, D80) |

Overrides live sparsely in `plans.thresholds`. `acting_user` and `build_recommendations` call `thresholds.use(plan)` (a
ContextVar) once per request, and code reads `thresholds.get(key)`. The Plan › Goal › **Card thresholds** sheet edits them.
`PUT /plan` rejects out-of-bounds values with 422.

## 4. The Today pipeline

`GET /api/v1/recommendations` → `build_recommendations(session, user)` (RC:1275-3150).

> **Rules are read-only on GET (PR 109).** A read never deletes, retires or acknowledges a rule. Orphaned exit rules
> (a sold position's stop, take-profit, trailing stop) are hidden from the Rules tab and from rule cards, and the
> 30-minute evaluator deletes them. Watch alerts (`buy_dip`, `price_below`, `price_above` on a ticker you don't hold;
> D-b) are kept, and the evaluator quotes them (`params.watch_price`). A rule whose card you dismissed is resolved by
> the evaluator (`heal_dismissed_rules`). A triggered rule with no served card is reported as `rule_banner.uncarded`,
> never "healed"; healing switched off one-shot stops. GET still writes two non-rule things: it releases buy-back
> holds whose price retreated, and it resolves strategy signals whose card was handled. It also clears standing-alert
> latches (`price_above`/`price_below`/`max_weight` back under their level).

### 4.1 Setup
1. Load positions; if there are none, return "Add holdings". `compute_snapshot` gives the ILS NAV.
2. Plan, `thresholds.use(plan)`, concentration cap, per-name caps (`rules_service.name_caps`), and cash.
3. Buy-back holds: release retreated holds, load active ones.
4. `_book_target`, plus funding exclusions (sleeves + core basket). **One `FundingLedger`** for the whole build, so two cards
   can never spend the same shares or cash (PR 60). The hold reserve is pre-committed. A `degraded` list collects failed producers.

### 4.2 Producers (fixed order)
Each producer is wrapped in try/except, and a failure is added to `degraded`. Risky ones run in a SAVEPOINT; slow ones run in threads.

| # | Card (id prefix) | Fires when | Accept (`apply.kind`) |
|---|---|---|---|
| 1 | `trim`: Trim X | A single name is over the concentration cap | `trim`: sell the excess to cash |
| 2 | `tax`: Harvest a tax loss | A loser outside the sleeves/core; the saving is measured only on what it sells | `sell_losers` |
| 3 | `rebal_merged`: Buy/Sell <class> | `AllocationEngine` drift vs the book target (Cash skipped) | `rebalance_to_objective`: a whole-book rebalance as real trades, tax quoted |
| 3a | `corebasket`: Bring your core back | Core basket off by > 5 pts | `core_to_basket` (prices the buys first; a sleeve trim comes only from sleeves over their size, from their own holdings, never a basket ticker: C15, D119) |
| 3b | `divrisk`: geo / currency / liquidity | `risk_alerts` vectors | geo: `buy_funded` VXUS (swap allowed); others: heads-up |
| 4 | `behind`: behind goal | The projection is short **and no fresh frontier goal verdict owns the goal** | `set_standing_order` / `set_objective_and_rebalance` |
| 5 | Fee swap | Expense ratio above threshold and a cheaper same-class alternative exists (FeeAgent) | `fee_swap` |
| 6 | `verdict`: Buy/Hold/Trim | Fundamentals score | heads-up |
| 7 | `near_rule`, `ran_up`, `near_cap` | Near own rule; gain ≥ `gain_line_pct`; within 2 pts of a cap | heads-up / `trim` / `edit_rule` (raise the cap to target + band) |
| 8 | `hedge` | Sector/factor concentration | heads-up |
| 9 | `mom_dn` / `mom_up` | Strong trend | `create_rules` (arm a stop) |
| 10 | `review`: holding review | The nightly measurement shows the sell signal beat buy-and-hold net of 25% CGT (PR 77) | `trim` / `sell_position`; otherwise arm a stop |
| 11 | `cashdrag`, `income_yield` | Idle cash / yield | heads-up |
| 12 | `redeploy`: Put cash to work; `held_cash` | Cash > redeploy trigger; a hold reserves cash | `redeploy_cash`; `held_cash` (three-way choice) |
| 13 | `commodity_add` | Commodities under target | `buy_funded` |
| 14 | `buy_idea` (≤ 2) | Screener ideas that `propose_funded_buy` accepts | `buy_funded` |
| 15 | `benchmark_lag` | Trailing the benchmark | heads-up |
| 16 | `macro`: risk-off | The strategies' regime (`engines/regime`, cached) is risk_off (PR 108; futures no longer drive it) | heads-up |
| 17 | `raise_cash` | Cash < hard floor | `trim` of the worst-fitting names |
| 18 | `stale_price` | A quote is frozen | heads-up |
| 19 | `core_breach` | The target solver says the floor breaches the ceiling | none, by design |
| 20 | `rec_frontier_*`, `rec_goal_core/protect/mix/target_*` | The latest frontier/goal verdict (≤ 36 h, same dials) | `frontier_resize` / `frontier_core` / `frontier_rules` (retune protection) / `set_plan` |
| 21 | `warroom_buy` / `warroom_trim` | A war-room signal DISPLAYED (≤ 3), grounded prices only | `trim` / `buy_funded`. Blocked from one-tap if it adds risk against the solver's ceiling |
| 22 | `rule_<id>` | A rule latched `triggered` | per its `execution_plan` |
| 23 | Advanced-rule cards | Band breach, buy-back level, drawdown ≥ guard, trend break, retune, one-time caps→bands | `buy_funded`, `redeploy_cash`, `trim`, `create_rules`, `edit_rule`, `caps_to_bands` |
| 24 | `stratsig_*` + discipline | A sleeve's rule flipped | `set_plan` / `create_rules` |
| 25 | `sleeve_coldstart`, `sleeve_drift`, `cap_below_sleeve` | Sleeve ticker ±5 pts off (skipped under a hold); a cap below the sleeve target | `fund_sleeve` / `fund_plan` / `trim` / `relevel_sleeve_caps` |
| 26 | `rules_suggested` | Protective rules suggested for holdings | `create_rules` |

### 4.3 Post-processing
1. **Redeploy dominance:** a redeploy card drops cash-drag, rebalance and buys of its own legs.
2. **A fired buy-back rule owns its ticker.**
3. **`_reconcile`:** merges rebalance legs, merges geo + currency, drops cash-drag when a rebalance deploys cash, and rewords risk-off as a timing note.
4. **Priority** (`card_priority`, PR 70): 0–100 = money (0–40) + time (0–35) + rules (0–25) + idea quality.
   - ≥ 70 **Important**, ≥ 50 **Worth doing**, ≥ 30 **Nice to have**, else **Minor**.
   - `fold_alternatives` attaches "Also fills this gap".
5. **Mute/done filter:** Ignore lasts 7 days and Done 14 days. Each card has one mark, the newest of its Ignore and Done. A muted card **re-fires** if its number moves ≥ 25% or ≥ ₪100, or its
   severity rises. It then says "You marked this handled. It is back because…". Hidden cards are listed in `hidden_cards`.
   - **Ignore on a fired stop keeps the stop** (PR 142, D117): Ignore sells nothing, so a stop-loss re-arms one ladder step
     (10%) below today's price and a trailing stop or profit-protect restarts its peak at today's price. Logged `kept`,
     `action.executed.by = "ignore"`. Done, Accept and Keep are unchanged. A fall through the new level is a new firing and
     un-mutes the card.
6. **Scope + contradictions:**
   - `card_scope.annotate` stamps SLEEVE / CORE / BOOK.
   - `contradictions.check` suppresses the loser and says so (selling an on-target sleeve, buying and selling one class, cards exceeding the book).
   - Rule-vs-sleeve and rule-vs-plan conflicts become **decision cards**.
7. **Heads-up split** (`headsup.split`). A card whose `apply.kind` is not in `_ACTIONABLE_KINDS`, or a trade below
   `max(min_card_trade_ils, min_card_trade_pct × NAV)` (unless it is a rule card), becomes a one-line heads-up.
   Heads-ups have no Mark-as-done, never notify, and a dismissed one never returns.
8. **Rule banner reconciliation:** a triggered rule with no visible or blocked card is resolved, so the banner and the cards always agree.
9. **Payload:** `recommendations[:12]`, `more_recommendations`, `headsups`, `goal_summary`, `book_target`, `holding_review`,
   `degraded`, `rule_banner`, `hidden_cards`, `timings_ms`.

### 4.4 Accept
`apply_recommendation` (RC:5899) works in five steps:
1. Rebuild Today and find the card. If it is gone, return **404**, and the UI says "That suggestion no longer applies".
2. Dispatch on `apply.kind`. `buy_funded` **prices the buy before selling anything** and refuses untouched (PR 40).
   Frontier kinds refuse if the book moved since the solve.
3. `_record_apply` writes ledger legs.
4. `_guard_every_share_is_a_trade` writes an `Adjust` row and logs an ERROR for any unexplained change.
5. `_conservation_report` checks NAV after = before − the card's tax.

Non-mutating kinds return `advisory: true` ("Marked as done: nothing was bought or sold").

## 5. Funding (`app/services/funding_service.py`)

- **`rank_trim_candidates`:** worst plan fit sells first.
- **`plan_funding`:**
  1. Cash first, down to the soft target (to the hard floor only with a `reserve_reason`).
  2. Then trims. **One overweight budget per class** = max(0, weight − target) × NAV, spent down as it sells.
     If anything is overweight, at- or under-target classes are skipped.
  3. Same-class or preferred sales bypass the budget (they are swaps).
  4. A name over its own cap may sell its excess.
  5. It respects exclusions (sleeves, core), the ledger's reservations and name caps, and grosses up for CGT.
- **`propose_funded_buy`:**
  1. Size = min(class gap, room under the tighter of plan cap and per-name rule).
  2. Refuse if the class is at/over target unless a swap is allowed.
  3. Fund against the shared ledger.
  4. **Simulate the post-trade class mix:**
     - < 0.5 pt move → `within_class_swap` (only if opted in; the impact text is forced to "Does not move your X weight… swaps which X you hold")
     - toward target → `toward_target`
     - away from target → no card
  5. Overshoot guard; **total drift may not rise**.
  6. Impact prose comes from the simulation, and the ledger commits only on return.
- **`FundingLedger`:** a per-build inventory. A card whose first funding source is taken is *re-sourced*, not dropped.

## 6. Engines, agents and the war room

- **Engines** (`app/engines/`, pure computation):

  | Engine | Computes |
  |---|---|
  | `frontier` | Ledoit-Wolf covariance, capped-simplex mean-variance, Kelly, goal probability |
  | `paths` | Fat-tailed filtered historical paths |
  | `blend` | Core+sleeves measured by blending daily target vectors |
  | `strategy_backtest` | Real-price rule backtests with leverage decay, costs, CGT and dividends |
  | `simulation_engine` | Lognormal Monte Carlo with CPI, FX and deposits |
  | `risk_engine` | GBM Monte Carlo + ruin veto |
  | `regime` | Price-derived regime |
  | `allocation_engine` | Drift → trades |
  | `tax_engine` | Israeli CGT/surtax |
  | `fee_engine` | Expense-ratio flags |
  | `screener_engine` | Value/Growth/Quality/Income percentile ranking |
  | `portfolio_risk` | VaR/CVaR, beta |
  | `scenario_engine` | Macro shocks |
  | `performance` | Return curves |
  | `return_convention` | Compound → arithmetic drift |

  Not on Today: `decision_engine`, `lag_engine`, `whs_engine`, `state_machine` (the war room and What-if use them).
  `safety_engine`, `xai_engine` and `learning_engine` were deleted in PR 113.
- **Agents** (`app/agents/`, judgement): Research (no data source: returns nothing, and says so), Opportunity (screener → buy ideas,
  commodity card, cash research), Fee, and Adversary (cross-examines war-room stages,
  can block). The full cited roster is in [AGENTS.md](AGENTS.md).
- **War room** (`war_room.build_war_room`). This is the **only live use of the typed state machine**:
  1. Lag scan: spot vs the 50-day SMA ("listing price" is a legacy name).
  2. Risk vet (can veto).
  3. Tax, then Decision, then the display gates, with the Adversary checking each stage.
  4. The target-solver verdict acts as a constraint: it removes one-tap Accept from risk-adding cards.

  Today promotes ≤ 3 DISPLAYED signals but **re-sizes and re-funds them through `propose_funded_buy`**. The Agents tab
  shows the transcript ("How the decisions are made"). Today's copy is cached 10 minutes and not narrated.

## 7. Health score and goal on Today

- The **health score** (`plan_health`, PR 70) = **On plan** (sleeves 30, mix 20, rules 20, concentration 15, cash 15) + **On track**
  (goal odds). It replaced the generic WHS risk/tax score, which read 3/100 on a deliberately leveraged book.
  **On track is the goal's one chance** (PR 120): the goal model's headline with the protective rules as they are,
  the number the Plan tab leads with. The Today "Chance of hitting your goal" row and the assistant/digest
  context quote the same number, all through `frontier_service.goal_chance` (PR 131, D96). A verdict that is merely
  OLD (past `MAX_AGE_H`, rules / sleeve sizes / settings changed since) is still quoted, with `current: false`, its age
  and the reason, and the screens ask once per load for a re-solve (`POST /plan/frontier/refresh?only_if_stale=true`,
  skipped when current or solved in the last 6 minutes). A verdict about a different goal, or built on stale
  measurements, is withheld. Only then — or when the goal was never solved — does the projection's no-rules figure
  stand in, labelled "rough estimate".
- **Sleeve occupancy** (`sleeve_occupancy`, PR 131, D95): a ticker several sleeves want is split between them
  pro-rata to their target weights on it, never credited whole to each, so the per-sleeve held figures add up to the
  sleeves' union (`allocated_held_pct`). Each row says which tickers it shares (`shared_with`).
- **Every sleeve a different bet** (`sleeve_diversity`, PR 132, D97). Each ticker maps to the market it is a bet on;
  a strategy's profile is, per market, its largest share in any state (on / base / risk-off / universe), cash excluded.
  Two strategies are the same bet when their profiles overlap >= `sleeve_overlap_alert_pct` (40) or their stored
  daily-return correlation >= `sleeve_correlation_limit` (0.80). Adding (or swapping in) a same-bet sleeve is refused
  with `replace`; resizing a held one is not. `/plan/sleeves` carries `same_bet` (catalogue -> held look-alikes) and
  `held_same_bets`; `/strategies/correlations` the measured table (job `strategy_correlation`, 03:50).
- **Wave-3 candidates** (`diversifier_candidates`, `diversifier_gate`, PR 133, D98): D1-D13 pre-registered, measured
  against B1 (beats QQQ, or raises the book's return), G2-G6 and the G1 "different engine" badge; they join the
  catalogue only after passing on production. The **book-effect verdict** (`book_effect.verdict`) names the failed test
  and its margin; its limits are the settings `book_effect_ratio_gain` / `book_effect_drop_tolerance_pts`.
- "Where this could end up": `simulation_engine` median / 5% / 95%. It always states that it assumes no deposits (W3).
- The account-history chart shows **real NAV snapshots only**, as time-weighted return vs SPY. It never draws a reconstruction.

## 8. Invariants and the tests that guard them

| Invariant | Code | Tests |
|---|---|---|
| Card claims = simulated outcome; swaps labelled | `propose_funded_buy`; frontier cards from simulated weights; one sizing function for card and Accept | `test_card_claims.py` (applies every card's `apply` and re-measures), `test_reviewer_card_claims.py`, `test_goal_cards_promise_what_accept_delivers.py`, `test_accept_honesty.py`, `test_redeploy_is_like_for_like.py` |
| Funding sells only overweight; one budget per class | `plan_funding` | `test_funding_service.py`, `test_c3_funding.py`, `test_two_cards_do_not_want_the_same_money.py`, `test_the_core_is_not_a_funding_source.py`, `test_every_funding_call_passes_the_book_target.py`, `test_a_cap_you_set_bounds_the_trade.py` |
| Cash target soft, floor hard | `spendable_cash` | `test_the_cash_target_is_soft_and_the_floor_is_not.py`, `test_cash_rules_in_shekels.py` |
| Every card acts or is a heads-up | `_ACTIONABLE_KINDS`, `headsup.split` | `test_every_card_can_be_acted_on.py`, `test_cards_are_short.py`, `test_the_server_alone_hides_cards.py` |
| Accept conserves NAV; every share is a trade | `_conservation_report`, `_guard_every_share_is_a_trade` | `test_accept_conserves_nav.py`, `test_the_ledger_counts_every_share_once.py`, `test_a_rebalance_is_real_trades.py` |
| No contradicting cards | `_reconcile`, `contradictions.check`, decision cards | `test_live_contradictions.py`, `test_a_rule_that_fights_a_sleeve_is_a_decision.py`, `test_a_rule_may_not_trim_through_the_plan.py` |
| One book target summing to 1 | `book_target.compose` | `test_one_target_holds_commodities.py`, `test_a_core_is_a_strategy_too.py`, `test_sleeves_mean_what_they_say.py` |
| ILS everywhere | `compute_snapshot`, `frontier_inputs` | `test_performance_fx.py`, `test_the_risk_report_is_in_shekels.py`, `test_minor_unit_currencies.py` |
| One goal; moves only on contributions | `goal_service` | `test_one_goal_everywhere.py`, `test_goal_moves_only_on_contribution.py`, `test_no_card_rewrites_the_target.py` |
| Every decision is a setting | `thresholds.py`, migrations 0026–0028 | `test_every_threshold_is_a_setting.py`, `test_every_limit_is_a_setting.py`, `test_frontier_dials.py` |
| Numbers grounded (LLM) | `context_service` {value, means}; `digest_service.verify` | `test_the_digest_cannot_compose_a_false_sentence.py`, `test_every_number_says_what_it_measures.py` |

## 9. The advisor (LLM)

- `ask_service.answer`:
  1. `context_service.gather` collects NAV, holdings, the asset-class breakdown, commodity and strategy options, the top-5 Today cards, and performance/risk/plan.
  2. `for_prompt` (pairs of {value, means}).
  3. Gemini.
  4. Without an API key, it returns the grounded context itself, and the UI shows `grounded_on`.
- The rule "**never invent specific numbers… end with 'Not financial advice.'**" lives in the prompt (`ask_service._PROMPT`).
  The **digest** is post-verified (`digest_service.verify`, PR 35: "the digest cannot compose a false sentence"); `/ask` is not.

## 10. Known gaps and naming debt

These are tracked in [BACKLOG.md](BACKLOG.md) and sequenced in
[plans/EXECUTION_PLAN_2026-10-08_hardening.md](plans/EXECUTION_PLAN_2026-10-08_hardening.md). Read them before trusting a name.

**Proven on 2026-10-08, and they break the invariants in §2 (fix in plan PR 105/106):**
- *Closed in PR 105:* the sleeve-drift trim sized ₪ ÷ native price, "Replace book" multiplied NAV by the rate, and five
  ILS leaks (assistant context, risk validation, sector card, fee scan, `/backtest`). Guarded by
  `test_no_native_value_is_called_shekels.py` (all of `app/`) and by `test_card_claims` measuring trims.
- *Closed in PR 106:* sleeve funding and the solver's cost preview now pass `book_target`, and sleeve funding runs a
  post-trade drift gate (`funding_service.funding_drift`). The AST guard walks all of `app/`.
- **Funding outside `propose_funded_buy`:** multi-leg sleeve funding still calls `plan_funding` directly (with
  `book_target` and the drift gate, so rule 2 holds), and `redeploy_cash` sizes on its own path (probed: drift
  0.50 → 0.23, the claim holds).
- **`cash_floor_pct()` / `cash_floor_ils()` return the soft *target*,** while the *setting* `cash_floor_ils` is the hard floor.
- **Two different `MIN_TRADE_ILS` values:** 100 (heads-up demotion) and 250 (funded leg).
- *Closed in PR 108:* **one regime acts.** Today's risk card, its reconciled heads-up and the Today AI summary read
  `regime_service.cached_regime_proxy()` (the price-derived `engines/regime`, cached hourly by `futures_warm`). Futures are
  a labelled cross-check on the Markets tab, their day change is measured against the previous daily bar, and
  `cached_regime()` has a 30-minute TTL.
- *Closed in PR 115 (D-c, D-d):* 13 more decisions are settings on the Card-thresholds sheet (23 in all): the concentration cap (default = your risk tolerance), sleeve drift band, resize minimum and the two materiality bars, the Ignore/Done mute days and the re-fire change, the near-take-profit/trail/cap heads-ups, and the buy-idea counts. One card minimum: the larger of the card minimums and the smallest funded trade. Still constants by choice: `CASH_FLOOR_PCT` and Kelly (already overridden per plan), `OBJ_TARGET` (the book target now comes from the core/frontier), ranking internals and computational sizes.
- **Stale comments:** `buyback_hold.py` (says kv_settings; it has been `trading_rules` since PR 83); `PlanSleeve.is_core` (says unread; it is read).
- **The goal model doesn't simulate stops** on non-sleeve tickers (BND/GOOGL/VTI/UGA/USO). This is disclosed in the UI.
- *Deleted in PR 113:* the legacy pipeline (`pipeline.py`, `AllocationAgent` veto, `SafetyEngine`, XAI, learning, decision feed).

- **Compare goals (D122):** the nightly goal solve also prices a grid of goals (%/yr 30/25/20/15/10 x deadline as set or +2
  years x +₪0 or +₪1,000 a month) on the same rules replay as the headline, with and without the rules, 500 paths, a
  120 s budget (`frontier_service._goal_grid`, `rules.goal_grid`). Shown as "Compare goals" on the Plan tab.
