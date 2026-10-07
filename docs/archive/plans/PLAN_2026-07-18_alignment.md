# Execution plan — 2026-07-18: recommendation alignment, cash visibility, signal grounding

Baseline: `main` @ `99dff7a`, in sync with origin. Tree clean (node_modules EOL noise only).
Source of truth for each diagnosis is cited as `file:line`.

---

## Part 1 — Diagnosis

### 1. Push says "do things", app says "nothing to do"

**Not the same bug you fixed on 2026-07-12.** That fix aligned the *recommendation* push path,
and it still holds: `push_service.evaluate_and_notify` (`push_service.py:196`) builds from
`build_recommendations` and respects server dismissals.

The notification in your screenshot is a different channel — **the weekly digest**
(`push_service.py:286`, `digest_service.py`). Two independent root causes:

- **1a — the digest LLM invents actions.** `context_service.gather` (`context_service.py:41`)
  passes `available_strategies` and `available_commodity_options` into the prompt as *grounding*
  context. The Gemini prompt (`digest_service.py:50`) never forbids treating that catalogue as
  advice, so it produced "consider exploring new strategies like *Aggressive AI & Semiconductors*"
  — a recommendation with **no corresponding Today card**. The numbers are grounded; the
  *action* is not.
- **1b — a real card is probably missing from Today.** The digest reports excess return
  **−17.95% vs SPY**. `_benchmark_recs` (`recommendations.py:760`) fires a HIGH-severity
  "You're trailing SPY" card at any excess below −3%. So Today should not be empty. Three
  candidate explanations, in order of likelihood:
  1. The card was dismissed — server dismissal (7-day TTL) or the local `iw_snoozed_v2` store
     (`index.html:507`). Expected, but the UI gives you **no way to see or undo an ignore**.
  2. `performance()` inside `build_recommendations` raised and was swallowed by the bare
     `except: pass` at `recommendations.py:327` — a silent failure with zero observability.
  3. The empty-state copy is wrong regardless: "Nothing to do right now. You're tracking your
     plan." is asserted even when cards exist but are all snoozed.

**Fix:** constrain the digest to only narrate real cards; make the empty state honest; make
dismissals visible and reversible; instrument the swallowed exceptions.

### 2. Total portfolio value shows no cost basis or % change

`index.html:932` renders `nav_ils` only. Every input already exists — `cost_basis` is stored
per-share per position — but "money in" is never aggregated or displayed.

### 3 & 4. Liquid money is invisible

Cash is *modelled* (`intake_service.credit_cash:144` — a `CASH` position, ILS-native, price 1.0,
`asset_class: "Cash"`), but it is only ever **created as a side effect of accepting a sell**.
There is no way to record cash you already hold, no cash line on Today or Holdings, and the
donut (`index.html:1142`) simply omits classes with no position — hence 100% Equities.

### 5. War-room buys (TEVA, GOLD) never reach Today

Two disconnected pipelines:

- **War room** — `war_room.py:34` runs `LagEngine → StateMachine` over
  `DEFAULT_OBSERVATIONS` (`demo_data.py:7`) and returns a transcript. Nothing is persisted and
  nothing is handed to the recommendation engine.
- **Today** — `build_recommendations` (`recommendations.py:144`) never imports `war_room` or the
  state machine at all.

Critically, **TEVA and GOLD are hardcoded demo data** — `spot_price=100, listing_price=108.2`.
Those are not real signals. Surfacing them as Today advice would violate the "never invent
numbers" rule in `CLAUDE.md`. Per your decision: ground the signals in live prices *first*.

### 6. No sell / liquidate recommendations

Partly correct, partly a gap:

- Sell logic **does** exist: concentration trim (`recommendations.py:162`), tax-loss harvest
  (`:191`), `_holding_verdict_recs` (`:521`), stop-loss rules.
- Each fires only under a narrow trigger. With ₪17.3k across five large-caps at a fairly even
  weight, none trip. So "no sell recs" is **plausibly correct behaviour, not a bug** — but it is
  unverifiable from the UI, and there is **no funding logic**: no card ever says "to buy X, sell
  ₪Y of Z". That is the real gap.

### 7. Strategies look identical apart from tickers

`strategies.py` gives each strategy `objective`, `risk_tolerance`, `preferred_depth` and
`target_allocation` — these really do change agent behaviour (rebalance targets, risk limits,
signal depth). But **all four Grow strategies carry `target_allocation: {"Equities": 1.0}`**
(`strategies.py:18,26,33,40`), and the UI shows only description + basket. So they genuinely
look the same, and the one dimension that differs most (risk) is a bare "HIGH RISK" chip.

### 8. Recommendations barely use market trends

`build_recommendations:334` calls `cached_regime()` — **cache-only, never triggers a fetch**. If
the futures cache is cold the call returns `{}` and no macro rec appears. Even when warm, only
`regime == "risk-off"` produces a card (`:336`); risk-on and neutral produce nothing, and the
regime never modulates the *other* cards' severity.

### 9. "What's moving" never becomes a recommendation

`market_impact.annotate` (`market_impact.py:88`) already computes, per event, the affected
holdings, the exposure, and a concrete `actions` list — that's the "What to do: 1… 2… 3…" you
see in Explore. **None of it is ever converted into a Today card.** The two surfaces are
disconnected in the same way as the war room.

### 10. (Found, not reported) — FX bug in "What's moving"

`market_impact.annotate:91` computes `val = quantity × current_price` with **no FX rate**, while
every other surface is ILS-normalized. That's why the panel says "₪5,680" for 100% of a portfolio
worth ₪17,306 — USD holdings understated ~3.05×. Violates the ILS-normalization rule in
`CLAUDE.md`.

---

## Part 2 — Execution plan

Five phases, each independently shippable and CI-gated. Phase 0 lands same-day.

### Phase 0 — Truth & quick wins (small, low risk)

| # | Change | Files |
|---|---|---|
| 10 | FX-normalize `annotate` — use `fx.fx_rate(price_currency(...))` as `current_mix` does | `market_impact.py` |
| 1b | Replace the three bare `except: pass` blocks in `build_recommendations` with `logger.warning(exc_info=True)` | `recommendations.py:327,347,354` |
| 1b | Honest empty state: distinguish "no cards" from "all cards ignored" — show "N ignored · Show them" | `index.html:667` |
| 1b | "Show ignored" control that clears local snooze + server dismissal | `index.html:749` |
| 2 | Total portfolio value gains **Invested ₪X** and **▲/▼ Y%** (Σ qty×cost_basis×fx) | `portfolio` route + `index.html:932` |

Tests: FX regression on `annotate` (USD position → ILS exposure), invested-total unit test.

### Phase 1 — Cash as a first-class citizen (items 3, 4)

- `POST /api/v1/portfolio/cash` — set/adjust the ILS cash balance, reusing `credit_cash`'s
  position mechanism (no migration).
- Holdings: an **"Add / edit cash"** control; the CASH row pinned to the top with a wallet marker.
- Today: a **"Liquid cash ₪X (Y% of portfolio)"** line beneath total value.
- Donut: always include Cash, even at 0%, so its absence is visible rather than invisible.
- Feed the real cash figure into the liquidity score and `_income_cost_recs`.

Tests: cash set/adjust round-trip; NAV and mix stay ILS-consistent with cash present; donut
payload includes a zero-cash slice.

### Phase 2 — Ground the signals, then unify the pipelines (items 5, 8, 9)

**2a — real observations.** New `app/services/signal_service.py` builds `LagObservation`s from
live prices for (a) your holdings and (b) a watchlist universe (`universe.py` +
`screener_agent`), using real spot vs. reference prices and realized volatility.
`DEFAULT_OBSERVATIONS` is demoted to a test/demo fixture only, behind a `DEMO_SIGNALS` flag.

**2b — one decision pipeline.** `build_recommendations` gains a war-room source: every
`DISPLAYED` decision becomes a Today card carrying its impact/confidence scores, its adversary
critique, and a war-room deep link. The war room and Today then explain the same decisions —
the war room becomes the *audit view* of Today rather than a parallel universe.

**2c — market regime actually drives recs.** `cached_regime()` → a warm-or-fetch call with a
short timeout; risk-on/neutral/risk-off each modulate severity and sizing, not just risk-off.

**2d — "what's moving" becomes actionable.** `market_impact.annotate`'s per-event `actions`
promote to Today cards when exposure exceeds a threshold, deduped against existing cards, tagged
with their source event.

Tests: war-room decision → Today card (id, scores, link); regime modulation across all three
states; event→card promotion with exposure threshold and dedupe.

### Phase 3 — Funding & sell recommendations (items 5, 6)

New `app/services/funding_service.py`:

- **Funding source selection** — cash first; otherwise rank holdings to trim by overweight vs
  target, momentum lag, fee drag, and CGT cost. Returns ticker, ₪ amount, share count, estimated
  tax.
- Every buy card gains a **"How to fund this"** block naming the source explicitly.
- New standalone card: **"Raise cash"** — fires when liquid cash falls under a plan-derived floor,
  naming which holding to trim and by how much.
- A **"Why no sell recommendations?"** explainer on Today, listing which sell triggers were
  evaluated and how far each is from firing. This makes item 6 verifiable instead of a mystery.
- Extend `apply_recommendation` with a paired `sell_to_fund` kind so Accept executes both legs.

Tests: funding prefers cash; funding picks the correct holding under each ranking dimension;
paired accept leaves NAV unchanged net of tax and fees; raise-cash floor.

### Phase 4 — Strategy differentiation (item 7)

- Give each strategy honest, distinct metadata: expected return, expected volatility, typical
  max drawdown, concentration profile, time horizon, and — most importantly — **what changes
  when you apply it** (target mix, risk limits, signal depth, which recommendations get louder).
- Fix the identical `{"Equities": 1.0}` targets so the four Grow strategies differ in substance,
  not just tickers.
- A **compare view** on the Plan page: side-by-side strategy table plus an explicit
  "vs. your current plan" diff before you apply.

Tests: every strategy carries the full metadata set; no two strategies share an identical
target allocation within a goal; diff computes correctly against the active plan.

### Phase 5 — Verification

- Full suite green locally (≈259 + ~25 new), ruff clean, then push and watch CI (lint + test +
  test-postgres).
- Postgres fixtures follow the throwaway-`NullPool`-engine rule in `CLAUDE.md`.
- Large writes to `index.html` verified per `safe-windows-edits` (it's ~1,290 lines and grows).
- Post-deploy QA checklist for the Pixel 9 via `mobile-qa`; SW cache bump `iw-v3`→`iw-v4` since
  the frontend changes materially.
- `STATUS.md` updated per `project-status-log`.

---

## Sequencing & risk

| Phase | Scope | Risk | Ships |
|---|---|---|---|
| 0 | Truth & quick wins | Low | Same day |
| 1 | Cash | Low | Independent |
| 2 | Signal grounding + pipeline unification | **High** — the substantial one | After 0/1 |
| 3 | Funding & sell logic | Medium — depends on Phase 1 cash | After 1 |
| 4 | Strategy differentiation | Low | Independent, can parallel |

Phase 2 is where the real work is: it removes demo data from a user-facing advice path and
merges two engines. It should be its own commit with its own CI run.

## Open items to confirm during the build

- ~~**Watchlist universe (2a)**~~ — **DECIDED 2026-07-18: all candidates are eligible** (holdings +
  `universe.py` + screener-ranked names). The agents rank the full field and recommend which to
  buy, rather than the user pre-filtering the universe.
- ~~**Cash floor (Phase 3)**~~ — **DECIDED 2026-07-18: % of NAV**, varying by plan objective
  (Preserve holds more, Grow less), with an optional user override. Months-of-expenses can come
  later if spending data is ever tracked.
- **Benchmark window:** −17.95% vs SPY is a large gap. Worth confirming the comparison window
  matches your actual holding period before the card leans on it harder.

_Not financial advice._
