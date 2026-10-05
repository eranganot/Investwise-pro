# Execution plan — the efficient frontier (PR 88–92)

_Approved by Eran 2026-10-05. Scope: options #2 + #3 + #4 from the 2026-10-05 validation, plus Kelly._

## Finding that started this

The plan does **not** optimise on the efficient frontier:
- Core targets are constants — `allocation_mix.OBJ_TARGET`, each catalog strategy's `target_allocation`.
- `book_target` adds the sleeves to a core chosen in isolation.
- T2/T6 (`target_solver`, `split_solver`) maximise excess CAGR under a drawdown ceiling, over sleeve **share** only.
- `engines/portfolio_risk.py` computes a covariance matrix that is used for reporting, never for a weight.

## One pipeline, one answer

```
inputs (#3a)  →  propose (#2 + #3b)  →  validate (#4)  →  size (Kelly)  →  book_target → funding/cards
```

Three optimisers quoting three targets would break "one function, so two screens cannot quote two answers".

## Decisions (Eran, 2026-10-05)

1. **Apply mode:** propose + Accept. Never auto-rebalance.
2. **Sleeves:** held fixed by the core optimiser; sleeve resizes from #4 are a separate card.
3. **Kelly fractions:** Grow ½, Balanced ⅓, Preserve/Income ¼. Never full Kelly.
4. **Universe:** asset classes plus a US/intl equity split — not tickers.

## PRs

| PR | What | Behaviour change |
|---|---|---|
| **88** ✅ | `engines/frontier.py`: Ledoit-Wolf, sleeve-conditional optimiser (projected gradient on a capped simplex), frontier, target-volatility point, Kelly growth/position. | none |
| **89** ✅ | `services/frontier_inputs.py` + `GET /plan/frontier/inputs`: ILS-denominated return matrix (class proxies + **sleeve backtest streams**, not tickers); Black-Litterman mu — equilibrium prior + `measured_assumptions` OOS views, confidence by observations; NOT_MEASURED / degraded reasons. | none |
| **90** | Block-bootstrap resampling (21-day blocks, ~200 draws) → averaged weights + stability band; no-trade zone when the current target sits inside it; min-variance fallback when no views. **Read-only Frontier card**: book vs target vs frontier, efficiency gap, Kelly position, over-Kelly warning. | read-only |
| **91** | Whole-book mean-CVaR validation: `split_solver.search` with core classes as axes, `blend.measure_blend`, OOS-ranked, CVaR95 cap + drawdown ceiling. Nightly job cached in `solver_store` + a test that actually runs the job. Disagreement with #2 shown; the more conservative wins. | read-only |
| **92** | `plan.target_source = "fixed" \| "frontier"`; frontier core feeds `book_target` only on Accept and only when the gain beats cost + tax (`tax_engine`). Every buy via `funding_service.propose_funded_buy()`; `test_card_claims` re-measures the post-trade mix. | **live, opt-in** |

## Rules every PR keeps

- Returns are ILS — USD/ILS risk is inside the covariance.
- Stated returns are CAGR; the engine converts once through `return_convention.drift_pct`.
- Every out-of-sample figure carries `split_solver.OOS_CAVEAT` (one bear market is a sample of one).
- SQLite + Postgres + ruff green before push.

## Change, 2026-10-05 (Eran): actionable cards, user dials, target-aware

- Cards must be **actionable**: Accept executes. The first is the resampled shrink-only **sleeve resize** (the live
  inputs show the sleeves carry the risk; core-only re-optimisation reaches just 1.14x → 1.08x Kelly).
- **Kelly fraction** becomes a plan setting (default by objective, Grow 0.5; range 0.25–1.0).
- **Return confidence** becomes a plan setting: shrunk (default), blended, measured.
- The card is **target-aware**: expected compound growth against `target_roi_pct` under both estimates, and it says
  so when no setting reaches the target, or when a resize would drop the book below it under the measured numbers.

Renumbered:

| PR | What |
|---|---|
| **90** | migration 0026: `plans.kelly_fraction`, `plans.return_confidence` (add-only, ships alone) |
| **91** ✅ | the actionable resize card + both dials + target comparison + materiality gate (the work drafted as PR 90) |
| **92** | whole-book mean-CVaR validation, gating the cards |
| **93** | core-tilt card with `plan.target_source = "frontier"` |
