# Beat the Market — remaining work (v2, for approval)

_Revised 2026-08-04 after review. Every code claim was checked; file and line
references are what to open first. Nothing here is built yet._

**Decisions already taken** (do not re-litigate in the next session):

| # | Decision |
|---|---|
| Sleeve enforcement | **max-weight cap** (option a). Per-ticker allocation targets (b) → backlog, only if (a) proves insufficient. |
| Rules engine | **protection + entry/exit** rules, suggested and armed on approval. |
| Push triggers | **all four**: signal flips, rule triggers, sleeve drift, new rules available. |
| Regime input | **option (b)** — reconstruct a historical regime proxy so signals stay backtestable. Moved earlier, now phase 3. |
| War-room strategy debate | backlog |
| Multiple concurrent sleeves | backlog |
| Smoke tests | every phase ships one, and each re-runs all earlier phases' checks. |

---

## Ordering, and why

```
P0 safety  →  P1 sleeve is real  →  P2 card  →  P3 regime proxy  →  P4 rules + notifications
```

**P3 comes before P4 deliberately.** The rules engine generates entry/exit rules
*from the strategy's signal definition*. If regime lands afterwards, the signal
changes and every rule generated before it is wrong — they would need
regenerating, and any already armed would be enforcing an obsolete rule. Build
the final signal first, then generate rules from it.

---

## P0 — Safety. First deploy, nothing else in it.

### P0.1 "Load this basket" deletes every holding

`app/services/strategy_service.py:87`

```python
# full replace: delete existing holdings, then insert the basket
for p in await list_positions(session, user):
    await session.delete(p)
```

Deletes V, MSFT, COW and everything else, replacing them with the strategy's
basket. Defensible for a *static model basket* — its original purpose. Wrong for
a **sleeve**, which by definition coexists with the rest of the book.

**Change** — `load_basket(mode=...)`:

* `mode="fund"` — **new default for Beat the Market.** Raise the sleeve amount
  through `funding_service`: spendable cash first (down to the objective's cash
  floor), then worst-fitting holdings ranked by plan fit, each leg showing
  ticker, amount, share count and estimated CGT. Everything not sold survives.
* `mode="replace"` — existing behaviour, retained for the four static families,
  behind a confirm that **lists each position and its value** before deleting.

Button relabelled "Fund this sleeve" on rule-based cards, so two very different
actions are not one click apart with near-identical wording.

**Files** `strategy_service.py`, `funding_service.py` (reuse), `routes/strategy.py`, `static_app/index.html`
**Tests** funding path leaves unrelated holdings untouched; replace path still replaces; a sleeve larger than fundable cash + sellable positions **abstains with a reason** rather than partially executing.
**Risk** medium — money path. Mitigated by reusing the funding engine already in production.

### P0.2 A kept measurement renders as "Couldn't measure"

`static_app/index.html`, `measuredProfile()` — checks `if(!bt.ok)` before reading
metrics. But phase 19 deliberately **keeps** the last good measurement through a
failed refresh, so a card with good numbers and one throttled refresh shows
"Couldn't measure — MISSING_TICKER".

**Change** — if `bt.metrics.cagr_pct` exists, render the chips plus a "refresh
failing since X" note. Show the failure state only when nothing has ever been
measured. **Risk** none.

### P0.3 A dead ticker's frozen price is accepted as current

Reported: COW shows ₪2,109 and **0.0% since you bought**, unchanged for months.

Measured against the live provider:

```
COW    quote OK: 39.87 USD   as_of = 2025-06-11   <- 14 months stale
SPY    quote OK: 773.26 USD  as_of = 2026-08-07   <- current
MSFT   quote OK: 499.99 USD  as_of = 2026-08-07   <- current
```

COW (the iPath livestock ETN) has been delisted. Yahoo still returns its final
traded price and will do so forever. The quote does not fail, so the 30-minute
reprice "succeeds" every single time and the position looks healthy.

`pricing_service.py:79` stores `price_as_of` in the position's meta — and
**nothing anywhere reads it**. There is no staleness check on a quote.

This is worse than a cosmetic bug: the frozen price is counted in NAV, so
`invested_ils`, `gain_pct`, the allocation mix, the concentration caps and every
recommendation sized against NAV are all computed partly from a 14-month-old
number. The app cannot tell "this holding did not move" from "this holding has
no market any more".

**Change**
* `refresh_all_positions` compares `q.as_of` against now. Older than a
  configurable window (suggest 5 trading days) → do **not** treat it as a fresh
  price. Mark the position `price_stale` with the quote date.
* Holdings shows a "price from <date>" badge instead of a confident 0.0%.
* `/portfolio` returns `stale_positions`, so NAV consumers can say which part of
  the number they do not trust.
* A Today card: "COW has not traded since 2025-06-11 — it looks delisted.
  Confirm what happened to it." Guidance, not an automatic write-off: the app
  must not decide a holding is worthless.

**Also audit every holding's freshness**, not just COW — the same silence
applies to anything else delisted, renamed, or merged.

**Files** `pricing_service.py`, `routes/intake.py`, `static_app/index.html`,
`recommendations.py`
**Tests** a stale-dated quote does not overwrite the price and flags the
position; a fresh quote clears the flag; NAV reports which positions are stale.
**Risk** low-medium. Read carefully: it must not flag a holding merely because
a market is closed for a weekend or a holiday.

### P0.4 Rule suggestions never reach Today

`rules_service.suggest_rules_for_holdings` is exposed **only** through
`GET /rules/suggestions` (`routes/rules.py:32`), which feeds a panel on the Rules
page. It is never called from `build_recommendations`, so it cannot appear on
Today.

That is backwards for the app's own model: Today is "what to do now", and an
unarmed protective rule on a real holding is exactly that. Burying it on a
settings-shaped page means it is only found by someone already looking for it.

**Change** — a Today card summarising the outstanding suggestions, actionable
via the existing `create_rules` apply-kind (one card, not one per rule). The
Rules page panel stays for browsing and fine-tuning.

**Tests** suggestions appear as a Today card; arming them from Today clears the
card; the card does not reappear for rules already armed or dismissed.

### P0 smoke — `smoke-p0.ps1`
Load-in-fund-mode preserves a named holding; replace mode still replaces; a card
with kept metrics shows chips not an error; every holding's `price_as_of` is
within the freshness window or flagged; rule suggestions appear on Today;
**plus the full existing `smoke-e2e.ps1` must stay green.**

---

## P1 — Make the sleeve mean something

### P1.1 The sleeve does not affect anything the plan acts on

`strategy_catalog.as_legacy_strategy()` hardcodes
`"target_allocation": {"Equities": 1.0}` for every strategy. `sleeve_pct` changes
only the *basket*, consumed by `load_basket`. **Apply at 20% and at 90% write an
identical plan.** I said the sleeve would drive the target mix; it does not.

Asset-class allocation *cannot* express this — TQQQ and QQQ are both Equities.

**Change (decided: option a)** — applying a rule-based strategy also arms a
`max_weight` rule at the sleeve size on the aggressive ticker, through the
existing rules engine. The sleeve becomes a real, continuously enforced ceiling
that appears in the Rules UI and fires when breached.

Honest about its limits, and the card must say so: this **caps** the sleeve, it
does not make the rebalancer **aim** for it. Growing into a 20% sleeve is P1.2's
funding plan; not exceeding it is this cap.

*Backlog (option b):* per-ticker targets in `AllocationEngine`. Correct and
larger — touches the rebalancing every other family depends on.

### P1.2 "What changes?" doesn't show what changes

The preview returns asset-class rebalance actions. On a book already ~88%
equities against a 100% target those are near-empty, which is why applying reads
as a no-op.

**Change** — for rule-based strategies show the **funding plan**: which positions
get sold, for how much, estimated CGT, and what gets bought. This is the direct
answer to "what do I need to get rid of?" `funding_service` already produces
this shape.

**Tests** preview at 10% vs 90% differs; preview mutates nothing; the arming of
the cap is idempotent across repeated applies.

### P1 smoke — `smoke-p1.ps1`
Apply at two sleeve sizes produces two different plans; the cap rule exists at
the chosen size; preview shows funding legs; **plus P0 + e2e.**

---

### Target behaviour after P0 + P1 (the answer to "what should these buttons do?")

Stated explicitly because today's behaviour is different from all three, and the
next session should be able to check itself against this.

**"What changes?"** — read-only. Shows: the plan fields that change, the sleeve
you have chosen, and the **funding legs** — which positions get sold, for how
much, estimated CGT, and what gets bought. Mutates nothing.

**"Apply strategy"** — writes the plan (objective, risk tolerance, strategy id,
sleeve %), and arms a `max_weight` rule at the sleeve size on the aggressive
ticker so the sleeve is enforced from then on. **Places no trades and changes no
holding.** Returns the funding plan as the suggested next step.

**"Fund this sleeve"** (renamed from "Load this basket") — executes the funding
plan against the **tracked book**: sells the named legs, credits net-of-CGT
proceeds to cash, buys the sleeve. Every other holding survives. No brokerage
order is placed; the card says so and the real trade is yours to mirror.

**Existing holdings**, concretely, for a 20% sleeve on a ₪21,600 book: the app
needs ~₪4,300 in the aggressive ticker. It spends idle cash first down to the 3%
Grow floor (~₪2,000 available), then sells the worst-fitting ~₪2,300 by plan fit
— naming each position and its tax cost before you confirm. Nothing else is
touched.

## P2 — The card

* **Style + Horizon chips.** Horizon is already in the catalog; style/concentration
  computes from the basket exactly as `strategy_profile` does. Keep the label
  **"Backtested"**, never "Est. return" — measured and estimated are different
  claims.
* **Tab row wraps** — `.stgoal` (index.html:170) is `flex-wrap:wrap` and five tabs
  no longer fit. Fix with `flex-wrap:nowrap; overflow-x:auto` or reduced padding
  under 400px.
* **"VERY HIGH RISK" breaks across two lines** — `.rk` (index.html:175) lacks
  `white-space:nowrap`.

**Risk** none, presentation only. **P2 smoke** — chips present on every card; plus P0 + P1 + e2e.

---

## P3 — Regime-aware signals (moved up; highest impact)

### The constraint that decides the design

Live regime today comes from the Yahoo **futures** feed (`markets_service`),
which has **no usable history**. If live reads futures and the backtest reads
something else, they are different rules and the card's numbers stop describing
what actually runs — the same dishonesty this whole build exists to avoid, just
hidden better.

**So (b) means: one regime function, price-derived, used identically in both
paths.** Futures become a display-only cross-check on the Markets page, never a
signal input.

### P3.1 A backtestable regime proxy

New `app/engines/regime.py`, pure, no I/O — same shape as `strategy_backtest`.
Computed from index series we already hold 10 years of (SPY / QQQ / VIX-proxy):

* trend — index vs its 200-day
* volatility state — realized vol against its own trailing percentile
* breadth proxy — how many of a small index set are above their 200-day
* → `risk_on` / `neutral` / `risk_off`, plus the raw components

### P3.2 Wire it into signals and backtests together

`strategy_backtest` gains an optional regime gate; `strategy_signal_service`
calls the **same** function on live prices. Every strategy is re-backtested with
the gate and the card shows both numbers: with and without regime. If the gate
does not improve the measured result, **do not ship it on** — that is what
measuring is for.

### P3.3 Cross-check, not input

The Markets page keeps showing the futures regime, labelled as a cross-check.
If futures and the proxy disagree, that is worth seeing, not worth acting on
automatically.

**Risk** medium-high — changes what every strategy does. Contained by the
proxy being pure and backtested, and by keeping the gate off unless it measures
better.
**P3 smoke** — regime endpoint returns a state with components; every strategy
has both gated and ungated metrics; live and backtest call the same function
(structural test); plus P0–P2 + e2e.

---

## P4 — Rules engine + notifications + Today

### P4.1 Suggested rules: protection **and** entry/exit

Extends phase E's `discipline_rules`.

**Protection** (exists, keep): trailing stop and sleeve cap sized from measured
volatility. Add take-profit and a hard stop where the strategy's drawdown
profile justifies one.

**Entry/exit (new)** — derived from the strategy's own signal definition, so the
rules ARE the strategy rather than a parallel guess:
* trend strategies → "buy when QQQ closes above its 200-day for 3 sessions",
  "exit when it closes below"
* dip-buy → "buy TQQQ when RSI(2) < 15 while QQQ is above its 200-day"
* breakout → "buy on a 20-day high", "exit on a 10-day low"

Each carries the level, the reasoning, and **the backtested statistics for that
exact rule** — its win rate, average hold, expectancy — so an armed rule is not
an opinion.

**Firewall (`investing-discipline` §5, non-negotiable):** the app never places an
order. A firing produces an alert, a Today card, and a one-tap apply against the
**tracked book only**, with the card stating that the real trade is yours to
place at the broker.

### P4.2 Notifications — all four triggers

| Trigger | Rate limit |
|---|---|
| Strategy signal flips | immediate — rare and material |
| A rule you armed triggers | immediate |
| Sleeve drift beyond a band (±5pts) | at most daily |
| New rules available to arm | **weekly**, in the 07:00 digest |

Entry rules can fire on any cooperative day. Suggestions regenerate as prices
move, so "new rules available" goes in the existing `push_digest` rather than
firing live — the one that gets people to disable notifications entirely.

### P4.3 Today

* Strategy signal flip (exists — wire the push)
* Rule triggered (exists via the rules engine — extend to strategy-derived rules)
* **Sleeve drift** — "you chose 20%, you're at 4%", with a funded plan to close
  it. **Decided: Accept executes** — it runs the funded rebalance against the
  tracked book (sell the named legs, credit net-of-CGT proceeds, buy the sleeve)
  exactly as the existing buy cards do. Still no brokerage order: the card says
  so, and mirroring the trade at the broker is yours.
* **Rules available to arm** — one card, not one per rule

**Risk** medium-high. Largest surface: new rule types, notification volume, and
the closest this app comes to automated trading.
**P4 smoke** — a rule of each type can be suggested, armed, fires, and clears;
push fires once per event and not repeatedly; the digest carries suggestions
rather than live pushes; every card states no order is placed; plus P0–P3 + e2e.

---

## Backlog (agreed, not scheduled)

* **Per-ticker allocation targets** (P1.1 option b) — if the max-weight cap proves insufficient.
* **War-room strategy debate** — the war room has no concept of a strategy, so the absence is structural, not a bug. Also the slowest agent (5.4s cold, slower warm from the per-signal LLM call); adding observations makes that worse. Would need `narrate=False`.
* **Multiple concurrent sleeves** — `plans.strategy` is a single `VARCHAR(40)`. Sleeves should compose (20% trend + 15% factor + 65% core). Needs a `plan_sleeves` table and touches apply, preview, funding, signals and discipline rules.

---

## Smoke test convention

Each phase ships `scripts/smoke/smoke-p<N>.ps1` which:

1. runs its own new checks,
2. **then runs every earlier phase's smoke**, so a later phase cannot silently regress an earlier one,
3. ends with one PASS/FAIL/SKIP total.

`smoke-e2e.ps1` gains a `-Phases` switch to run the chain in order. Rules carried
over from this session, each learned the hard way:

* **A SKIP is never a PASS.** A permanent skip is indistinguishable from a broken check.
* **Never assert a guess.** "cash may be at the floor" was a hypothesis baked into a message; the book was at 12.3% against a 3% floor and a real finding hid behind it for days.
* **Read paired state in one breath.** Two failures this session were a stale snapshot compared against fresh data — the app was correct both times.
* **Print `degraded` on failure.** "No cards because nothing fired" and "no cards because the agent raised" look identical without it.
