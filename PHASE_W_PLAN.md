# Phase W — execution plan

_Written 2026-08-25, after mapping the code. Spec:
`BEAT_MARKET_TARGET_SOLVER_PLAN.md:931-947`._

Phase W is the last planned phase and the only one aimed at **more return**
rather than at honesty about the return already there. The spec is explicit that
it follows Phase T so it can be *"evaluated rather than believed"*.

Three parts, three PRs, independently shippable:

| | | why separable |
|---|---|---|
| **W1** | Walk-forward parameter sweeps | the big lever; new module, no schema |
| **W2** | The `vol_target` regime-gate re-decision | a threshold and a card, not a measurement |
| **W3** | Contribution modelling | needs `SimulationEngine` restructured; touches nothing else |

---

## What the map changed

**Three findings that alter the spec's framing.**

**1. The spec says sweeps exist "on every overlay". They exist on 6 of 7.**
`btm_factor_stack` (`strategy_catalog.py:116`) is `buy_hold` and correctly has no
parameter. Not a defect — but W1 must not assume a sweep exists, and must report
the strategy it could not sweep rather than omitting it.

**2. The sweeps already run nightly, and nothing reads the answer.**
`backtest_service.py:174-181` runs `bt.sweep()` for all six on every refresh and
persists to `StrategyBacktest.robustness`. Grepping every renderer, route and
script: **`robustness.sweep` has zero readers.** The only consumer of
`robustness` at all is `index.html:2371`, which reads `out_of_sample`.

So the compute is already being paid and discarded. That does not make W1 free —
the existing sweep is full-sample and selects nothing — but it does mean the
nightly job is already the right home, and no new scheduled job is needed.

**3. `run()` enforces a 260-session floor, so folds cannot be calendar years.**
`strategy_backtest.py:680` abstains under `_MIN_OBSERVATIONS = 260` (*"~1
trading year; below this, CAGR is noise"*). A calendar year is ~252 sessions, so
**any geometry with a one-calendar-year test window abstains on every fold** —
including the 3y/1y one recommended before the floor was found. Folds are
therefore defined in **trading sessions**, not dates.

This is the second time this floor has decided a design: it also forced PR 13's
ranking window from the 250 sessions the decision was worded in up to 260. Worth
saying once, here: any window this repo measures over is bounded below by 260
sessions, and a plan expressed in years cannot be checked against that.

---

## W1 — walk-forward parameter sweeps

### The question it must answer

Not *"which parameter value wins"*. That question always has an answer and the
answer is always overfitted. The question is:

> **Does fitting the parameter beat not fitting it?**

So every fold measures two things out-of-sample: the value **fitted** on the data
available at the time, and the value **already in the catalog** — what you would
have run had you never swept. The baseline is the catalog default, not the median
of the grid, because the default is the counterfactual that actually existed.

If fitting earns nothing — the common outcome for sweeps of this kind — that is
the finding, and the payload must be able to say it. A report that can only
conclude "parameter X won" is a report that will always find an edge.

### Geometry — rolling window, in sessions

**Decided 2026-08-25**, after a correction. The first recommendation was
"3y fit / 1y test" with the test window described as *"~250 sessions... just
barely"* clearing the engine's floor. **It does not clear it.** A calendar year
is ~252 sessions and `run()` abstains under 260, so that geometry would have
produced nothing at all. The floor was only noticed while working through the
expanding-window alternative. Everything below is stated in **trading sessions**
for that reason — a fold geometry expressed in calendar units cannot be checked
against a floor expressed in sessions.

```
sessions available: 2513     fit: 756 (FIXED)     test: 260     step: 260

  fold 1: fit    0.. 756 (756)    test  756..1016
  fold 2: fit  260..1016 (756)    test 1016..1276
  fold 3: fit  520..1276 (756)    test 1276..1536
  fold 4: fit  780..1536 (756)    test 1536..1796
  fold 5: fit 1040..1796 (756)    test 1796..2056
  fold 6: fit 1300..2056 (756)    test 2056..2316
```

Six folds. **Every fit window is the same length**, and that is the whole reason
to prefer this over the expanding window: it makes the folds comparable to each
other, so *"the parameter is stable"* becomes a claim the data can support. Under
an expanding window fold 1 chooses on 260 sessions and fold 8 on 2,080, and a
value that changes between them tells you nothing about the parameter — only
about how much data produced each choice.

The cost is that it does **not** mirror how a live system behaves, which is the
expanding window's genuine advantage and the reason it was chosen first. Both are
defensible; this one buys a checkable stability claim and gives up realism.

**Two things this geometry must report about itself:**

- **The last 197 sessions are in no test window.** 2,513 does not divide evenly,
  and the leftover sits at the *recent* end — the most relevant nine months are
  the ones not tested. Extending the final fold would make it ragged and break
  the comparability the geometry exists for, so the remainder is left out and
  **named** rather than quietly absorbed.
- **Test windows sit exactly on the 260-session floor.** The engine's own comment
  is *"below this, CAGR is noise"*, which makes every fold's out-of-sample figure
  the noisiest measurement the engine is willing to produce. That belongs in the
  payload, not in this document.

**Backlog, not W1:** run the 5y/1y rolling and the expanding geometries too and
report whether the conclusion survives all three. Fold geometry is itself a
researcher choice that can manufacture a result. ~3x the compute.

### Module shape

New `app/services/walk_forward.py`. **Pure** — takes a `measure` callable, no
price data, no engine, no database, matching `split_solver` / `core_search` /
`sleeve_count`. Every rule testable without a provider.

```
folds(n_sessions, *, fit=756, test=260, step=260) -> list[fold]
walk_forward(values, default_value, folds, measure) -> dict
    measure(value, fold, phase) -> float | None      # phase in {"fit", "test"}
```

Per fold: score every value on the fit window, take the argmax, then measure
**both** the argmax and `default_value` on the test window.

**Payload, and each field exists because a reader would otherwise infer it
wrongly:**

- `folds[]` — chosen value, fit and test session ranges, fitted test score,
  default test score, and the difference. Per fold, always, including the folds
  where fitting lost.
- `verdict` — `FITTING_HELPS` / `FITTING_DOES_NOT_HELP` / `INCONCLUSIVE`, with
  the mean difference and how many of six folds fitting won. One in six is
  noise; five in six is a finding. With only six folds the bar for
  `FITTING_HELPS` has to be set before the numbers are seen, not after.
- `stability` — how often the fit picked the same value. A parameter that jumps
  every fold has not been measured, it has been resampled.
- `abstained[]` — folds or values that could not be measured, with the reason.
  Never silently dropped.
- `coverage` — which sessions were tested and which were not, so the untested
  recent remainder is visible rather than inferred.
- `caveat` — that every test window sits exactly on the engine's noise floor,
  stated in the payload rather than left in a comment.

### Reuse

`split_solver`'s honesty helpers do this shape already and should be borrowed,
not re-derived: `fit_test()` (`:223-260`) reports the winner's decay **and the
median's**, flagging the winner only when it decays more than typical; `spread()`
(`:145`); `stability()` (`:177-221`).

`search()` itself is **not** reusable — it assumes `point` is an n-tuple summing
under `max_total`, and a parameter grid is not a simplex. W1 needs its own grid
driver.

### Inherited invariants — do not break

1. **Rank key decided once per run** (`split_solver.py:305-309`) — ranking some
   candidates out-of-sample and others full-sample compares two questions.
2. **The reported point is re-measured in full with `detail=True`**, never
   reported from the sweep (`target_solver.py:989-995`).
3. **`OOS_SPLIT` is imported, never redefined** — `ship-t6a.ps1:113` hard-fails
   any file that declares its own boundary. W1's fold boundaries are session
   indices and are not a second definition of that constant; the plan says so
   explicitly so the check is not worked around by accident.
4. **A measured-better variant ships OFF** until a human compares the numbers
   (`backtest_service.py:182-186`). **W1 does not write a fitted parameter back
   into any spec.** It reports; you decide.

### Cost and where it runs

6 overlays x 5 values x 6 folds x 2 phases ≈ **360 simulations**. An explicit
button and a nightly job, never inline on a card — the same rule T6a's cost
comment already sets.

### The trap

`slice_series` (`target_solver.py:887-903`) **silently drops any ticker left with
fewer than 3 rows**. Harmless over ten years, a survivorship filter on short
folds. W1 must count what it drops and report it.

---

## W2 — the `vol_target` regime-gate re-decision

**This is already measured.** `STATUS.md:2911`: `vol_target` buys **8.32 points
of drawdown for 1.71%/yr**. It fails only because `MAX_CAGR_GIVEUP_PCT = 1.0`
and 1.71 > 1.0 — and `strategy_backtest.py:60-64` records that the constant is
*chosen, not discovered*. `STATUS.md:2931` already puts the question:

> *"on a 3x leveraged sleeve, 8 points of drawdown for under 2%/yr is arguably a
> trade worth taking... whether `vol_target` in particular should be switched on
> is Eran's call."*

So W2 is not a measurement, it is a **threshold you own** — the same shape as the
drawdown ceiling in PR 14, and it deserves the same treatment:

- Surface the gated-vs-ungated comparison where it can be seen. Today
  `robustness.regime` is computed at `backtest_service.py:187-194` and rendered
  nowhere.
- Make `MAX_CAGR_GIVEUP_PCT` settable rather than a module constant.
- **The gate still ships off by default.** Two independent places currently
  enforce that (`backtest_service.py:194` literal `"enabled": False`, and
  `run(regime_gate=False)`); W2 keeps both and adds a per-strategy opt-in.
- Enabling costs no extra data: `tickers_needed(spec, regime_gate=True)` already
  fetches the regime indexes everywhere.

Small PR. Mostly surfacing and one setting.

---

## W3 — the projection is blind to your deposits

`SimulationEngine.run()` (`simulation_engine.py:27-39`) is a **single-shot GBM**:

```
gbm = np.exp((mu - 0.5 * sigma**2) * T + sigma * np.sqrt(T) * z)
nominal = initial_value * gbm * fx_factor
```

One multiplicative draw on starting NAV, **no time-stepping** — so there is
nowhere to insert a periodic deposit without restructuring it into a stepped
simulation. That is the bulk of W3's work.

**The engine already admits the gap** (`simulation_engine.py:89`, assumptions:
*"no rebalancing or cash flows during the horizon"*) — but `plan.py:602` builds
its own user-facing assumptions string and **does not propagate that caveat**. So
the card shows a no-contribution projection without saying so. That one line is
the cheapest honest fix in Phase W and should land first, before any modelling.

**The asymmetry W3 removes.** `contributions` (`tables.py:425`) is a well-modelled
signed ledger, and `recommendations.py:1976-1993` already solves the annuity
*backwards* — "what monthly deposit closes the gap":

```
pmt = gap * rm / (((1 + rm) ** n) - 1) if rm else gap / n
```

The projection is the shortfall **input** to that solve, so it can never fold the
contribution back in. Today the app can tell you what deposit closes a gap but
cannot show you the future in which you make it.

**Scope:**
1. Propagate the "no cash flows" caveat to the card. One line, ships alone.
2. Restructure `SimulationEngine.run()` to step, preserving the existing
   single-shot result exactly when contribution is zero — asserted, so the
   restructure cannot quietly change every existing projection.
3. Take a contribution rate: default it from the **measured** ledger
   (`total_contributed` over elapsed time), never invent one, and let it be
   overridden. `total_contributed` returns `None` rather than `0` on an empty
   ledger, and that distinction must survive.
4. Show the projection with and without it, because the spec's real claim is
   that *"at the book's current NAV the contribution rate moves terminal value
   more than any admissible excess does"* — and that is checkable only if both
   are on screen.

---

## Sequencing

W1 → W2 → W3, with W3's step 1 (the missing caveat) pulled forward and shipped
whenever convenient, since it is one line and the card is currently silent about
an assumption that changes its meaning.

## Not in Phase W

Still open and deliberately not folded in: the `AGENT_API_KEY` rotation (now in
**31 committed scripts**, the oldest open item), the intermittent `invested_ils`
balloon from CI #169, `alembic upgrade head` from scratch, the equity-universe
liveness gate, and rule notes freezing their creation-day price.

Not financial advice.
