# Funding / FX / side-effect investigation (worktree docs-overhaul @ 93732f9)

Scratch probes: `C:\Users\erang\.claude\jobs\a36f9c6f\tmp\scratch_funding\`
Run from the repo dir: `PYTHONUTF8=1 python -m pytest -q -s -p no:cacheprovider -p no:logging --rootdir . -c pyproject.toml <probe>`

---

## C1. Sleeve funding + solver preview measure "overweight" against OBJ_TARGET, not the book target

**SYMPTOM.** Funding a sleeve sells a class the book target says is *underweight*, and the card text calls it "the most overweight class".

**EVIDENCE.** `test_c1_sleeve_funding.py`. Book: plan objective Grow, core `bal_6040`, SOXL sleeve 10%. Holdings V/SCHD/MSFT/VTI 200 sh each (basis 40, price 100) + BND 200 sh (basis 99). No cash.

```
MIX current      {'Equities': 0.8, 'Fixed Income': 0.2, 'Cash': 0.0}
MIX book target  {'Equities': 0.58, 'Fixed Income': 0.32, 'Commodities': 0.1}  source sleeves+core
POST /plan/sleeves/fund?dry_run=true  (fund_plan, strategy_service.py:891)
  SELL BND Fixed Income 37100.0 | cheapest way to raise it: Fixed Income is the most
       overweight class (20% against a 10% target) and BND is among its least-taxed positions
_fund_sleeve (strategy_service.py:780)  sells [('BND', 'Fixed Income', 37100.0)]
target_solver._funding_preview (:603)   legs [('BND', 37100.0)]
plan_funding same args, no book_target  -> [('BND', 37100.0)]
plan_funding same args, book_target=bt  -> [('V', 43647.04)]   (same-class swap)
```
Fixed Income is 12 points UNDER its 32% book target; the plan sells half of it (FI -> 10%, 22 points under). The screen's own `/mix` says 32%; the funding card says 10%. Exactly the 2026-09-03 failure that `test_every_funding_call_passes_the_book_target.py` was written to stop, on a different screen.

**RULED OUT.**
- *Single-name cap drives the sale* -- first probe (V at 40% > 25% cap) sold V via `name_over`; the final probe keeps every name at 16% so only `class_over` can authorise; BND still sold. Killed.
- *Book target genuinely says FI overweight* -- `/mix` and `book_target()` both print FI 0.32 vs held 0.20. Killed.
- *buying_class same-class swap explains it* -- BND is Fixed Income, buying class Equities; it is authorised only by `class_budget["Fixed Income"]` = (0.20-0.10)*NAV computed from `targets_for(objective, None)` = `OBJ_TARGET["Grow"]`. Passing `book_target` flips the result to V. Confirmed cause.

**ROOT CAUSE.** `app/services/strategy_service.py:780` (`_fund_sleeve`), `:891` (`fund_plan._fits`, also used at the final `_fits(running)` ~line 995) and `app/services/target_solver.py:603` (`_funding_preview`) call `plan_funding(...)` without `book_target=`. `plan_funding` -> `targets_for(objective, None)` (funding_service.py:279-281) returns `OBJ_TARGET[objective]`, so both `class_budget` (funding_service.py:513-515) and `rank_trim_candidates` `class_over` (:322-330) measure against the objective table. With a core/sleeves that carry more FI than the objective table (any 60/40 or 70/30 core under Grow), FI looks overweight while being under target. `target_solver` additionally omits `buying_class`, so its cost preview also disagrees with what the sleeve path would actually do in the other direction (it cannot use same-class swaps).

**WHY SILENT.** The AST guard `tests/test_every_funding_call_passes_the_book_target.py:35` scans only `MODULES = ("app/services/recommendations.py", "app/services/funding_service.py")`. strategy_service.py and target_solver.py are outside it -- the same "list stopped early" failure the guard's docstring describes. Existing sleeve tests use books with no core set (book target falls back to OBJ_TARGET scaled) or all-equity books, where the two targets agree in sign.

**BLAST RADIUS.** Searched `rank_trim_candidates(|plan_funding(|propose_funded_buy(` across `app/`: only the three sites above miss it. `advanced_rules.py:423` passes it. recommendations.py: all pass it (`:1188` `_btgt`, `:2314` `btgt.get("target")`). `targets_for` in engines/blend, strategy_backtest, frontier_inputs, strategy_signal_service is a different function (backtest weights). Execution path: `_fund_sleeve(dry_run=False)` and `fund_plan(dry_run=False)` -> `_execute_funded_sleeve` would WRITE these sales to the ledger.

**CONFIDENCE.** PROVEN (observed on all three call sites).

**FIX.** Resolve `bt = await book_target(session, user, objective)` once in `_fund_sleeve`, `fund_plan`, `_funding_preview` and pass `book_target=bt["target"]` (and `buying_class` in the solver preview, computed like `_fund_sleeve`). Widen the guard: make `MODULES` = every `app/**/*.py` (walk the tree, skip the definition module), so a new module cannot opt out by existing. Data-failable test: the probe book above (Grow + bal_6040 + 10% sleeve, FI 20%) -> assert no sell has `asset_class` whose held weight <= `/mix` target while `/mix` reports any class over target; and assert post-trade FI weight did not fall.

**EFFORT.** S (3 call sites + guard scope + one test). **RISK.** Low-medium: sleeves that previously funded from bonds may now report "not enough fundable money" on books where equities are not over the book target -- that is the rule working, but users will see more abstentions.

---

## C2. Multi-leg sleeve funding / redeploy_cash bypass `propose_funded_buy`

**Verdict:** the structural gap is real and has two OBSERVED card-claims violations; redeploy_cash was probed and held up.

### C2a. Sleeve funding raises drift. PROVEN, same root cause as C1
Using the C1 probe's observed sells (BND 37,100 sold to buy SOXL; NAV 371,000) against the book target 58/32/10:
before Eq .80 / FI .20 -> drift .22+.12+.10 = **0.44**; after Eq .90 / FI .10 -> .32+.22+.10 = **0.64**.
`propose_funded_buy` returns None when `after_drift > before_drift` (funding_service.py ~934-942). `_fund_sleeve` and `fund_plan` call `plan_funding` directly and never run that check, so nothing stops them. Today reaches this through `apply.kind == "fund_sleeve"` (the cold-start "Finish the move" card, recommendations.py:2493, and the sleeve-drift "Add about" card via `_fund_action`, :1259/:2559) and `"fund_plan"` (:6122-6128). Fix is C1's fix plus a drift gate: after `plan_funding`, simulate the class mix of legs + sells against `book_target` and refuse or relabel when drift rises, unless every sell is same-class (a swap).

### C2b. Sleeve-drift TRIM card oversells by the FX rate. PROVEN, NEW
**SYMPTOM.** "SOXL has drifted to 30% of a 25% sleeve" / "Trim about ₪18,550 of SOXL back to your 25% sleeve." Accept sells 185 shares (₪68,635 at 3.71), so the sleeve ends at about 11.5%, not 25%.

**EVIDENCE.** `test_c2_drift_trim.py` (Grow; SOXL 300 sh @ $100, four other equities 175 sh each; SOXL sleeve 25%):
```
SOXL before {'quantity': 300.0, 'value_ils': 111300.0, 'current_price': 100.0}
TITLE  SOXL has drifted to 30% of a 25% sleeve
ACTION Trim about ₪18,550 of SOXL back to your 25% sleeve.
APPLY  {'kind': 'trim', 'ticker': 'SOXL', 'shares': 185}  est_amount 18550.0
ACCEPT ok
SOXL after 115.0          # correct: 300 - 18550/(100*3.71) = 250
```

**RULED OUT.** *Tax or lot netting*: the trim handler sells `spec["shares"]` as given (recommendations.py:5995). *A price refresh between build and accept*: the spec already says 185 before accept. *Rounding*: 185 = int(18550/100) exactly, so the divisor is the USD price.

**ROOT CAUSE.** `app/services/recommendations.py:2555-2558`: `shares = max(1, int(_amount / current_price))`. `_amount` is ILS (`abs(_gap)/100*nav`, :2390) and `current_price` is native USD. PR 102 fixed this exact bug on the concentration trim (:1375-1378, comment: "`int(trim / price)` divided shekels by dollars for a US ticker") and did not fix this sibling. A ₪ amount below 1 share also becomes `max(1, …)`, one whole share.

**WHY SILENT.** `tests/test_card_claims.py:139-140` has `elif spec.get("kind") == "trim": pass  # a trim's size lives outside the spec; covered by its own tests`. No test measures post-trim weight for this card, and the drift-card tests in `test_cards_know_their_strategy.py` / `test_p42_notifications.py` check scope and notification only.

**BLAST RADIUS.** Every trim builder that sizes shares (recommendations.py:1154, 1378, 2197, 3881; advanced_rules.py:204/218/531; rules_service.py:403/429): the ones read use `price_ils`, a quantity fraction, or lots. Only :2556 divides ILS by a native price. The audit-trail formula at :1403 still prints `trim / price` with the native price (display only).

**CONFIDENCE.** PROVEN. **FIX.** `shares = round(qty_held * (_gap_abs/100) / w_actual, 6)` (the PR 102 form), or `_amount / (price * fx_rate(price_currency(...)))`. Test: in `test_card_claims`, make `trim` real. Look up the position's price_ils, subtract `shares*price_ils` from the class, and assert that the post-trade weight of the ticker is within the band of the target the card names. Data that fails it: any USD sleeve over its target. **EFFORT** S. **RISK** low (it only shrinks oversized sells).

### C2c. redeploy_cash: probed, no violation
`test_c2_redeploy.py` (Grow + bal_6040 + SOXL 10%, ₪60k cash). Card: "Moves every underweight asset class toward its target ... cuts cash from 13.9% to 3.0%"; legs BND ₪28,075, PPLT ₪18,995. After Accept, measured drift vs the book target went **0.4955 -> 0.2274**, FI 17%->26%, Commodities 0->4.25%, cash 2.9%. The claim held. The legs are cash-only and read `book_mix`, so the cross-class sale risk does not arise. The NAV increase after accept comes from `buy_into_book` repricing the whole BND line to the live quote, which is mark-to-market and not a defect. The gap stays structural: there is no simulated post-trade check, and the "every underweight class" prose is asserted from `_leftover`, not measured.
Side finding (INFERRED): `_apply_redeploy_cash` (recommendations.py:6386-6390) converts `q.price * fx_rate(q.currency)` without `to_major_units`, which `_apply_buy_funded` (:6440-6450) adds for the TASE agorot (ILA) trap. A `.TA` redeploy leg would buy 100x too few shares, or fall back on an unknown "ILA" rate. To prove it: run a redeploy leg on a `.TA` ticker with a stub quote in currency ILA and read the booked shares.

---

## C3. ILS leak: native-currency `quantity * current_price` valuations

**SYMPTOM.** Several consumers value holdings in the price's own currency and then compare or label that as ILS. On any book that mixes USD with ILS (cash, TASE), USD holdings are understated by the FX rate (3.71 under the builtin table).

**EVIDENCE.** `test_c3_fx.py::test_c3_probe`. Book: SPY (NASDAQ) 100 sh @ $100 = ₪37,100, GOVIL.TA (TASE, ILS) 371 sh @ ₪100 = ₪37,100, cash ₪10,000.
```
CTX nav_ils 84200.0                                       # correct (compute_snapshot)
CTX holdings [('SPY', 10000.0, ...), ('GOVIL.TA', 37100.0, ...), ('CASH', 10000.0, ...)]   # SPY should be 37,100; rows sum to 57,100 != nav
CTX asset_class_breakdown {'Equities': 0.175, 'Fixed Income': 0.65, 'Cash': 0.175}
current_mix (FX-normalised) {'Equities': 0.441, 'Fixed Income': 0.441, 'Cash': 0.119}
CTX beta_validation structural_beta 0.2726                # from risk_validation (recommendations.py:2927)
expected structural beta from FX-normalised mix 0.5067
```
`test_c3_fx.py::test_c3_replace_book`: **NEW and SEVERE.** It mints money on "Load basket" (mode=replace):
```
BEFORE  book = SPY ₪37,100 (Grow, no sleeves)
PREVIEW budget 37100.0  LOAD MSFT weight .2 price 144.83 value 7420.0 ...   # "value" is USD-priced shares sized with an ILS budget
REPLACE ok True
AFTER total value_ils 137640.98   nav 137640.99                            # 3.71x the ₪37,100 it replaced
```
Fee agent (pure call): QQQ 100 @ $100 (₪37,100) -> `positions_to_holdings` value_ils **10000.0**; `total_annual_saving_ils` 28 (should be ~104).

**RULED OUT.**
- *compute_snapshot is wrong*: `nav_ils` 84,200 equals `current_mix` NAV. compute_snapshot applies `fx_rate` (portfolio_analytics.py:45-49). Killed.
- *Builtin FX table is stale*: the rate is the same 3.71 everywhere. The error ratio is exactly the rate, so the cause is a missing multiply, not a bad rate. Killed.
- *Assistant gets correct numbers elsewhere*: `nav_ils` is right, but `holdings[].value_ils` and `asset_class_breakdown` are the fields it reasons from for "diversification", and both are wrong. Killed.

**ROOT CAUSE.**
- `app/services/context_service.py:59` (`value_ils = quantity*current_price`) and `:66` (breakdown): `pdicts` keeps native prices; only `compute_snapshot` converts.
- `app/services/recommendations.py:2927-2928`: `bt_holdings value_ils = d["quantity"]*d["current_price"]` -> `BacktestEngine.structural_beta` weights by it -> `risk_validation` (:3144) -> `ctx["beta_validation"]`. The comment at :2949-2954 names this exact hazard for the contradictions weights and fixes that site only. Asset class here is also raw `meta.asset_class` (`or "Equities"`), not `classify_holding`.
- `app/services/strategy_service.py:1183,1196-1207` (`_replace_book`): `budget` = ILS NAV, `price` = native quote, `qty = weight*budget/price`, stored with market NASDAQ -> NAV x FX. UI path: `index.html:3715-3738` `loadStratBasket*` always sends `mode:"replace"`. It is also the default mode for every static family (strategies.CATALOG) (`_default_mode`, :628-630).
- `app/agents/fee_agent.py:19`: fee savings understated by FX (used by `/fees` and the Today fee cards, recommendations.py:1944).

**WHY SILENT.** `test_strategies.py:42` checks only which tickers replaced which. `test_accept_conserves_nav.py` does not cover load-basket. The context/digest tests check that numbers are *traceable* (`digest_service.unsupported_numbers`), and a wrong-but-present number passes that check. Tests mostly use books with one currency, where native weights equal ILS weights.

**BLAST RADIUS** (grep `quantity … * … current_price` across app/):
| site | FX? | effect |
|---|---|---|
| context_service.py:59,66 | NO | assistant + digest prompt: holding values, class breakdown |
| recommendations.py:2928 | NO | risk_validation beta / worst drawdown (also shown to the assistant) |
| strategy_service.py:1201 (_replace_book) | NO | **mints NAV x3.71 on Load basket** |
| agents/fee_agent.py:19 | NO | fee savings ₪ understated 3.71x |
| recommendations.py:3791 (sector hedge) | NO | `val/nav` mixes USD value with ILS NAV, so a USD sector reads at 1/3.71 and the 40% hedge card is suppressed. INFERRED: needs fundamentals. To prove it, stub `_fundamentals` to sector "Tech" on a 100% USD book and check that no "Reduce Tech concentration" card appears |
| api/routes/backtest.py:17 `_holdings` | NO | POST /backtest structural beta, same as 2928 |
| api/routes/intake.py:148,397 | native by name (`val_native`, add-holding echo) | OK / display only |
| strategy_service.py:624, allocation_mix:134, funding_service:228, plan.py:102, market_impact, measured_assumptions, plan_health, reconcile, buyback_hold, recs 1700/2759/2961/4463/4783/6544 | yes | OK |
`digest_service.build` consumes `gather()`, so the digest LLM prompt carries the wrong holding values. The deterministic fallback uses only `nav_ils`, which is correct.

**CONFIDENCE.** PROVEN for context_service, risk_validation, _replace_book, fee_agent. INFERRED for sector hedge and /backtest.

**FIX.** Route every one through `fx.position_value_ils` (fx.py:189-198 exists for exactly this). In `_replace_book`, size `qty = weight*budget / (price * fx_rate(quote.currency))` after `to_major_units`, and set market from the quote. Tests that the data can fail: (1) load-basket replace on a USD book asserts NAV after == NAV before within ±1%; (2) `gather()` asserts `sum(holdings.value_ils) == nav_ils` and `asset_class_breakdown == current_mix` on a mixed USD/ILS book; (3) an AST/grep guard that `quantity * current_price` is never assigned to a name containing `_ils` without `fx_rate`.

**EFFORT.** S-M (six sites, one helper). **RISK.** Low. Replace-book is the urgent one: one tap inflates the tracked book 3.71x and the goal/projection inherits it.

---

## C9. GET /api/v1/recommendations writes

**SYMPTOM.** Building Today mutates trading rules and signals. It runs on every GET, in the assistant context (`context_service.gather` -> `build_recommendations`, also `ai_service.py:48`), in the digest, in push evaluation (`push_service.py:364,469`), and in the warm-up loop (`warmup.py:48`, every **45 min**, `EVERY_S = 45*60`, for every user), whether or not anyone opened the app.

**EVIDENCE.** `test_c9_get_writes.py`. Book: V/MSFT/SCHD/QQQ/VTI. Rules created through `POST /api/v1/rules` (each returned `ok: True`, no warning), then latched `triggered=1`:
```
RULES t0          AMZN buy_dip 90 (1,1) | GOOGL price_below 90 (1,1) | MSFT take_profit | NVDA stop_loss 150 (1,1) | SCHD trailing_stop | V stop_loss
GET#1 rule cards  ['rule_<V>', 'rule_<MSFT>', 'rule_<SCHD>']   banner healed: []
RULES after GET#1 MSFT take_profit | SCHD trailing_stop | V stop_loss     <- AMZN, GOOGL, NVDA rows DELETED by a GET
```
The AMZN `buy_dip` and GOOGL `price_below` are watch-list rules on tickers not held. Both were accepted at create time, had **fired**, and were deleted before any card or banner showed them. Dismissing the V card resolved that rule at dismiss time (`active 0, triggered 0` before GET#2), so the GET-time self-heal (recommendations.py:2785-2822) was a no-op on this fresh data. It only acts on cards hidden before rule-clearing shipped.

**Inventory of writes in `build_recommendations`** (read + observed):
| write | where | commits? | status |
|---|---|---|---|
| buy-back hold release | recommendations.py:1331 -> `buyback_hold.release_retreated` | — | **No-op since PR 83** (buyback_hold.py:195-200 returns []). Not a defect any more |
| purge rules for unheld tickers (DELETE) | `rules_service.list_rules` -> `purge_orphan_rules` (rules_service.py:1242-1245, 304-345), called from the build (recommendations.py:1959; also `strategy_signal_service.py:370`) | yes (:342) | OBSERVED above |
| standing-rule latch clear (`r.triggered=False`) | `triggered_rule_recs` rules_service.py:991-993, 1020-1025 | via later commits | by design (screen honest about NOW) |
| self-heal dismissed rule cards -> `resolve_rule(..., "acknowledged")` | recommendations.py:2785-2822 | yes (rules_service.py:882) | ONE_SHOT rules go `active=False` |
| resolve strategy signal of a dismissed card | recommendations.py:2826-2842 -> `resolve_signal` | yes (strategy_signal_service.py:416) | |
| orphan-rule heal (triggered, no visible card) -> `resolve_rule` | recommendations.py:3027-3080 | yes | ONE_SHOT -> `active=False` |

**RULED OUT.**
- *The deletions came from the evaluator job*: `ENABLE_SCHEDULER=false`, and nothing but the GET ran between the two `_rules()` reads. Killed.
- *Buy-back release*: the function is an explicit no-op. Killed.
- *Self-heal / orphan heal deleted them*: those call `resolve_rule`, which deactivates and never deletes, and `banner.healed` was empty. The rows were deleted, so the cause is `purge_orphan_rules`. Confirmed.

**ROOT CAUSE / harm assessment.**
1. **Harmful, PROVEN:** `purge_orphan_rules` treats every rule on an unheld ticker as stale. That is right for stop/take-profit on a sold position, which is what Eran asked for (docstring :307-317). It is wrong for `buy_dip` / `price_below` / `price_above`, which are entry alerts for a name you do not own yet. `create_rule` (rules_service.py ~1100) accepts them with no warning, and the next GET, assistant question, push evaluation, or warm-up within 45 minutes deletes them, even after they fire. Nothing is logged except an INFO line.
2. **Risky, INFERRED:** the orphan heal (:3061-3080) permanently deactivates a ONE_SHOT rule (stop_loss, take_profit, trailing_stop, buy_dip) whenever *this build* produced no card for it. The guards are "trading_rules agent degraded" and "suppressed by contradictions" (`_blocked`). They do not cover a card that vanished because some other agent failed quietly, or a build whose recs were partial. The warm-up and assistant builds can then retire a stop-loss the user never saw fire. One observation would prove it: a triggered stop_loss whose card builder raises (monkeypatch `execution_plan` to throw for one ticker without degrading `trading_rules`), then call `warm_once()` and read `active`.
3. **Benign:** clearing standing-rule latches and resolving signals of dismissed cards. These are idempotent and follow user actions.

**WHY SILENT.** No test asserts that GET is read-only for rules. `test_history_is_read_only.py` covers history only. The purge was added deliberately on the read path ("Self-healing on a read follows the existing precedent", :1239-1241), so each write was reviewed alone and never as "a GET with N writers".

**BLAST RADIUS.** Same writes via `/api/v1/rules` GET (`list_rules`), `plan_health` (`list_rules`, plan_health.py:197), `strategy_signal_service.py:370`, assistant, digest, push, warm-up.

**CONFIDENCE.** PROVEN that the GET deletes rules, including fired watch-list rules. INFERRED that the orphan heal can retire an unseen stop-loss.

**FIX.** (a) Exempt entry-type rules (`buy_dip`, `price_below`, `price_above`) from `purge_orphan_rules`, or refuse/warn at create time. That is Eran's call, per "every decision is a setting". (b) Move the heal/purge writes out of `build_recommendations` into the 30-minute evaluator, and give the build a `read_only=True` mode used by warm-up/assistant/digest/push. (c) Test: GET /recommendations twice and assert the `trading_rules` and `rule_events` row hashes are unchanged, except for explicitly allow-listed latch clears.

**EFFORT.** M. **RISK.** Medium: the heals exist to stop banner deadlocks, so moving them needs the evaluator to own them.

---

## C10. Two regimes: Today's risk-off card vs the strategies' regime

**SYMPTOM.** Today's "Markets look risk-off" card comes from the Yahoo **futures** regime (`markets_service._regime`: VIX level + same-day ES/NQ/YM change). Every sleeve signal, backtest, and frontier input uses `app/engines/regime.py` (SPY 200-DMA trend, realized-vol percentile, breadth). The code says in two places that futures must never drive anything: regime.py:20 "Futures stay on the Markets page as a labelled cross-check, never an input", and gfinance.py:30-31 "Display only… can never drive a backtested signal". The Today card is an input to the user's decisions anyway.

**EVIDENCE.** `test_c10_regimes.py`:
```
engines/regime.latest -> {'state': 'risk_on', 'score': 0.95}          # calm uptrend, what the sleeves act on
markets_service._regime -> {'regime': 'risk-off', 'score': -3, 'rationale': 'VIX 22.5, equity futures -0.40%'}
cached_regime() with a 3-day-old cache -> risk-off                     # no TTL check on the read path
CARD Markets look risk-off — phase the rebalance in | ... | ['Split the rebalance into 2-4 tranches ...']
cold cache -> risk-off cards: []
```
One mildly red morning (VIX 22.5, futures -0.4%) is enough to put the card on Today while the strategy regime is `risk_on` (score 0.95). Without a rebalance card it reads "Consider raising cash by ~5-10% … trimming your most volatile positions" (recommendations.py:2172-2181). On this user's book, those positions are the TQQQ/SOXL sleeves, whose own signal says stay invested.

**RULED OUT.**
- *They are the same rule with different labels*: different inputs (VIX/futures vs 200-DMA/realized vol/breadth), different horizons (intraday vs months), opposite answers above. Killed.
- *Futures only reach the Markets page*: recommendations.py:2169-2171 reads `cached_regime()` into a Today card, and `_reconcile` (:812-825) rewrites it into rebalance-timing advice. Killed.
- *Stale cache in production*: `cached_regime()` (markets_service.py:131-135) ignores `ts`. The scheduler refreshes it every 5 min with `force=True` (scheduler.py:147-158), and a failed fetch still overwrites with a "limited data → neutral" result. So staleness only happens when the scheduler is off or dead. That is a latent hazard, not the main defect.

**ROOT CAUSE.** recommendations.py:2165-2184 predates the PR that made `engines/regime.py` the single regime ("A market regime that can be backtested, because the backtest and the live read call this same function"). Only the strategy/backtest/frontier paths were migrated.

**USER IMPACT.** (1) Two answers to one question. The Markets page shows both and admits they "disagree today", while Today shows only the one the app says is not measurable. (2) The card advises trimming volatile positions and raising cash against the sleeves' own risk-on signal, and the contradictions pass does not catch it because the card has no `apply`. (3) It flickers intraday with 5-minute futures moves, under a stable id (`rec_macro_riskoff`), so a dismissal hides future, real risk-off states for the dismissal window. (4) After a cold start (deploy, `ENABLE_SCHEDULER=false`) the card never appears. If the scheduler dies, a stale risk-off persists for the life of the process.

**CONFIDENCE.** PROVEN (divergence and card observed). Production frequency of disagreement is INFERRED; `/api/v1/markets/futures` already returns `regime_agreement`, so logging that daily would measure it.

**FIX.** Drive the card from `regime.latest` (the proxy `gfinance.regime_proxy` already computes), or drop it. If futures stay as a cross-check, label the card "futures cross-check — not what your strategies act on" and never give it the "raise cash" action. Add a TTL check to `cached_regime`. Test: given proxy `risk_on` + futures `risk-off`, assert no "raise cash / trim volatile" card. Given proxy `risk_off`, assert the card appears and cites the proxy components.

**EFFORT.** S. **RISK.** Low. The proxy needs the SPY/QQQ/IWM history fetch, so it must be cache-only on the Today path, like `cached_regime`.

---

## L4. Naming debt: cash floor vs cash target, two MIN_TRADE_ILS

**EVIDENCE.**
Direct read (`python -c` against funding_service/headsup/thresholds, NAV ₪100,000, Grow, no plan):
```
funding_service.MIN_TRADE_ILS = 250.0 | headsup.MIN_TRADE_ILS = 100.0
funding_service.min_trade_ils() = 250.0 | headsup.min_trade_ils(nav=100000) = 500.0   # same name, different signature, different meaning
funding_service.cash_floor_ils(100000, Grow) = 3000.0 | cash_floor_pct = 0.03        # the SOFT target
cash_soft_floor_ils = 3000.0 | cash_hard_floor_ils = 150.0                             # the setting named `cash_floor_ils` IS this 150
plan_funding payload cash_floor_ils = 3000.0                                           # payload key = soft target
```
**Confirmed. Naming debt with real user-facing wording consequences, not a numeric defect.**

1. **`cash_floor_*` names the soft TARGET. The setting `thresholds["cash_floor_ils"]` ("Cash hard floor (₪)", thresholds.py:29) names the HARD floor.** The same token means two things. funding_service.py:109-111 records this as debt. What a user sees:
   - Today "Raise ₪X of cash" (recommendations.py:2196-2210): "against a 3% **floor** … rebuild your cash **floor** … Restores your liquidity **floor** to ₪3,000". It fires below the soft target Eran said may be crossed. Settings show "Cash hard floor ₪150".
   - Plan health (plan_health.py:216-225): "below your ₪3,000 **floor**".
   - Sleeve-funding refusal (strategy_service.py:801): "cash above your 3% Grow **floor**".
   - War-room card how-line (recommendations.py:1235): "floor of ₪{cash_floor_ils} stays untouched", which prints the target. It is true only because that path never passes `allow_reserve`. The one path that does (recommendations.py:4983) uses `describe_funding`'s reserve sentence instead, so the two do not collide today.
   - Callers that read the soft value under a "floor" name: funding_service.py:150 (correctly maxed with hard), :641-642 (payload), recommendations.py:2190, 2204, 4672, 6661 (`core_realign(cash_floor_ils=…)` -> core_realign.py:68,148 spends down to the TARGET, which is correct but misnamed), plan_health.py:132/216, rules_service.py:971-978 (correctly renamed locally to `_cash_target`), target_apply.py:181-225 and target_solver.py:42/1115 (the solver's `cash_floor` reserve = soft target), test_c3_funding.py:107 (`spendable = 100000 - f["cash_floor_ils"]`).
   - Most likely to be misread: anyone adding a "never below the floor" guard will grab `cash_floor_ils()` (₪3,000) or the setting (₪150) depending on which file they are in.
2. **`MIN_TRADE_ILS` / `min_trade_ils` in two modules.** funding_service: 250, the smallest *funded* leg (setting `min_funded_trade_ils`), resolved through a PEP 562 `__getattr__`. headsup: constant 100, but the effective card floor is `max(100, 0.5% NAV)` = **500** on this book (setting `min_card_trade_ils` + `min_card_trade_pct`). Both are settings with distinct labels, so they behave correctly. The constant names do not. Callers: funding_service users (advanced_rules, frontier_service.py:989/2134, rules_service.py:144-145/425-427, strategy_service, recommendations) all import the funding one. Only `headsup.split` (:73) and `thresholds.py:53` read the headsup one. No cross-import was found, so nothing reads the wrong one today.

**CONFIDENCE.** PROVEN (values observed). **FIX.** Rename to `cash_target_ils()/cash_target_pct()` and `HARD_FLOOR`. Keep the payload key `cash_floor_ils` as a deprecated alias with a new `cash_target_ils`, and change UI text "floor" -> "target" wherever the value is the soft one. Rename `headsup.MIN_TRADE_ILS` -> `MIN_CARD_TRADE_ILS` and `headsup.min_trade_ils` -> `min_card_trade_ils`. Test: a guard asserting that no user-facing string says "floor" next to a value that equals `cash_soft_floor_ils` while it exceeds `cash_hard_floor_ils`. **EFFORT** S-M (UI reads the payload key). **RISK** low.

---

## Summary

| item | verdict | severity | effort |
|---|---|---|---|
| C1 sleeve funding / solver preview ignore book target | **PROVEN defect.** Sells BND at 20% vs a 32% book target, card says "most overweight (20% vs 10%)". All 3 call sites; guard `MODULES` misses both files | High (Accept executes the sale) | S |
| C2a sleeve funding has no post-trade drift gate | **PROVEN defect.** Drift vs book target 0.44 -> 0.64 on the C1 book; `propose_funded_buy` would have refused | High (same path as C1) | S-M |
| C2b sleeve-drift TRIM card sizes ILS / USD price | **PROVEN defect (new).** "Trim ₪18,550 back to 25%" sells 185 sh (₪68,635); SOXL 300 -> 115 sh, ~11.5% | **Critical** (one tap oversells 3.71x) | S |
| C2c redeploy_cash claim | **NOT a defect** in probe (drift 0.50 -> 0.23, claim held); structural gap only. `.TA` agorot trap in `_apply_redeploy_cash` INFERRED | Low / (Med if .TA legs occur) | S |
| C3 ILS leak: assistant context + risk_validation + fee agent | **PROVEN defect.** SPY ₪37,100 shown as 10,000; class mix 17.5/65 vs 44/44; beta 0.27 vs 0.51; fee savings 3.71x low | Medium | S |
| C3+ `_replace_book` (Load basket, mode=replace) | **PROVEN defect (new).** ₪37,100 book becomes ₪137,641 (NAV x3.71) | **Critical** (mints money in one tap) | S |
| C3 sector-hedge `val/nav`, POST /backtest holdings | INFERRED (same pattern; not run) | Low-Med | S |
| C9 GET /recommendations writes | **PROVEN.** GET deletes rules on unheld tickers, including fired `buy_dip`/`price_below` watch alerts accepted at create. Orphan-heal retiring an unseen stop-loss INFERRED. Buy-back release is now a no-op | Medium | M |
| C10 two regimes | **PROVEN.** Futures say risk-off while engines/regime says risk_on (0.95); Today shows the card the code calls "display only". `cached_regime` has no TTL | Medium | S |
| L4 naming debt | **PROVEN (naming/wording only).** `cash_floor_ils` = ₪3,000 target vs setting `cash_floor_ils` = ₪150 hard floor; `min_trade_ils` 250 vs 500 | Low | S-M |

Probes: `C:\Users\erang\.claude\jobs\a36f9c6f\tmp\scratch_funding\test_c1_sleeve_funding.py`, `test_c2_drift_trim.py`, `test_c2_redeploy.py`, `test_c3_fx.py`, `test_c9_get_writes.py`, `test_c10_regimes.py`.
