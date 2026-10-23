# Glossary

Terms as this codebase uses them. Where the code name differs from the screen name, both are given.

| Term | Meaning | Code |
|---|---|---|
| **Accept** | Executes a card on the *tracked* book: rows, ledger, rules or plan. Never places a broker order. | `apply_recommendation` |
| **Actionable card** | A card whose `apply.kind` mutates something. Otherwise it is a heads-up. | `_ACTIONABLE_KINDS` |
| **Adversary** | The agent that cross-examines each war-room stage and can block. | `app/agents/adversary.py` |
| **Agent vs engine** | An agent exercises judgement or a veto; an engine computes. See [AGENTS.md](AGENTS.md). | `app/agents/`, `app/engines/` |
| **Asset class** | Equities, Fixed Income, Commodities, Cash (+ Real Estate / Alternatives). | `allocation_mix.classify` |
| **Black-Litterman (BL)** | Blends a prior with measured returns; "blended" confidence uses τ = 0.2 (the prior is worth 5 years). | `frontier_inputs.py` |
| **Blend** | Measuring core + sleeves by blending daily target vectors and simulating once, never by averaging results. | `engines/blend.py` |
| **Book** | Everything the user holds, including cash, as tracked by InvestWise. | `positions` |
| **Book target** | The single target mix for the whole book: sleeves + core + 10% Commodities + cash reserve = 1.0. | `book_target.py` |
| **Buy-back hold** | After a take-profit sale, the proceeds stay reserved until the price falls back below the sale price. | `buyback_hold.py`, rule type `buy_back` |
| **Stop hold** | After a stop sells a holding, the app will not buy it back until its sleeve's signal goes out and comes back in, or you end the hold. | `buyback_hold.py`, `buy_back` with `kind: stop` |
| **Card** | One sized, funded, explained proposal on Today. | `build_recommendations` |
| **Card threshold** | A user-editable number that gates cards (minimum trade, band, gain line…). | `thresholds.py`, `plans.thresholds` |
| **Cash floor (hard)** | Absolute minimum cash (default ₪150). Only a card with a `reserve_reason` may go below the soft target, and never below this. | setting `cash_floor_ils` |
| **Cash target (soft)** | Where routine cards stop spending cash. *The code calls it `cash_floor_pct` / `cash_floor_ils()`, which is naming debt.* | `funding_service` |
| **Contribution** | External money in or out (a deposit or withdrawal). It drives "you put in" and the goal. | `contributions` |
| **Core** | The part of the book not claimed by sleeves, run as a named static basket (e.g. 60 VTI / 40 BND). | `sleeve_service.core_strategy_id` |
| **Crash-risk limit** | The maximum acceptable chance that the book halves peak-to-trough (default 1%). | `plans.halve_limit_pct` |
| **Decision card** | A card with a choice of tiles, e.g. rule vs sleeve (Sell / Keep & move rule) or held cash (Keep / Buy back / Invest elsewhere). | `rule_vs_sleeve`, `held_cash` |
| **Degraded** | A Today producer that failed this build. Shown as "Some checks didn't run". | `degraded` |
| **Frontier** | The efficient frontier: the best return for each level of risk over asset classes. | `engines/frontier.py`, `frontier_service.py` |
| **Funding ledger** | The per-build, in-memory record of cash and shares already promised to earlier cards. Not the trade ledger. | `FundingLedger` |
| **Goal** | Target = Σ contributions × (1 + ROI)^years to the deadline. It moves only on deposits, withdrawals or edits. | `goal_service.compute_goal` |
| **Goal verdict** | The result of the nightly or on-demand goal solve: ON_TRACK, LESS_RISK, REBALANCE, IMPROVE, OUT_OF_REACH, TAIL_DISAGREES… | `solver_verdicts` |
| **Heads-up** | A one-line note in a folded list at the bottom of Today. No Mark-as-done, never notifies, never returns once dismissed. | `headsup.py` |
| **Health score** | On plan (sleeves, mix, rules, concentration, cash) + On track (goal odds). | `plan_health.py` |
| **Holding review** | A nightly per-holding test of whether selling on the signal beat holding, net of tax. | `holding_review.py` |
| **Kelly** | The growth-optimal risk level. The app uses a fraction of it as a ceiling (½ / ⅓ / ¼ by objective; the goal can raise it to 1×). | `plans.kelly_fraction` |
| **Ledger** | The `transactions` table: one row per trade leg written by an Accept or a repair. | `ledger_service.py` |
| **Measured** | Derived from a stored backtest of the actual strategy or basket, as opposed to *est.* (instrument-table assumptions). | `measured_assumptions.py` |
| **NAV** | Net asset value of the book in ILS, FX-normalised. | `compute_snapshot` |
| **On-track bar** | The goal probability counted as "on track" (default 70%). | `plans.on_track_pct` |
| **Protective rules** | Stops, trailing stops, profit-protect and the drawdown guard on sleeve tickers. Replayed on the goal's paths, because they change the odds. | Plan › My mix |
| **Re-fire** | A muted card returning because its number moved ≥ 25% / ≥ ₪100, or its severity rose. | `moved_on` |
| **Regime** | Market state. Two exist: price-derived (`engines/regime.py`) and futures (`markets_service`). | |
| **Rule** | A user trading rule. Firing latches it and raises a card; it never trades on its own. | `trading_rules` |
| **Scope** | Which part of the book a card serves: SLEEVE · name / CORE / BOOK. | `card_scope.py` |
| **Sleeve** | A rule-based strategy given a fixed % of the book. | `plan_sleeves` |
| **Smoke** | A read-only PowerShell script that checks a deploy against production, one per PR. | `scripts/smoke/` |
| **Solver** | Target solver ("What would it take?"), split solver, core search, frontier/goal solve. All store `solver_verdicts`. | |
| **Standing order** | A monthly deposit the app credits automatically. You set up the real transfer at your bank. | `standing_orders` |
| **Swap** | A sell-and-buy inside one asset class. It doesn't move the mix and must be labelled as a swap. | `within_class_swap` |
| **TWR** | Time-weighted return. Deposits don't count as performance. | `nav_history` |
| **War room** | The Agents tab: the transcript of the lag → risk → tax → decision pipeline with the Adversary. | `war_room.py` |
