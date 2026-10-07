# Beat the Market — Phase T: the target solver

Written 2026-08-14, revised the same day after review. **Revalidated 2026-08-15
against the Card Claims batch (`98c8b26`).** Sits after Phase C6 and after Card
Claims. Phases are lettered **T** so they cannot be confused with the `P0–P4` of
`BEAT_MARKET_NEXT_PLAN.md` or the `C1–C6` sleeve phases in `STATUS.md`.

---

## Revalidation — 2026-08-15, against the Card Claims batch

Checked after the *"a card may not claim a change it does not produce"* work
shipped (`f127d25` → `453188e` → `ddd0d81`, merged `cb2b72b`, `30a3752`,
`98c8b26`; 790 passed, 4 skipped).

**The plan still holds. Nothing in it is invalidated.** Neither
`app/engines/blend.py` nor `app/services/target_solver.py` exists yet, so no work
has been started against a stale assumption. Every symbol the plan depends on is
present and unchanged: `benchmark_ticker` at `app/core/config.py:106`;
`cagr_pct`, `max_drawdown_pct`, `benchmark_cagr_pct`, `excess_cagr_pct`,
`gross_cagr_pct`, `tax_drag_pct`, `DEFAULT_COST_BPS`, `OOS_SPLIT`;
`sleeve_service.validate` / `all_sleeve_targets` / `add_or_resize`.

**T0.3's two defects are still live and still worth fixing.**
`performance_service` still emits `start_value_ils` / `end_value_ils` with no FX
rate applied anywhere in the function, and still puts `cagr_pct` next to
`excess_return_pct` — an annualised figure beside a total-period one.

### Five things the batch changed that this plan must absorb

**1. `fund_plan` is in `strategy_service.py:594`**, not `funding_service`. It
calls `plan_funding` at line 641. Corrected throughout.

**2. `plan_funding` now raises NET proceeds.** It previously filled a gross
target and subtracted tax at the end, which made nearly every card announce a
shortfall it did not have. `would_execute.funding_required_ils` and
`estimated_cgt_ils` must be **net-consistent with this**, or Phase A reproduces
the exact "still leaves ₪430 short" bug the batch just killed.

**3. Sleeve exclusion is now a real, threaded mechanism — use it.**
`exclude: set[str]` runs through `rank_trim_candidates` → `plan_funding` →
`propose_funded_buy`, and `recommendations.py:540` passes
`sleeve_service.sleeve_tickers(...)`. Defect #5 of the batch was funding raiding
TQQQ and SOXL to pay for a Today card — the sleeves themselves. **Phase A must
thread the same `exclude`**: funding one sleeve's increase must never sell
another sleeve. And it must follow the batch's failure posture — when sleeve
tickers are unavailable, `recommendations.py:475` logs a warning and reports
degraded rather than proceeding unprotected.

**4. `FundingLedger` (`funding_service.py:531`) — a new cross-path hazard.** It is
threaded through every `propose_funded_buy` *in one build*, so no two Today cards
spend the same shares. Phase A applies from a **different request**, and nothing
today prevents a solve-driven apply and a Today card from claiming the same
shares in the same window. **This is the one genuinely new risk the batch
creates for this plan**, and Phase A cannot ship without deciding it: either
Phase A joins a shared ledger scope, or it re-reads and re-validates funding legs
at apply time and refuses on drift. Named here so it is not discovered live.

**5. `propose_funded_buy`'s drift guard does NOT sit on the sleeve path — and
that is deliberate.** It refuses a buy into a class at or over target, or any buy
that increases total drift. Sleeve funding calls `plan_funding` directly
(`strategy_service.py:549, 641`) and bypasses it, because a sleeve is a
*ticker-level* intent and asset-class allocation cannot express it — the P1.1
finding, that TQQQ and QQQ are both Equities. Phase A inherits that bypass.
**Record it as intentional**, because it reads exactly like the bug the batch
just fixed and a future session will otherwise "correct" it.

### The batch's real lesson, applied to this plan

> *"Every test in the area asserted on strings… Text can agree with itself while
> the arithmetic disagrees with both."*

`tests/test_card_claims.py` now asserts on **portfolio state**, by applying each
card's own `apply` spec and re-measuring across five adversarial books. That is
the house standard now, and this plan must meet it rather than the older
shape-and-string bar:

* **T4's card is a claim.** "At 17% the blend measured +2.1% excess at 44%
  drawdown" is a claim about a change, and under the new rule it may only come
  from the measurement that produces it. T1's design already does this — it
  blends the targets and re-measures rather than combining headline numbers —
  but the card's test must assert the rendered figures **equal a re-measurement**,
  not that the fields are present.
* **Phase A's tests apply and re-measure.** Assert the resulting book, not the
  preview text: post-apply NAV, cash, per-sleeve percentage and per-ticker
  quantity, compared against what the dry-run said would happen. A dry-run whose
  prose is right and whose arithmetic is wrong is precisely the failure mode.
* **`smoke-t2.ps1`'s read-only assertion stays state-based**, which it already is.

---

## Read this section first: what this phase is, and what it is not

**This phase adds no return. If it appears to, something is wrong.**

It builds a measuring instrument. That is the whole of it. Stating this plainly
up front because the honest version of the question — *"if it adds no strategy,
what does it do for beating the market?"* — deserves a direct answer rather than
a plan that quietly implies otherwise.

### What "Beat the Market" is today

A label on a tab. Nothing in the app can currently answer any of:

* Am I beating the benchmark? *(the Performance card says −6.98% over 250 days, but that figure lives on a different screen from the sleeves that are supposed to produce it)*
* By how much would my sleeves have to change to beat it?
* Is my target reachable at all with the strategies I have?
* When I change something, did it help?

Four questions, zero answers. A goal you cannot measure is a slogan.

### The three deliverables

**1. A verdict.** Given the sleeves you run, the caps that bind you, and a
drawdown you are willing to hold through: the best measured excess achievable,
and whether your target is inside or outside that set. One of five outcomes, all
of which are legitimate answers — including "no".

**2. A named binding constraint.** When the target is unreachable, the payload
names *why*: which component's measured excess is holding the blend down, or
which cap stops the sleeve growing, or which drawdown ceiling binds first. This
is the actionable part. "You cannot get there" is a dead end; "you cannot get
there because `btm_trend_soxl` measured +1.2% excess and would have to measure
+9% for any admissible size to work" is a work item.

**3. A control loop.** One number, re-measurable on demand, that tells you
whether the *next* thing you build actually helped. Every return-generating idea
in the backlog — walk-forward sweeps, vol targeting, better entry rules — needs
this instrument to prove it worked. Without it, you ship changes and guess.

### How you are affected, concretely

Today: you press **Resize sleeve** and nothing on the screen tells you what the
size buys. The slider offers 5% to 100% and every position on it looks equally
legitimate.

After: before pressing anything, you see — at 10% the blend measured +0.4%
excess at 21% drawdown; at 25% it measured +2.1% at 44% drawdown; your ceiling of
30% drawdown binds at 17%; your target of +8% is not reachable at any admissible
size, and here is the component responsible.

The change is not that you earn more. It is that you stop making the decision
blind, and you find out *which* thing to fix in order to earn more.

### What actually generates return — and where it lives

Not here. The levers that plausibly add measured excess are, in order of how
much I would trust them:

1. **Walk-forward parameter sweeps.** `strategy_catalog` already carries
   `sweep_param` and `sweep_values` on every overlay. They have never been run
   walk-forward. This is the only untested source of *additional* measured edge
   in the entire family.
2. **Vol targeting**, on its own P3 numbers: 8.32 points of drawdown for
   1.71%/yr. Not a return lever — a *survivability* lever, which is what lets a
   larger sleeve exist at all under the drawdown constraint this phase
   introduces.
3. **Cost reduction.** Already largely done, and done well — see "Credit where
   due" below.

That work is **Phase W**, and it should follow immediately. Phase T exists so
Phase W can be evaluated rather than believed. Building W first would mean
shipping changes with no way to tell if they helped, which is how the regime
gate nearly shipped enabled before P3 measured it.

### No promises

A standing constraint on this phase, since the ask was explicitly for logic
rather than promises:

* **No figure the app emits may be a forecast presented as a result.** Every
  measured number carries its window and its source. The Monte Carlo band is the
  only projection, and it is labelled one.
* **The phase's success criterion is not a return figure.** It is: does the card
  produce a correct, reproducible verdict, including when the verdict is "no"?
* **`UNREACHABLE` is a passing outcome.** Given the current card, it is also the
  likely one. Shipping it legibly *is* the deliverable.

---

## Why this phase exists (the evidence)

**The book is losing to SPY with a worse drawdown.** The Performance vs Benchmark
card, backfilled over 250 days from current holdings (source yahoo):

```
Total 13.63%   CAGR 13.8%   Max drawdown 18.31%   vs SPY  −6.98%
```

Positive absolute return, negative excess, and a drawdown deeper than the index
it lost to. More risk *and* less return. Nothing on the Plan screen connects that
result to the sleeve sizes that produced it.

---

## The target: excess, at a bounded drawdown

**Not absolute.** "30%" silently changes meaning with the market — a heroic
demand in a flat decade, an underperformance in 2023–2024. `strategy_backtest`
already computes `benchmark_cagr_pct` and `excess_cagr_pct`, and
`backtest_service.measure()` already threads the benchmark through, so the
relative framing is the one the engine is built for.

**And not raw excess either.** Anyone beats SPY's CAGR by holding 1.3× SPY. No
skill involved. An objective of *maximise excess subject to the caps* is
therefore satisfied by the most leveraged admissible blend, every single time —
and since every sleeve in this family is leveraged or concentrated, that is not a
corner case, it is the default answer. A solver optimising raw excess is a
leverage-maximiser wearing a strategy costume.

### The objective function, stated once

Whatever goes in the box is what the solver optimises for, so it is worth writing
down exactly:

```
maximise   excess CAGR over the benchmark
subject to blended max drawdown <= D           (hard, user-set)
           sleeve_service.validate passes      (caps, cash floor)
report     excess at equal risk, alongside
```

Three parts, each doing a job the others cannot:

* **Excess** is the goal, because "beat the market" is a relative claim.
* **The drawdown ceiling `D`** is what stops the answer being "more leverage". It
  constrains the *admissible set*; it is not a number reported afterwards.
  Without it every other safeguard in this plan is decoration.
* **Excess at equal risk** — the CAGR the blend would have produced scaled to the
  benchmark's own drawdown — is the honest scoreboard. Leverage scales both sides
  of it, so it cannot be gamed. Reported, never optimised: a ratio makes a poor
  target and an excellent verdict.

Absolute CAGR stays on screen as a secondary figure. It is never the target.

---

## Decisions taken (agreed 2026-08-14 — do not re-litigate)

| # | Decision |
|---|---|
| Target units | **Excess CAGR over the benchmark, with a hard blended-drawdown ceiling.** Two inputs, not one. |
| Scoreboard | **Excess at equal risk** reported beside every result, because it is the one figure leverage cannot inflate. |
| Benchmarks | **Two, never conflated.** Each sleeve is judged against its own `base` (QQQ for TQQQ, SMH for SOXL); the *book* is judged against `settings.benchmark_ticker`. |
| Solve scope | **All of them** — resize current sleeves, add catalog strategies, vary the core — sequenced smallest-search-first (T2) to full search (T6). |
| Concurrent sleeves | **Discovered, not capped.** The search grows the sleeve count until adding one stops improving the constrained objective, and reports where that happened. |
| Write access | **Read-only for all of Phase T.** No endpoint in this phase mutates `plan_sleeves`, holdings or cash. |
| Future execution | Built for it from T2: every result carries a `would_execute` block whose legs are emitted in the **`orders` table's own column shape** (`BROKER_INTEGRATION_PLAN.md` §3), so both Phase A and broker Phase 2 construct from it directly rather than re-deriving. |
| Acting on the answer | **Phase A**, a separate phase — applies to the *tracked book* only. Kept out of T so T's read-only assertion stays absolute. Real orders remain `BROKER_INTEGRATION_PLAN.md` Phase 2, gated on credentials and a signed agreement. |
| Core return | **Measured from the tickers the core actually holds**, over the same dates as the sleeve, in one simulation. No assumed core return, no assumed correlation. |
| Blend method | **Simulate the blended daily targets. Never average the headline numbers.** |
| War room | The solver is **not an agent**. Its output becomes a **constraint the existing agents read** (T5). |
| Abstain | A target no admissible allocation reaches is a **first-class result** with a named binding constraint — never a slider left implying it is one drag away. |
| Card default | Target field defaults to **the book's current measured excess**, so the first thing shown is where you are. |

---

## The one engineering idea

The obvious implementation is wrong, and it is worth stating why before any code.

**Wrong:** blend the headline numbers — `0.7 × core_cagr + 0.3 × sleeve_cagr`.
That is a weighted average of two CAGRs, which is not the CAGR of the blended
book. It ignores rebalancing and the order returns arrive in. For drawdown it is
worse than wrong: averaging two max-drawdowns assumes the two never bottom on the
same day, and a trend-filtered TQQQ sleeve and an equity core bottom on *exactly*
the same days. The averaged figure would understate the real drawdown by the
largest margin precisely when it matters most — and under the new objective,
drawdown is a hard constraint, so an understated drawdown admits blends that
should have been rejected.

**Right:** blend the daily target vectors and run the simulator once.

`strategy_backtest.targets_for(px, spec)` already returns a per-session list of
ticker→weight dicts, and `_simulate(px, targets, ...)` already turns that into an
equity curve from which `_metrics` derives `cagr_pct`, `max_drawdown_pct` and
`excess_cagr_pct`. So:

```
blended_targets[t] = Σ_i  w_i · sleeve_targets_i[t]  +  w_core · core_targets[t]
```

fed to the *existing* `_simulate`, produces a measured blended CAGR and a measured
blended max drawdown over real dates, with the true correlation between core and
sleeve baked in — because it is the same simulated book on the same days. No
correlation parameter is invented, because none is needed.

This also makes the core requirement cheap: the core's "targets" are its held
tickers at their current weights, held every day.

---

## Ordering, and why

```
T0 benchmarks & windows  →  T1 blend engine  →  T2 constrained solve
   →  T3 what it costs  →  T4 the card  →  T5 war room  →  T6 full search

then, as separate phases:   A  apply to the tracked book
                            W  walk-forward sweeps (the return work)
                            broker Phase 2 (real orders — gated externally)
```

**T0 comes first for the same reason P3 came before P4.** Every number after it is
an excess measured against a benchmark across a window. Settle the benchmark and
window after building the engine and every figure it produced is quietly wrong,
and the two cards on the Plan screen disagree by construction.

**T6 comes last** because it is the only phase whose search space is
combinatorial, and a full search built on an unverified blend engine produces
confident nonsense across seven strategies instead of one.

---

## T0 — Two benchmarks, and every number carrying its window

No solver yet. This phase makes the existing measurements able to sit on one
screen without contradicting each other.

### T0.1 The book benchmark and the sleeve benchmark are different questions

The sleeves are TQQQ, SOXL and factor ETFs — Nasdaq and semis. Measured against
SPY, most of their apparent "excess" is a QQQ-vs-SPY factor bet, not strategy
skill. The catalog already gets this right internally: `base` is `{"QQQ": 1.0}`
for the TQQQ sleeves and `{"SMH": 1.0}` for SOXL.

**Change** — two figures, never conflated, both always labelled:

| question | benchmark | what a positive answer means |
|---|---|---|
| Does the rule work? | the sleeve's own `base` | the timing rule beats simply holding what it levers |
| Does the book beat the market? | `settings.benchmark_ticker` | the whole thing beat the index |

Conflating them is how you get a strategy that "works" while the book loses —
which is approximately the current picture. Both go on the card.

### T0.2 Two measurements of "how am I doing" that will disagree

`performance_service.performance()` backfills the **current holdings** over
`history_days=252`. `backtest_service.measure()` measures a **strategy rule** over
ten years. They will disagree, and both are correct.

**Change** — every figure either service emits carries an explicit
`window: {start, end, sessions, kind}` where `kind` is `holdings_backfill` or
`strategy_backtest`, and the UI never renders two figures of different `kind`
without labelling both.

**Why this is a safety item, not cosmetics:** three of the last batch's live bugs
(STATUS.md — tax harvest selling the capped sleeve, the ₪12 CRITICAL, the
self-correcting cap that kept nagging) were agents individually correct and
mutually contradictory on one screen. Same failure shape, pre-empted.

### T0.3 Two defects found while grounding this plan

**(a) A CAGR and a total return share one line.** The card renders `13.8%` from
`summary["cagr_pct"]` — annualised — next to `−6.98%` from
`summary["excess_return_pct"]` — a total-period figure. Two different bases,
one row, no labels. Either annualise the excess or label both.

**(b) `start_value_ils` / `end_value_ils` are not ILS.** In
`performance_service._performance_from`, portfolio value is
`sum(qty[t] * maps[t][d])` over raw closes with **no FX rate applied anywhere in
the function**, yet the output fields claim ILS.

* If the book is all-USD, the index is USD, the SPY comparison is FX-neutral, and
  only the *label* lies.
* If it holds even one TASE ticker, that line **adds shekels to dollars** and the
  entire index — and therefore the vs-SPY figure — is meaningless.

**Inferred from code, not observed.** Per the investigation rule, T0 begins by
checking which case is live before anything is changed. The fix differs
completely between the two.

### T0.4 The benchmark is a setting, not a constant

`benchmark_ticker: str = "SPY"` exists at `app/core/config.py:106` and
`backtest_service.measure()` takes `benchmark_ticker=`. Confirm no second
hard-coded `"SPY"` sits on the path to the card, and add `benchmark_ticker` to
the `StrategyBacktest` row so a changed setting marks rows **stale** rather than
silently relabelling a SPY-measured excess as excess-over-QQQ.

**Files** `app/services/performance_service.py`, `app/services/backtest_service.py`, `app/models/tables.py` (+ alembic), `app/core/config.py`
**Tests** a figure without a window fails a schema check; a benchmark change marks rows stale rather than reinterpreting them; sleeve-vs-base and book-vs-benchmark are separate fields that cannot be swapped; the CAGR/total-return pair is either same-basis or separately labelled.
**Risk** low. Additive fields plus one migration — except T0.3(b), whose blast radius depends on the live check.

---

## T1 — Blend by simulating, not by averaging

New pure module `app/engines/blend.py`. No I/O, no session, no ORM — the same
discipline `_performance_from` follows, so it is testable without a price
provider and runs under `offload`.

### T1.1 `blend_targets(components, dates) -> list[dict]`

Takes `[{targets, weight}]` plus the aligned date axis, returns one target vector
per session. Rules:

* Components must already be aligned by `strategy_backtest.align()` on **one**
  date axis. A component whose history starts later is a hard error, not a silent
  left-pad — a sleeve that did not exist for three years would otherwise look
  like a sleeve that was in cash, which flatters it.
* Weights sum to ≤ 1.0. The remainder is **cash**, explicitly, not implicitly
  redistributed — the objective's cash floor is real money.
* Two components wanting the same ticker **sum** into one position. Same rule
  `_arm_sleeve_caps` uses for `max_weight` per ticker, and the same reason C4
  measures drift per ticker at the summed target. Anything else models a book the
  app cannot hold.

### T1.2 `measure_blend(series, components, benchmark) -> dict`

Calls `blend_targets`, then the existing `_simulate` and `_metrics`. Returns the
standard metrics dict, so a blended result is the same shape as a single-strategy
result and every existing renderer can read it. Plus four fields a blend needs:

| field | meaning |
|---|---|
| `components` | each id, weight, and its standalone `cagr_pct` / `max_drawdown_pct` on the same window |
| `excess_at_equal_risk_pct` | the CAGR this blend would have produced scaled to the benchmark's max drawdown, minus the benchmark's CAGR. The leverage-proof scoreboard |
| `diversification_delta_pct` | blended max drawdown minus the weighted average of component drawdowns. **Near zero is the expected answer** for a leveraged sleeve on an equity core; printing it proves that rather than asserting it |
| `cash_drag_pct` | CAGR cost of the unallocated remainder, so the cash floor's price is visible |

### T1.3 The core as a component

`core_component(session, user)` builds a buy-and-hold target vector from the
core's actual tickers at their current weights:

* **Explicit core** (`sleeve_service.get_core`, C6) — the family's weights.
* **Implicit remainder** (the common case) — held positions minus every ticker
  claimed by a sleeve, at current NAV weights.

A core ticker with insufficient history **abstains the whole blend** with
`INSUFFICIENT_HISTORY` and names it. Dropping the one holding with no history and
reporting on the rest is how a book measures better than it is.

**Files** `app/engines/blend.py` (new), `app/services/backtest_service.py` (fetch reuse), `app/services/sleeve_service.py` (read-only helper)
**Tests** one component at weight 1.0 reproduces that component's metrics exactly; two identical components at 0.5 each reproduce the same metrics; summed-ticker case matches a hand-built vector; misaligned history raises rather than pads; unallocated remainder shows as cash drag, not free return; `excess_at_equal_risk_pct` is invariant when every weight is scaled by a constant against a scaled benchmark; a core ticker with 40 sessions abstains and names itself.
**Risk** low — pure functions, no money path, high test leverage.

---

## T2 — The constrained solve, and its right to say no

New `app/services/target_solver.py`. Input is a **pair**: target excess, and a
drawdown ceiling `D`.

### T2.1 The sweep

Sweep sleeve weights and measure the blend at each step.

* Step **1 percentage point**, not the UI's 5. The answer is rounded to the
  slider's granularity *for display*; solving on the display grid would make the
  answer an artefact of the widget.
* **Admissible** means `sleeve_service.validate` passes (sleeves ≤ 100, cash floor,
  per-ticker concentration cap) **and** blended max drawdown ≤ `D`. The drawdown
  test is inside the admissibility check, not applied after — a blend that
  breaches `D` never enters the candidate set and can never be returned.
* Multiple sleeves: T2 sweeps them **on one axis** — total sleeve share, split at
  their current ratio. Independent search is T6. The card says so; a one-axis
  answer presented as an optimum is a lie of omission.

### T2.2 The five outcomes

Exactly one is returned, and the card renders each differently:

| outcome | meaning |
|---|---|
| `REACHED` | an admissible weight meets the target excess within the drawdown ceiling |
| `REACHED_ABOVE_CAP` | a weight exists but only past a cap — names the cap that binds and the weight it would take |
| `DRAWDOWN_BOUND` | the target is reachable on return alone, but only by breaching `D`. Reports the excess achievable *at* `D`, and the `D` that would be required. **The most informative outcome in the whole design** — it separates "your strategies are too weak" from "your risk tolerance is the binding constraint" |
| `UNREACHABLE` | no admissible blend reaches it at any `D`. Reports the best achievable excess, the weight producing it, and **which component's measured excess is the binding constraint** |
| `NOT_MEASURABLE` | a component abstained. Reports reason and ticker, per investing-discipline §1 |

`DRAWDOWN_BOUND` and `UNREACHABLE` are what this phase is for. Given the current
card, one of them is the likely answer, and delivering it plainly is success.

### T2.3 Built for execution, wired to nothing

`would_execute` is emitted in **the shape both downstream consumers already
need** — Phase A's tracked-book writers, and the `orders` rows that Phase 2 of
`BROKER_INTEGRATION_PLAN.md` stages. Getting the shape right now makes both a
wiring job rather than a redesign, which is the entire reason it exists in a
read-only phase.

```python
"execution_plan": None,   # Phase T is read-only, without exception
"would_execute": {
    # Plan-level intent — what Phase A's add_or_resize consumes
    "resizes": [{"strategy_id": ..., "from_pct": ..., "to_pct": ...}],

    # Leg-level intent — one entry per trade, in the `orders` table's own
    # column shape (BROKER_INTEGRATION_PLAN.md §3), so a staged order is a
    # direct construction from this and never a re-derivation:
    "legs": [{
        "side": "BUY" | "SELL",
        "ticker": ...,
        "market": ...,
        "quantity": ...,              # shares, not ILS — orders are in shares
        "order_type": "MARKET",       # LIMIT deferred to broker Phase 2
        "limit_price": None,
        "estimated_price_ils": ...,   # for preview only, never sent as an order
        "estimated_cgt_ils": ...,
        "price_as_of": ...,           # §1b — a leg priced from a stale quote
                                      # is surfaced, never silently executed
    }],
    "funding_required_ils": ...,
    "estimated_cgt_ils": ...,
}
```

Three rules that make the shape honest rather than merely convenient:

* **Quantities are share counts**, because that is what an order is. A leg
  expressed in ILS has to be re-derived at execution time against a price that
  has since moved, and the re-derivation is where the discrepancy hides.
* **`estimated_price_ils` is never an order input.** It exists so the preview can
  show a number; the broker path prices at the venue.
* **`price_as_of` travels with every leg**, per investing-discipline §1b. A leg
  built from a stale quote must be visible as such before anything is staged —
  the COW case is exactly this failure at book scale.

`would_execute` is **descriptive** in Phase T: the diff you would apply by hand
through the existing "Resize sleeve" and "Fund all sleeves" controls. No route in
Phase T accepts it as input.

**Why read-only now:** the C5 slider bug (STATUS.md) — a control seeded from the
*suggestion* rather than the sleeve, one press from taking a sleeve 8% → 40%. A
return target one tap from a book change is that bug with higher stakes.

**Files** `app/services/target_solver.py` (new), `app/api/routes/plan.py` (`GET /plan/target?excess_pct=&max_drawdown_pct=`)
**Tests** an achievable target returns a weight that re-measures as meeting it; a target breaching `D` returns `DRAWDOWN_BOUND` and never a weight; lowering `D` never increases the returned weight (monotonicity); an unachievable target returns `UNREACHABLE` with a named component; the endpoint mutates nothing (assert `plan_sleeves`, positions and cash byte-identical after a solve); `execution_plan` is `None` on every path; a 1-point solve rounds to the 5-point display grid without crossing back under the target or over `D`.
**Risk** low-medium. Read-only, but it is the number a person would act on.

---

## T3 — What the target costs

Alongside every `REACHED`, the same payload returns:

* **Blended max drawdown at that weight**, measured, plus the worst peak-to-trough
  in ILS at current NAV, and the recovery figure stated explicitly: down 45% needs
  +82% to get level.
* **Excess at equal risk**, as the headline verdict. If the blend only wins by
  carrying more risk than the benchmark, this number says so in one figure.
* **The distribution, not the point.** `SimulationEngine` returns p5/p50/p95
  nominal and real plus `probability_of_loss_real`. Seed it from the **measured**
  blended CAGR and volatility, and print **median beside mean**. For a leveraged
  blend the median sits well below the mean; a target expressed as an average is
  not the outcome you are most likely to get, and that gap is the single most
  useful thing this phase can show.
* **Real terms**, per investing-discipline §2, with the assumption stated.
  `SimulationEngine` already deflates by CPI — use `real`, label it.
* **Tax drag.** `strategy_backtest` emits `gross_cagr_pct` and `tax_drag_pct`; the
  cost of *getting* to the weight (25% CGT on trims from the core) comes from
  `funding_service`. Both shown: the cost to arrive and the cost to stay.

**Files** `app/services/target_solver.py`, reuse `simulation_engine.py`, `funding_service.py`
**Tests** median < mean on a leveraged blend; real < nominal at positive CPI; drawdown in ILS matches NAV × pct; the CGT figure matches `funding_service`'s preview for the same resize; `excess_at_equal_risk_pct` is negative for a blend that beats on return but loses per unit of risk.
**Risk** low. Read-only; every engine already exists.

---

## T4 — The card

### T4.0 — first, make a frontend write verifiable again

Not cosmetics, and it belongs to T4 specifically: T4 is the phase that writes to a
144KB `index.html` on a Windows mount, and `safe-windows-edits` Rule 3 says the
way to prove such a write did not truncate is `git diff --stat` matching the
expected magnitude. Right now that check is unreadable.

**Measured:** `git ls-files frontend/node_modules` returns **2,463 tracked
files**. `.gitignore` has no `node_modules` rule. `.gitattributes` declares
`*.ps1 text eol=crlf`, so every `node_modules/.bin/*.ps1` is stored LF and must
be rewritten CRLF on checkout — re-flagged on every operation that consults the
filter.

Consequences, all observed in one session:

* `git status` and `git diff` time out at 40s walking the extra tree.
* `git status --short` returned an **empty** view while five files were staged.
  A clean tree was nearly reported on that basis.
* It is the unfixed root of a ban this repo already documents:
  `.gitattributes`' own comment says EOL noise is "part of why `git add -A` is
  banned in this repo". The ban treats the symptom.

**Change**

```
git rm -r --cached frontend/node_modules      # index only; nothing leaves disk
```

plus `node_modules/` in `.gitignore`.

**Eran runs this, not the session.** A 2,463-file index change is gated
regardless of confidence — the evidence buys the right to recommend it, not to
perform it. Verify after with `git status -sb` returning promptly and
`git ls-files frontend/node_modules` returning zero.

**Open question, separate from the fix:** `frontend/` contains only
`node_modules` and `package-lock.json` — no source. The UI that ships is
`app/static_app/index.html`. Whether `frontend/` is a vestigial scaffold from the
v4 codebase effort is *inferred, not established*; untracking `node_modules` is
correct either way, and deleting the directory is a separate decision that needs
its own evidence.

**Why this lands in T4 and not earlier:** it was discovered during T3, but every
earlier phase touched Python only, where the noise costs time and nothing else.
T4 is the first phase whose safety check depends on a legible diff.

### T4.1 — the card itself

One card on the Plan screen, under the sleeve panel, in `app/static_app/index.html`.

* **Two inputs**: "beat *(benchmark)* by ___ %/yr" and "without exceeding ___ %
  drawdown". The second is not optional, and not hidden in an advanced section —
  it is half the question.
* **Target defaults to the book's current measured excess**, so the first thing
  shown is where you are and the edit is deliberate.
* **Output order**: verdict → required sleeve size (or the reason there isn't one)
  → drawdown that comes with it → excess at equal risk → median vs mean → the
  window and source of every figure.
* **Never a slider.** A slider implies every position on it is attainable. This is
  a number you type and a verdict you read.
* **Label is "Measured"**, matching the existing `measuredProfile` chips — never
  "Expected" or "Projected" for anything derived from backtests. The Monte Carlo
  band is the one projection, and labelled as such.
* **`DRAWDOWN_BOUND` and `UNREACHABLE` render as first-class states**, not as error
  styling. They are correct answers and must not look like the app broke.
* **P2's fixes apply**: `white-space:nowrap` on the verdict chip; the tab row is
  already at its wrap limit — check before adding.

**Files** `app/static_app/index.html`, `sw.js` cache bump
**Tests — state-based, per the Card Claims standard.** The card's figures must **equal a re-measurement**, not merely be present: render the card, take its claimed excess and drawdown at the stated weight, re-run `measure_blend` at that weight, assert equality. A card claiming a change it does not produce is the exact failure the 2026-08-15 batch existed to kill. Plus: all five outcomes render; no figure renders without its window; sleeve-vs-base and book-vs-benchmark are visually distinct; the `UNREACHABLE` state offers no button that changes the book.
**Risk** none — presentation. STATUS.md records the stale-shell cause (`cache.add` fetches through the browser HTTP cache), so bump `iw-vN` and keep the shell network-first.

---

## T5 — The war room, done the right way round

The war room today runs a **per-observation** pipeline —
`AGENTS = ["Research", "Alpha", "Risk", "Tax", "Decision", "Adversary", "UX"]` —
stepping one ticker signal through vet → optimize → rank → display. The solver
reasons about the **whole book**. That difference is why the backlog note says
the war room "has no concept of a strategy": it is not a missing feature, it is a
different unit of analysis.

**So the solver does not become an eighth agent.** Two reasons, both structural:

1. **It would run per ticker**, once for every observation in the loop, computing
   an answer that does not depend on the ticker. Waste, and the war room is
   already the slowest path (5.4s cold, 6.7s warm, per-signal LLM call).
2. **It is deterministic arithmetic.** An agent that debates is an agent whose
   output varies. A reproducibility-pinned number that comes out differently
   depending on how the conversation went is not a measurement any more.

**What it becomes instead: a constraint the existing agents read.** This is
strictly more useful than a seat at the table, and it costs nothing per signal
because the solve is computed once per book and passed in.

| agent | what the solver gives it |
|---|---|
| **Risk** | already vetoes on `probability_of_ruin` / `max_drawdown` at the *signal* level. Now also vetoes a signal that would push the **blended book** past the user's ceiling `D`. This is the first book-level veto in the pipeline |
| **Decision** | `scores.ret` currently scores return in absolute terms. Under the new objective it scores **excess over the benchmark**, so the ranker and the goal finally agree |
| **Adversary** | gains a genuinely new line of cross-examination: *"this buy raises the book's measured drawdown past your ceiling"*, or *"this trade's measured excess is below the sleeve you would trim to fund it"* — the first critique it can make from a book-level fact rather than a per-signal one |

**Not the decision maker.** The Decision engine ranks; the solver bounds. A
constraint that also chooses is a constraint nothing can overrule, and the whole
value of `D` is that a human set it.

This closes the "war-room strategy debate" backlog item without the LLM cost that
put it in the backlog — no new narration, no new per-signal call.

**Files** `app/services/war_room.py`, `app/engines/decision_engine.py`, `app/agents/adversary.py`, `app/services/recommendations.py`
**Tests** the solve is computed once per war-room build, not per observation; a signal that breaches `D` is vetoed with the ceiling named in the critique; `scores.ret` on a benchmark-matching trade is ~0 rather than positive; the war room still builds when no solve is available (degraded, reported — never silently unconstrained).
**Risk** medium. It touches the ranker, which every recommendation flows through. Ship the veto behind a flag and measure the change in what gets displayed before enabling.

---

## T6 — The full search

Now widen to what was agreed: **all of them.**

* **Add strategies not currently held** — every id in `strategy_catalog.CATALOG`
  with a fresh `StrategyBacktest` row.
* **Vary the core** — each static family in `services/strategies.CATALOG` as the
  core component, plus current holdings as the baseline core.
* **Discover the sleeve count, don't cap it.** Grow the number of concurrent
  sleeves while each addition improves the constrained objective; stop when it
  stops, and **report where it stopped and by how much the last one helped**. The
  count is then a measurement rather than a number someone picked. Keep a hard
  runtime ceiling as a backstop, and log it if the backstop is what stopped the
  search — a limit that binds silently reads as a finding.
* **Report the top N by constrained excess, each with its drawdown.** Ranking on
  return alone surfaces exactly the blends that are unholdable.

### The line this phase must not cross

investing-discipline §5: the app is a self-directed execution companion and never
recommends products. A ranked list of **the user's own configured rules**, each
with its measured numbers and drawdown, is a *measurement*, and the card is
worded as one — "these blends measured highest over this window", not "you should
hold". No security outside `strategy_catalog` / `strategies.CATALOG` may enter the
search: the moment the solver picks a ticker, it is recommending a product.

### The overfitting problem this phase creates

Searching thousands of blends over one history and reporting the winner is
**selecting on one sample** — exactly what the "measure both, ship OFF" decision
for the regime gate exists to prevent. All of these are required:

* Rank on the **out-of-sample** split `backtest_service` already computes
  (`OOS_SPLIT = "2022-01-01"`), not the full-sample figure — while carrying that
  constant's own comment onto the card: *"the only real bear market these
  instruments have seen."* An out-of-sample window containing one bear market is
  a sample of one, and a ranking built on it should say so.
* Show the fit/test gap on every ranked row. A blend that wins in-sample and
  collapses out-of-sample is the most useful row on the screen.
* Print how many blends were searched, next to the winner. A winner drawn from
  4,000 candidates is a different claim from a winner drawn from 6.
* No auto-apply, ever, from a ranked list.

**Files** `app/services/target_solver.py`, `app/services/backtest_service.py` (bulk fetch reuse), `app/static_app/index.html`
**Tests** the search never proposes a blend `validate` rejects or that breaches `D`; ranking is on OOS; searched-count is reported; the discovered sleeve count is reported with the marginal improvement of the last addition; a strategy without a fresh backtest row is excluded and named, not silently skipped; runtime bounded, and a run stopped by the backstop says so.
**Risk** medium — largest surface, and the one most able to produce a confident wrong answer.

---

## Reproducibility pins (§3)

Every solver payload carries, and every card displays on demand:

```
engine_version · benchmark_ticker · drawdown_ceiling · period_start..period_end
· observations · data_source · component ids and their backtest computed_at
```

A component whose `StrategyBacktest` row is stale by `_is_stale` invalidates the
solve — the result renders as stale rather than current, as the strategy cards
already do. A new `engine_version` invalidates cached solves; no silent reuse.

---

## Smoke test convention

`scripts/smoke/smoke-t<N>.ps1`, following the existing rule: run its own checks,
then every earlier phase's smoke, then one PASS/FAIL/SKIP total. A SKIP is never
a PASS.

`smoke-t2.ps1` must include the read-only assertion — snapshot `plan_sleeves`,
positions and cash before and after a solve, and fail on any difference. That is
the check protecting the decision the whole phase rests on.

---

## What this phase does NOT do

Stated so the next session does not quietly add it:

* **Places no order and changes no holding.** Nothing in T0–T6 writes to the book.
* **Adds no return, and no strategy.** The search space is the catalog you already
  defined. If measured excess improves during this phase, that is a bug in the
  measurement, not a win.
* **Promises nothing.** No forecast is presented as a result. The likely honest
  answer for the book as it stands is `UNREACHABLE` or `DRAWDOWN_BOUND`.
* **Does not change how sleeves are funded.** `funding_service` is read for its
  CGT preview only.

---

## Phase N — what your money actually did

Separate from T4, and it cannot borrow from it: T4's Today chart is a
reconstruction, and this is the real thing.

### The finding that decides the design

`grep` for `Transaction(` across `app/`, excluding the models: **zero hits.**
The table and its relationship exist; nothing has ever written a row.
`whs_snapshots` stores health scores, not value, and nothing writes it on a
schedule either. `contributions` holds one dated entry.

**So past NAV cannot be recovered — only started.** Every day without a snapshot
is a day of real history that never exists. That is the whole argument for
building this early rather than well-placed in a queue.

### The correctness issue that dominates it

**Deposits must not read as performance.** Put ₪5,000 into a ₪20,000 book and a
naive endpoint-to-endpoint percentage shows +25% — the chart says you had a
great day when you had a bank transfer. The `contributions` ledger is dated
precisely so this can be neutralised: chain sub-period returns across each
cash-flow boundary (a time-weighted return) rather than comparing the ends.

Without that this feature is **worse than the backfill it replaces**, because it
carries the authority of real data while being wrong in the direction that
flatters. It is the reason this is a phase and not a chart tweak.

### Shape

* **`nav_snapshots`** — subject, date, `nav_ils`, `cash_ils`, `invested_ils`,
  `source`. One row per user per day. A migration, guarded against `create_all`
  the way 0013–0015 are.
* **A daily job** on the scheduler that already runs (`backtest_refresh` 03:30,
  `strategy_signals` 06:15). NAV from `strategy_service._snapshot` — the same
  single source T3 was corrected to use, so the chart and the solver can never
  disagree about what the book is worth.
* **Time-weighted return** across contribution boundaries, computed from the
  snapshots plus the ledger. Money-weighted (which answers a different, also
  useful question) is a later addition, not a substitute.
* **An honest empty state.** "Recording since <date> — N days" until there is
  enough to draw. Never interpolate, never seed it with the backfill: a seeded
  curve is the reconstruction wearing the real thing's label, which is exactly
  the confusion this phase exists to end.
* **The swap.** Today's chart moves to snapshots once N days exist, and says
  which one it is showing. The backfill does not disappear — it answers "what
  would this book have done", which is a real question, just not this one.

### Its own smoke, per the standing rule

The snapshot job must be provably running (a job with a next-fire time, and rows
appearing), the series must be gap-aware rather than silently interpolating, and
a synthetic deposit must move NAV **without** moving the return line. That last
one is the check that proves the time-weighting works, and it is the only one
that would have caught the failure mode.

---

## Phase A — acting on the answer, against the tracked book

A separate phase, deliberately not a `T`. Phase T's smoke asserts that a solve
changes nothing; a writing step inside T would make that assertion untrue, and
that assertion is what protects against the C5 shape.

**What it does:** turns a `REACHED` verdict into one tap that calls the existing
`sleeve_service.add_or_resize` at the solved weights, then hands off to
`strategy_service.fund_plan` (`strategy_service.py:594`, which calls
`plan_funding` at 641) — the same path "Fund all sleeves" already runs. Nothing
new touches money; the solver stops being a thing you read and starts being a
thing you press.

### Four constraints inherited from the Card Claims batch

Not optional, and not restatements of the general gates below — each maps to a
defect that batch fixed:

1. **Thread `exclude=sleeve_tickers`.** Funding one sleeve's increase must never
   trim another sleeve. When sleeve tickers are unavailable, follow
   `recommendations.py:475` — warn and report degraded; never fund unprotected.
2. **Net, not gross.** `plan_funding` raises net proceeds. Every figure in
   `would_execute` and in the dry-run must be net-consistent, or the preview
   announces a shortfall that does not exist.
3. **Decide the `FundingLedger` scope before shipping.** A Today build holds a
   ledger so no two cards spend the same shares; a Phase A apply arrives on a
   different request and no such guard spans them. Either join a shared scope, or
   re-read and re-validate every leg at apply time and refuse on drift.
4. **The class-target drift guard stays bypassed**, deliberately — sleeve funding
   goes through `plan_funding` directly because a sleeve is a ticker-level intent
   and asset classes cannot express it (TQQQ and QQQ are both Equities). Leave a
   comment saying so; it reads like the bug that batch fixed.

**What it does not do:** place an order anywhere. This is the tracked book — the
app's own record. The card must say so in the words the existing funding cards
already use: *no brokerage order is placed; the real trade is yours to mirror.*

### The three gates, all required

1. **Dry-run by default** (§1c). Print every leg — ticker, side, share count,
   estimated price, estimated CGT — plus before/after NAV, cash and each sleeve's
   percentage, *before* anything changes. Live action needs an explicit confirm.
2. **Order operations so failure is visible and reversible** (§1c). Credit cash
   before deleting a position, never the reverse.
3. **State the invariants preserved.** NAV unchanged (value moves between
   sleeves); `invested_ils` untouched, because a rebalance is neither a deposit
   nor a withdrawal.

### The one thing that must not be built

**No auto-apply, and no apply from a ranked list.** T6 can return a hundred
blends sorted by measured excess; Phase A applies only a solve the user
explicitly requested and explicitly confirmed. A one-tap path from a ranked
search result to a book change is how a measurement becomes a recommendation
engine by accident.

### On whether this adds return

It does not, and it can subtract. The only return-adjacent argument is
**implementation shortfall** — a signal acted on three days late fills at a price
the measurement never assumed, so closing that gap makes realised results track
the measured strategy more closely. That is tracking-error reduction, not edge,
and it cuts both ways.

Against it, and measurable with the numbers already in the engine: `_simulate`
charges `DEFAULT_COST_BPS = 5.0` per traded leg plus 25% CGT, and the catalog
docstring records a variant that *"produced 342 trades a year in testing — at 25%
CGT and a dealing spread that is a strategy whose costs eat its own edge."*
Friction is currently what limits turnover. Phase A removes friction. Ship it for
the convenience, measure the turnover after, and be willing to conclude it was
not worth it.

**Files** `app/services/target_solver.py` (consume `would_execute`), `app/services/sleeve_service.py`, `app/services/strategy_service.py` (`fund_plan`), `app/services/funding_service.py`, `app/api/routes/plan.py`, `app/static_app/index.html`
**Tests — state-based, per the Card Claims standard.** Apply the spec and re-measure; do not assert on preview wording. Post-apply NAV, cash, per-sleeve percentage and per-ticker quantity must equal what the dry-run said. Plus: dry-run mutates nothing and prints every leg; live path requires an explicit flag; cash credited before any position is removed; `invested_ils` unchanged; a leg whose `price_as_of` is stale blocks the apply and names the ticker; **no other sleeve's ticker appears in any trim leg**; the funding figure is net; no route reachable from a T6 ranked row can apply.
**Risk** medium-high — the first writing path this design opens. Its own smoke, and the read-only assertions in `smoke-t2.ps1` must still pass unchanged afterwards.

---

## Real orders — not here, and already planned

Order placement is **Phase 2 of `BROKER_INTEGRATION_PLAN.md`**, which already
specifies the correct shape: Accept creates a `STAGED` order rather than mutating
holdings, the user reviews an Orders screen and taps Execute, and status moves
`STAGED → SUBMITTED → FILLED/REJECTED` with holdings updated from the actual fill
rather than the estimate — behind `TRADING_ENABLED`, per-order confirmation and a
daily notional limit.

Two facts worth recording so no session assumes otherwise:

* **Nothing in the repo can place an order today.** `place_order` exists as an
  abstract method only (`app/brokers/base.py:52`); no adapter implements it, and
  Plaid and Yodlee are read-only aggregators, not order routes.
* **It is not blocked on code.** It is blocked on broker API credentials and a
  signed brokerage API agreement — and, first, on confirming that the target
  brokers expose an order API to individual accounts at all. Verify that before
  scoping Phase 2; it determines whether the phase exists.

The firewall holds throughout (§5): even with a working adapter, a human presses
Execute on a staged order. The app never places one on its own.

---

## Phase W — where the return actually comes from

Not part of Phase T, but named here because Phase T is only worth building if
this follows it.

* **Walk-forward parameter sweeps.** `sweep_param` / `sweep_values` already exist
  on every overlay and have never been run walk-forward. The only untested source
  of *additional* measured edge in the family, and a bigger lever on excess than
  any sleeve size.
* **Re-decide `vol_target`'s regime gate on the P3 numbers** — 8.32 points of
  drawdown for 1.71%/yr. Not a return lever, but under a hard drawdown ceiling it
  is what lets a larger sleeve be admissible at all, which is the constraint the
  solver will keep hitting.
* **Contribution modelling.** At the book's current NAV the contribution rate
  moves terminal value more than any admissible excess does. The solver answers
  "what return do I need"; it cannot answer "should I be solving for return at
  all", and the projection card is where that belongs.

---

## Credit where due

Two things the existing code already gets right, recorded so no one "fixes" them:

* **The cost model.** `DEFAULT_COST_BPS = 5.0` per traded leg, CGT modelled inside
  `_simulate`, and expense ratio, decay and financing cost come free from
  simulating the leveraged fund's own price series. Most backtests omit these and
  overstate edge by several points a year.
* **`base` is the core, not cash.** The catalog docstring records the measurement:
  the identical dip-buy rule scored 4.15%/yr against T-bills and 15.64%/yr against
  a QQQ core. Parking idle capital in T-bills and reporting the result would have
  measured a savings account with a strategy attached.

---

## Open questions — resolved 2026-08-14

1. **Default target on the card** → the book's **current measured excess**.
2. **Concurrent sleeve count in T6** → **discovered by measurement**, not capped.
3. **Which window leads the headline** → *see below; still open.*

### The one decision still outstanding

Two measurements can lead the card, and they answer different questions:

| | ten-year `strategy_backtest` | 250-day `holdings_backfill` |
|---|---|---|
| measures | the **rule**, over ten years of real closes | the **book you actually hold**, over one year |
| answers | "does this strategy work?" | "how am I doing?" |
| sample | ~2,500 sessions, one bear market | ~250 sessions, no bear market |
| weakness | you have never held this for ten years | one year is mostly noise |

**The decision:** which one the verdict is computed *from*. Not which is
displayed — T0 makes both legible and both appear. The solver needs one window to
solve against, and the two will give different answers.

**Recommendation: the ten-year backtest.** A target solved against 250 sessions
is solved against noise — a single good quarter would move the required sleeve
size by more than any real change in edge. The 250-day figure belongs on the card
as "recently", next to the ten-year verdict, precisely so the gap between them is
visible when the rule and the reality disagree.

---

Not financial advice.
