# PR 16 — the forward numbers stop guessing

_Written 2026-08-29, after Eran's eight-item review of the live app. Chosen by
him over three other fixes because it is the root cause of four of them._

## The finding

Four separate complaints — the projection, the health score, the expected
return, and the year-6 chart — are **one defect**.

`_portfolio_stats` (`plan.py:36-55`) and `compute_snapshot`
(`portfolio_analytics.py:27-77`) both derive the book's forward return and
volatility from `strategy_profile.assumptions_for()` — a static per-ticker
lookup keyed by instrument character. TQQQ and SOXL are priced there at
**12%/yr return, 55% volatility: buy-and-hold**.

Grepping `portfolio_analytics.py`, `strategy_profile.py` and the plan routes for
`StrategyBacktest`: **zero readers.** Every measurement Phase T produced is
invisible to the numbers on Today.

He does not hold TQQQ and SOXL buy-and-hold. He holds them inside
`btm_swing_dip` and `btm_trend_soxl` — rule-based sleeves whose *whole purpose*
is to be out of the instrument some of the time, and whose measured drawdowns
are 43–50%, not what 55% buy-and-hold volatility implies.

### Reproduced against the live screens

| screen | shown | reproduced from | ✓ |
|---|---|---|---|
| Risk chip | 15 | weighted volatility **45.6%** vs a 25% cap | ✓ |
| Health | 69 | 0.30×15 + 0.25×100 + 0.25×100 + 0.20×71 = 68.7 | ✓ |
| Projection | ₪21,723 / ₪3,493 / ₪135,825 / 56% | NAV ₪21,689, 10.87%/yr, **46%** vol, 6y | ✓ |

The arithmetic is right everywhere. **The input is wrong in one place and four
screens inherit it.** At 45.6% volatility the drag term σ²/2 is 10.4 points a
year, which consumes essentially the whole 10.87% expected return — which is why
the median line is flat while the 95th percentile reaches ₪135k.

---

## What this PR does NOT get to decide

**A measured CAGR is what happened, not what will.** W1 measured exactly this
question three days ago and found **not one strategy** in the catalog reaches
`FITTING_HELPS` — nothing shows a stable, out-of-sample edge. A full-sample CAGR
for a 3x leveraged trend strategy over 2015–2025 is a bull-market
extrapolation, and shipping it as "your expected return" would be the same
overfitting W1 exists to detect, wearing a better label.

So this PR separates the two halves of the assumption, because the evidence for
them is not the same:

**Volatility — measured, always, where a measurement exists.**
Volatility is the stable half of a backtest. A trend rule that sits in cash
through drawdowns genuinely has lower realised volatility than the instrument it
trades, and that is a property of the *rule*, not of the regime. A static 55%
describes an instrument Eran does not run.

**Return — measured OUT-OF-SAMPLE by default, and never silently.**
`robustness.out_of_sample.cagr_pct` already exists on every row, computed at
`OOS_SPLIT`, and the app already *ranks* on it (`split_solver` decides its rank
key once per run for exactly this reason). Using the full-sample figure forward
would ignore the app's own standard of evidence. The full-sample number is
carried alongside so the gap is visible, and `ASSUMPTION_RETURN_BASIS` moves it
without a code change — the same treatment W2 gave the gate threshold.

Both defaults are stated here, before the numbers, and both are settings.

---

## Rules the module holds to

Checked against `investing-discipline` §1, §3, §4:

1. **Ground or abstain.** A measurement is used only when `ok` is true, its
   `engine_version` matches the current `ENGINE_VERSION`, and `observations >=
   260` — the same floor `strategy_backtest` enforces and the same one that has
   now decided four designs. Otherwise the component **abstains by name** and
   falls back to the static table *labelled as static*. Nothing is invented and
   nothing silently substitutes.
2. **Per sleeve, not per ticker.** A sleeve's measurement belongs to the
   *strategy*, not to the ticker it happens to hold. Weighting is by each
   sleeve's **held** share (`sleeve_occupancy.held_pct`), not its target — the
   distinction PR 6 exists to make. Tickers no sleeve claims keep the static
   table.
3. **Provenance on every component.** Each carries `return_source` and
   `volatility_source` independently, because a component can legitimately have
   a measured volatility and a static return. A blended number whose halves came
   from different places must say so.
4. **Reproducibility pins (§3).** Every component carries `engine_version`,
   `period_start`, `period_end`, `observations` and `computed_at`. A stale pin
   is an abstention, not a silent reuse.
5. **Behaviour-preserving by default.** `_portfolio_stats` and
   `compute_snapshot` gain an *optional* override argument. Called without it
   they produce byte-identical output to today — asserted, the same discipline
   W3's engine branch used. Eight callers of `compute_snapshot` do not change.
6. **The score is allowed to move, but not quietly.** This changes Eran's health
   score. The basis is on the payload and on the chip, and the card says which
   of its inputs were measured.

---

## Shape

New `app/services/measured_assumptions.py`. Pure core plus one async loader,
matching `split_solver` / `core_search` / `sleeve_count` / `walk_forward`.

```
blend(components) -> dict                       # pure; NAV-weighted, abstentions named
book_assumptions(session, user) -> dict         # loads occupancy + backtests, blends
```

Payload: `expected_return_pct`, `volatility_pct`, `basis`
(`measured` / `mixed` / `static`), `measured_pct_of_book`, `components[]`,
`abstained[]`, `pins`, and the W1 caveat in words.

Consumers: `/plan`, `/plan/projection`, and the health path. Frontend labels the
expected-return banner and the Risk chip with the basis.

## Not in this PR

The other three findings from the same review, each its own PR: the
contradicting cards (`contradictions.py` rule 2 matches on title text), the
account-history flow blindness (`time_weighted` reads only the contributions
ledger, so a holding added through intake reads as +37% performance), and the
split search ignoring an accepted sleeve total.

Also not here: `nav_snapshots` records a daily health score and this PR changes
what that score means. The stored series will have a seam. Recording the
assumption basis on the row needs a migration and is named as follow-up rather
than smuggled in.

Not financial advice.
