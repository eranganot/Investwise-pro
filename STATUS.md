# InvestWise Pro — Status

_Last updated: 2026-09-21 by Claude (PR 68.1 on top of the weekly review; HEAD was `aa7826c` = PR 68)._
_Seeded from git history + prior transcripts._

---

## 📍 Where this repo actually is — 2026-09-21

**HEAD is `aa7826c` = PR 68** (2026-09-20) — cash is not a bank stock; standing orders (migration
`0023`); per-holding chart; redeploy card follows sleeve targets. Three PRs landed since the 09-14
review: PR 67, PR 67.1, PR 68. _(This block read "HEAD is PR 66" until 2026-09-21 — two PRs stale;
the per-PR sections below were current throughout, it was the spine that lagged, the same failure
mode the 08-31 restructure was supposed to end.)_ PR 47 is reverted (see PR 48); production came back on `6f3cc4f` and is
stamped at `0020_solver_verdicts` — the first time the migrations have ever run against it,
by the deploy that took the site down. Confirm with `/health` that it serves this push's SHA. NAV ₪20,950.86 across 5 positions: TQQQ, SOXL,
VTI, BND and cash.

**`measured_pct_of_book` is 97**, up from 78.9. The core card was pressed on 09-02 and the
realign landed in full: `/mix` reports Fixed Income drift `0.0` and `rebalance_required:
false`, the book now holds VTI and BND and no longer holds V, and no `core_to_basket` card
remains on Today. The ₪565 funding question closed with it.

**For what is still OPEN, read `EXECUTION_PLAN_2026-09-03_v6.md`.** It supersedes V5, which
was re-verified row by row on 09-03: six of its rows had changed state within a day and four
were wrong on their own terms.

**For what V5 said, read `EXECUTION_PLAN_2026-09-02_v5.md`.** That file replaces the
"Next" list that used to sit here and supersedes `STATUS-V2.md` as the open-items list;
it was built by re-validating every candidate against production rather than against these
documents, which retired twelve items. **This file is the history**: the PR entries below
are what shipped and why, and they are the best record of the reasoning. Detail from
2026-08-03 → 08-12 lives in `STATUS-ARCHIVE.md`.

### 🟢 Closed since the 08-31 review — do not re-open

| item | evidence |
|---|---|
| Rotate `AGENT_API_KEY` / `AUTH_PASSWORD` — the 4-week-old top item | **Done.** The key in `scripts/.agent-key` returns `200`; the published default password returns `401` on `/auth/token`. One env var holds one value, so the published key cannot also be live. |
| Enable notifications on the Pixel 9 | **Done.** `subscriptions: 1`; 07:00 digests fired 08-30 → 09-02 plus rule triggers. |
| Delete the corrupt 08-22 snapshot | **Done.** Absent from `/portfolio/nav-history`. |
| Push PR 26 / PR 27 | **Done**, and thirteen PRs have shipped since. |
| `scripts/.agent-key.previous` not gitignored | **Fixed** — `.gitignore:31`, `scripts/.agent-key*`. |
| `STATUS-V2.md` invisible to a fresh clone | **Fixed** — it and `STATUS-ARCHIVE.md` are tracked. |
| **Press the core card** — the 09-02 top item | **Done 09-02.** `measured_pct_of_book` 78.9 → **97**; `/mix` Fixed Income drift `0.0`, `rebalance_required: false`; the book holds VTI and BND, V is gone; no `core_to_basket` card remains. The **₪565 funding decision closed with it**. |
| **Decide what the ₪618.65 in cash is for** — the 09-03 top item | **Decided and shipped in PR 64 (09-06).** Eran's answer: cash is a **soft target** (7.5%, or the objective table) with an **absolute hard floor of ₪150** (`CASH_HARD_FLOOR_ILS`). `plan_funding(allow_reserve=True)` is a TypeError without a `reserve_reason`, so a card cannot spend the reserve without printing why. This is the fastest an ops item on this board has ever closed — four days. |

### 🔴 Ops actions (Eran only) — with age
_Dates are when the item was FIRST flagged, carried forward, never reset._

| first flagged | age | item |
|---|---|---|
| 2026-08-03 | **7 weeks** | Pin `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` in Railway. Live: `vapid_pinned_by_env: false`, `vapid_env_matches_db: null`, `subscriptions: 1`. Runbook `scripts/pin-vapid-from-db.sql` — **pin the pair the database already minted**; a newly generated pair kills the live subscription silently. |
| 2026-08-03 | **7 weeks** | **Rotate the other three secrets** — GitHub token, Railway token, Postgres password (`PRODUCTION.md` §1). Never rotated. With the agent key closed, this is the oldest **security** item on the board (broker integration, 07-13, is the oldest item overall). |
| 2026-07-13 | **10 weeks** | **Decide on broker integration** (`BROKER_INTEGRATION_PLAN.md`). Stated "next initiative", unstarted, while **90+ commits** have now shipped past it. At ten weeks this is the oldest item on any of the four boards. Start it, or move it to the backlog and stop listing it as next. |
| 2026-08-10 | **6 weeks** | Pixel QA for the P0 safety batch (`qa/QA-2026-08-10-p0-safety.md`). Never run; **forty-one PRs have shipped on top**, so its pre-flight notes are already stale. Re-scope it or void it. |
| 2026-08-31 | **3 weeks** | **Untracked code: still exactly 2 files, unchanged.** The tarballs, the 14 `scripts/`, `apply-pr26.ps1`, the plan docs and `_to_delete/` are all gone from the tree. What remains is the part that was always the real item: **`app/services/candidate_search.py` and `tests/test_candidate_search.py`** — real code, written, never committed, and not imported by anything on the live path. Land it or delete it; a service that exists only in one working tree is not a service. _(2026-09-21: unchanged for a **third** week — same two files, same 2026-08-25 mtime. The repo shipped three PRs this week, so it is neither too busy nor idle; the file is simply not being decided about. Land it or `rm` it.)_ |
| 2026-09-14 | **resolved same day** | ✅ Stale `.git/index.lock` left here by the weekly review's own sandbox `git status`; Eran cleared it, confirmed empty. Kept as a one-line record because the rule is permanent: **weekly runs must use `git --no-optional-locks status`** — plain `git status` writes a lock the sandbox cannot remove. |

---

## 🔖 Backlog — carried, with the evidence so it need not be re-derived

### B1. PR 55.1's `sigma/sqrt(f)` scaling was WRONG — **WITHDRAWN in PR 56.** Evidence kept.

**Closed as a defect; open as a question.** The scaling is gone from the code and pinned out by
`tests/test_the_book_volatility_is_not_rescaled_by_exposure.py`. What is *not* answered is what
the plan screen should show instead, so the measurement below stays on the board.

**Defect, not a decision.** PR 55.1 conditions each sleeve's volatility on its exposure using
`sigma_in = sigma_measured / sqrt(f)`, which requires the out-of-market days to be **flat**.
They are not. `strategy_backtest._weights_or_off` says so in its own docstring: *"a swing rule
is an OVERLAY -- it decides between the aggressive instrument and the core holding, not
between the aggressive instrument and being out of the market."* The sleeves hold their
**base**, never cash.

Measured directly on 2016-09-06 → 2026-09-04 (2,514 sessions, adjusted closes):

| sleeve | in market | measured vol | **true** in-market vol | out-of-market vol | PR 55.1's formula | error |
|---|---|---|---|---|---|---|
| `btm_swing_dip` (TQQQ/QQQ) | 15.6% | 33.91% | **67.97%** | 22.46% | 85.96% | **+17.99** |
| `btm_trend_soxl` (SOXL/SMH) | 74.0% | 83.50% | **93.95%** | 40.97% | 97.05% | **+3.10** |

The variance decomposition checks out (`f·σ_in² + (1−f)·σ_out²` = 33.84% against 33.91%
measured), so this is not ambiguous.

**Why it has not shown up on screen.** The blend lands at **67.43%** against a realised
**67.48%** — 0.05 apart. It reconciled *for the wrong reason*, and I took the reconciliation as
proof. On a book whose sleeves genuinely sat in cash, or with different exposures, it would be
visibly wrong.

**A second thing PR 55 over-claimed.** The plan's blend is built from **ten-year** backtests and
the realised figure from the **last 251 days**. Blending the *true* in-market vols gives 60.01%
— below the realised 67.48%, which a weighted average cannot be. The gap is the window, not the
exposure. So "the realised volatility exceeds the upper bound" was a real observation with two
causes, and PR 55 named only one.

**The fix is not a better formula, and PR 56 did not write one.** The backtest should *store*
in-market volatility at nightly refresh, where the price history already is, instead of `/plan`
deriving it from metadata that cannot support the derivation. And any plan-vs-realised comparison
has to match windows first. Until both exist, `/plan` reports the strategies' full-window blend
labelled `volatility_window`, `/portfolio/risk` reports what the book did, and the note on the
gap names both candidate causes without asserting either.

### B2. Retire `btm_swing_dip`'s overlay and hold QQQ — DECIDED, then held for later

Eran chose this on 2026-09-04 and then parked execution. The measurement is recorded here so
the decision does not have to be re-made. Same window, same engine:

| variant | CAGR | volatility | max DD | in TQQQ | trades/yr | ₪10,000 → |
|---|---|---|---|---|---|---|
| hold TQQQ, do nothing | **40.25%** | 66.74% | 81.66% | 100% | 0.1 | 294,462 |
| hold QQQ, do nothing | 20.73% | 22.56% | 35.12% | 0% | 0.1 | 65,789 |
| the rule, signalled on QQQ | 18.07% | 33.97% | 40.55% | 15.1% | 44.6 | 52,650 |
| **the rule as shipped** | **17.19%** | 34.35% | 40.55% | 15.6% | 45.4 | **48,855** |

**Leverage decay was not the cause** — TQQQ held blindly doubled its own base. **Being out of
the market during the compounding was.** The overlay turned a 40.25% instrument into 17.19%,
below simply holding QQQ, while carrying a *deeper* drawdown (40.55% vs 35.12%). Gross of costs
it makes 24.11%, so ~7 points a year go to the 45 round-trips — and even gross it trails holding
TQQQ by 16.

**What execution needs, and why it is not one API call.** `DELETE /plan/sleeves/btm_swing_dip`
retires the whole sleeve, which is more than was decided: its 36.05% claim would collapse into
the core, the core's share would go 27% → ~55%, and the book target would swing from 7.8% Fixed
Income to ~22% — the app would start asking to sell equities into BND. "Retire the overlay, hold
the base" means keep the position and drop the rule, and **nothing in the catalogue holds a bare
index**. It needs a `btm_qqq_hold` entry (buy-and-hold QQQ, no `base` key — it *is* one), the
sleeve swapped to it at the same 36.05%, and then the broker trade.

One caveat on the evidence: 2016-2026 contains one sustained bear market. A dip-buying rule that
trails in a bull decade is paying for insurance that mostly was not needed; whether that premium
is worth keeping is a judgement the backtest does not make.

---

## 📦 Product backlog — parked deliberately, 2026-09-05

Eran chose to carry ops items 1-3, the actionable-cards work and the overlay decision (B2), and
to park everything below. **Parked is not forgotten and not "someday"**: each row says what would
un-park it, so the next reader is not re-deriving the reasoning from scratch.

| item | why it is parked | what un-parks it |
|---|---|---|
| **Broker integration** (`BROKER_INTEGRATION_PLAN.md`) | 7 weeks as "next initiative", unstarted while 45+ commits shipped past it. It was assumed to block the actionable-cards work; **investigated 2026-09-05, and it does not** — the app already mutates the tracked book for 15 apply kinds, each ending "no brokerage order was placed", so that work proceeds without it. | A decision to place real orders rather than mirror them in the tracked book. |
| **`candidate_search` land-or-discard** (T6b) | 196 lines, 16 tests passing, ruff clean, **zero callers**, untracked since 2026-08-25. Not broken — unreached. | A caller. Landing it tracked-but-dead repeats the `settings.objective` shape PR 58 removed. |
| **B1 — what `/plan` should show for volatility** | The app now states the gap honestly and asserts neither cause (PR 56). The real fix needs the backtest to STORE in-market volatility at nightly refresh, and any plan-vs-realised comparison to match windows first. | Someone wanting one number instead of two labelled ones. |
| **Pixel QA — P0 safety batch** (`qa/QA-2026-08-10-p0-safety.md`) | 3 weeks old with **nineteen PRs shipped on top**, so its pre-flight notes describe an app that no longer exists. | A re-scope. Running it as written would test the wrong build; voiding it is a one-minute decision. |
| **Set `cash_target_pct`** | PR 54 built the field and the card; nobody has set a number, so ₪618 is still "drift" rather than a decision. | Eran naming a percentage, or deciding zero is right. |
| **Israeli index funds on TASE** | Verified 2026-09-06: the price provider serves TASE **shares** completely — LUMI, ICL, BEZQ, TSEM and 12 more returned price, currency ILA, sector, P/E, growth and history — but every TASE **fund** trades under a numeric symbol (1159250, 1146836) and returns Not Found. `TA35.TA` resolves as the INDEX, which is not investable. Eran, 2026-09-06: *"let's start with the Israeli companies for now, add the Israeli indexes to the backlog"*. | A price provider that serves TASE fund symbols. Until then the class fallbacks (`VTI`, `BND`, `BIL`) stay US-listed. |
| **Repo hygiene** — `_to_delete/`, `iw-src.tgz`, `plans.tgz`, 14 loose `scripts/` | Cosmetic. Nothing depends on it and nothing breaks. | Five spare minutes. |
| **Rotate the Postgres password** | **Deliberately last, and this is a recommendation not an oversight.** It is the only ops item whose failure mode is an outage rather than exposure: never published, wired through Railway variable references, and a wrong move takes the database offline. | A reason — a suspected leak, a departing collaborator — not a schedule. |

---

## PR 68.1 — the cash card in shekels, the ledger that lost every trim, and what "%" means

_2026-09-21. Two questions from Eran after PR 68 went live (`aa7826c`, schema 0023): "I have more
than 1000 in cash but no card", and "what does this % represent -- check and validate". Status:
**pending deploy + smoke.**_

### 1. No cash card — not a defect; the rule was a percentage and he wanted a number

```
OBSERVED   cash 1,661.12 ; NAV 23,057.90 ; target 7.5% = 1,729.34  -> 68 UNDER target
           spendable_cash = 0 -> _redeploy_cash_recs returns [] ; /recommendations: no cash card,
           none hidden. The liquidity "raise cash" card also silent (68 < the 250 minimum trade).
```

Eran: "the card should appear if I have more than 1500, not by % of my portfolio", then chose: above
1,500 the card invests down to the 150 floor. So the TARGET (what is kept) and the TRIGGER (when the
card shows) are two plan columns (migration 0024, mirrored in main.py): `cash_target_ils` (150) and
`cash_redeploy_at_ils` (1,500). A shekel target is converted to a share of today's NAV in exactly two
places -- `funding_service.cash_floor_pct(..., nav=)` / `cash_floor_ils` and `book_target` -- and every
percentage reader (rules, target_apply, target_solver, the liquidity card) now passes NAV, so /mix,
the funding floor and the cards cannot disagree. Setting either unit clears the other.
Reproduced on the live book's values: 1,661 cash -> "Put ₪1,511 of cash back to work" (BND +740 where
Fixed Income is short, GOOGL +772 at plan weight; SOXL at its 42% cap gets nothing); 1,461 -> no card.
UI: Holdings -> Cash -> "When to put cash to work". `tests/test_cash_rules_in_shekels.py`.

### 2. The "%" on each holding — validated; label was wrong; last buy added

It is `(current_price - cost_basis) / cost_basis`: today's price against your AVERAGE buy price per
share, in the ticker's own currency (so FX moves are excluded, and profit already taken on trims is
not in it). Checked against /portfolio for all five holdings -- SOXL (136.40 - 112.69)/112.69 = +21.0%,
QQQ +2.3%, GOOGL +2.1%, BND +0.1%, AGG +0.3%, exactly what the screen shows. SOXL's 112.69 blends the
09-14 re-buy at 102.93 with top-ups at 118-124; the 09-14 full exit reset the basis, so gains or
losses from before it are not in the number. "since you bought" misdescribed it: now
"vs avg price 112.69", plus a second line "+x% vs last buy" from the ledger's most recent priced buy
since the position (re)opened (`last_buy` on /portfolio). Eran asked "last purchase or average?"
-- average stays the headline (it is the return on the shares you hold); last buy is context for
timing the next top-up.

### 3. The trade ledger lost every trim and counted sleeve buys twice — PROVEN

```
SYMPTOM      could not validate cost basis against the ledger (question 2)
EVIDENCE     /position-history trades, range=ALL, 2026-09-21: every source="trim" Sell has quantity
             0.0 (15 rows: SOXL, QQQ, BND). Identical Buy/Sell pairs at the same second, sources
             "sleeve_fund" + "fund_sleeve": SOXL +3.9046 x2, +6.2778 x2; BND -6.7875 x2, -4.4084 x2.
ROOT CAUSE   (a) the trim handler returned {"sold": ["SOXL"], ...} with no share count, and
             _record_apply records `detail.get("shares") or 0`.
             (b) _execute_funded_sleeve records its own legs (sleeve_fund); the fund_sleeve card path
             then recorded the same legs again in _record_apply.
RULED OUT    reconcile/Adjust rows (none involved); nav_history (reads traded DATES only, unaffected);
             net_flows_ils (Adjust rows only, unaffected)
WHY SILENT   record_leg accepts any quantity; nothing reconciled ledger share counts with positions
             until PR 68's chart became the first reader of them
BLAST RADIUS the PR 68 "Your position" line only. Also found: range=ALL downsampled 10y to 400
             points BEFORE clipping, so a September position had <2 points left ("no record yet").
```

Fix: trim detail carries `shares`; `_execute_funded_sleeve` returns `ledger_recorded: True` and
`_record_apply` skips such handlers. The chart builds the value line at full resolution, and REFUSES
to draw it (price only, reason stated) when a 0-share sale is in the window or when undoing every
recorded trade does not land on ~0 (or on the hand-entered count) just before the position opened.
**The bad historical rows are still in `transactions`**; repairing them is a data change and is
Eran's call -- until then SOXL/QQQ/BND show the price line with the reason.
`tests/test_the_ledger_counts_every_share_once.py` (trim case confirmed failing on 84be80b).

---

## PR 68 — cash is not a bank stock; deposits that record themselves; a chart per holding; surplus cash that follows the plan

_2026-09-20. Eran asked for four things: a monthly standing order into cash, a chart per holding,
a fix for "the system sometimes confuses cash money with the CASH stock", and a card that says how to
put cash above the 7.5% soft target back to work. Decisions he made: auto-credit + push; chart shows
position value with a toggle to ticker price, ranges 1D/1W/1M/3M/1Y/All; redeploy fires above 7.5%,
deploys the excess only, with an Accept button. Status: **pending deploy + smoke.**_

### 1. The CASH confusion — PROVEN on production, fixed

```
SYMPTOM      the app sometimes treats cash as the NASDAQ ticker CASH (Pathward Financial)
EVIDENCE     rec_1e8c9d (2026-09-17) "CASH is in a downtrend" -- momentum loop fetched Pathward's history
             POST /portfolio/performance: end_value_ils 147,014.61 on NAV 22,148.23
               147,014.61 - 20,487.11 (every non-cash row) = 126,527.50 ; / 1,661.12 cash units = 76.17
               GET /market/quote?ticker=CASH -> 76.17 USD. Cash was valued as 1,661 Pathward shares.
RULED OUT    pricing refresh (cash priced 1.0 on /portfolio -- pricing_service has its own guard);
             rules (rules_service retires CASH rules; none armed); screener card (skips cash explicitly)
ROOT CAUSE   cash is a position whose ticker is the string "CASH", which is also a real symbol; ~15 call
             sites check for it by hand and three loops did not:
               performance_service._performance_from -> guarded_history("CASH")
               recommendations._momentum_recs        -> guarded_history("CASH")
               recommendations._hedge_recs / income  -> guarded_fundamentals("CASH")
WHY SILENT   Yahoo answers "CASH" with a plausible series; /portfolio (the screen that is looked at) was
             right; only derived numbers were wrong
BLAST RADIUS everything reading performance(): the "You're trailing SPY" card (-8.74% was Pathward's
             gap), max_drawdown 60.02% (range=MAX) which SEEDS THE SOLVER'S RISK CEILING -- the 58.59%
             quoted in PR 13b-era notes was measured the same way and is very likely contaminated too
             (INFERRED: cash was ~618 then, still larger than the rest of the book once priced at ~$70);
             context_service (the agents' performance block); nav_history's day-gap filler
```

Fix: performance() values cash as a flat base-currency amount on every date (it returned 0%; dropping it
would overstate every return and drawdown by the weight of the risky rows); `cash_flat_value` reported.
Momentum, hedge and income loops skip cash. **The check the data can fail:**
`tests/test_cash_is_never_sent_to_the_market.py` spies on a provider that answers ANY symbol and drives
performance (1Y and MAX), the whole Today build, rule suggestions and the new chart endpoint; if "CASH"
reaches the provider from anywhere it fails. Confirmed failing on 84be80b for performance x2 and Today.

**Expect the numbers to move after deploy**: 1Y return, max drawdown, the excess vs SPY and therefore
whether "You're trailing SPY" shows at all. The solver's stored ceiling is NOT re-seeded (PR 11 rule: a
constraint the user owns is not moved silently) -- re-check it on the Plan tab against the new drawdown.

### 2. Standing orders (new: `standing_orders`, migration 0023)

A run is exactly a manual deposit: a `contributions` row (so gain, `contribution_rate` and nav_history's
flow detector see money-in, never a return) plus a cash credit, in ONE commit with `next_run_on`
advancing. Second guard: the contribution note names the order and the date it is for, so a race cannot
credit a month twice. Dated the day it was FOR (local noon, Asia/Jerusalem), caught up after downtime
(max 3 per tick), first run strictly after the day it is created, and resuming a paused order does not
back-fill the pause. Hourly idempotent job, primed at boot; push on credit (category info).
`tzdata==2025.2` added: slim images are not guaranteed an IANA database.
The projection counts it (`source: standing_order`, outranks the measured rate, loses to an explicit
override). The behind-your-goal card now counts it too, asks for the DIFFERENCE, and its Accept sets
the order (`set_standing_order`) instead of being guidance-only.
API: `GET/POST /portfolio/standing-orders`, `PUT/DELETE /portfolio/standing-orders/{id}`,
`POST /portfolio/standing-orders/run`. UI: Holdings -> Cash -> "Monthly standing order"; the Cash row
shows the monthly total and next date.

### 3. Per-holding chart (`GET /portfolio/position-history?ticker=&range=`)

Price line: any range (1D = 5-min bars, 1W = 30-min, via a new `get_intraday` on the market tier;
falls back to daily closes and says so). Value line: shares ACTUALLY held at each moment, rebuilt
backwards from today's count through the trade ledger -- never today's shares priced back (Phase N).
It starts at the later of position-opened and ledger-began, and says which; a ledger that does not
reconcile with today's count hides the line and says why. The change is split into price moves vs
money moved by trades, so a top-up never reads as a gain. Buy/sell dots on the line. Cash refused (422)
before any provider call.

### 4. Redeploy card follows sleeve targets — PROVEN by reproduction

Reproduced with the live book's own values (`/portfolio`, `/plan/sleeves`, `/mix`) plus a 1,067/month
order, accepting each month's card: month 3 put **623 into QQQ (9% sleeve target, held ~11%)** and 270
into SOXL (42% target, held ~38%). Cause: inside a class the budget was split evenly and the smallest
holding got the remainder; the card was never given sleeve targets. Now each held name has a target (a
sleeve's own; core names share what the class target leaves, in proportion to current value), money
goes only to names below target, and whatever remains after every gap is invested at plan weights
(moves every class toward target, since cash is the only thing over). Books without sleeves keep the
old path. Also: the why no longer says "the sale proceeds left your mix off-plan" (false for a
deposit), and "8% floor" is now "your 7.5% cash target; the hard floor is 150".
`tests/test_redeploy_follows_sleeve_targets.py` (7 cases, all failing on 84be80b).

### 5. A test that failed every weekend

`test_the_nav_snapshot_job_actually_runs` asked the real calendar; the job correctly skips
non-trading days, so it was red every Saturday and Sunday (observed 2026-09-20:
`is_trading_day() -> "2026-09-20 is a Sunday"`, `nav_rows: 0`). Calendar pinned in the test.

### Smoke
`scripts/smoke/smoke-pr68-cash-standing-orders-charts.ps1` (read-only).

---

## PR 67.1 / 67.2 - the cards that replaced a silence with a noise, and the test that aged out

Found by RUNNING the PR 67 smoke script against the live build an hour after it shipped, which is
the only reason any of this was seen. Three defects, two of them in code written that morning.

### The trailing-stop card fired on three positions sitting at their peaks

A trailing stop RATCHETS to `peak x (1 - trail)`, so a 10% trail sits ~10% below the price **by
construction** whenever the position is at its peak. PR 67 measured nearness as distance from the
level as a share of price, which is the trail's WIDTH, not danger. Live, all at once:

```
QQQ   9.3% above its stop  ->   7.8% of the trail actually given back
VTI   8.8%                 ->  12.9%
BND   8.7%                 ->  14.2%
```

QQQ was 7.8% of the way from its peak to its stop and the app said it was "closing on your trailing
stop". Now measured as `(peak - price) / (peak - stop)`, firing at 60%. All three go quiet.

Eran's "within 10% of the level" answer was right for a take-profit -- a level approached from
below. It was applied to a mechanism that moves with the price, which was mine to get right.

### The near-cap card shipped `est_amount: 0.0`

The trim required at a cap you have not breached is zero, and `moved_on` un-mutes on the amount
MOVING. So the card could never un-mute -- **the exact defect these cards were written to fix**.
Now the headroom in shekels (SOXL 47, QQQ 195), which moves whenever the position does.

Worse: the unit test asserted `est_amount is not None` and PASSED on 0.0. A check that could not
fail for the thing it was checking. Now asserts `> 0`.

### The ghost was hiding the app's own top-ranked commodity

`_buy_idea_recs` skips a ticker the book already holds -- correct, that card exists to surface names
the book does NOT hold. But `held` was built from every position row with no quantity filter. USO sat
at 0.0 shares and ranks **85.4** on the commodity screen, ABOVE the COMT at 79.2 that the card was
recommending. The ghost made the app skip its own best candidate and name the runner-up, with
nothing on screen to say why.

Third consequence of that one row, after the three armed rules and Commodities reading 0%.
`rules_service._positions_index` and `_profit_recs` already filtered it; this did not.

### CI had been red for days, and not because of PR 67

`test_a_real_quote_afterwards_heals_the_row` failed on every run since **2026-09-09**, with no app
code changing. `_quote()` hardcoded `as_of="2026-09-01T20:00:00+00:00"`;
`refresh_all_positions` refuses a quote older than `STALE_AFTER_TRADING_DAYS` (5) and does not write
it. Measured at the fix: **9 trading days old against a 5-day window**, so `updated` was 0 and the
"heals the row" case had become impossible to pass -- for the staleness guard working perfectly.

The other cases in that file kept passing because they assert a REFUSAL, and a stale quote is
refused too. So the suite read 1815/1816 healthy while one test was unpassable. **A date literal in
a fixture is a test that schedules its own failure.** The fixture now stamps `now()`.

Gate after: **1820 passed, 0 failed** -- the first fully green run on this repo in at least a
fortnight. Railway deploys on red CI, which is why nobody had to notice.

### What the smoke script now pins

`scripts/smoke/smoke-pr67-the-market-was-shut.ps1`, read-only by default. Beyond the PR 67 claims it
asserts the two noise defects cannot return: no trailing-stop card on a position that has given back
less than 60% of its trail, no profit card with a zero or null `est_amount`, and no lower-ranked
commodity carded while a higher-ranked one goes unnamed. Check 1b -- the trading-day guard -- can
only be PROVED on a Saturday, and says so rather than passing on a weekday.

## PR 67 — the market was shut, the ledger was blind, and nothing ever said "take some off the table"

Eran, 2026-09-14, five numbered complaints from one screenshot. Four were the same two causes wearing
different clothes, one was a feature that did not exist, and one was not a defect at all.

### 1 + 2 — the holes in the chart, and the ILS 208.56 that never moved

**Symptom.** *"I'm missing records on 31/8, 07-08/09, 12/09 so the graph is missing (and not enough
price history???)"* and *"there are few holdings add/removed with no deposit/withdrawal recorded"*.

**Evidence.** Every snapshot existed. What was missing was the MEASUREMENT. The reconstruction index
from `POST /portfolio/performance` runs `... 09-02, 09-03, 09-04, 09-08, 09-09, 09-10, 09-11` —
**2026-09-07 has no bar at all, because it was US Labor Day.** Run `window_return_from` against the
unmeasured days and all four fall out of two lines of that function:

| snapshot | prev | resolves to | why `None` |
|---|---|---|---|
| 09-07 (Mon, holiday) | 09-06 (Sun) | 09-04 -> 09-04 | `ka == kb` |
| 09-08 (Tue) | 09-07 (holiday) | 09-04 -> 09-08 | span 4 days vs 1 asked, `> want + 2` |
| 09-12 (Sat) | 09-11 (Fri) | 09-11 -> 09-11 | `ka == kb` |
| 08-29 (Sat) | 08-28 (Fri) | 08-28 -> 08-28 | `ka == kb` |

**Root cause (both).** `nav_snapshot` is a CALENDAR cron — 22:10 every night, weekends and holidays
included — while the price history it is measured against has only TRADING days. And the ±ILS
figures were not deposits at all: every book change in that window came from
`/recommendations/*/accept`, `/plan/target/apply`, `/plan/sleeves/fund`, `/strategies/*/apply` and
`/plan/sleeves/*/swap` — internal reallocations, all of them. `nav_history` read only the
CONTRIBUTIONS ledger, which correctly says nothing about trades, and **never asked the TRADE
ledger**, which exists for exactly this. `/plan/sleeves/fund` compounded it by never writing to that
ledger at all — the hole `ledger_service`'s own docstring warns about, in its own words: *"if a write
path can move a position and does not call `record_leg`, that path is a hole in the ledger."*
So the app estimated, and reported its own reconstruction's error term as money: **+132.11 on 08-31,
-340.67 on 09-01, captioned as "-208.56 ILS you moved in or out"** on a book where nothing moved in
or out.

**RULED OUT.** *Lost snapshot rows* — all present in `/portfolio/nav-history`. *A broken price feed* —
`price_refresh` has run 327 times with 0 failures. *A genuinely short history* — the index is a year
deep and healthy; the caption "there is not enough price history" was true of nothing.

**WHY SILENT.** Both failure modes render as prose, not as an error. "Not enough price history" is a
sentence the reader has no way to check, and an estimated flow is a plausible number in a field that
normally holds a real one.

### 3 — USO at 0 shares, holding three armed rules

`/api/v1/portfolio` returned USO with `quantity: 0.0`, cost basis intact, counted in `count: 6`,
rendered as "0 shares · +9.1% since you bought", holding Commodities at 0% of the book — which is what
the live "Buy COMT" card was reacting to — and `/api/v1/rules` showed it carrying a take-profit at
168.17, a 16% trailing stop and a stop-loss at 124.92, **all `active: true`, on nothing.**

**Root cause.** `recommendations.py`'s `kind == "trim"` branch was the ONLY sell path in the app with
no delete-at-zero test; `sell_position`, `sell_losers`, `_sell_ils`, the core realign and
`_execute_funded_sleeve` all have one. It wrote `max(0.0, qty - shares)` and kept the row. The manual
edit endpoint was a second way in.

**`purge_orphan_rules` was not broken.** It deletes rules whose ticker is not in `held`, and `held`
comes from `load_positions`, which still returned the zero row. It did its job perfectly on a lie —
which is why the fix is to delete the position, not to add a second mechanism beside a correct one.

### 4 — prices: NO DEFECT FOUND

`price_refresh`: **327 runs, 0 failures**, reading Yahoo's `regularMarketPrice`/`regularMarketTime` —
the live intraday quote, not a daily bar. Every holding stamped `2026-09-11T20:00Z` because the
complaint was made at 06:18 UTC on a Monday, with the US market shut since Friday. A probe against
production and against Yahoo directly, every 10 minutes across the session, found the app's stored
price identical to Yahoo's at every sample. **Nothing was changed for this.** What was wrong is that
the screen said `price_freshness: "fresh"` over a Friday close, which reads as broken on a Monday.

### 5 — no card ever said "take some off the table"

Three separate things, none of them a bug, which is why it survived so long:

* the only ACTIONABLE trim in the app was a concentration-cap breach;
* `Trim: <ticker>` was a FUNDAMENTALS verdict (fires at score < 40) that mentioned a gain only as a
  clause in its sentence and shipped `apply: {"kind": "none"}` with `est_amount: None`;
* SOXL's take-profit is set at **+80%** (target 201.39 against 121.82), so nothing was going to fire.

Meanwhile SOXL sat at **37.8% of the book against a 38% cap he set himself** — two tenths of a point
from breaching, with the app silent.

**And one real defect found on the way:** `execution_plan` returned `sell_position` — a FULL EXIT —
for a fired `take_profit`, while the note the app writes on its own take-profit rules reads *"Takes
some off the table if it climbs ~10% more."* Live on QQQ at 791.02 and USO at 168.17. A card may not
claim a change it does not produce, and this one claimed the opposite of what it did.

**Why the slice is fixed and not trend-conditioned.** Eran asked exactly that. This repo already ran
the experiment — backlog B2, 2,514 sessions: the trend overlay on TQQQ returned **17.19% CAGR against
40.25%** for simply holding it and 20.73% for holding QQQ, with a **deeper** drawdown (40.55% vs
35.12%) and ~7 points a year lost to 45 round-trips. The trend-dependent exit already exists and is
correct: `trailing_stop` ratchets off `peak_price`, so it sells late in a strong trend and early in a
weak one without forecasting anything. Two mechanical rules doing one job each beats one rule
guessing.

### What shipped

| # | change |
|---|---|
| 1 | `is_trading_day()` — weekends decided with no feed call at all; a weekday holiday decided from the benchmark's own last close. **Fails open**: a snapshot not taken is permanent data loss (this job lost 7 nights once), a snapshot on a dead day costs one unmeasured step, so every uncertain case records. |
| 1 | `window_reason_from()` — the caption names the market being shut instead of blaming the price history. |
| 2 | `ledger_service.traded_dates()`, consulted by `nav_history`. A step whose shape change is covered by recorded buy/sell legs books from the recorded NAVs as pure return: no estimation, no invented flow. |
| 2 | `_execute_funded_sleeve` records its legs — the `/plan/sleeves/fund` hole, closed. |
| 3 | the `trim` branch deletes at zero; `update_position` treats an edit to 0 shares as an exit; `_positions_index` ignores zero-quantity rows so the rules machinery cannot be lied to again. |
| 5 | `take_profit` fires as a **25% trim** and re-arms one rung higher (`next_rung`) instead of latching off — a winner is sold in slices at successively higher prices. `trim_fraction` (migration `0022`) sizes it per rule; null means the house default. A DECLINED firing still latches, as before. |
| 5 | `_profit_recs` — three cards: one of YOUR levels is within 10%; a holding is up 25%+; a winner is within 2 points of its own cap. Every one carries `est_amount`, because `moved_on` cannot un-mute a card whose number never moves — which is why `Trim: SOXL` stayed hidden for a fortnight after being marked done on 09-08. |
| 6 | the SPY line on the account chart, built from SPY's closes on the SNAPSHOT dates and broken on the SAME unmeasured steps. Measured: **you +4.11%, SPY +0.18%, +3.93 points.** |

**The trap in 6, avoided deliberately.** `performance()` already returns a `benchmark_index`, and
drawing it here would have been wrong in the most convincing possible way: it is aligned to TODAY's
book priced backwards, which over the same window reads **-8.28%** against the account line's
**+4.11%**. Both correct, and the comparison between them meaningless — the two-targets-one-book
shape this repo has paid for before. `tests/test_the_benchmark_line_answers_the_same_question.py`
pins `portfolio_index` out of `series_for` by name.

**Caught on the way in by this repo's own guard:** `test_the_loop_stays_free_while_a_provider_answers`
failed on the first version of the benchmark fetch — a synchronous `guarded_history` on the event
loop. Both it and `is_trading_day` now go through `offload`; note the guard only catches a DIRECT
call, so the second one was invisible to it and not to the loop.

**Known-remaining, pre-existing, not from this PR:** `tests/test_a_quote_of_zero_is_not_a_quote.py::
test_a_real_quote_afterwards_heals_the_row` fails on `main` at `fab6e46` — a refresh that should heal
a zeroed row reports `updated: 0`. Untouched here; it is red before and after.

## PR 66 — a cap that fires on a sleeve it just funded, and a card that can't say how much

Eran, 2026-09-06, on two cards: *"why do I still getting guidance cards? all these cards should be
actionable"*, and then, precisely: *"1. it doesn't tell me what to do (how much to sell) 2. it doesn't
tell me what do do instead (buy something else or keep it in cash) 3. it doesn't do the action for
me — it's only a guidance"*.

He was right that something was wrong, and the thing that was wrong is not the missing button.

### What the numbers said

Measured on the live book the same minute:

```
SOXL  38.002% against a 38% cap  ->  0.44 shekels to trim
QQQ   37.004% against a 37% cap  ->  0.87 shekels to trim
```

Both fired, both latched, and both rendered a card whose own `how` read *"There's no single obvious
trade here, so the app won't guess one."* The Rules tab said it in as many words: **"Triggered ·
37.0% of book vs a 37% cap"**. The cards were guidance because there was **nothing to trade**.

### The cause is structural, and it is ours

1. `_arm_sleeve_caps` arms a `max_weight` cap at **exactly** the sleeve's target. Both rules carry
   `_cap_note`'s wording, so both are sleeve-armed.
2. `_fund_sleeve` funds the sleeve to **exactly** that target.
3. `_evaluate` fired on `weight >= level` — **at equality**.

So every correctly funded sleeve tripped its own cap the moment it was funded. **QQQ's card exists
because PR 61 worked**: Eran tapped "Finish the move", the sleeve funded to 37%, and the cap armed at
37% fired on it.

Then `execution_plan` refused to name a trade — twice over, since `weight_pct` is rounded to one
decimal so `38.0 <= 38.0`, and its own `MIN_TRADE_ILS` guard would have refused ₪0.44 anyway. And
`clears_with_margin` wanted SOXL down at **37.05%** before it would re-arm: a nag with no exit, which
is the exact phrase the previous re-arm fix used about the last one.

### The fix, in three parts

**A cap breach is a breach when there is a trade behind it.** `MIN_TRADE_ILS`, not a new tolerance
band — the app already has a number for "not worth the friction" and it governs every other sizing
decision here. `execution_plan` has applied this test since the MSFT case, one layer too late, where
it could only downgrade the card instead of preventing it. `cap_trim_ils` is now the single answer
the firing test, the re-arm test and `execution_plan` all read.

**The amount is in shekels, net of tax, through the function Accept runs.** The card computed
`shares * price` — the position's **native** currency, no FX, no tax, rendered without a symbol on a
screen where every other number is shekels. On SOXL at $117.28 a six-share trim reads *"~737"* while
Accept credits about ₪2,700 to cash. The same defect as `_rebalance_to`'s "sell 4,324" against a
12,139 execution, one order of magnitude quieter. It now calls `_sale_value_ils`, the same function
the apply handler uses.

**And it says where the money lands.** A trim credits cash, so the card names the net proceeds, the
resulting cash percentage against his 7.5% target, and which side of it that falls on — above, and
the redeploy card will size where it goes; at or under, and it is dry powder doing its job.

Deliberately **not** a replacement buy inside the same card. That is a second sizing decision inside
a card that already has one, which is the shape PR 60 fixed when two cards wanted the same ₪7,476.

### What Eran will see

His two cards disappear, because they were about 44 and 87 agorot. A real breach — SOXL at 45%
against a 38% cap, ₪1,290 of trim — fires, says how much in shekels, says where the proceeds land,
and carries `kind: "trim"`, which is in `_ACTIONABLE_KINDS`. Points 1 and 3 were already right on
that branch and he had never reached it; point 2 was genuinely missing.

### Two existing tests caught the change, and both were fair

`test_the_index_carries_an_ils_value_not_a_native_one` grepped `_positions_index`, whose body moved
into `_positions_index_and_nav` — the cap card needs the NAV the index was measured against, and
loading the book twice to get it is how two numbers on one screen come to disagree. The guard now
follows the body **and** pins the delegation.

`test_a_cap_that_has_corrected_itself_stops_nagging` built its fixture as *"1 unit at 200 in a 2,000
book = 10% exactly -> breach"* — a breach of zero shekels, used as a convenient way to get a rule
into a triggered state. Its subject is the re-arm, so the fixture now starts from a breach worth
acting on. Its docstring already described this family of failure: *"a nag that no user action could
clear."*

`tests/test_a_cap_breach_needs_a_trade_behind_it.py` — 12 tests, 4 confirmed RED against `2edba83`.

Gates: 1774 SQLite, 1774 PostgreSQL on a dropped and recreated database, ruff clean.

---

## PR 65 — V5's "PR C" was two claims, and one of them was wrong

The row has been carried since V5 on 2026-09-02 and re-verified into V6 and V7 as *"the veto and the
cap that nothing reads"*, the oldest untouched item on the board. Both halves were finally measured
against the running code, and they did not survive equally.

### Claim A — "nothing on the live path stops a card buying into an overweight class"

**False.** `AllocationAgent.review_buy` really is reachable only from `pipeline.py:102` /
`POST /decision-feed/generate` — `grep -rn review_buy app/` still returns the definition and that one
caller. But the live card path does not need it, because `propose_funded_buy` enforces a **stricter**
rule of its own. Measured 2026-09-06 on a book 100% in Equities against a Grow target:

```
buy into Equities      -> REFUSED (None)
buy into Fixed Income  -> card for 1,500
```

`review_buy` asks one question: *is this class overweight*. `propose_funded_buy` asks three — does
the trade move the class **toward** its target, is it a within-class swap that must be **labelled**
as one, and does **total book drift** get worse. The weaker check is the one sitting on the quieter
path.

So the fix is not the one three plans have proposed. Wiring `review_buy` into Today would add a
second, laxer implementation of a rule already enforced — the exact shape this codebase has spent
PRs removing (`settings.objective`, the duplicate `max_weight` producers, `_core_plan`'s one sizing
and two callers). `review_buy` stays where it is, guarding `/decision-feed/generate`, and the
guarantee the live path gives is now **pinned by a test** so it cannot regress into being true.

### Claim B — "a hand-set cap plays no part in what gets trimmed"

**True, and it is worse than trimming.** It played no part in **buying** either, and the buy is what
creates the breach.

Measured on a book with NVDA at 10%, a hand-set 12% `max_weight` rule, and Equities *under* its Grow
target so the purchase was otherwise legitimate:

| | before | after |
|---|---|---|
| sized buy | **₪4,500**, NVDA lands at **40.0%** | **₪300**, NVDA lands at **12.0%** |
| the same position at 20%, ranked for trimming | 3rd, *"corrects no drift"* | **1st**, *"NVDA is 20% of the book, above your own cap on it of 12%"* |

Bounded only by the plan-wide `concentration_cap` — twenty-eight points past the limit the user set —
and the `max_weight` rule then fired a card telling him the cap had been breached. **A cap that
alerts after the trade it could have prevented is not a cap.**

### The fix

`rules_service.name_caps(session, user)` loads ticker → cap as a **fraction** from active
`max_weight` rules (the column is a percentage for this rule type; the `/100` happens once, there).
`funding_service.effective_name_cap(cap, ticker, name_caps)` returns the **tighter** of the plan-wide
cap and the ticker's own — neither may relax the other — and it is read by `name_room_ils` on the buy
side, by `name_over` and the `name_allow` budget on the sell side, and by the three redeploy sizings.

Threaded like `prefer` (PR 61.1) and `live` (PR 63): one read at the top of the builder, handed to
every producer. It goes at the **top** deliberately — the first version loaded it beside the screener
and left the geographic card at ~1650 still reading the plan-wide cap alone.

Only **active** rules. A disabled cap is a cap the user switched off, and a constraint that outlives
its own switch is worse than no constraint.

`tests/test_a_cap_you_set_bounds_the_trade.py` — 8 tests, 3 confirmed RED against `8292c89`. The
other five pin Claim A's guarantee and the loader's units, which is the point: they pass today and
their job is to keep passing.

Gates: 1762 SQLite, 1762 PostgreSQL on a dropped and recreated database, ruff clean.

**The lesson worth keeping.** This row survived three re-verification passes — V5 wrote it, V6
re-checked it and kept it, V7 called it the oldest untouched item — and half of it was wrong the
whole time. Each pass confirmed that `review_buy` has one caller, which is true, and inferred from
that what the live path does, which is not the same thing. `grep` proves where a function is called;
only running the other path proves what it does instead.

---

## PR 64 — the cash target became soft, and the floor became a number

Eran, 2026-09-06:

> *"it should be a soft target. meaning if there is a good/valueble/interesting holding that I must
> buy you can use the cash to do it --> but if you recommend me to use my cash for this investment
> you should explain why I should go below the 7.5%. the hard minimum cash I should hold is 150"*

**This reverses a decision the code stated outright**, and the reversed argument is kept in the file
rather than deleted. `cash_floor_pct`'s docstring said:

> *"THE FLOOR IS THE TARGET, and that is the point rather than a shortcut. A reserve the machine is
> allowed to nibble into is not a reserve: on the live book, `Grow` floors cash at 3% and the balance
> sat at 2.92%, so the app had already spent the reserve down to its own limit and nothing on any
> screen called that a decision."*

That was written against a real failure and the failure has not gone away — which is exactly why the
change is narrow rather than a repeal.

### Two numbers where there was one

| | value | who may cross it |
|---|---|---|
| **target** | `plans.cash_target_pct` = 7.5% (or the objective table) | nobody routine — every ordinary card still stops here, unchanged |
| **hard floor** | **₪150**, absolute | nothing, ever |

`CASH_HARD_FLOOR_ILS` is in shekels rather than a share of NAV on purpose: a percentage scales with
the book, which is right for a target and wrong for a floor. `cash_soft_floor_ils` takes the **max**
of the two, because on a small book 7.5% can be under ₪150 and a percentage must not quietly
overrule an absolute.

### The reason is a precondition, not a convention

`plan_funding(..., allow_reserve=True)` is **refused without a non-empty `reserve_reason`**. Same
shape as `describe_funding`'s required `buying_class`, and for the same reason recorded there: *"a
missing argument is now a TypeError at the call site instead of a missing sentence in production
prose."* A boolean on its own would let a future call site opt in and forget to explain, which is the
whole of what Eran asked for.

The card then says it in four parts — how much of the reserve, where cash lands against the target,
that the floor is untouched, and his own why:

> *"This spends ₪850 of your cash reserve, taking cash to 1.3% against your 7.5% target (₪150 left,
> hard floor ₪150 untouched). Worth it because TSEM ranks 88/100 on the Grow screen — strong
> momentum; cheap on earnings, and nothing in the book was overweight enough to trim for it."*

`reserve_reason` is cleared on the payload unless the reserve was actually spent, so the sentence
cannot be printed with nothing behind it.

### The bar is structural, not a score threshold

The obvious design — let a buy idea above score N reach the reserve — needs a number, and **any
number here is one I invented for someone else's money**. Instead the buy-idea card plans the
ordinary way first and retries against the reserve only when trims and spendable cash could not pay
for the purchase **at all**. The app never decides a name is worth the reserve; it puts the score,
the ranking reasons and the resulting cash percentage on the card and Eran taps or does not.

Bounded by two things already in place: `_MAX_BUY_IDEA_CARDS = 2`, and the funding ledger's
`cash_used`, so two cards in one build cannot each plan against the same reserve.

### Debt, named rather than left implicit

`cash_floor_pct` / `cash_floor_ils` now return the **target**, and their names say floor. They are
payload keys the UI reads, so renaming the concept end to end is a separate change. The docstring
says what the function means today and quotes what it used to mean; the rename is the outstanding
part.

`tests/test_the_cash_target_is_soft_and_the_floor_is_not.py` — 12 tests, 8 confirmed RED against
`6dc3c8d`.

Gates: 1754 SQLite, 1754 PostgreSQL on a dropped and recreated database, ruff clean.

---

## PR 63 — a sleeve's target was the catalogue's, on every day of the year

Eran, 2026-09-06: *"the swing should be a card that replace the TQQQ with QQQ automatically"*, and
on the design fork: *"I agree (a) is right."*

`sleeve_targets` read the catalogue's static `weights` minus `base` and stopped there. So
`btm_swing_dip` targeted **TQQQ** whether or not its rule was in a setup — including the ~85% of
the year it is not. The catalogue's own docstring says what that means: *"`base` is the core
holding, not cash. A swing setup is live perhaps 10-15% of the time."* For most of the year the
Plan tab, every drift card, the funding engine and the armed caps described a position the rule
was not asking for, and **nothing in the app could tell "the rule wants TQQQ" apart from "the rule
has never been consulted"**.

### The fork, and why (a)

| | when the rule is off | on Eran's book |
|---|---|---|
| **(a) — chosen** | the sleeve targets its **base** | TQQQ → QQQ |
| (b) | the sleeve targets nothing; the money returns to the core | TQQQ → VTI + BND |

(b) is cheaper and reads well — *"capital sits in the core between setups"* is the catalogue's own
phrase — but his core is Classic 60/40, so it would sell the position into bonds. The strategy
says the money parks in QQQ; (b) parks it somewhere else.

**(a) overturns a docstring, and the overturned sentence is worth keeping in view.**
`sleeve_targets` said funding the base would mean *"selling your holdings to buy an index fund you
never asked for"*. True of the catalogue default; false here. You asked for it by choosing a
strategy whose spec parks the money in the base between setups.

### One read, passed down

`sleeve_service.live_sleeve_targets` loads the stored `StrategySignalState` once per request and
hands it to the still-pure, still-synchronous `sleeve_targets(..., live=...)`. The alternative —
making that function async — drags a session through `card_scope.scope_for`, which is documented as
pure *"so the mapping is testable without a book"*.

Threaded through every consumer, because a primitive that means one thing in six places and
another in a seventh is how PR 60 got two cards wanting the same ₪7,476: `all_sleeve_targets`,
`sleeve_occupancy`, `_fund_sleeve`, `_arm_sleeve_caps`, `sleeve_cap_plan`, `swap_sleeve`,
`card_scope` and the Today drift loop. `strategy.py`'s apply-preview keeps the catalogue — it
previews a strategy that is not applied yet, so there is no signal to follow.

`retire_strategy_rules` now takes the **union** of both legs. A sleeve that has flipped once has
armed a cap on the aggressive instrument *and* on the base; retiring it with only today's leg in
hand would leave the other armed with no owner, which is the exact failure PR 59.1 exists to
prevent.

### No staleness gate, deliberately

The obvious guard — ignore a target older than N days, fall back to the catalogue — **fails in the
dangerous direction**. The catalogue default is always the leveraged leg, so a dead evaluator would
turn *"the rule says hold QQQ"* back into *"the sleeve wants TQQQ"*, and the app would recommend
its way into a 3x fund on the strength of no signal at all. The last thing the rule actually said
is strictly more informative than a default asserting a signal nobody produced. The age travels
with the answer instead: `/plan/sleeves` now carries `following_signal` and `signal_as_of` per
sleeve, so a stale evaluation is visible rather than silently overruled.

### The card that nearly told a lie, and the test that stopped it

The pending-signal card said *"The app is not moving anything for you — place the change at your
broker and mirror it in Holdings."* With the target now following the signal that reads as stale,
so the first version of this change replaced the line. **Two existing tests failed**
(`test_the_signal_card_does_not_claim_the_app_will_act` and
`test_a_pending_flip_becomes_one_card_that_says_no_order_is_placed`) and they were right: the
sentence is still true *of that card*. Nothing moves until something is tapped and no brokerage
order is ever placed. What was missing was not a correction but a pointer, so the promise is kept
and a line is added saying the funding card on Today can now size and apply the tracked-book side.

### What it adds up to

Once the sleeve targets QQQ and holds none of it, TQQQ is a position no sleeve claims — and PR 61's
`steps_back_to` rule makes selling it to buy QQQ actionable in one tap, funded from that exact
position by PR 61.1. The three PRs are one behaviour: **the rule steps back, the target follows,
and the card finishes the move.**

`tests/test_the_sleeve_holds_what_the_rule_wants.py` — 10 tests, 8 confirmed RED against `6dc3c8d`.

Gates: 1742 SQLite, 1742 PostgreSQL on a dropped and recreated database, ruff clean.

---

## PR 62 — the Tel Aviv section was a label with nothing behind it

Eran, 2026-09-06: *"the app can (and should) recommend me assets from TASE"*, and then
*"let's start with the Israeli companies for now, add the Israeli indexes to the backlog."*

**It could not, and the source said it could.** `universe.py:74` carried a section headed
``# ---- Tel-Aviv (TASE) names (Yahoo .TA suffix) ----`` and a module docstring promising
"Tel-Aviv (TASE) names". Every one of its seven entries was `"market": "NASDAQ"` or
`"NYSE"` with no `.TA` suffix — the **US ADRs** of Israeli companies. `equity_universe()` is
the only list the screener (`screener_agent.py:146`) and the signal service
(`signal_service.py:152`) rank, so not one shekel-denominated instrument could ever be
proposed, on a book denominated in shekels and taxed under Israeli CGT. Every suggestion the
app has ever made carried an unhedged USD exposure it never named.

**Everything else had been built for weeks.** `MARKET_CCY["TASE"] = "ILS"` (`fx.py:33`), the
`.TA` symbol path (`live.py:50`), the ILA/agorot minor-unit detection and the 100x row
repair (`fx.py:45-47`, `pricing_service.py:293`). Only the candidate list was missing, which
is the part that decides what can ever be suggested.

**Verified live through this app's own provider, 2026-09-06.** All 17 entries returned
price, currency `ILA`, sector, P/E and 60 sessions of history: LUMI, POLI, MZTF, DSCT, PHOE,
HARL, ICL, TSEM, NVMI, CAMT, NICE.TA, ESLT.TA, TEVA.TA, BEZQ, AZRG, ORA, ELAL — six sectors,
because a ranking over six banks is a sector bet wearing a country's name.

TEVA, NICE and ESLT moved to Tel Aviv rather than being duplicated: both listings are real
and genuinely different instruments, but one company holding two slots in one ranking is a
thumb on the scale. CHKP, WIX, MNDY and GLOB have no TASE line here and stay where they trade.

### And adding them fires a gun V6 saw loaded

V6's PR D called the unnormalised price writers *"a loaded gun, not a firing one, because no
broker sync runs today and no CSV import is scheduled"*. **Putting Tel Aviv in the universe
is what schedules it**, so the writer is fixed in the same PR rather than after the first
wrong purchase.

`_apply_buy_funded` read `float(q.price)` and the raw currency code, then computed
`qty = budget / fx_rate(ccy) / price`. For LUMI.TA that is **7916 agorot used as a shekel
price** — a hundredth of the shares bought — against an FX code, `ILA`, that no provider
knows, so `fx_rate` errors and falls back rather than converting. Two wrongs that do not
cancel. `to_major_units` says *"use at every boundary"* in its own docstring; PR 45 fixed
`add_holding` and this boundary was left because nothing in the universe could reach it.

`broker_sync._to_intake` is the same fault on the aggregator path, and it threw away a
`currency` field `BrokerPosition` has carried since it was written — **including on the cost
basis**, which is worse, because a wrong basis is a wrong CGT estimate on every card that
quotes one afterwards. Both halves are fixed here; that closes the second and third of the
four writers V6's PR D named.

### What is NOT here, and why

Israeli **index funds**. A TASE tracker trades under a numeric symbol (`1159250.TA`,
`1146836.TA`) and the provider returns Not Found for every one tried; `TA35.TA` resolves as
the **index**, which cannot be bought. So there is no shekel-denominated ETF in this set and
`recommendations.CLASS_ETF` stays US-listed — a test asserts both, because a row claiming
otherwise would be this same defect in the other direction. Parked in the product backlog
with its un-park condition: a price provider that serves TASE fund symbols.

`tests/test_the_universe_reaches_tel_aviv.py` — 9 tests, 5 confirmed RED against `446ceaa`.
One of them, `test_no_entry_claims_a_market_its_symbol_contradicts`, asserts the *shape* of
the original defect over every row rather than the ones I touched: nothing structural
prevented a NASDAQ ticker being filed under TASE, so nothing structural prevented it coming
back.

Gates: 1732 SQLite, 1732 PostgreSQL on a dropped and recreated database, ruff clean.

---

## PR 61.1 — the card was right and the funding plan was wrong

Eran, minutes after PR 61 deployed: *"the second card is still guidance only and not
actionable!!"*

`/health` served `446ceaa`, so this was not a stale deploy. Asking production for the plan
behind that card is the whole diagnosis:

```
SELL VTI   2,925.31  | cheapest way to raise it: Equities is the most overweight class
                       (81% against an 80% target) and VTI is among its least-taxed positions
SELL TQQQ  4,145.14  | ... and TQQQ is among its least-taxed positions
from_cash    179.88
```

**The funder was not selling TQQQ alone.** VTI does not step back to QQQ, so PR 61's rule
correctly refused — to do something that would have been wrong anyway: selling ₪2,925 of
core money doing its job to start a sleeve whose funding was sitting in the leveraged
position the sleeve replaces. The card was the messenger.

**The defect is that funding is ranked on fit and tax.** That is the right question when
paying for a purchase out of whatever the book can best spare, and the wrong one when the
purchase IS a conversion. `rank_trim_candidates` scores `name_over × 400 + class_over × 100
+ tax_friendliness × 10`; with Equities barely over and TQQQ carrying the larger gain, VTI
simply scores higher. Every sentence in those two reasons is true.

**And my reproduction is what hid it.** PR 61's fixture put TQQQ at 59% of the book, which
trips the 25% single-name cap, so `name_over` dominated and TQQQ was picked alone. The live
plan runs `risk_tolerance: High` — `concentration_cap` **0.40** — and TQQQ sits at ~35%,
*under* its cap. That one number is the difference between a ranking that picks the right
leg by luck and one that does not. The new fixture pins the risk tolerance for exactly this
reason, and reproduces the production legs to the shekel:

| | pre-fix | post-fix |
|---|---|---|
| funding legs | `VTI 3,420.00` + `TQQQ 3,884.37` | `TQQQ` only |
| the card | guidance, `apply.kind: none` | actionable, `fund_sleeve` |

**The fix is in the funder, not the card.** `rank_trim_candidates` and `plan_funding` gain
`prefer`: a set of tickers that rank ahead of every fit-and-tax term (+10,000, where
`name_over` maxes near 400), are never dropped for scoring zero, are eligible on their own
terms for the same reason `buying_class` is — a step-back swap converts one Equities
position into another, so the class weight does not move and the overweight budget has
nothing to protect — and carry their own reason, because *"least-taxed position in the most
overweight class"* does not describe why that position was chosen. `_fund_sleeve` fills the
set with exactly the holdings for which `strategy_catalog.steps_back_to(held, sleeve_ticker)`
holds.

**One rule, two callers.** `steps_back_to` now decides both whether the card may act and
which position the funder sells, so the card can no longer be offered a plan it has to
refuse. Splitting that judgement across two functions is what produced this.

Gates: 1723 SQLite, 1723 PostgreSQL on a dropped and recreated database, ruff clean. Both
new tests confirmed RED against `446ceaa`.

**The lesson worth keeping, and it is now three in a row.** PR 60.1 shipped a dead deferral
because the test asserted the layer below the bug. PR 61's first rule was caught by an
existing test, not mine. This one was caught by Eran, in production, because my fixture
differed from his book in a single setting I never thought to copy. A reproduction is not a
reproduction until the setting that decides the branch is in it.

---

## PR 61 — the card that finishes the move, and the first rule for it was wrong

Eran, looking at Today after the B2 swap: *"the swing should be a card that replace the TQQQ with
QQQ automatically."*

PR 60 stopped the **core** card offering to liquidate the ₪7,476 of TQQQ that no sleeve claims any
more. This is the other half — the card that offers to complete the move — and it turns on one
question the file had already answered in the negative:

> "one tap taking a book from nothing to a fifth in a 3x fund is a different decision from topping
> up. That one routes to 'Fund this sleeve'."

That objection is right. It is also not a description of every cold sleeve, and after a swap it
describes the opposite of what is happening.

**The first rule I wrote was wrong, and the suite caught it.** I made the card actionable when every
funding leg was cash or a holding **no sleeve claims**. Full SQLite run:
`test_sleeves_mean_what_they_say.py::test_k_an_empty_sleeve_card_cannot_be_buried_by_accept` — **1
failed, 1718 passed**. On that fixture the rule would have taken a book from nothing to **10% SOXL
by selling an ordinary V position**: precisely the decision the guidance split exists to keep out of
one tap. Being *unclaimed* is not the same as being *earmarked*, and I had tested the wrong one.

**The rule that survives is the relationship, not the owner and not the size.** Actionable only when
every position being sold is a leveraged instrument whose **base is this sleeve's ticker** — a new
`strategy_catalog.steps_back_to(aggressive, base)`, read straight off the specs:

| sold | sleeve | `steps_back_to` | card |
|---|---|---|---|
| TQQQ | QQQ | yes — three rules hold TQQQ over a QQQ core | **actionable** |
| SOXL | SMH | yes | actionable |
| V | SOXL | no — V is not an overlay instrument | guidance |
| QQQ | TQQQ | **no** — the helper is asymmetric on purpose | guidance |

The asymmetry is the whole point. Selling the 3x fund to hold the index it was leveraging is a
**downgrade of an exposure the book already has**, under a rule that is no longer running. The
reverse is the move the objection was written about.

Still guidance when a leg belongs to another sleeve (the piggy-bank exclusion `_fund_sleeve` already
enforces) or when there is a shortfall (a position at a size nobody chose, which that function
abstains over rather than half-install).

**Priced through the same function the button runs.** The card computes
`load_basket(..., mode="fund", dry_run=True)` inside `_agent_tx`, and quotes
`min(amount_ils, funded_ils)` — what the executor actually spends. That is the `_core_plan`
discipline, which exists because a card once said *"sell 4,324"* and Accept sold **12,139**.
Measured on the reproduction of Eran's book: card `est_amount` **₪15,671.01**, Accept moved
**₪15,670.96**, sleeve went 0% → 33.3%, and the card stopped firing because the sleeve is no longer
cold.

**And the guidance branch stopped pointing at a tab.** It now carries the funding engine's own
refusal, which was already written to be printable: *"You asked for a 40% sleeve (₪44,520). The most
this book can fund right now is 0% — ₪44,520 short… Lower the sleeve to about 0%, or add cash."* A
card that says which number to change beats one that says which tab to open.

`tests/test_the_card_finishes_the_move_it_recommended.py` — 9 tests, 6 confirmed RED against the
pre-change tree (the other 3 assert invariants that already held).

Gates: 1717 SQLite (+4 `caplog` errors that are an artefact of `-p no:logging`, green with the
plugin on), 1721 PostgreSQL on a dropped and recreated database, `ruff check app tests alembic`
clean.

**Ops note, same day: the first ship attempt died on a stale index lock, and the lock was mine.**
Not a running git process -- a ZERO-BYTE file stamped 14:37, owned by the Linux sandbox user backing
my shell into the repo, twenty-three minutes before the 15:00 run. At 14:37 that shell ran `git
status --short` while inserting this entry; git writes the index lock to refresh the index and then
unlinks it, and that shell is not permitted to delete files inside the folder, so the unlink failed
and the lock survived. It printed as a `warning:` on a command that still exited 0, which is why I
read the line as being about the two temp files removed on the same line. Every later git WRITE --
three `git add` calls and the commit -- then refused.

Ruled out: a live git process (0 bytes, 23 minutes stale, owned by the sandbox user rather than
Eran's account); an interrupted command of Eran's (nothing of his ran at 14:37); a permissions fault
on the repo (`git --no-optional-locks status` read fine, and ruff plus 1737 tests ran off the same
tree).

**And it had happened before.** The 08-31 repo-hygiene row above records "a `_to_delete/` folder
holding stale `.git/index.lock` files moved out of `.git/`" -- the same fault, worked around by
moving the files rather than diagnosed. That is the whole cost of a warning on a zero-exit command.

Fix: read-only git in that shell now uses `git --no-optional-locks`, which never takes the lock.
`scripts/deploy/resume-pr61-after-the-stale-lock.ps1` clears a stale file -- refusing if it is
non-empty or under two minutes old, so it can never delete a live one -- and re-hashes all four
changed files against what the gates passed on before skipping to the commit.

**The lesson worth keeping.** Two PRs in a row, the thing that caught my mistake was not the test I
wrote for the feature — it was an existing test asserting an invariant I had stopped thinking about.
PR 58 was the war-room speaker sequence; this was the guidance split. A rule that is "obviously
right" about the case in front of you is worth running against the case that made the rule exist.

---

## PR 60.1 — the deferral shipped dead, and finding out why found something worse

PR 60's step 5 asked production and printed **"STILL SELLING TQQQ. The deferral did not take."** The
headline fix and the cash line had landed — the card now read *"Rebalance about ₪7,476"* with *"About
₪7,041 of the proceeds would stay as cash"* — but the deferral itself never fired.

**My bug.** `sleeve_occupancy` emits `target_pct`. I read `sleeve_pct`, which is not a key on that
dict, so `float(s.get("sleeve_pct") or 0.0)` was `0.0` for every sleeve, `unfunded_ils` came to
**0.00** on a book with a 33% sleeve holding nothing, and the deferral was skipped. Green tests, dead
feature — the tests asserted `plan_realign`'s behaviour given a `deferred` list, and never that
`_core_plan` produced one.

**The one it led me to is worse, and it predates this work.** The loop directly above mine makes the
same mistake:

```python
want_pct = float(s.get("sleeve_pct") or 0.0)     # always 0.0
if held_pct > want_pct:
    over += (held_pct - want_pct) / 100.0 * nav  # so: the sleeve's ENTIRE holding
```

`over` is `sleeve_over_ils` — the money `core_realign` is allowed to reclaim from the sleeves. Its
docstring promises *"It will not trim a sleeve BELOW its own target. The sleeves' sizes are a
decision the user made on the Plan tab; this may reclaim what is over that decision and may not
overrule it."* With `want_pct` pinned at zero, **every shekel in every sleeve counted as overshoot** —
measured on the live book, SOXL's whole ₪7,686 position. The guarantee was vacuous.

It has never bitten because that funding path only engages when the buys exceed sells plus spendable
cash, which this book has not hit. On an under-funded core it would have sold a whole sleeve.

**And `gap_pct` was in two units at once.** `target_pct` is 0-100; `held_pct` is a fraction — a
convention the UI documents at `index.html:1579`. `gap_pct` was `target_pct - held_pct`, so a sleeve
at a 33.9% target holding 35.09% of the book reported `+33.54`: reading as *a third of the book
under-funded* when it is **1.2 points over**. Wrong by the whole held fraction, and always in the
direction of "you hold nothing".

| | before | after |
|---|---|---|
| `unfunded_ils` (deferral) | 0.00 | ₪7,247 |
| `sleeve_over_ils` | 7,684.71 (SOXL's whole position) | 0.00 |
| `gap_pct` for SOXL | +33.54 | −1.19 |

Gates: 1712 SQLite, 1712 PostgreSQL on a dropped and recreated database, ruff clean.

**The lesson worth keeping.** The deploy check found this, not the suite. A step 5 that asks
production what it is actually saying is worth more than a green test that asserts the layer below
the bug — twice now: PR 56.1's leftover key, and this.

---

## PR 60 — two cards wanted the same ₪7,476, and the headline understated one by 17x

Eran, looking at Today after the B2 swap: *"I don't understand these cards... why is TQQQ part of
the core and not the sleeves?"*

Two cards were on screen at once:

* **"Bring your core back to Classic 60/40"** — *Sell all TQQQ, about ₪7,476 (not in the core's
  basket). Buy VTI, about ₪422.* Headlined **"Rebalance about ₪422 inside your own book."**
* **"You chose a 33% QQQ sleeve and hold none of it"** — *Closing it means moving about ₪7,238
  into QQQ.*

**Same money, two destinations, neither card aware of the other.**

### Why TQQQ was "in the core"

The core is the **remainder** — `PlanSleeve`'s docstring says it: *"the core is the implicit
remainder, not a row."* PR 59 swapped `btm_swing_dip` for `btm_qqq_hold`, so nothing claimed TQQQ
any more, so it fell into the core region, so rule 1 of `plan_realign` said *not in the core's
basket, sell it in full*. Every step correct and the conclusion wrong: "retire the overlay, hold
the base" means the money goes to **QQQ**, not to VTI and cash.

The collision was real, not theoretical. `_fund_sleeve` builds `exclude = set(targets) |
sleeve_tickers(...)` — only tickers a sleeve **currently** claims. TQQQ was no longer one, so the
sleeve's own funding plan was free to sell exactly the same position.

### The headline was wrong by 17x

```python
_moved = float(_plan.get("buys_ils") or 0.0)   # = 422
"action": f"Rebalance about {_ils(_moved)} inside your own book — no new money."
```

`buys_ils` is only what the basket's legs are *short*. Step 1 sold ₪7,476. Every individual leg was
honest — `describe()` listed them — but the sentence on top of them was not, and `est_amount` feeds
the mute, re-fire and merge logic, so the card was sized as a small one.

**And nothing said where the rest went.** Sells ₪7,476, buys ₪422, tax ₪14 — about **₪7,040**
becoming cash, roughly a third of the book, with no line on the card. `describe()` was written as
the card-claim invariant in one function and listed every LEG; a *consequence* of the legs, at that
size, is a change the reader has to be told about too.

Reproduced exactly before fixing: `sells 7476.0, buys 422.4, tax 14.02, to_cash 7039.58` — the same
three numbers that were on his screen.

### What ships

* `to_cash_ils` on the plan, and a `describe()` line naming it.
* `deferred` on the plan: holdings the caller pulled out of the core region, **with the reason**, so
  the card says what it is deliberately not touching. Silence there reads as "the app did not
  notice", which is how two cards came to propose two destinations for one position.
* `_core_plan` reserves unclaimed non-basket holdings while any sleeve is under-funded. The tie
  breaks toward the sleeve because a sleeve size is a decision made on the Plan tab and the core is
  what is left over. Reserved **whole**, not sliced: the funding plan decides how much it needs, and
  a core card selling part of it first is the pre-emption being removed. A basket leg is never
  deferred — that is core money doing its job.
* The headline reports the money that moves, and names the leftover cash.

Gates: 1710 SQLite, 1710 PostgreSQL on a dropped and recreated database, ruff clean. Three of the
four new assertions confirmed RED against the pre-fix tree.

**Still open, and it is the rest of item 4:** the sleeve's target does not follow its signal, so a
swing card cannot be actionable yet, and there is no single card that says *"move ₪7,476 from TQQQ
to QQQ"* in one tap. This PR stops the app proposing the wrong destination; it does not yet propose
the right one in one action.

---

## PR 59.1 — a retired sleeve left its stops behind, and nothing could tell they were its

**Found on the live book, the day after PR 59 shipped.** The B2 swap ran and did what it claimed:
33.09% moved from `btm_swing_dip` to `btm_qqq_hold`, nothing was bought or sold, the TQQQ
`max_weight` cap went `active: False`, and the SOXL cap re-levelled 40.0 → 33.9 (correcting a stale
hand-set value on the way). Then `/api/v1/rules` showed this:

| ticker | rule | level | active | strategy_id |
|---|---|---|---|---|
| TQQQ | `max_weight` | 39.0 | **False** | `sleeve` |
| TQQQ | `take_profit` | 41.0 | **True** | *(none)* |
| TQQQ | `trailing_stop` | 20.0 | **True** | *(none)* |
| TQQQ | `stop_loss` | 65.7 | **True** | *(none)* |

**Root cause.** `_arm_sleeve_caps` retires a cap for a ticker no sleeve wants, and its own docstring
says why — *"a row removed without re-levelling the caps leaves a ceiling on the book with nothing
behind it"*. The stop and the take-profit had no equivalent path, and not because anyone decided
they should outlive the sleeve: **they could not be identified.** `discipline_rules` emitted them
with no `strategy_id`, the apply path passes that field straight through, and they landed in the
database as NULL. Nothing could say which sleeve armed them, so nothing could retire them.
`signal_rules` had stamped its own all along; only the discipline path did not.

**The notes settled which was which** — attribution by evidence rather than by level arithmetic:

| level | note | verdict |
|---|---|---|
| 41.0 | *"Swing: Dip-Buy in an Uptrend: bank a 41% gain…"* | the retired strategy's. Disarmed. |
| 20.0 | *"From uptrend alert (protect gains, 20% trail)"* | an alert, not a sleeve. Kept. |
| 65.7 | *"Caps downside ~15% below today's 77.30."* | the holdings suggester. Kept. |

Two of the three had nothing to do with the sleeve, which is the case for refusing to guess:
retiring "every active rule on a ticker no sleeve wants" would have disarmed both — and the book
still **holds** TQQQ, so those two are protecting a live position.

**What ships.** `discipline_rules` stamps `strategy_id`. `retire_strategy_rules` deactivates rules
carrying the departing id, wired into **both** `swap_sleeve` and `retire_sleeve`, and running
*before* `_arm_sleeve_caps` because that function commits. Ownership is **marked, not assumed** —
the same lesson `_arm_sleeve_caps` records learning the hard way — so only a stamped rule is
touched, and a rule with no owner on an affected ticker is **named and left alone**, returned as
`left armed - no owner recorded`.

**It cannot clean up rules armed before the stamp**, including the three above, and does not
pretend to. Those carry NULL and guessing at them is the failure this PR is about.

Also in: `scripts/ops/pre-push`, a hook that refuses a force-push or a deletion of `main` (verified
against a throwaway origin: normal push allowed, `--force` refused, delete refused). It exists
because **GitHub will not enforce a ruleset on a private repository on this plan** — the Rulesets
page says so itself — so the two correct boxes were configured against a rule that would never
fire. The hook is not equivalent: it runs only where installed and `--no-verify` walks past it. It
stops the accident, which is the whole class of incident here.

Gates: 1706 SQLite, 1706 PostgreSQL on a dropped and recreated database, ruff clean. Three of the
three new assertions confirmed RED against the pre-fix tree.

---

## PR 59 — B2: retiring a rule and retiring an allocation stop being the same button

Eran decided this on 2026-09-04 and parked execution; B2 held the measurement so the decision would
not have to be re-made. Executing it turned out to need two things the app did not have.

### What the measurement said

Shipped engine, 2016-09-06 → 2026-09-04, 2,514 sessions, adjusted closes:

| variant | CAGR | volatility | max DD | in TQQQ | trades/yr | ₪10,000 → |
|---|---|---|---|---|---|---|
| hold TQQQ, do nothing | **40.25%** | 66.74% | 81.66% | 100% | 0.1 | 294,462 |
| hold QQQ, do nothing | 20.73% | 22.56% | 35.12% | 0% | 0.1 | 65,789 |
| **`btm_swing_dip` as shipped** | **17.19%** | 34.35% | 40.55% | 15.6% | 45.4 | **48,855** |

The overlay turned a 40.25% instrument into 17.19% — **below simply holding the base** — while
carrying a *deeper* drawdown than the base (40.55% against 35.12%) and paying roughly 7 points a
year to 45 round-trips. Gross of costs it makes 24.11%, so even before fees it trails holding TQQQ
by 16 points. **Leverage decay was not the cause. Being out of the market during the compounding
was.**

### Why "drop the rule, keep the position" was not expressible

`DELETE /plan/sleeves/btm_swing_dip` retires the whole **sleeve**. On this book that is 36.05%
collapsing into the core, the core going 27% → ~55%, and the book's Fixed Income target swinging
7.8% → ~22% — after which the app starts asking to sell equities into BND. Dropping a *rule* and
dropping an *allocation* were one operation, and only one of them was ever wanted.

**`btm_qqq_hold`** — the catalogue had no way to say "hold the index". Every other entry is a rule,
so a retired overlay had nowhere to land. It declares **no `base`**, which is the definition rather
than an omission: a base is where a rule goes when it is off, and there is no rule. It is also what
makes the sleeve fundable at all — `sleeve_targets` funds `weights` minus `base`, so an entry
naming QQQ in both would ask the app to fund nothing and the sleeve would sit permanently empty.
The engine needed no new support: a spec with no overlay already falls through to `buy_hold`, so it
is measured by the same code path as everything else, and `backtestable()` picks it up for the
nightly refresh and the solver automatically.

**`swap_sleeve`** — one operation that moves the size across. The order is forced: `validate`
measures the new sleeve against the *free* share of the book while the old one still occupies its
own, so adding first would put this book at 109% and be refused. Done as two API calls it leaves a
window in which the book is under-allocated and the core oversized — harmless for seconds, except
that anything reading the book in that window (a refresh, a card, the 06:15 job) sees an allocation
nobody chose. Caps re-level **once, at the end**, for the reason `retire_sleeve` already documents:
a row removed without re-levelling leaves a live `max_weight` on a position now held for some other
reason, and re-levelling between the halves would arm caps against a book that exists for one
statement.

It also checks the destination is a real strategy, which `validate` never did — it only ever asked
about size and duplication, so a typo'd id would have been accepted as a sleeve for a strategy that
does not exist, sized, capped and funded against nothing. The refusal comes **before** the removal,
so a typo cannot cost you the sleeve you had.

**Nothing is bought or sold.** The swap changes what the app steers toward, not what is held.
Afterwards TQQQ is a holding no sleeve wants and QQQ is unfunded, and the existing cards already
say both. Asserting otherwise would be the failure `test_card_claims` exists to catch, so a test
pins it directly.

### A stale comment the change surfaced

`sleeve_count.py` read *"Seven is the whole `backtestable()` pool today, so `MAX_SLEEVES` only ever
binds if that pool grows"* — with `MAX_SLEEVES = 7` typed beneath it. The pool became eight, which
is exactly the condition that sentence described, and nothing would have noticed: `MAX_SLEEVES`
would have started binding, and a search that stops because it hit a backstop reads as a search
that converged — the distinction the whole module exists to report. The number is now derived from
the catalogue and the test asserts the derivation rather than the digit.

Gates: 1703 SQLite, 1703 PostgreSQL on a dropped and recreated database, ruff clean. Eight of the
nine new assertions were confirmed RED against the pre-PR-59 tree; the ninth is the derived-ceiling
check, which passes on both sides by construction and fails only when the two diverge — which is
the whole point of it.

---

## PR 58 — the screen stopped reporting things that did not happen

Three claims went in. Two held. One was mine and was wrong, and the correction is
recorded here rather than quietly dropped.

### 1. "What's moving" reported four events that never happened — HELD

`BuiltinEconomicDataProvider` returned four hardcoded sentences, unchanged since the day they
were typed: *"US CPI release above consensus; rate-path implications."* (severity 80),
*"USD/ILS broke a multi-week range."*, *"Israeli surtax threshold guidance revised."* (severity
95), *"TA-35 constituent earnings cluster this week."*

**What made them harmful is everything true that was wrapped around them.** `market_impact.annotate`
named the reader's actual holdings in each one and computed the actual shekel exposure — *"touches
36% of your portfolio (₪7,634)"*. `_playbook` turned each into advice — *"Consider hedging the
currency or rebalancing toward ILS assets"*. And the card printed `last_refreshed` above it, which
comes from `market_status()`: **the price refresh clock**. So a frozen list wore a moving timestamp
and read as market intelligence that had updated four minutes ago.

Every layer is honest by itself. The exposure arithmetic is right, the FX normalisation is right,
the playbook is a defensible mapping from event type to action. They were composed on top of four
sentences that were not true, and the composition is what reached the user.

**Credit where it is due: `docs/AGENTS.md` already found all of this**, including the borrowed
clock, and `test_the_agent_roster_is_what_actually_runs.py` pinned it as a defect with the comment
*"Passing means the bug is still there."* PR 58 is the fix for a defect that was already written
down — which is the argument for writing them down.

**The fix is to return nothing, not to label the fiction.** The frozen FX table survives being
labelled because it has a job: some number must value a dollar holding, and a stale one with a loud
warning beats a crash. Nothing needs an economic event to exist. `get_events()` returns `[]`, the
borrowed clock is gone, and the payload carries `source` / `live` / `source_note` instead. The card
says *"No market-intelligence source is configured, so there is nothing to report here."*
`economic_provider()` now warns once per process the way `fx_provider()` does.

`annotate`, `_playbook` and the exposure maths are **untouched**. They are the part worth keeping;
wire a real provider and the whole panel comes back.

**The bug this PR nearly introduced by fixing it.** With events gone, `research` is empty on every
deploy, and the war room's Research line — which was inside `if research:` — simply stopped
appending. `AGENTS` advertises "Research" to `/war-room` as the cast list, so the transcript would
have named a speaker who never speaks: *the same defect, one layer down, created by removing it.*
Research now always speaks, and says it has nothing and why. Caught by an existing test
(`test_war_room_produces_agent_transcripts` asserts the first three speakers), which is the whole
argument for asserting on sequence rather than membership.

### 2. `Settings.objective` was a wiring diagram for a wire nobody ran — HELD

Written by `plan_settings`, carrying the comment *"plan objective (Grow/Balanced/Preserve/Income)
the agents optimize toward"*, and read by **no agent**. Every reader goes to `plan.objective`
directly: `recommendations.py:991/1178/3730`, `strategy_service.py:515/630`. The only thing that
ever touched the settings copy was a test asserting it round-tripped — and that assertion was the
only evidence the wire existed, which is what made a dead field look load-bearing.

Field, write and assertion all removed. A field whose comment describes behaviour it does not have
is worse than no field.

### 3. `war_room.AGENTS` names seven agents that do not exist — **DID NOT HOLD**

**This claim was mine and it was wrong.** All seven names are emitted in transcripts built by the
real pipeline, and five of them unconditionally. I had read a true observation in
`docs/AGENTS.md` — *"`war_room.py:18` declares SEVEN agents, five classes exist under `app/agents/`,
and the overlap is TWO"* — and drawn a false conclusion from it. Those are two different taxonomies:
`app/agents/` holds five decision-making modules, while the war room's names are pipeline **roles**,
most of which are engines. Whether calling an engine an "agent" in a "chat transcript" is itself
overclaiming is a fair argument, and a separate one; the list is not fabricated.

`test_every_war_room_agent_is_one_that_actually_speaks` now pins it in both directions — no name
advertised that never speaks, no speaker missing from the cast — so the question gets checked
rather than remembered.

Gates: 1686 SQLite, 1686 PostgreSQL on a dropped and recreated database, ruff clean. Five of the
seven new assertions were confirmed RED against the pre-PR-58 tree. Five existing tests asserted
the old behaviour and were **rewritten rather than deleted**, each keeping the property that
mattered: the research agent's evidence contract now runs against a stub instead of the fixture,
and the "is the agent thinning the list?" check still works with nothing to thin.

---

## PR 57 — C8: the schema has one author, and the container is not root

Two halves of the same idea: stop the deploy doing things nobody asked it to do.

### 1. `AUTO_CREATE_TABLES` defaults to false

**What it was doing.** At boot the app ran `create_all` from today's models plus a
23-statement `ALTER TABLE ... IF NOT EXISTS` list in `main.py`. For most of this repo's life that
list was not a convenience beside the migrations — **it was the mechanism**, and
`test_startup_ddl_covers_migrations.py` exists because it twice failed to keep up (0011's
`last_error`, 0015's `benchmark_ticker`, the second of which degraded silently for ten days).

**Why it can go now.** PR 52 moved `alembic upgrade head` into Railway's `preDeployCommand`, and
PR 54 proved it runs: `/health.schema_revision` moved `0020_solver_verdicts` → `0021_plan_cash_target`
across that deploy, and nothing in `app/` writes `alembic_version`. With migrations running for
real, the boot-time builder is no longer a safety net — it is a way for a migration that did not
run to be papered over from the models while the deploy reports healthy and `alembic_version` sits
behind. **The fallback's danger was always that it is silent.**

**Measured before flipping it, not argued.** Two databases on postgres:16 — one built by `alembic
upgrade head` on a virgin database, one by `create_all` plus the 23 startup statements:

| | alembic only | create_all + startup DDL |
|---|---|---|
| columns | 272 | 272 |
| indexes | 72 | 72 |
| constraints | 46 | 45 |
| column defaults | identical | identical |

The single constraint difference is `alembic_version`'s own primary key, which the second database
does not have because it has no `alembic_version` table. **Nothing is lost by turning it off.**

**Off by default, not off by env var.** An env var someone has to remember to set is precisely the
failure mode this removes, and the ops table above carries four-week-old items of exactly that
shape. `tests/conftest.py` turns it on explicitly, and a fresh clone builds its schema the way
production does — `alembic upgrade head`, which `test_the_migration_chain_runs_from_nothing.py`
already proves works from an empty file.

**Made observable.** `/health` gained `builds_own_schema`. `schema_revision` says where the
migrations got to; it cannot say whether something *else* is also writing the schema, and a
revision at head while `create_all` runs at boot cannot distinguish "the migrations built this"
from "the models did, and the migrations came along". The production warning became an `error`,
and no longer says "prefer Alembic migrations" — that read as advice about a choice, and there is
no longer a choice.

**The new guard runs the other way round.** `test_startup_ddl_covers_migrations.py` checks every
migration is mirrored into the startup list. C8 inverts which one is authoritative, so
`test_the_migrations_are_the_only_thing_that_builds_the_schema.py` checks the reverse: every
`ADD COLUMN` in the startup list is a column `alembic upgrade head` actually builds. Get that
backwards and the failure is silent in the worst way — the app comes up, every table exists, and
one column that only the startup list knew about is missing from a database the repo cannot
reproduce. It carries a guard on itself, too: the parser reads a literal block out of `main.py`,
so an assertion that the block still yields at least five statements stops a refactor turning the
whole check vacuously green.

### 2. The container

| | before | after |
|---|---|---|
| `.dockerignore` | **did not exist** | 60 lines; `.env`, `.git`, `scripts/`, tarballs, tests |
| user | root | `appuser`, uid 10001, created after the COPYs |
| base image | `python:3.12-slim` (moving) | `python:3.12.14-slim-bookworm` |
| `HEALTHCHECK` | none | stdlib urllib against `/health/ready` |

**On the missing `.dockerignore`, precisely.** The Dockerfile COPYs only `app/`, `alembic/`,
`alembic.ini` and `requirements.txt`, so `scripts/.agent-key` never reached a layer. Build *context*
is not the image — and that distinction is the whole reason this was easy to leave undone. But the
context was the entire working tree, uploaded to the builder and cached, and one `COPY . .` written
in a hurry is the whole distance between "not in the image" and "in a layer, forever". The key is
named explicitly as well as covered by `scripts/`, so narrowing that line later cannot silently
re-admit it.

`USER` comes after the COPYs deliberately: the application files stay root-owned and
world-readable, so the process can read them and cannot rewrite them. `alembic upgrade head` runs
in this same image as the pre-deploy step and needs no write access either — it writes to the
database.

**Untested by build, and bounded.** There is no Docker daemon in these gates, so the image was not
built here. `tests/test_the_container_is_not_root_and_ships_no_secrets.py` pins the properties
statically instead. The risk is bounded by PR 49: an image that fails to build never deploys, and
one that builds but cannot serve `/health/ready` never takes traffic — the previous deployment
keeps serving in both cases.

Gates: 1679 SQLite, 1679 PostgreSQL on a dropped and recreated database, ruff clean. Five of the
nine new assertions were confirmed RED against the pre-C8 tree; the other four are structural pins
that pass on both sides, including the column-coverage check that makes the flip safe.

---

## PR 56.1 — the withdrawal missed a call site, and production said so

**Found by the deploy check, not by a test.** PR 56's step 5 asked production whether
`portfolio_volatility_pct_strategy_longrun` was gone. It was not. Everything else about the
withdrawal had landed on `f0e570c` — the plan headline read 45.4 (the blend) rather than ~68,
`volatility_window` was present, and `volatility_exceeds_assumption` had correctly gone loud again.

**Root cause, reproduced rather than argued.** PR 55.1 published the figure at **two** sites under
**two names**:

| site | key |
|---|---|
| `_portfolio_stats` (the producer) | `volatility_strategy_longrun` |
| `_plan_dict:186` (the API mapper) | `portfolio_volatility_pct_strategy_longrun` |

PR 56 removed the producer. `stats.get("volatility_strategy_longrun")` then returned `None`, and
line 186 assigned it unconditionally, so `/plan` went on carrying the retracted number's *name*
with a null value. Reproduced locally by calling `_plan_dict` directly: `KEY PRESENT: True`,
`VALUE: None`, headline `45.4`.

**Ruled out, each by the observation that killed it:**

| candidate | killed by |
|---|---|
| The deploy did not land | Same output: headline 45.4 not 68, `volatility_window` present, flag flipped to true — all three are PR 56 behaviour. |
| The deploy check itself is wrong | The local reproduction: the key really is in the dict. PowerShell's `PSObject.Properties[...]` returns an entry for a present-but-null property, which is exactly right. |
| Something else emits it | `grep` across `app/`: exactly one site, `plan.py:186`. |

**Blast radius: one null key on `/plan`, for one deploy, with no reader.** Nothing in
`app/static_app/` references either name. Nothing sizes a trade from it. The harm is
epistemic rather than numeric — a key that only ever holds null reads as *"we measure this and it
came back empty"*, which is precisely the claim PR 56 existed to withdraw.

**Why it was silent, and this is the part worth keeping.** PR 56's own test asserted
`"volatility_strategy_longrun" not in out` — on the **stats dict**, one layer *below* the rename.
A test beneath the API boundary cannot see a key the API layer adds. Same shape as **PR 43**, where
PR 42's fix survived at a second call site. `test_the_plan_payload_itself_carries_no_trace_of_the_withdrawn_number`
now asserts on the payload `_plan_dict` actually returns, and it matches on `"longrun" in k or
"conditional" in k` across the whole payload rather than on one spelling — so the next retraction
cannot leave a differently named survivor behind.

Gates: 1670 SQLite, 1670 PostgreSQL on a dropped and recreated database, ruff clean. The new
assertion was confirmed RED against the shipped `f0e570c` bytes before the fix went in.

---

## PR 56 — the ops track, and the withdrawal of PR 55.1

Two things in one PR because they share nothing except the date, and splitting them would have
meant two deploys of the same day's work. The honest label for the second half is a **retraction**.

### 1. PR 55.1 is withdrawn, one deploy after it shipped

**What it claimed.** PR 55 showed `/plan.portfolio_volatility_pct` (44.62) sitting 23 points below
the realised `/portfolio/risk.annualized_volatility_pct` (67.48), and showed that this breaches a
bound: a NAV-weighted average of component volatilities is an upper bound on any portfolio built
from them, since `sqrt(w'Sw) <= sum(w.sigma)`. PR 55.1 then said *why*: each sleeve's backtested
volatility averages over days the rule was out of the market, so with an in-market fraction `f`
and flat days otherwise, `sigma_in = sigma_measured / sqrt(f)`. It put the rescaled figure in the
`/plan` headline, kept the blend beside it, and fed the rescaled one to the risk report so the
comparison would be like-for-like.

**Why it was accepted.** It reconciled. 68.02 against a realised 67.48 — half a point.

**Why that was not evidence.** The scaling needs the out-of-market days to be *flat*, and they are
not. `strategy_backtest._weights_or_off` hands the sleeve its `base` when the rule is off, and says
so in its own docstring: *"a swing rule is an OVERLAY -- it decides between the aggressive
instrument and the core holding, not between the aggressive instrument and being out of the
market."* Neither live sleeve declares `risk_off`. Measured on the shipped engine over
2016-09-06 → 2026-09-04 (2,514 sessions, adjusted closes):

| sleeve | in market | measured vol | **true** in-market vol | out-of-market vol | the formula said | error |
|---|---|---|---|---|---|---|
| `btm_swing_dip` (TQQQ/QQQ) | 15.6% | 33.91% | **67.97%** | **22.46%** | 85.96% | **+17.99** |
| `btm_trend_soxl` (SOXL/SMH) | 74.0% | 83.50% | **93.95%** | 40.97% | 97.05% | **+3.10** |

The variance decomposition closes (`f·σ_in² + (1−f)·σ_out²` = 33.84% against 33.91% measured), so
this is not a matter of interpretation. **The half-point agreement came from two errors of opposite
sign cancelling** — which is the specific reason this defect was worse than an ordinary one: it
manufactured its own confirmation.

**Not replaced with a corrected version, because a corrected one does not reconcile either.**
Blending the *true* in-market figures gives 60.01% against the realised 67.48% — below the
portfolio it is supposed to bound, which is impossible for a weighted average of the same
components over the same period. So a second difference is doing work and PR 55 named only one:
the components are **ten-year** backtests and the realised figure is **251 days**. Two numbers over
different windows need not obey a bound derived for one.

**What shipped instead.** `volatility_conditional_pct` / `_method` / `_floored`,
`EXPOSURE_FLOOR_PCT` and `_in_market_volatility` are removed. `/plan` publishes one volatility
again — the blend — and `volatility_strategy_longrun` is gone with the number that displaced it.
The blend now carries `volatility_window: "strategy backtests, full measured window"`, which is the
label whose absence let a ten-year average be compared against a 251-day measurement as though they
were rivals. `exposure_pct` **stays** on each component, re-commented as reported and not used to
scale anything: it is a true and useful fact about a sleeve, and the analysis that retired the
formula is worth being able to redo. `portfolio_risk.analyze` no longer calls the gap a
"contradiction"; it states the bound, names both candidates, and says which figure describes the
book held today.

**Blast radius while it was live: one deploy, and cosmetic.** `_portfolio_stats` feeds eight
screens, so the plan headline read 68.02 where it now reads 44.62. Nothing sized a trade from it —
`book_target.compose()` and the funding layer use weights and NAV, not this figure — and
`/portfolio/risk` computed its own covariance throughout, so VaR, beta and the goal projection were
never touched.

**Pinned by `tests/test_the_book_volatility_is_not_rescaled_by_exposure.py`** (9 tests). Five fail
against the reconstructed PR 55.1 code — the withdrawn keys, the floor constant, the `/plan`
headline, the window label and the note's wording. Four are structural pins that pass on both
sides and are there to make the *next* attempt notice: each live sleeve declares a `base` and no
`risk_off`, `_weights_or_off` returns that base rather than an empty book, and `exposure_pct`
scales nothing. A future strategy that genuinely sits in cash will declare `risk_off`, and those
tests will tell whoever adds it that the arithmetic differs for that one.

### 2. Ops — the four items that have been on the board four weeks

None of this is code the app runs. It is the part of the board that only Eran can close, written
down so it stops being re-derived every fortnight.

**`scripts/ops/ops-check.ps1`** — 14 read-only checks, each reporting `DONE` / `TODO` / `MANUAL` /
`UNKNOWN`, across branch protection (via an optional `$env:GITHUB_TOKEN`), the agent key, the VAPID
pin, `DATABASE_PUBLIC_URL`, the three rotations, and the deploy invariants that PR 49/52 installed.
It changes nothing and needs no secrets to run; the checks that cannot be answered without one say
`UNKNOWN` rather than guessing.

**`scripts/ops/OPS_RUNBOOK.md`** — the same items in the order they should be done, with branch
protection first because it is two minutes and the only one that *prevents* a class of incident
rather than reducing exposure to one. It records the fact that makes it urgent: `git push origin
main` deploys to production, so there is currently no step between a local commit and the live
site. The Postgres password rotation is explicitly recommended **not now** — it is the one item
whose failure mode is an outage, and it should not be done in the same week as a deploy-gate change.

**Untracked code, left untracked deliberately.** `app/services/candidate_search.py` and
`tests/test_candidate_search.py` (T6b) are still uncommitted and are **not** part of this PR; the
untracked-files row in the ops table above stands.

---

## PR 55.1 — the book's volatility is about the book, not the strategies

PR 55 found that `/plan` said 44.62% and the realised measurement said 67.48%, and that this
was a **contradiction**: the plan's figure is a weighted average of component volatilities,
which is an upper bound, and the realised figure was above it. This is why, and it is
**measured rather than inferred** — the backtests already store `time_in_market_pct`.

| sleeve | w% | backtest vol | in market | implied in-market vol |
|---|---|---|---|---|
| `btm_trend_soxl` (SOXL) | 35.09 | 83.95% | 73.4% | 97.99% |
| `btm_swing_dip` (TQQQ) | 35.22 | 34.24% | **15.6%** | 86.69% |
| `bal_6040` core | 26.77 | 11.59% | 100% | 11.59% |
| cash | 2.92 | 0% | — | 0% |

```
blend of LONG-RUN  vols = 44.62%   <- what /plan reported
blend of IN-MARKET vols = 68.02%   <- realised measured 67.48%
```

**Half a point.** `btm_swing_dip` holds TQQQ and sits in cash **84.4% of the time**, so its
34.24% is a ten-year average made mostly of flat days. The book is holding TQQQ right now, so
that sleeve has been contributing something near 87% volatility on a third of the book all
along, and no screen said so.

**The scaling, stated so it can be argued with.** If a strategy is in the market a fraction
`f` of the time and flat otherwise, the flat days contribute no variance, so the variance
measured over the whole window is `f·σ_in²` and **`σ_in = σ_measured / √f`**. It assumes
out-of-market days are genuinely flat — true when the sleeve sits in cash — and that in-market
days are homogeneous. The reconciliation above is the evidence it holds here. It is an
estimate, not an identity, and the test fails if it drifts more than two points from the
realised figure.

**Both numbers are true and they answer different questions.** ~68% is what the book is doing
today with its sleeves in the market; ~44.6% is what it will average over a year with
`btm_swing_dip` flat most of it. **Eran's decision: the headline is today's exposure**, because
that is what every other risk figure on the screen is about — VaR is a statement about the
book you hold this morning. `/plan.portfolio_volatility_pct` now carries the conditional
figure and `portfolio_volatility_pct_strategy_longrun` carries the other, labelled.

**Guards on the edges, because `σ/√f` is sharp:**

- An **absent** exposure is not an exposure of zero. A component with no `exposure_pct` — the
  cash line, or a backtest predating the field — keeps its measured number rather than being
  scaled by a guess.
- A component that is in the market **100%** of the time is untouched; scaling the core's
  11.59% would invent risk that is not there.
- Below `EXPOSURE_FLOOR_PCT` (10%) the scaling is **floored**, and `volatility_conditional_floored`
  says it bound. At 1% exposure the formula implies ten times the measured volatility on a
  measurement resting on a handful of days — that is arithmetic, not evidence, and a silent
  clamp is a lie the size of the difference.
- Only a **measured** volatility is conditioned. A static instrument figure already describes
  buy-and-hold, so scaling it would invent exposure the number never averaged over.

**The observable proof.** `portfolio_risk_service` now passes the conditional blend as
`assumed_volatility_pct`, so PR 55's contradiction check compares like with like — and
`volatility_exceeds_assumption` should go **false** on the next deploy. That flag going quiet
is the confirmation the conditioning was the missing piece; if it stays true, the scaling has
stopped describing this book and the headline is wrong again.

Gates: 1667 passed on SQLite, 1667 on PostgreSQL, `ruff check app tests` clean.

---

## PR 55 — the forward numbers say what they are

Two numbers the app presents as forecasts, neither of which stated its basis. Measured on
production `6044124`, one book, one moment.

### 1. The goal projection extrapolated the trailing year

```
POST /api/v1/portfolio/risk
  annualized_return_pct   107.08
  goal: {"prob_reach": 0.9604, "median_terminal": 1,407,623, "p90_terminal": 9,601,434}
```

`index.html:2890` renders the first of those as **"Reach ₪102,973 in 5y — 96%"**, on a
**₪21,208** book. The drift was `port_rets.mean() * 252` — the last 251 trading days
annualised — pushed five years through a GBM. A 3× leveraged NASDAQ-and-semiconductor book
after a good year is the worst possible input for that, and the number it produced was the
most confident thing on the screen.

**The plan already held a stated forward return, with provenance and a caveat**:
`expected_return_pct` 22.18, `return_basis` `measured_full`, 97.1% of the book measured, and
a caveat reading *"A measured CAGR is what happened over one window, not what will happen."*
Nothing read it. `analyze` now takes `drift_pct` and builds the projection from that and
nothing else.

**`None` means no projection, not a fallback.** The old fallback *was* the trailing mean, so
keeping it as a fallback would preserve the defect and hide it better. The payload omits
`goal` and says why in `goal_omitted_reason`. The trailing figure is still reported, as
`trailing_return_pct` — a name that cannot be read as a forecast — because hiding it would
trade one unstated basis for another.

**The convention decision, which moves the answer more than the fix does.** `drift_pct` is a
**CAGR**: `measured_assumptions` builds each component's return from `metrics["cagr_pct"]`
(line 365) and its caveat calls it one. A CAGR is already geometric — the −½σ² drag is inside
it — so it is compounded directly and the drag is **not** applied twice. Subtracting it anyway
would have been the conservative-*looking* mistake: σ is 67.5%, so ½σ² is 22.8%, which would
have cancelled the entire 22.18% assumption and put the median five-year outcome below today's
NAV for arithmetic reasons rather than for anything measured. Asserted, not commented.

What production will report after this ships:

| | before | after |
|---|---|---|
| `prob_reach` | 0.9604 | **0.3683** |
| `median_terminal` | ₪1,407,623 | **₪63,003** |
| `p10_terminal` | ₪204,801 | **₪9,166** |
| `p90_terminal` | ₪9,601,434 | **₪429,767** |

### 2. The plan's volatility is not a portfolio volatility

`measured_assumptions.blend` computes `Σ(w·σ) / Σw` — a NAV-weighted **average** of component
volatilities. Reconciled exactly against the live components:

```
35.09% x 83.95 + 35.22% x 34.24 + 26.77% x 11.59 + 2.92% x 0.00 = 44.62
```

Expected return is linear, so blending returns that way is correct and 22.18 checks out the
same way. **Volatility is not.** Portfolio volatility is `sqrt(w'Σw)`, and a weighted average
of component volatilities equals it only when every pair is perfectly correlated — otherwise
the average is strictly larger. It is an **upper bound**, not an estimate. The payload now
says so: `volatility_method: "weighted_average"`, `volatility_is_upper_bound: true`.

### 3. Which makes plan-vs-actual a contradiction, not a difference

| | | |
|---|---|---|
| `/plan.portfolio_volatility_pct` | **44.62** | upper bound |
| `/portfolio/risk.annualized_volatility_pct` | **67.48** | realised, real covariance |

**The realised volatility exceeds the upper bound.** Under those component volatilities the
book *cannot* be more volatile than 44.62; it measured 67.48. So at least one component
understates what the book is doing — which is what you would expect when a sleeve's ten-year
backtest includes every period it sat in cash, and the book is holding the leveraged names
right now. Two honest measurements of two different questions, and nothing said which question
was being answered.

The reconciliation lands on the risk endpoint rather than `/plan`, because `/plan` has no price
history and giving it some would put N provider fetches on a hot endpoint. The risk call
already reads the plan and already computes the covariance, so it is the one place the two can
be compared without paying for anything twice: `assumed_volatility_pct`,
`volatility_exceeds_assumption`, and a `volatility_note` that states the bound argument.

`book_assumptions`'s own docstring already reached for half of this, about the core: *"a
per-ticker table has no covariance term — so it overstates the core's volatility by
construction."* True of the whole blend.

**One existing test asserted the behaviour removed here** and is rewritten rather than deleted:
`test_goal_probability_present_with_target` passed `nav`, `target` and `years` and expected a
probability — and got one, built from the trailing mean of the history it had just generated.
It now asserts both halves, because the refusal is the part that would regress quietly.

Gates: 1661 passed on SQLite, 1661 on PostgreSQL, `ruff check app tests` clean.

---

## PR 54.1 — two cards for one ₪972, and the wrong one could not do what it said

**A defect PR 54 introduced, found on production `6044124` within minutes of it shipping.**
I predicted the "raise ₪972" card and did not check what *else* renders an asset class.
Putting a `Cash` key in the book target for the first time reached renderers that had never
been asked to draw a class you cannot buy.

**Card 1, correct** — `liquidity`, core scope: *"Raise ₪972 of cash — Sell 4 BND (~₪972) to
rebuild your cash floor. cheapest way to raise it: Fixed Income is the most overweight class
(11% against an 8% target) and BND is among its least-taxed positions."*

**Card 2, wrong twice over** — `allocation`, book scope: *"Buy Cash — Buy about ₪971 of Cash
to move toward 8%"*, `how: Buy Cash by ~₪971 — e.g. BIL`.

1. **BIL is a T-bill ETF. Buying it spends cash.** The card's stated purpose is to raise the
   cash weight. `CLASS_ETF["Cash"] = "BIL"` had sat in the source unreachable since it was
   written, because no book target had ever carried a Cash key.
2. **It cannot do what it claims even in principle.** Its apply spec is
   `{"kind": "rebalance_to_objective", "asset_class": "Cash"}`, and `_rebalance_to` drops
   cash rows from the classes it moves — with a comment ending *"Turning cash into securities
   is a purchase and belongs to the funded-buy path."* Pressing Accept reshuffles Equities and
   Fixed Income and moves the cash weight by **nothing**. That is the card-claim invariant.

Both cards quote the same ₪972 because PR 54 made the floor equal the target, so two
independent generators measure the same gap against the same number.

**A second one, found in the sandbox while reproducing the first.**
`_holding_verdict_recs` walked every row and rated the cash balance like a security: *"CASH
screens well (fundamentals 80/100) and is only 1% of your book — adding on weakness is
reasonable."* It has never reached production only because Yahoo returns no fundamentals for
`CASH`, so the score is `None` and the loop skips it **by accident**. An accident is not a
guard; the day the provider changes, it is a card telling you to buy more of your own bank
balance.

**The Cash target still reaches `AllocationEngine.compute`, and must** — it is what shrinks
the investable legs. What is dropped is the CARD.

**The first version of the test passed against the bug**, and that is worth recording.
It asserted on `apply.asset_class`, and `_reconcile` had merged four legs into one card whose
top-level class was `Equities`, with `Buy Cash (~₪4,271)` sitting inside `apply.legs` and
`meta.merged_legs`. **A guard that reads only the summary of a merge does not guard the
merge.** The shipped version walks the legs.

RED gate taken with PR 54 in place and only these two guards removed, so the failure is the
defect and not a missing column:

```
'Rebalance toward your target mix': ['legs: buy 4270.95', 'merged_legs: Buy Cash']
'Buy more: CASH': 'CASH screens well (fundamentals 80/100)...'
```

Gates: 1655 passed on SQLite, 1655 on PostgreSQL, `ruff check app tests alembic` clean.

---

## PR 54 — the book can hold cash on purpose

**Eran's decision, 2026-09-04: target 7.5% cash as dry powder — *"so I'd be able to buy
something if there is a good opportunity."*** This is the implementation, and the two
judgement calls inside it are stated rather than buried.

**What it replaces.** Cash was not in the book target at all, so its target was zero by
omission — the same silent-zero trap `targets_for` was written to avoid one layer down.
`/api/v1/mix` on `c93ceb9`: `target_allocation {"Equities": 0.892, "Fixed Income": 0.108}`,
`current_allocation` carrying `"Cash": 0.0292`, drift `+0.0292`, `rebalance_required: false`.
₪618.65 — twice `MIN_TRADE_ILS` — sat there because a class the target never modelled cannot
drift.

It was quietly worse than "no opinion": `CASH_FLOOR_PCT` floors a `Grow` book at 3%, so
`spendable_cash` was already returning zero. **The app had spent the reserve down to its own
limit and nothing on any screen called that a decision.**

**Judgement call 1 — the reserve comes out of the CORE, never the sleeves.** `compose` builds
the target as sleeves + core, core being the remainder. Making room by scaling everything by
0.925 would silently manage a sleeve the user set at 38.4% of NAV to 35.5%, which is the
defect `book_target`'s own docstring exists to record: *"The app was telling him to destroy a
sleeve it had set itself."* So the reserve comes out of the part of the book he delegated,
not the part he specified. On the live book: sleeves 73% untouched, remainder 27% → cash 7.5%
+ core 19.5%, and `bal_6040` at 60/40 across 19.5 gives Equities 11.7 / Fixed Income 7.8.

| | weight |
|---|---|
| Equities (0.73 sleeves + 0.117 core) | 0.847 |
| Fixed Income (core) | 0.078 |
| Cash (the reserve) | 0.075 |
| | **1.000** |

**Judgement call 2 — the floor IS the target.** A reserve routine funding may nibble into is
not a reserve. `cash_floor_pct` now reads `plans.cash_target_pct`, so cards may not fund
themselves out of the powder and deploying it is a deliberate act. This is why
`spendable_cash` correctly returns **0 at target**; trims still fund buys, the reserve does
not. Two numbers if that is ever wrong.

**A dead branch found on the way.** `cash_floor_pct` read `getattr(plan, "cash_floor_pct")`
— a column `Plan` has never had — so it returned None on every call in production and the
objective table was the only answer the function could give. Its test passed for months
against a `SimpleNamespace` that fabricated the field: **a fixture that invents what it is
testing proves the branch, not the behaviour.** Both now name the real column.

**What this changes on screen, in the opposite direction from today.** Against the new target
the book is 4.6 points UNDER on cash (≈₪972) and 2.9 points OVER on Fixed Income. Today says
there is nothing to do; with the reserve it should say to raise about ₪972, mostly out of BND.
That is what asking for dry powder means, and it is written here so it is a consequence
someone chose rather than one that appeared.

Storage: `plans.cash_target_pct`, a PERCENTAGE (7.5) like every other `_pct` column on that
table, converted to a fraction exactly once in `book_target`. Nullable, and **null is not
zero** — null means nobody has been asked, zero means someone chose none. Migration `0021`,
guarded, and **mirrored into the `main.py` ALTER list**, which is what actually reaches
production until the pre-deploy step is confirmed. Bounded at the API (`ge=0, le=50`) so a
750 typed for 7.5 is a 422 rather than a clamp nobody sees.

Gates: 1650 passed on SQLite, 1650 on PostgreSQL, `ruff check app tests alembic` clean.

**This PR is also the decisive test of PR 52.** Migration `0021` is the first new revision
since the pre-deploy step shipped. If that step runs, `/health.schema_revision` moves
`0020_solver_verdicts → 0021_plan_cash_target`. If it does not, `create_all` and the ALTER
mirror still add the column and the stamp stays at `0020` — the app works either way, and the
field says which happened. The ship script asserts it.

---

## PR 53 — the risk report was denominated in dollars

**Found while pulling live numbers to answer the open volatility decision, which is the
decision it turns out to have been corrupting.** `POST /api/v1/portfolio/risk` returned
`"nav": 7437.82` on a book `/api/v1/portfolio` values at **₪21,207.78**, at the same moment,
on commit `6f3cc4f`.

**Root cause, PROVEN by arithmetic reconciliation rather than inferred.** `portfolio_risk`
sized every holding as `quantity x current_price` and stopped. `current_price` is in the
position's TRADING currency. Drop the rate and add the same five rows:

| | quantity | price | unconverted | as `/portfolio` values it |
|---|---|---|---|---|
| TQQQ | 34.3489 | 72.03 USD | 2,474.15 | 7,470.20 |
| SOXL | 23.0906 | 106.74 USD | 2,464.69 | 7,441.64 |
| VTI | 2.9716 | 380.93 USD | 1,131.87 | 3,417.76 |
| BND | 10.4040 | 71.93 USD | 748.36 | 2,259.52 |
| CASH | 618.6500 | 1.00 **ILS** | 618.65 | 618.65 |
| | | | **7,437.72** | **21,207.78** |

The report said **7,437.82**. The ten agorot are the displayed prices rounding. That is an
identity, not a resemblance.

**Blast radius — four consequences, in ascending order of how much they matter:**

1. `var_95_1d_ils` is `var95 * nav`, so the shekel loss on screen read **₪480** against a
   real 6.45% of ₪21,207.78, which is **₪1,368**.
2. The Monte Carlo starts at `nav`, so `median_terminal`, `p10_terminal` and `p90_terminal`
   were all scaled by 0.35.
3. `prob_reach` compares that scaled distribution against the plan's **real** shekel target.
   It is the one number the error made *pessimistic*, and it is the one the UI renders as a
   percentage (`index.html:2890`).
4. **The weights were wrong for cash, and only for cash.** Dropping the rate is a common
   factor across the four USD names, so their weights relative to one another survived it.
   CASH is already in shekels, so it was scaled up by a rate it never needed: **8.3% of the
   risk book against 2.9% of the real one.** Cash has no volatility, so an over-weighted cash
   line drags portfolio volatility and beta *down*. **The endpoint was understating the risk
   it exists to measure** — which means the 63.85% volatility reading, already 2.5x the
   book's own 25% cap, was the optimistic version.

**Why it was silent.** Every figure it produces is internally consistent; the only external
cross-check is NAV, and `report["nav"]` is not displayed beside the portfolio NAV on any
screen.

**The fix is a call, not a formula.** `fx.position_value_ils` already exists, and its
docstring already anticipated this: *"Five call sites computed
`quantity * current_price * fx_rate(...)` inline. The formula is not the hard part; agreeing
on it is. A money invariant that measures the book with a different expression from the one
the user is shown is not an invariant."* This was the sixth call site and the one that
disagreed. It is now offloaded as well, per PR 50 — `fx_rate` reaches `guarded_fx`
underneath.

**Found and NOT fixed here**, because PR 50's guard has a blind spot this exposed: fourteen
`async def` sites call `fx_rate` / `fx_rate_with_status` directly, which reaches the same
blocking provider one level down. The AST guard matches `guarded_*` by name and does not
follow the wrapper. Lower stakes than PR 50 — the USD/ILS rate is usually served from the
breaker's cache, so the blocking window is small — but it is the same fault, in money-moving
code, and it deserves its own gated PR rather than a rider on this one.

**Also in this PR:** `scripts/deploy/ship-pr50-the-loop-stays-free.ps1` called
`/portfolio/risk` as a GET. It is a POST route. The first run threw 405 there and aborted the
smoke before the concurrency check ran — the deploy itself was already verified by then, so
nothing was at risk, but a smoke that stops early reports less than it claims.

Gates: 1641 passed on SQLite, 1641 on PostgreSQL, `ruff check app tests` clean.

---

## PR 52 — the migration retry, as a pre-deploy step

**Approved by Eran on the recommendation, with the root cause still UNKNOWN.** The deploy log
that held the answer is gone, so this changes the MECHANISM rather than guessing at the cause,
and it is designed so that every surviving hypothesis is contained rather than tested on
production.

**What was established after the revert, and inverts the original diagnosis:** the PR 47
migration **ran to completion**. `/health` returned `schema_revision: "0020_solver_verdicts"`
afterwards; nothing in `app/` writes `alembic_version` (health.py only reads it); and the
reverted boot logs `Ensuring database schema (auto_create_tables=True)` with no alembic line.

RULED OUT, each with the observation that killed it:

| candidate | killed by |
|---|---|
| the `&&` chain itself | re-run exactly as deployed against an empty database: `sh -c 'alembic upgrade head && echo ...'` exits **0** and continues |
| `alembic/env.py` importing the app | read it — imports only `app.core.config`, `app.models.base`, `app.models`; no `app.main`, no scheduler, disposes its engine |
| the new `/health.schema_revision` field | `/`, `/docs`, `/openapi.json` and `/health/ready` **all** returned 502, so no handler ran at all |

STILL OPEN, and not distinguishable without the lost log: alembic exited non-zero at
interpreter teardown despite doing the work (an unraisable at shutdown does exactly that, and
`&&` swallows the boot); or the process never exited, so the chain never advanced.

**How each is contained:**

- `preDeployCommand` runs the migration as **its own step with its own log**. The missing
  artifact is precisely what ended the last investigation.
- `preDeployTimeoutSeconds: 300` turns "the process never exited" from an unbounded hang into
  a failed deploy at a known time.
- `healthcheckPath` (PR 49) means a container that does not answer never takes traffic. A
  repeat of PR 47 is now a **failed deploy with production still up**, not an outage.
- The database is already stamped at `0020_solver_verdicts`, so `alembic upgrade head` is a
  **no-op** on this run. The first attempt tests the mechanism against a database that needs
  nothing done to it.

**Rehearsed** against a production-shaped postgres:16 — schema built by `create_all` plus the
`main.py` ALTER list, then stamped at head, which is the state PR 47 left production in:

```
$ sh -c 'alembic upgrade head && alembic current'
0020_solver_verdicts (head)
PRE-DEPLOY EXIT CODE=0
```

`alembic current` is chained after the upgrade so the log carries a positive statement of
where the database ended up rather than only the absence of an error.

**`AUTO_CREATE_TABLES=false` is deliberately NOT in this PR, and the ordering is the reason.**
Today `create_all` plus the `main.py` ALTER list *is* what maintains production's schema.
Turning it off in the same push that first tries the pre-deploy step would mean that if the
step fails, production boots with nothing maintaining the schema at all — and, worse, two
changes landing together makes the result unattributable. It goes in the push **after** a
deploy log has shown the pre-deploy step running clean. Dockerfile hardening waits for the
same reason: it changes the image, which adds a variable to a retry whose cause is unknown.

Gates: 1639 passed on SQLite, 1639 on PostgreSQL, `ruff check app tests` clean.

---

## PR 51 — the working tree stopped hiding things

`git status` carried fifteen untracked entries, some of them for weeks. That is not
untidiness, it is the failure mode `.gitignore:17-22` is already written about: *"git status
--short returned an EMPTY view while five files sat staged, which was very nearly reported as
a clean tree."* A status listing nobody reads is a status that cannot warn anybody.

**Tracked**, because they are the record of what was actually run against this book: ten
one-off scripts under `scripts/` — `commit-t0`, `fix-t3`, `portfolio-restructure`,
`ship-t0`–`ship-t3`, `ship-t6b`, `smoke/smoke-sleeves`, `verify-t03` — and
`EXECUTION_PLAN_2026-08-29_v3.md`, which was the only plan in the series not tracked while
V5 and V6 both were. All ten were scanned for credentials before tracking: no key, token or
password literal, and `scripts/.agent-key` appears in none of them (it is read at runtime and
git-ignored by `.gitignore:31`).

**Ignored and deleted**: `iw-src.tgz` and `iw-src-sync.tgz`. Both are `git archive` snapshots
made to move the tree to a sandbox — 1.7 MB each, byte-identical to commits already in the
history, and worth nothing that `git archive` cannot reproduce in a second. `*.tgz` and
`*.tar.gz` are now ignored.

**Deliberately left untracked: `app/services/candidate_search.py` and
`tests/test_candidate_search.py`.** That is an open decision (land or discard) and it is
Eran's, not mine — see `EXECUTION_PLAN_2026-09-03_v6.md`. Recording it here so the two
remaining `??` lines are an answer rather than a question: everything else in that listing
has been resolved, so anything new that appears is genuinely new.

---

## PR 50 — one slow provider call was an outage for every other request

**Root cause, PROVEN by reproduction on 2026-09-04 against `6f3cc4f`.** `guarded_quote`,
`guarded_fx`, `guarded_history`, `guarded_events` and `ResearchAgent().scan()` are plain
`def` functions that make **synchronous** network calls. Thirteen call sites invoked them
directly from inside `async def`. On one uvicorn worker that holds the event loop for the
whole upstream round trip, so while any one of them waits the app answers **nobody**.

In-process, with the market provider held at 2.0 s:

```
 quote:  2.00s  HTTP 200
health:  2.01s  HTTP 200
BLOCKED: /health waited 2.01s behind a 2.0s provider call. The loop was not free.
```

**RULED OUT — "httpx's ASGITransport serialises requests, so this measures the harness."**
Killed by a control run: the same 2.0 s wait placed behind `await asyncio.sleep` instead of
`time.sleep`, same transport, same loop.

```
  awaited: slow  2.01s   /health  0.00s
 blocking: slow  2.00s   /health  2.01s
```

The transport is concurrent. The loop was not. That control is kept in the suite as
`test_the_harness_is_concurrent`, so a future httpx cannot turn the real test into a
tautology that passes for the wrong reason.

**Not a new fault, a missed sweep.** `app/core/offload.py` exists for exactly this and its
docstring already carries the production measurement that motivated it — `/plan` alone
0.38–0.52 s, `/plan` issued while `/recommendations` was in flight 2.09 / 2.22 / 3.05 s,
finishing the instant the loop was released. #15 Phase B moved the **agents** off the loop
and left the provider calls made straight from handlers and services.

**Blast radius, and why it was silent.** It never produced a wrong number, only a stalled
one, and a stall is indistinguishable from a slow upstream from the outside — which is
precisely how the original was found by measurement rather than by report. The worst site
was not an endpoint: `pricing_service.refresh_all_positions` is the **scheduled** refresh
running in-process, so a refresh over N positions held the loop for N round trips and the
app went unresponsive on a timer rather than under load.

**Found by AST walk, not by grep.** The first sweep matched `scan` on the method name and
produced four false positives — `LagEngine().scan(observations)` is arithmetic over a list
already in memory and belongs on the loop. The guard now requires the receiver to be
`ResearchAgent()`. Grep is what produced PR 42's non-fix; this class of change gets a tree
walk.

**Thread safety was already settled.** `recommendations.py` has offloaded `_buy_ideas` —
which hits the same providers through the same circuit breakers — since #15 Phase B, so
provider calls already run on worker threads in production.

Fixed at all thirteen sites plus the Yahoo fallback in `pricing_service`, which the guard
does not catch and is the same call by another name. Guarded by
`tests/test_the_loop_stays_free_while_a_provider_answers.py`: two behavioural tests and an
AST walk that fails with a file-and-line list. Gates: 1634 passed on SQLite and 1634 on
PostgreSQL with this change alone; **1637 and 1637 with PR 49's three deploy tests in**,
which is what a run after PR 49 lands will show. `ruff check app tests` clean throughout,
and the PostgreSQL database was dropped and recreated before the run — a dirty one left by
an interrupted session has produced 46 false failures on card-generation state before.

---

## PR 49 — a broken deploy could replace a working one, and did

**The gap, stated plainly: nothing in the pipeline asked the new container whether it
answers before the old one was retired.** PR 47 shipped a start command production could
not survive; every endpoint returned 502 for two hours; and Railway's card read
**"Deployment successful"** throughout, because that card reflects build-and-deploy, not
whether anything responds. The only honest signal in the whole episode was the ship
script's own `/health` wait — and by the time it spoke, the healthy container was gone.

**Railway already had the gate and this service was not using it.** From their healthcheck
reference: *"Railway will query the endpoint until it receives a successful response (any
2xx status code). Only then will the new deployment be made active and the previous
deployment inactive,"* and if it never passes, *"the deployment will be marked as failed
after the timeout expires."* `healthcheckPath` and `healthcheckTimeout` are both real keys
under `deploy`, verified against the live `railway.schema.json` on 2026-09-04.

**So PR 47 would have failed its own deploy and left the working container serving.** The
outage was a bad start command shipped through a pipeline with no gate; only the first half
of that has been addressed until now.

`healthcheckPath` is `/health/ready`, not `/health`. That distinction is the whole point and
is asserted in the suite rather than assumed: `/health` answers 200 with no database at all,
by design — `schema_revision()` swallows its own error because a liveness probe that fails
on a side query is worse than the thing it reports. A probe that cannot fail cannot gate
anything. `tests/test_a_broken_deploy_never_takes_traffic.py` pins three things: the key is
set, the path is a route this app actually serves (a 404 is not a 2xx, so a typo would fail
*every* deploy), and the path goes red when the database is unreachable. Gates: 1637 passed
on SQLite, 1637 on PostgreSQL, `ruff check app tests` clean.

`scripts/deploy/verify-deploy.ps1` is the second half, and the reason it is one file rather
than a paragraph copied into each ship script: Railway's healthcheck cannot tell you *which*
commit answered, because a healthcheck passing on the **previous** container looks identical
from outside. The script waits for `/health.commit` to equal the SHA just pushed, then asks
`/health/ready` and reports `schema_revision`. It throws rather than warns.

**What this does not do:** it does not make the migration safe to retry. That root cause is
still unknown and the deploy log that held the answer is gone. What changes is the cost of
finding out — the next attempt fails its healthcheck and leaves production up, instead of
taking it down for two hours.

---

## PR 48 — the revert: production could not survive the boot migration

**PR 47 took production down for two hours.** Every endpoint — `/`, `/docs`,
`/openapi.json`, `/health`, `/health/ready` — returned
`502 Application failed to respond`.

`alembic upgrade head && uvicorn` means a failed migration stops the boot. The
migration did not complete against production, and the `&&` did exactly what it
was written to do. **The failure mode worked; the migration did not.**

**Railway reported "Deployment successful."** That reflects build-and-deploy, not
whether anything answers — Initialization, Build, Deploy and Post-deploy all
green on a service returning 502 to every request. The ship script's own
12-minute `/health` wait was the check that was right, and the dashboard was the
one that misled. *Worth remembering the next time a deploy card is used as
evidence.*

**Ruled out on evidence, not by elimination:** the new `/health.schema_revision`
field. If that query had been hanging, `/health` would stall while everything
else served. `/`, `/docs`, `/openapi.json` and `/health/ready` returned 502 too,
so the process was not listening and **no request handler ran at all**. The field
is kept — it costs nothing while the migrations are not running, and it is how
the next attempt gets verified rather than hoped at.

**CORRECTION, 11:20 GMT+3 — the migration SUCCEEDED.** Written before this was
known, this entry said the migration "did not complete". It did.

```
/health after the revert:
  "commit": "6f3cc4f", "schema_revision": "0020_solver_verdicts"
```

Nothing in `app/` writes `alembic_version` — `health.py` only reads it — and the
reverted boot does not run alembic (its log shows `Ensuring database schema
(auto_create_tables=True)` and no alembic line). So **the only thing that can
have stamped that row is PR 47's own migration step, and it ran to head.**

That inverts the diagnosis. The migration is not the broken part. Production's
schema is now stamped at `0020_solver_verdicts` for the first time ever, by the
deploy that took the site down. What failed is what happened *after* it: the
`&&` never reached uvicorn, or uvicorn never bound.

**ROOT CAUSE: UNKNOWN.** Same treatment as the `+739` mint and the TEVA agorot
test. What is established:

* The chain runs clean **twice** against a production-shaped postgres:16 in the
  sandbox — `create_all`-built, never stamped, no `alembic_version` row — landing
  on `0020_solver_verdicts`. That was verified before PR 47 shipped and again
  after.
* **RULED OUT — the migration failing.** Production is stamped at head, so it
  ran and finished. Every candidate about *what the migration might have hit*
  (a hand-patched schema, 0018's constraint pair, a lock) is dead with it.
* **RULED OUT — `/health.schema_revision` hanging.** `/`, `/docs`,
  `/openapi.json` and `/health/ready` returned 502 too, so no handler ran.
* **RULED OUT — the shell chain itself.** Re-run in the sandbox as the deploy
  ran it: `sh -c 'alembic upgrade head && echo ...'` on an empty database exits
  **0** and the chain continues. `alembic upgrade head; echo $?` also gives 0.
* **STILL OPEN**, and one of these: alembic exited non-zero at teardown despite
  having done the work (an unraisable at interpreter shutdown would do it, and
  `&&` would then swallow the boot); or the process did not exit at all and the
  chain simply never advanced; or Railway does not run `startCommand` through a
  shell that honours `&&`. The first two are indistinguishable from outside and
  the third is checkable in one line of their docs.
* **The failed container's deploy log is still the only artifact that separates
  them**, and that deployment has been replaced. If Railway has kept it under
  HISTORY for commit `d2dc681`, it decides this in thirty seconds.
* **The deploy log is the only place the answer exists and it has not been read.**
  No speculative fix, and no second attempt before it has been.

**Tests: three removed, two kept.** The three that pinned the migration into the
start command went with the revert — *a test that demands a behaviour production
cannot survive is not a guard, it is a countdown.* Kept are the two that never
depended on the decision: `railway.json` and the Dockerfile `CMD` must agree, and
**if** the boot migrates it must chain with `&&` rather than `;`.

That agreement test caught itself during the revert: Docker's exec form is a JSON
array, so the words are present and the spaces are not, and a substring match for
`uvicorn app.main:app` failed against a Dockerfile that was correct. It parses
the array now.

**Still true, and still why PR 46 and PR 47 exist.** Nothing runs the migrations
against production. Its schema is maintained by `create_all` at boot plus the
hand-maintained `ALTER TABLE ... IF NOT EXISTS` list in `main.py`, and that
arrangement has already cost ten days of silently missing measurements when
migration 0015 was never mirrored into it. The problem is unchanged; only the
attempt is withdrawn.

Not financial advice.

---

## PR 47 — the migrations run where the database is reachable

**Nothing ran them, and nothing could.** `railway.json` started uvicorn and only
uvicorn; the Dockerfile said the same. So on every deploy since the first, the
only thing shaping production's schema was `create_all` at boot plus a
hand-maintained list of `ALTER TABLE ... IF NOT EXISTS` statements in
`main.py` — whose own comment concedes the position:

> *"This list IS the mechanism that keeps production's schema current, so a
> migration that adds a column and does not appear here has not really shipped."*

**That list exists because alembic could not be run against production at all.**
The comment records both attempts: once against the sqlite URL in a local
`.env`, once on DNS, because Railway's `DATABASE_URL` names a private host that
does not resolve from outside their network. The cost is recorded in the same
block — `strategy_backtests.benchmark_ticker` was added by migration 0015 on
2026-08-15, never mirrored into the list, and every read of that table failed
for **ten days** while the app degraded silently to *"no strategy has ever been
measured"*.

The database is reachable from exactly one place: **inside the deploy.** So the
migrations now run there, ahead of uvicorn, chained with `&&` so a failed
migration stops the boot rather than starting an app against a schema it does
not have. `railway.json`'s `startCommand` is what Railway uses and the Dockerfile
`CMD` is kept identical, so `docker run` reproduces the deploy instead of
quietly skipping the step.

### Proven end to end before shipping, on postgres:16

```
1. booted the app the way production boots  (create_all + the DDL list, no alembic)
   /health -> schema_revision: None
2. alembic_version table in that database   -> 0 rows.  Never stamped.
3. ran the deploy's new migration step      -> alembic_version = 0020_solver_verdicts
```

Step 1 is the shape production is in right now. Step 3 is what the next deploy
will do to it. This is only safe because PR 46 made the chain runnable from
nothing and idempotent — the first run executes 0001 through 0020 against a
database `create_all` already built, and every revision guards itself.

### `/health` now answers the question

`schema_revision` is the companion to `deployed_commit()`, added for the same
reason that one was: the deploy's schema state could only be checked by eye, and
here it could not be checked at all. **`null` is a real answer, and it is the one
production gives today** — the migrations have never run against it. It never
raises: a liveness probe that can fail on a side query is worse than the thing it
reports.

That field is what makes the *next* step checkable rather than hopeful.
`AUTO_CREATE_TABLES` and the `main.py` DDL list are deliberately **left in place**
for this deploy — they are the backstop while the migration step proves itself.
Removing them is the PR after this one, and it should not happen until
`/health` has said `0020_solver_verdicts` out loud.

**Tests:** `tests/test_the_deploy_runs_the_migrations.py`, four cases, all
confirmed failing against `cce75af` first — the migration runs, it runs
*before* the server, it is chained with `&&` and not `;`, and the Dockerfile
does not drift from `railway.json`.

**Gate:** SQLite **1632 passed / 0 failed / 0 skipped**; Postgres **1632 passed /
0 failed / 0 skipped**; `ruff check app tests` clean.

**Smoke:** for once this one asserts something production could not previously
say. `/health.schema_revision` must go from **absent** to **`0020_solver_verdicts`**.
If it comes back `null`, the migration step did not run and the deploy log is
the next thing to read.

Not financial advice.

---

## PR 46 — the schema can be built from nothing again

**Reproduced before it was fixed**, on postgres:16 against an empty database:

```
$ alembic upgrade head
sqlalchemy.exc.ProgrammingError: DuplicateColumnError:
  column "subject" of relation "transactions" already exists
[SQL: ALTER TABLE transactions ADD COLUMN subject VARCHAR(255)]
```

**The cause was already written down in this repo six times.** 0013, 0014, 0015,
0016, 0019 and 0020 each carry a comment saying it: `0001_initial` runs
`Base.metadata.create_all`, so a fresh database is built from **today's** models
and already has every column the later revisions add. Each of those revisions
guards itself. `0018_transaction_ledger` shipped with **ten DDL statements and no
guard**, and one unguarded revision is enough — alembic stops there and never
reaches the rest.

**A second break, which only the test found.** With the duplicate columns
guarded, the chain still died on SQLite:

```
sqlite3.OperationalError: near "ALTER": syntax error
[SQL: ALTER TABLE transactions ALTER COLUMN position_id DROP NOT NULL]
```

SQLite has no `ALTER COLUMN`. On a fresh database the column is **already**
nullable — `create_all` built it from a model that has said so since this
revision was written — so the statement was not only unsupported, it was
unnecessary. It now asks first, and falls back to `batch_alter_table` where a
real rebuild is needed.

**Guarded per statement, not with one early return**, because the database this
has to survive is not only the empty one. Production's schema was built by
`create_all` on boot, so "has this column but not that index" is a realistic
state and a whole-revision skip would step over the half that is missing.

**Why nobody noticed.** `AUTO_CREATE_TABLES=true` in production is what made this
survivable and also what made it invisible: the app builds its own schema on
boot, so the migrations were never run and never found out they could not run.

**Tests:** `tests/test_the_migration_chain_runs_from_nothing.py` — two cases,
both confirmed failing against `bddba29` first. They run the real
`alembic upgrade head` in a subprocess against a scratch SQLite file, nothing
stubbed, and the second runs it **twice**, because a redeploy does. Verified
separately on postgres:16: `upgrade head` twice from empty, landing on
`0020_solver_verdicts (head)`.

"The migrations work" is not something a reader can check by reading, which is
why it is a test and not a note.

**Next, and only in this order.** Flipping `AUTO_CREATE_TABLES` to false is the
PR after this one — the whole point of the ordering is that the guards have to be
live in production *before* the app stops building its own schema. Then the
Dockerfile.

**Gate:** SQLite **1628 passed / 0 failed / 0 skipped**; Postgres **1628 passed /
0 failed / 0 skipped**; `ruff check app tests` clean.

**Smoke:** read-only, and it proves nothing about this change on purpose.
Production runs `create_all` on boot and does not run alembic, so there is no
migration behaviour to observe there yet — that is exactly the problem the next
PR closes. The tests are the proof; the smoke confirms the deploy is healthy and
the book did not move.

Not financial advice.

---

## PR 45 — intake wrote a price it could not explain, and one of them was 100x

Two faults at one boundary, found together because they are the same mistake:
**intake wrote a number without writing what the number meant.**

### The 100x, and why it was invisible

`to_major_units` says *"Use at every boundary"* in its own docstring, and exactly
one boundary used it. Yahoo quotes Tel Aviv in **agorot** and says so in the
CURRENCY CODE rather than in the price — `TEVA.TA` comes back as `11030.0` with
currency `ILA`, meaning 110.30 shekels. `add_holding` took `.price` and threw
`.currency` away.

What makes this worse than a wrong price is that **it cancelled out at the moment
it was made**:

```
budget 1,000 ILS / 11,030 = 0.0907 shares booked at 11,030 each  ->  NAV 1,000
```

Nothing downstream disagreed either: `MARKET_CCY["TASE"]` is `"ILS"`, and the row
carried no currency stamp to contradict it. Then the **next scheduled price
refresh** wrote the correct major-unit price of 110.30 against those 0.0907
shares, and the holding was worth **₪10**. An error invisible until a job that
did nothing wrong exposed it — the mirror image of PR 32, where a bad quote
self-healed in thirty minutes.

### The provenance

`upsert_positions` did `row.meta = meta` — a wholesale replace of a dict the
price pipeline also writes to. Every re-intake dropped `price_source`,
`price_currency` and `price_as_of`, leaving a stored price with nothing saying
what currency it was in; and the CSV path and `broker_sync` never wrote those
keys at all. A previous fix had already noticed the replace and rescued exactly
**one** key (`asset_class_source`) — which is how the next one gets lost.

`_intake_meta()` now merges, the way `pricing_service` always has
(`{**(p.meta or {}), ...}`), and then **re-stamps** the price keys deliberately:
a row whose price came from a CSV must not still claim it came from Yahoo, and a
repair flag about a price that no longer exists is worse than no flag. That one
helper covers three of the four writers the plan listed — `add_holding`, the CSV
path and `broker_sync` all go through `upsert_positions`.

### Scope note, against what the plan said

V5 called this *"four writers put a price on a holding without normalising it"*.
Only one of the four has a currency to normalise **from**: the CSV and broker
paths carry a bare number and no code, so for them the gap was provenance, not
normalisation. Fixed as it actually is rather than as it was described.

**Tests:** `tests/test_intake_prices_are_shekels_and_say_so.py`, three cases, all
confirmed failing against `712ff7c` first. The share count is the assertion that
matters — 9.07 shares, not 0.0907 — because that is the number the next refresh
turns into a loss.

**Gate:** SQLite **1626 passed / 0 failed / 0 skipped**; Postgres **1626 passed /
0 failed / 0 skipped**; `ruff check app tests` clean.

**Smoke:** read-only, and honest about it. This path fires only when a holding is
added, and adding one to a live book to prove a fix is not a smoke test. The
tests are the proof; the smoke confirms the deploy is healthy and that no
existing row moved.

Not financial advice.

---

## PR 44 — the core is not spare change

**Decided by Eran, 2026-09-03**, on the card PR 43's smoke had just made honest:

```
Consider COMT (Broad commodities) - ₪1,465
How it's funded:  Sell 6.7347 BND (~₪1,465)
reason:           "cheapest way to raise it, but it corrects no drift:
                   Fixed Income is at 11% against a 11% target ...
                   Selling it does not move you closer to your plan"
```

₪1,465 of a ₪2,263 position — **65% of the bond leg of the core** — which would
take Fixed Income from 10.8% to 3.8% against an 11% target. The sentence was
true. **Saying it is not the same as declining to do it.**

The book is exactly on its target and nothing is overweight, so
`plan_funding`'s documented `book_has_budget` fallback funded the buy from
whatever it could reach. Sleeves were off-limits (C3). The core was not.

**One list, not two.** Sleeves were the whole exclusion set. PR 41 bolted the
core onto the *tax card's* copy of that idea and left funding's copy alone, so
for two deploys the same screen refused to harvest BND and offered to sell 65%
of it. `_funding_exclusions(session, user, btgt)` is now the single source, used
by `_war_room_recs`, `build_recommendations` and the tax block, and it degrades
to the sleeves alone on failure rather than to nothing — a missing core basket
must not silently widen what is sellable.

**The core is not frozen.** `core_to_basket` (PR 39) trades it, sized by one
plan, and that remains the path allowed to touch it. What is now impossible is
the core being sold as a side effect of buying something else.

**Effect on Today, and the smoke expects it:** every holding is either a sleeve
(SOXL, TQQQ) or the core basket (VTI, BND), and spendable cash is zero — ₪618.65
against a 3% floor of ₪628. So nothing is fundable and **Today shows no buy
cards at all.** That is the honest state of a fully-invested book sitting on its
target: there is no trade the plan is asking for. Cards return the moment a class
drifts, a name breaks its cap, or new money arrives.

**Tests:** `tests/test_the_core_is_not_a_funding_source.py`, both confirmed
failing against `b7d369f` first.

* The behavioural one: `_funding_exclusions` covers the core basket.
* The structural one, which matters more: an AST walk asserting that every
  `exclude=` handed to `propose_funded_buy` / `plan_funding` /
  `rank_trim_candidates` is either a forwarded parameter or a name bound from
  `_funding_exclusions`. Against `b7d369f` it names both offending sites. A
  second list of what the plan holds on purpose is not a second opinion; it is
  the bug this PR closes, waiting for a book that distinguishes them.

Note the first draft of that guard banned `sleeve_tickers` outright and was
wrong: `_core_plan` and the core-ceiling card use it to decide what a sleeve
*claims* versus what the core holds, which is a different question. The guard
pins the funding path only.

**Gate:** SQLite **1623 passed / 0 failed / 0 skipped**; Postgres **1623 passed /
0 failed / 0 skipped**; `ruff check app tests` clean.

Not financial advice.

---

## PR 43 — PR 42 was right and incomplete, which is the same as wrong

**PR 42 shipped as `5e3fa1c` and the card did not change.** Eran screenshotted
Today and the USO card's funding leg still read:

```
"cheapest way to raise it: Equities is the most overweight class
 (86% against a 80% target) and VTI is among its least-taxed positions"
```

on a book whose own Equities target is 89.2% and whose `/mix` reports
`rebalance_required: false`.

**Root cause: the call-site list was truncated and I treated it as complete.**
The grep that found the callers of `propose_funded_buy` was piped through
`head`, which returned ten lines. Two call sites sat below the cut —
`_buy_idea_recs` (`recommendations.py:3189`) and `_commodity_recs` (`:3303`) —
and the first of those is what builds the screener cards on Today. PR 42
threaded the book target through the funding layer correctly and then handed it
to four of six callers. A fix that reaches most of the call sites is
indistinguishable, from the screen, from no fix at all.

This is the failure mode `safe-windows-edits` Rule 1 names for reads — *never
trust a truncated view* — applied to a search rather than a file.

**The fix.** Both sites now pass `book_target`: `_buy_idea_recs` takes it as a
parameter, threaded from `btgt["target"]` at its call site, and `_commodity_recs`
forwards the `book_mix` it was already being handed.

**The guard, which matters more than the fix.**
`tests/test_every_funding_call_passes_the_book_target.py` walks the **AST** of
`recommendations.py` and `funding_service.py` and fails if any call to
`propose_funded_buy`, `plan_funding` or `rank_trim_candidates` omits
`book_target`. It does not care what is passed — only that the caller says which
target it means, so a new call site cannot silently inherit the objective table.
Walking the tree rather than grepping is the point: the defect being pinned was a
search that stopped early. A second test builds the exact missed call shape and
asserts the walker flags it, so a green run is evidence rather than an absence.

Against `d4ca292` the guard names all five sites; against the deployed `5e3fa1c`
it names the two that were missed.

**On the single card.** Today shows one card, not two, and that is **not** this
bug. `_ledger` nets funding across the card set — the comment at the
`_buy_idea_recs` call site is explicit that the screener runs last so "an
optional idea can only ever use what a needed trade did not". USO claimed the
Commodities gap; COMT is still ranked (84.7) and correctly has no card of its
own. Two cards spending the same shekels is the defect PR 1 fixed.

**Also found, not fixed here.** `_commodity_recs:3264` and
`_redeploy_cash_recs:2860` both read `book_mix or OBJ_TARGET[objective]`. That
`or` is the same trap `targets_for` was written to avoid: a book target that is
truthy but silent about a class yields **zero** for it, not the objective's view.
On this book that is why `_commodity_recs` emits nothing at all. Converting them
to `targets_for` would change which cards exist, so it is listed on the plan
rather than done quietly.

**Gate:** SQLite **1621 passed / 0 failed / 0 skipped**; Postgres **1621 passed /
0 failed / 0 skipped**; `ruff check app tests` clean.

*Postgres note for the next session: an interrupted run leaves rows behind and
the next run failed 46 tests on card-generation state that had nothing to do with
the change. `dropdb && createdb` before believing a Postgres-only failure.*

Not financial advice.

---

## PR 42 — the funding layer was measuring against a target the rest of the app had left behind

**Measured on production, 2026-09-03 at `d4ca292`.** Both cards on Today were
funded by selling **VTI** — the Classic 60/40 core basket bought two days
earlier — and both carried the same reason:

```
"cheapest way to raise it: Equities is the most overweight class
 (86% against a 80% target) and VTI is among its least-taxed positions"
```

`/api/v1/mix`, at the same moment, on the same book:

```
target_allocation   {"Equities": 0.892, "Fixed Income": 0.108}
current_allocation  {"Equities": 0.8625, ...}
drift_percentage    {"Equities": -0.0295}
rebalance_required  false
```

**Opposite signs, same book, same screen.** 80% is `OBJ_TARGET["Grow"]`, the
objective table, which knows nothing about sleeves or a core. 89.2% is the book's
own target — 73% from two sleeves plus 16.2% from the core — and it is what
`/mix`, the rebalance card and the contradictions check all read. Equities at
86.25% is six points over the first and three points **under** the second. The
funding layer was selling equities to correct an overweight that did not exist,
and reaching for the core to do it, because sleeves are excluded from trims and
the core is not.

**Three faults, and each survives the one before it being fixed.**

1. `rank_trim_candidates`, `plan_funding` and `propose_funded_buy` read
   `OBJ_TARGET[objective]` with no way for a caller to supply the book's target.
   `funding_service.targets_for()` is now the one seam; all three take
   `book_target`, and the three call sites in `recommendations.py` pass
   `_book_target(...)["target"]`.
2. The reason sentence claimed *"the most overweight class"* **unconditionally**.
   It is reached whenever a candidate scores at all, and the score carries a
   tax-friendliness term that stays positive on a book where nothing is over
   anything. Fixing the target alone would have left the sentence intact.
3. And it must be **material**. With the book target threaded in, BND came out
   over by 0.06 points — **₪15.73**, far below `MIN_TRADE_ILS` — and inherited
   the claim VTI had just been relieved of: *"Fixed Income is the most overweight
   class (11% against a 11% target)"*, a sentence naming an overweight of zero.
   The claim now requires the overweight to be worth at least one trade.

**The merge, and why it is a merge.** `targets_for` overrides only the classes
the book target names. Sleeves plus core describe Equities and Fixed Income and
say nothing about Commodities — neither a 60/40 core nor two equity sleeves has
an opinion on it. Replacing the table outright sets the Commodities target to
zero, and `propose_funded_buy` refuses a buy into a class with no gap, so **both
commodity idea cards would have vanished** — not because the plan rejected them
but because the plan had never been asked. Pinned by
`test_the_book_target_overrides_only_the_classes_it_names`.

**What changes on the live book, simulated before shipping:**

```
Commodities buy gap   ₪2,552  (open, so the USO and COMT cards survive)
VTI    "cheapest way to raise it, but it corrects no drift: Equities is at 87%
        against a 89% target ... Selling it does not move you closer to your plan"
BND    same shape, 11% against 11%
```

**What this PR deliberately does NOT do.** The cards still fund by selling VTI —
`plan_funding`'s `book_has_budget` fallback is explicit that when nothing is
overweight, the buy is funded from wherever it can be, and that was a deliberate
decision with a comment on it. This PR makes the sentence true; **whether the app
should sell the core to buy a class the plan does not model is a decision for
Eran**, and it is on the plan's decision list rather than settled here.

**Tests:** `tests/test_the_funding_layer_reads_the_books_target.py`, four cases,
all four confirmed **failing against `d4ca292`** first.

**Gate:** SQLite **1619 passed / 0 failed / 0 skipped**; Postgres **1619 passed /
0 failed / 0 skipped**; `ruff check app tests` clean.

Not financial advice.

---

## PR 41 — the tax card counted losses it was not going to sell

**Measured on production, 2026-09-03, the morning after the core realign:**

```
apply.tickers          VTI, BND
est_amount             146.46 ILS      "save about 146 in tax this year"
losses on VTI + BND     11.17 ILS  ->    2.79 ILS at the 25% CGT rate
losses on SOXL + TQQQ  574.58 ILS  ->  143.65 ILS, excluded from the sale
                                        three lines earlier as strategy
                                        holdings, and counted anyway
```

**98% of the headline came from two positions the same code block had just
decided not to touch.** A 52x overstatement of the card's own number. `save` was
read straight off `tax_opportunities`, which scans the whole book, while `losers`
had already been filtered by the sleeve exclusion. `_unrealized_ils(rows,
only=losers)` now measures the loss over the tickers the card actually sells,
FX-converted per holding.

**Three more, on the same card.**

- **It offered to sell the core.** The exclusion knew about sleeves only, so on a
  book that had realigned into `bal_6040` the day before, the card proposed
  selling VTI and BND — the basket itself. Accepting it would have undone the
  realign and taken `measured_pct_of_book` back down from 97. PR 37 settled that
  a core is a strategy too; the sentence already in the code applies unchanged —
  *a tax saving is not a reason to sell your strategy.*
- **It promised this year.** Every position in the book was below cost, so there
  were no gains to offset. `tax_opportunities` knew, and says so in its own
  description — *"future gains (carry-forward)"* — while the card built from it
  said *"Lowers this year's tax bill"* regardless. An unoffset loss carries
  forward; it does not reduce a bill that does not exist. The card now says what
  the loss is worth **once you have a gain to offset**, and names the
  carry-forward when there is none.
- **`gains` were unrealized and called realized.** Where a book does show
  winners they owe no tax until sold, so the loss offsets nothing until they are.
  `portfolio_analytics.py:195` said "realized gains"; it now says "gains once
  they are realized", and the card names the unrealized figure as the thing that
  would have to be sold too.

Plus the disclosure the engine was already carrying and the card never repeated:
`_TAX_ASSUMPTIONS` states *"no inflation-linked cost basis adjustment"*, so the
estimate is on the nominal gain and is an upper bound. The card says so now.

**Effect on Today, and it is the smoke test:** on the live book every holding is
either a sleeve (SOXL, TQQQ) or the core basket (VTI, BND), so after this PR
there is **no harvest card at all**. That is the correct answer — there is
nothing harvestable that the plan does not hold on purpose. Today goes from three
cards to two.

**Tests:** `tests/test_the_harvest_card_measures_what_it_sells.py`, three cases,
all three confirmed **failing against `8541583`** first. One existing test changed
with it: `test_tax_harvest_never_targets_the_strategy_sleeve` pinned that the
exclusion ran *before* the saving was quoted — which it did, and the saving was
wrong anyway. It now pins that the whole-book read is **absent** and that the
measurement is scoped to what survives the exclusion. Strengthened, not relaxed.

**Gate:** SQLite **1615 passed / 0 failed / 0 skipped**; Postgres **1615 passed /
0 failed / 0 skipped**; `ruff check app tests` clean.

Not financial advice.

---

## PR 40 — a buy that cannot be priced sells nothing

**The defect, in one sentence: two executors sold first and asked for a price
afterwards.** `_apply_buy_funded` ran every sell leg, realized the capital gains tax, and
only then quoted the instrument it was buying. When that quote failed it credited the
proceeds to cash and returned a note saying so — but the positions were already gone and
the card had claimed a purchase that never happened. The user's book came back as
something neither the card nor the executor had described.

`_apply_fee_swap` had the identical order with more at stake: it sells the **whole**
position. A fee swap whose replacement could not be priced was a full exit the card never
proposed, tax realized, proceeds in cash.

**This was live, not theoretical.** Today carried two `buy_funded` cards when this was
written — *Consider USO — ₪1,308* and *Consider COMT — ₪775*. Commodity tickers are exactly
the names most likely to be missing from a given provider, which is how the path gets
reached.

**Ordering is the entire fix.** `delete_position` commits, so once the first sell lands
there is nothing left to roll back; before it is the only place either function can refuse
safely. Both now quote the buy leg first and return
`{"aborted": True, "unpriced": <ticker>}` with the book untouched.

`_apply_core_to_basket` (PR 39) was already written this way and called the older behaviour
*"survivable for a single swap"*. It is not: the rule that **a card may not claim a
portfolio change it does not produce** has no size threshold. Its docstring is corrected
here — it described a sibling's behaviour that no longer exists.

**Tests:** `tests/test_an_unpriceable_buy_sells_nothing.py`, three cases.
`test_a_buy_that_cannot_be_priced_sells_nothing` and
`test_a_fee_swap_that_cannot_price_its_replacement_sells_nothing` were both confirmed
**failing against `4076ef1`** first — the buy case sold 2 V and credited ₪2,696.48 to cash
for a purchase that never happened; the swap case liquidated the position outright.
`test_a_priceable_buy_still_executes` passes in both states by design: the change is a
reordering, not a new veto, and a guard that only passes after the fix would not prove that.

**Gate:** SQLite **1612 passed / 0 failed / 0 skipped**; Postgres **1612 passed / 0 failed /
0 skipped**; `ruff check app tests` clean. Both backends run in the sandbox against the
Postgres 16 service, per `CLAUDE.md`.

**Smoke:** read-only. The behavioural change only fires when a provider fails, which is not
something to induce in production. What the smoke proves is that the card path is unbroken:
`/health` serves the new SHA, `/api/v1/recommendations` still returns its cards with their
`apply.kind`s, and `/api/v1/portfolio` reports the same NAV. The refusal itself is proved by
the two tests above, not by production.

Not financial advice.

---

## PR 39 — the core card stops giving advice and does the thing

> *"I'm missing the most important thing in these cards --> guidance is not
> enough... I want these cards to act on my behalf (as other cards can do it)."*
> — Eran, 2026-09-02

PR 38 shipped three cards and made every one of them advice, reasoning that this
app cannot fund a ₪3,392 purchase from a ₪619 cash balance. **The reasoning was
half right and the conclusion was wrong.** The money is already in the book,
sitting in the wrong places:

| | | |
|---|---:|---|
| V | ₪955 | in the core region, not in the core's basket |
| BND | ₪576 | above the core's own 40% leg |
| sleeves | ₪1,268 | above the sleeve sizes he set (79% held vs 73%) |

Bringing the core back to Classic 60/40 is a **rebalance, not a purchase**, and
this app rebalances. Three cards became one that acts: *"Bring your core back to
Classic 60/40"*, `apply.kind = core_to_basket`.

### The one rule

`_core_plan` sizes it, and **the card and the button both call it**.
`_rebalance_to` is the cautionary tale this repo already carries: the card read
one target, Accept applied another, the screen said "sell 4,324" and the button
sold 12,139. The arithmetic itself lives in `core_realign.plan_realign` — pure,
no ORM, no I/O — so the tests assert a real trade's numbers without mocking one.

### What it will not do

- Take cash below the app's own floor.
- Trim a sleeve **below** its own target. The sleeve sizes are a decision made
  on the Plan tab; this reclaims what is above that decision and never overrules
  it.
- Buy more than the sells raised. On his book, after CGT (₪143) and the cash
  floor, everything the book can raise still leaves **₪565 unfunded** — and the
  card says that on its face rather than quietly buying less than it promised.
- Sell anything before it knows it can buy. Every buy leg is priced **first**;
  one timed-out quote aborts the whole action with the book untouched.
  (`_apply_buy_funded` sells first and holds proceeds as cash — survivable for
  one swap, not for three positions.)
- Take the sleeve trim from one ticker: it is pro-rata, because concentrating it
  would silently re-weight the sleeves against each other.

Every leg is listed on the card **before** the button, from the same plan the
executor runs. And it updates the tracked book — no brokerage order is placed.

20 new tests; the PR 38 test asserting "this card declares no apply block" was
rewritten, since it documented the decision this PR reverses.

Gate: SQLite 1609 / 0, Postgres 16 1609 / 0, ruff clean.

---

## PR 38 — a done the book contradicts, and the card that names the ticker

> *"if I'm not holding VTI as part of my portfolio --> I expect to get a
> recommendation card to purchase it."* — Eran, 2026-09-02

**The card existed.** "Buy Equities", MEDIUM, ₪1,215 — hidden since
**2026-08-21**, because he pressed Done. Measured on 2026-09-02, twelve days
later, the equities gap was ₪1,194: the same instruction, to within 2%.

### Half one — "done" is a claim about the book, and nothing asked the book

PR 34's rule is why it stayed hidden, working exactly as designed: a muted card
returns when its own number **moves**. For a card you *ignored* that is right —
you looked, you chose not to act, and only a changed situation earns your
attention again.

**For a card you marked done the signal is inverted.** If the same instruction
still stands, unchanged, after the trade would have settled, then the number
*not* moving is the evidence the action was never taken. The app took the user's
word for it and never checked; nothing else in this file trusts a claim it can
measure.

Deliberately narrow, because the failure mode of this rule is nagging: only
`done`, never `ignored`; only after `_DONE_SETTLE_DAYS` (3), so real settlement
is not called a lie; only for a card asking to move real money; and a book that
*did* move is never accused — it gets PR 34's neutral wording instead.

### Half two — a class is not an instruction

Even when that card fires it says *"Buy Equities, about ₪1,215"*. On this book
the equities target is 89.2% — 73% from two leveraged sleeves plus 16.2% from
the core — so **buying more TQQQ satisfies it completely** and leaves the core
still holding no VTI.

The core is the one place the app knows the instrument, because a core *is* a
basket (PR 37). The leg target is `core_pct × weight × NAV` — the same
arithmetic `_book_target` already folds *into* the class target, here left
un-aggregated. On his book, NAV ₪21,092, core 27%:

| leg | target | held | card |
|---|---:|---:|---|
| VTI | ₪3,417 | ₪0 | Your core is missing VTI — buy ~₪3,417 |
| BND | ₪2,278 | ₪2,854 | Your core holds too much BND — trim ~₪576 |
| V | ₪0 | ₪955 | V is not in your core's basket |

**Advice, not an action.** No `apply` block: funding a ₪3,417 purchase out of a
₪619 cash balance is not something this app can carry out, and a button that
reports success without doing anything is the exact defect `_ACTIONABLE_KINDS`
exists to prevent.

24 tests, **18 fail against PR 37**. The six that pass pin the leg arithmetic
(₪3,417 / −₪576 / ₪955), which PR 37's `core_matches_basket` already supplies —
they guard the numbers the cards are built from.

Gate: SQLite 1589 / 0, Postgres 16 1589 / 0, ruff clean.

---

## PR 37 — a core is a strategy too

> *"that's not true.... the sleeves have their strategy and the core has its own
> strategy. the portfolio is being managed by two different strategies (sleeves
> strategy and core strategy)"* — Eran, 2026-09-02, on the line PR 36 shipped

He is right, and this repo already agreed with him everywhere except the one
place it mattered. `core_search` ranks **fourteen** candidate cores on measured
return, volatility and drawdown, through `blend.core_spec` +
`blend.measure_blend`. His core is `bal_6040` — a basket plus a rebalance
discipline, sized at 27% of the book.

But `backtest_service.refresh_all` only ever walked `strategy_catalog`, the
**rule-based sleeves**. No static family had ever been backtested, so
`book_assumptions` had no core row to read — and it never asked for one. Every
unclaimed holding went through the per-ticker instrument table. That is the
whole of the 79%.

**The fallback was not neutral.** A per-ticker table has no covariance term, so
pricing a rebalanced 60/40 as the weighted average of a VTI row and a BND row
**overstates its volatility by construction**: the one thing rebalancing between
two weakly-correlated legs does is the one thing the table cannot express.

So the core is measured nightly, into the same `StrategyBacktest` table, gated by
the same `usable()` — one fetch for the union of every basket, but each basket
measured on a feed **scoped to its own tickers**, because `align()` intersects
the date axis and one late-listing ticker would otherwise truncate all fourteen
(the trap `target_solver._core_blocking` documents).

**A fixed basket has no fitted parameter**, so it gets no out-of-sample fold.
`_pick_return` prefers the OOS figure because a *rule* can be tuned until the
past looks good; splitting a 60/40's history measures the decade, not the
strategy. That is stored as a named reason (`NO_FIT_TO_TEST`), not as an empty
`robustness` a reader could mistake for a failure.

**And one gate a sleeve does not need.** A sleeve's tickers are its tickers; a
core's basket is a *target*, and a book can name `bal_6040` and hold something
else — which is exactly what Eran's book does: BND and V, no VTI at all. Pricing
those holdings from a measurement of a basket they are not is the same class of
error as PR 6's target-weighting, so composition is checked first against
`recommendations.SLEEVE_DRIFT_BAND_PCT` (the app's existing answer, not a second
private tolerance) and a mismatch abstains **by name**:

> *"Classic 60/40 (bal_6040) IS measured, and your core is not holding it: you
> hold none of VTI; V is not in the basket…"*

So on his book today the numbers do not move — and the app finally says why, in
terms he can act on. It is the same finding as issues 2 and 3 arriving through a
third door.

21 tests, **20 fail against PR 36**. The one that passes guards a pre-existing
invariant — that `bal_6040` is in `core_candidates()` with a basket — because if
that stops being true, `measure_cores` measures nothing and tells nobody.

Gate: SQLite 1565 / 0, Postgres 16 1565 / 0, ruff clean.

---

## PR 36 — the target card remembers what you accepted

> *"I want these fields (beat the benchmark & without exceeding) to be the
> default of what I have approved."* — Eran, 2026-09-02

He had accepted **12 / 49** and the card kept opening on a measurement. Both
defaults are seeded in `_seedTarget`: the ceiling from
`performance(range=MAX).max_drawdown_pct`, the target from
`max(0, ceil(excess_cagr))`. That was right while nothing remembered an
answer — a made-up default is the card guessing on your behalf, which is what
this card exists to stop.

But an accepted target is neither a guess nor a measurement. It is the question
he decided to ask and then acted on — **and it has been recorded on every apply
since Phase A.** `tgtAccept` sends `context.excess_pct` and
`context.ceiling_pct`; `_record` writes them verbatim onto the
`PlanApplication` row. Nothing had ever read them back. So this is a **read
path and no new storage** — no table, no column, no migration.

Two rules the read path has to get right, both about not putting words in the
user's mouth:

- **An undo withdraws the acceptance.** Undo restores the sizes the apply
  replaced, so the target that produced them stops describing the book. Seeding
  from it afterwards would put a decision he reversed back in the box. The
  newest row decides, in both directions.
- **Half a seed is worse than none.** A row that lost one of the two figures
  returns `None` rather than a partial — one remembered number beside one
  measured one, with nothing saying which is which, is exactly the confusion
  this card is for.

The measured pair is still fetched, still shown in the note, and one tap away
behind *"use what the book measures now"*. Silently replacing a remembered
decision with a measurement is the mirror of the bug. And the boxes now say
where they came from: *"These are the target you accepted on 2026-08-16 — not a
measurement"*, or *"These start from what your book measures, not from a target
you have set."* A number in an input with no provenance is the same problem as a
figure on a card with no basis.

**Issue 7 rides along, and the first draft of it was wrong.**
`measured_assumptions._headline` said *"78.8% of the book is priced from its own
measured strategies"* and stopped. That reads as a coverage hole — it was the
first thing Eran asked about — so the line now names the other half.

The first attempt named it as *"your core and cash, which run no strategy to
measure"*. Eran: *"that's not true.... the sleeves have their strategy and the
core has its own strategy — the portfolio is being managed by two different
strategies."* He is right, and this repo agrees with him: `core_search` ranks
fourteen candidate cores on measured return, volatility and drawdown through
`blend.core_spec` + `blend.measure_blend`. His core is `bal_6040` — a basket
plus a rebalance discipline, sized at 27% of the book.

So the line now names **a gap in the app**: `book_assumptions` never asks for
the core row, so even a core held exactly to its basket lands in `unallocated`
and is priced ticker by ticker. That is not a neutral substitution — a
per-ticker table has no covariance term, so pricing a rebalanced 60/40 as the
weighted average of a VTI row and a BND row **overstates the core's volatility
by construction**. Closing it is PR 37.

14 tests, **13 fail against PR 35**. The one that passes guards the untouched
fully-static branch.

Gate: SQLite 1544 / 0, Postgres 16 1544 / 0, ruff clean.

---

## PR 35 — the digest cannot compose a false sentence

The 07:00 push on **2026-09-01**, verbatim:

> "Hello! Your account is currently valued at ₪21,474.35, with a total return of
> 17.76%. You have an 89.2% probability of reaching your ₪102,973 goal in 5
> years. To diversify your holdings, consider buying ₪2,147 of PDBC (Broad
> commodities) to reach your 10% target in this asset class."

Measured against the app the same morning:

| figure | verdict |
|---|---|
| 21,474.35 | **real** — the NAV snapshot for 2026-08-31 |
| 17.76% | **real, and not a return on that NAV.** A *backfill* of today's holdings priced through 252 sessions of real closes. The account's return on the ₪20,000 contributed was **+3.89%** |
| 89.2% | not in the context at all. `/plan/projection` says **22.7%** |
| 102,973 | not in the context. The plan target is **100,288.77** |
| 5 years | not in the context. The horizon is **6** |
| 2,147 | exactly 10% of NAV, **computed by the model** |
| 10% | from `OBJ_TARGET["Grow"]`, which is not in the context either |

The first two are the interesting failure. **Both numbers were true and the
sentence was false**, because they measure different things and nothing in the
payload said so. The prompt's *"never invent numbers"* was not violated.

So the fix is not a better instruction:

1. **Every quotable figure travels as `{value, means}`** — `FIGURE_BASIS` is one
   table mapping a dotted path to what that number is a measurement *of*, in the
   words the digest should use. A BACKFILL now says it is a backfill inside the
   same object as the number.
2. **Every number in the generated text must appear in the context**, or the
   whole digest is discarded and the deterministic one is sent.
   `unsupported_numbers` matches by **rounding to the decimals the text used** —
   never a tolerance band, because a band wide enough to accept "21,474" for
   21474.35 also accepts 17.76 for 17.03. Fractions may be quoted as percentages
   (0.227 → "23%"), and numbers that reached the context inside a *string* are
   quotable, so repeating a card's own wording back is not an invention.

Run against the real notification, the check names **6 unsupported figures**:
`17.76, 89.2, 102973, 5, 2147, 10` — and leaves 21,474.35 alone.

Two source problems went with it. `risk.goal` carried a **second** probability
of reaching the target, from a different engine than the Plan screen, and it was
`null` on the day the push claimed 89.2% — it is no longer copied, and
`/plan/projection` is the one source. And the fallback said *"Performance:
17.03% total"* directly under the net-worth line: the same two figures in the
same order, without a model in between. It now names the basis every time.

**The labelling is applied at the seam, not in `gather`.** The first version
wrapped the values inside `gather` itself, and `/api/v1/ask` returns that object
to the browser — `context.nav_ils` stopped being a number. `test_assistant.py`
caught it. `for_prompt()` returns a labelled *copy* and the API shape is
unchanged; the assistant's prompt gets the same protection.

15 tests, all 15 fail against PR 34.

Gate: SQLite 1530 / 0, Postgres 16 1530 / 0, ruff clean.

---

## PR 33b — the tests PR 33 rewrote and PR 33 did not commit

**CI run #194 went red on both backends**, three failures, all of them the tests
PR 33 rewrote:

```
test_a_spike_is_not_a_flow::test_a_removal_that_is_NOT_reversed_stays_an_ordinary_flow
test_history_does_not_call_an_intake_performance::test_the_step_is_named_rather_than_folded_in_silently
test_history_does_not_call_an_intake_performance::test_a_holding_sold_out_of_is_not_a_loss
```

PR 33 changed *where* an unexplained step lands, and rewrote those three to
assert the property rather than the mechanism. **Two of the three files were
missing from `ship-pr33`'s `$Files` list.** `New-Commit` is path-limited by
design — that is the "never `git add -A`" rule — so it committed the new code
and left the rewritten tests sitting in the working tree.

**The local gate could not have caught it.** It ran the new tests against the
new code and went green, correctly, because that is what was on disk. The
divergence existed only between the working tree and the commit — and nothing
looked there.

So the check goes where the two actually diverge. `New-Commit` now lists every
**modified tracked file that is not in `$Files`**, says plainly that the gate
tested them and CI will not have them, and requires a confirmation before
committing without them. Exclusions are the tracked files that are dirty for
reasons unrelated to any commit: `investwise.db` (rewritten by every local
pytest run), `frontend/node_modules`, `scripts/railway-error.log`, and
`COMMIT_MSG.txt`.

The general shape, worth keeping: **a gate that tests the working tree cannot
verify a commit.** Every rule in this repo about naming files on a commit buys
safety at the cost of a blind spot, and the blind spot needs its own guard.

No application code changes.

Gate: SQLite 1515 / 0, Postgres 16 1515 / 0, ruff clean.

---

## PR 34 — a mute does not outlive its number

**Nine cards were hidden, and two of them were the ones he needed.** Read live
from production on 2026-09-02, while Eran was asking why the app had nothing to
say about a book that was off target:

```
/api/v1/mix              rebalance_required: true
                         BUY  Fixed Income  2,243.96
                         SELL Equities      1,626.87
/api/v1/recommendations  4 cards, all screener verdicts
                         dismissed_count: 4   completed_count: 5
```

A card's id is `sha1(category|ticker)` — no amount, no date — so the card you
file today and the card the same condition generates next week are **the same
id**, and one click mutes both for a fortnight however far the number travels in
between. `_DONE_TTL_DAYS` was already cut from 90 to 14 for this reason, and the
comment that did it says so: *"making the id carry its own content is the actual
fix and is a separate change."* This is that change, **and it does not touch the
id.**

**Why not a content-bearing id.** The id is what Restore, the audit log and the
push dedupe all key on, and quantising an amount into it churns on every
boundary — 1,499 and 1,501 become different cards, and a card that re-fires on a
0.1% move is as useless as one that never re-fires. So the *mark* remembers the
situation instead, and the situation is compared. No boundary, no new identity,
and the comparison is legible in the reason the card carries back.

Two things un-mute a card (`moved_on`): its **severity went up** — "I dealt with
that" was said about a MEDIUM and the app now calls it HIGH — or its **own
number moved** by more than 25%, floored at ₪100 so a card asking for pocket
change does not re-fire on rounding. A mark with no situation (every mark made
before this ships, and every card that carries no amount) keeps exactly the old
behaviour: muted until the TTL runs out. **Nothing is migrated and no existing
mute silently changes meaning.**

The amount comes from the client, because rebuilding the whole Today list on a
button press would put a multi-second sweep on the slowest path in the app. That
trust is bounded by construction: `moved_on` only ever uses it to bring a card
**back** early. A wrong value cannot extend a mute, hide anything, or move a
single number in the book.

**A card that comes back says why it came back** — reappearing with no
explanation reads as the app having forgotten you dealt with it, which is the
complaint the mute exists to prevent, arriving through another door.

**The second half was misdiagnosed at first and the correction matters.** The
"N suggestions are hidden" line already existed and already rendered next to
live cards — the count was never missing. What was missing is *which*: it said
"4 other suggestions are ignored" while the two rebalance cards sat inside that
4 and nothing named them. `hidden_cards` now carries id, title, severity,
dimension, how it was hidden and when, and the screen lists them.

15 tests; **14 fail against PR 33**. The one that passes is
`test_dismissing_without_a_body_still_works` — a deliberate no-change guard, so
it must pass on both.

Gate: SQLite 1515 / 0, Postgres 16 1515 / 0, ruff clean.

---

## PR 33 — the chart measured the day it could not explain

**Six of eleven recorded days were reported as exactly 0.00%.** `time_weighted`
set the flow to the whole inter-snapshot delta whenever the position count
changed and the ledger said nothing:

```
f = nav - prev_nav
r = (nav - f) / prev_nav - 1     ==  0.0, arithmetically, every time
```

So the one day the book changed shape is the one day the chart reports as
nothing happening — and it reports it as a *number*, not as a gap.

**Measured.** On 2026-09-01 Eran deleted two holdings the app had priced at 0
(PR 32 is why they were 0). NAV went 21,474.35 → 20,777.45 and the chart booked
all **696.90** of it as "money you moved out", drawing the day at 0.00%. From
real closes: SOXL −6.10%, TQQQ −3.85%, V −1.77%, BND −0.62%, on a book that is
79% TQQQ and SOXL. **The 696.90 was the market. Nothing left the book.**

**The fix: don't guess, measure.** `window_return` prices the book the user holds
today across the same two dates from real closes (reusing
`performance_service`, not a second implementation). What that explains is
performance; only the residual is a flow — `flow = nav − prev × (1 + r_held)` —
and the step's return then falls out of the *unchanged* formula as exactly
`r_held`.

Three outcomes, each said out loud:

| | |
|---|---|
| residual is real and correctly signed | an **estimated** flow, labelled with its method |
| residual is noise or wrong-signed | `resolved_changes` — the app checked, and the step was market movement |
| no usable history, or the reconstruction contradicts the book | `unmeasured_days` — **the line breaks** |

Three guards, each from a real failure on this book:

- **A removal cannot bring money in.** The 1 Sep residual is +219 ILS on a day
  two holdings were *removed*. Reading it as a deposit would replace one false
  claim with another; it is the method's error term, and the recorded NAVs win.
- **Both ends resolving to the same close is not a zero return.** It would make
  the residual the whole NAV delta again — the original defect, restored.
- **The resolved window must cover the same market time.** `performance`
  intersects dates across every holding, so one missing bar drops the day for
  the whole book (2026-08-28 is absent because V and BND had no close in the
  feed). Without the span guard the 30→31 Aug step priced *one* day of NAV
  against *four* days of market and invented a **1,088 ILS** flow.
- **A wrong-signed residual above 2% of NAV is the method not applying**, not
  noise. 2026-08-24 is a reconcile that replaced half the book; today's four
  tickers miss it by 3,615 ILS (17% of NAV). That day is a hole.

Weekends resolve back to the last close (3-day lookback) — a book does not move
while the market is shut, and 2026-08-30 is a Sunday.

**On the real series this turns `+0.128% over 11 days` into `−3.12%, measured
over 7 of 10 steps, with three named holes.`** The old number was a fiction
built from six fake zeros. Nothing is written to the database: `time_weighted`
runs on every request, so the whole history repairs itself on the next page load.

Gate: SQLite 1500 / 0, Postgres 16 1500 / 0, ruff clean.

---

## PR 32 — a quote of zero is not a quote

Eran opened the app on **2026-09-01**, found holdings showing **0 ILS**, and
deleted two of them — a holding worth nothing looks like junk. By the next
morning the remaining rows were priced normally again, and nothing on any screen
said what had happened. The audit log has the two `DELETE /portfolio/position`
calls at 17:19:09 and 17:19:49. It has nothing about the zero, because nothing
recorded one.

**Measured on the previous commit, by the new test:**

```
NAV went 3710.0 -> 0.0 because a provider answered 0. Nothing was bought or sold.
```

**The hole.** `refresh_all_positions` guards, refuses and *discloses* every other
way a quote can be unusable — `q is None` is `failed`; a quote nothing has traded
against is `stale` + `not_written`; a currency that contradicts the market is a
`conflict` + `not_written`. Its own comment states the rule: *"Do NOT overwrite
the price: a quote nothing has traded against is not a current price."* A price
of **0** is the same class of thing, and it was the one case with no guard at
all — straight through to `p.current_price = money(q_price)`.

Both adapters guarded `price is None` and neither guarded a zero. Yahoo returns
`regularMarketPrice: 0` for a symbol it half-resolves (a delisted line, a venue
suffix it does not carry, a ticker that changed); FMP answers an unknown symbol
with `"price": 0` rather than an error.

**Not a display bug.** `value_ils` is `quantity × price × rate`, so a zeroed
holding leaves NAV — and NAV is what the mix, the rebalance sizing, the
concentration cap, the projection and the 22:10 snapshot are measured against. A
snapshot taken inside a zeroed window is wrong *permanently*; it is the one
number the app cannot recompute. And it self-heals in 30 minutes, which is why
it has never been caught: by the time anyone looks, the evidence is overwritten.

**Two layers.** The adapters raise — the better place, because the fallback
provider then gets its turn and `guarded_quote` caches no poisoned zero. The
refresh refuses as well, counts it and logs it, so no quote source (present or
future, including the builtin one the suite runs on) can write a holding down.

**And the disclosure, which is the half that would have saved the two deleted
rows.** `/api/v1/portfolio` carries `unpriced_positions` and `unpriced_cost_ils`
beside the existing `stale_positions` and `fx_unresolved_positions`, and the
holdings card renders such a row as *"could not be priced — not ₪0"* with what
you paid, instead of a flat zero that reads as "this is worthless, delete it".
Derived from the **price**, not a meta flag: a flag only describes rows written
by the build that sets it, and the rows that caused this predate any flag.

NAV is deliberately not padded with a guessed price. `unpriced_cost_ils` reports
what is missing *at cost* — the only number the app has for a holding it cannot
price. Inventing a price is the one thing worse than admitting there isn't one.

All six new tests were confirmed to fail against `PR 31` before being accepted,
and the end-to-end one is ordered so its failure names the damage rather than a
missing counter.

Gate: SQLite 1489 / 0, Postgres 16 1489 / 0, ruff clean.

---

## PR 31 — the bond it called a stock

**13.7% of the book was bonds and every screen called it equity.** Measured on
production `63c0559`, read-only, 2026-09-02, while answering seven items Eran
raised against the live app.

```
/api/v1/portfolio   BND 13.1331 sh @ 71.79 USD = 2,842.15 ILS of a 20,777.45 NAV
                    meta.asset_class = "Equities"
/api/v1/mix         current_allocation {"Equities": 0.9703, "Cash": 0.0298}
                    Fixed Income: ABSENT. Not 0.0 — absent.
                    BUY  Fixed Income  2,243.96
                    SELL Equities      1,626.87
```

`2,243.96` is exactly **10.8% of NAV — the whole fixed-income target**, because
the app believed the holding was zero. Accepting that card lands the book at
~24% bonds against a 10.8% target. The equity leg was wrong in the same breath:
the book was 83.3% equities against 89.2%, so the card asked to **sell what it
was short of and buy what it already held too much of**. Both signs inverted,
from one string.

**The chain.** `buy_into_book` defaulted `asset_class="Equities"`;
`_execute_funded_sleeve` — the sleeve funder, its busiest caller — passes none
(the audit log puts the two `POST /plan/sleeves/fund` calls that created this row
at 17:23 and 17:24 on 2026-08-31); so the row was stamped on the way in; and
`classify` returned that stamp on its **first line**, before reading
`FIXED_INCOME_TICKERS` four lines below — where `BND` has sat since the fix for
this same failure one asset class over.

Nothing here is a new capability. Every part of the answer was already in the
file and the precedence read them in the wrong order.

**Now:** Cash first and alone (`CASH` is in neither table, so tables-first would
file the shekel row as equity and `_class_gap_recs` would offer to buy cash with
cash) → a class a **human** set, recorded as `asset_class_source="user"` by
`update_position`, the one writer of that flag → the tables → whatever was
stored, which is only ever a default → Equities. `_from_tables` is split out so
the tables can be consulted *without* the Equities fallthrough: "I do not
recognise this ticker" and "this ticker is an equity" are different answers, and
collapsing them is what let a default outrank a lookup.

**No data migration.** The stale stamps stay on the rows and stop being
authoritative — strictly smaller than rewriting a user's holdings in place, and
there is no half-done state it can leave behind.

**Five other readers had the same raw read**, including `intake.py:154` (the
holdings card — literally where `BND · NASDAQ · Equities` was printed) and
`context_service`, which read the stamp raw *and* applied a second
`or "Equities"` on top of it: two independent fallbacks to the same wrong answer
in the one module whose docstring says the LLM "can't invent" these numbers. A
number it cannot invent can still be wrong. `classify_holding()` is now the one
way a held row is classified.

**One existing test was asserting the defect** — `test_pr4_cards.py:329` pinned
`classify("GLD","NYSE",asset_class="Equities") == "Equities"`. The lookup was
well covered; the branch that outranked it was not covered once, because every
other test in the area called `classify` with no meta at all.

Gate: SQLite 1483 / 0, Postgres 16 1483 / 0, ruff clean.

---

## PR 30 — three things the app knew and would not say

None of these is a new feature. Each is a value the app already had and threw
away before a reader could see it. All three came out of validating the plan
backlog **against production** rather than against the documents — which also
retired eight items that were recorded as open and were not.

### 1. An unresolved exchange rate reached NAV looking like a peg

`fx.py` is careful: `fx_rate_with_status()` returns `(rate, status)` where
`"unresolved"` means *"the provider failed, nothing was ever cached, this 1.0
means no idea"* — its own docstring says **"Never present it as a value."**

`fx_rate()` then hands back the rate alone, correctly, for the call sites doing
plain arithmetic. But `/portfolio` — **the one route that builds NAV** — used it
too. Read through that wrapper, the 1.0 is indistinguishable from a genuine peg,
so a holding in a currency nobody could resolve is counted **at par** and lands
in the total with no signal at all. On a TASE or LSE position that is not a
rounding difference, it is the entire conversion.

`value_ils` is `quantity × price × rate`. `stale_positions` has said since PR 20
when the **price** cannot be trusted; nothing said when the **rate** could not.
Now the other half of the multiplication is disclosed the same way:

* per row, `fx_status` — `live` / `stale` / `unresolved`
* per payload, `fx_unresolved_positions` + `fx_unresolved_value_ils`, and
  `fx_stale_positions` kept **separate** — a stale rate is real and a few hours
  old; an unresolved one is not a rate at all, and the two want different
  responses.

NAV still includes the holding. Writing one down is not the app's call — the same
reasoning as `price_stale`. The roll-up selects on the **status**, never on
`rate == 1.0`; testing the value is the broken signal that made the status field
necessary in the first place.

### 2. The performance chart was handed its dates and drew no axis

`index.html` passed `r.dates` as the labels and set `ticks:{display:false}`. A
chart spanning up to ten years had no x axis, and nothing on screen said which
end was which. V4 called it the best ratio on the list and every renumber pushed
it back.

Not simply `display:true`: MAX is ~2600 daily labels and Chart.js will draw all
of them into a smear. `autoSkip` + `maxTicksLimit:6` + `maxRotation:0` — **the
tick config this same file already uses on another chart**, so the two read
alike rather than drifting apart.

### 3. Pinning VAPID to the wrong keypair is silent, and kills every subscriber

`vapid_pinned_by_env` was `false` — measured on production, not assumed. Pinning
is an ops action, but it has a trap worth code: **a browser subscription is bound
to the public key it was created with.** Pin a freshly generated pair and every
existing subscriber becomes unreachable — the send fails at the push gateway, so
the app records a fan-out to nobody and the phone just goes quiet.

This app already knows that failure one layer downstream: `DEAD_CODES` includes
403 precisely because *"the keypair no longer matches the one the subscription
was created with"*, and the comment there calls the un-pruned version **"a
permanent, completely silent outage."** `get_vapid` now compares the pinned
public key against the pair this database minted and says so at ERROR, and
`push/status` carries `vapid_env_matches_db` (`None` when nothing is pinned —
"not pinned" and "pinned wrongly" are different states and a bool cannot hold
three). The env still wins; this reports, it does not overrule the operator.

**It is the same shape as the agent key this morning**: two locally generated
values on the machine, a third on the server, and a resolver reporting that
everything was fine.

⚠️ **The pin itself is an ops action, and the order matters.** Use the pair
already in `kv_settings` — `scripts/pin-vapid-from-db.sql` reads it. Generating
a new pair would kill the subscription registered today.

### One of my own tests was wrong first

`assert "display:false" not in call` also matches `grid:{display:false}`, which
is deliberate — that chart has never drawn gridlines. It failed, and had I read
it as a code fault I would have "fixed" the wrong thing. The assertion is now
scoped to the ticks, and pins the gridlines staying off.

**Gate:** SQLite **1491 passed / 0 failed**; Postgres **1491 passed / 0 failed**;
ruff clean; `node --check` on the inline script. 6 of the 9 new tests confirmed
failing against the old code (the other 3 are no-change guards).

---

## PR 29 — the screener finally reaches a screen, as a card

Eran, looking at Today: *"I don't have a buy idea section on any of my tabs."*
He was right, and the reason is worse than a missing section.

```
grep buy_ideas app/static_app/ frontend/   ->  0 matches
grep screener  app/static_app/index.html   ->  0 matches
```

**`buy_ideas` has been in the `/recommendations` payload for months and nothing
in the app has ever read it.** Its only consumer was one `next()` inside
`_redeploy_cash_recs` — the single top-ranked name in a class the book holds
none of. **PR 24, 25 and 27 were fixing a ranking that reached the user through
one ticker, on one card, in one situation.** BRK-B's P/B being wrong by 1,650x
mattered only because it could have won that one slot.

### Cards, not a section — and that was Eran's call, not mine

I proposed a section. He asked why it wouldn't be cards under "What to do", and
that is the better answer: **a separate list would be a second surface proposing
trades**, which is exactly what the card-claim invariant and `contradictions.py`
exist to police — and the only such surface with no `apply` spec to check its
claim against. STATUS already records three cards arming three trailing stops on
one ticker because three producers could not see each other. A ranked list would
have been a fourth producer and the least verifiable one.

As ordinary cards they inherit every guard:

| guard | what it buys |
|---|---|
| `propose_funded_buy` returns `None` | no card when the plan doesn't want the class, it can't be funded, or it would push a class further from target |
| the shared `FundingLedger` | these cards cannot spend shares a needed trade already claimed |
| `already_named` | never proposes a ticker another card is already naming |
| built **last** among buying cards | an optional idea uses only what a needed trade left |
| held-ticker skip | topping up an existing position stays with the drift and rebalance cards |
| `_MAX_BUY_IDEA_CARDS = 2` | Today is a list of things to do, not a leaderboard |

**The screener call was hoisted above the card producers.** Appended after the
reconcile-and-sort pass, these cards would have skipped reconciliation — and a
card that dodges reconciliation is how two cards end up about the same shekels.

### What the card says that the silent pick never did

The score, the reasons, and — the payoff for PR 27 — **the gaps**:

> Ranked WITHOUT 2 of its eight factors (pb, debt_to_equity), which the provider
> did not report — so this score rests on less than the others.

`data_gaps` was recorded as data in PR 27 precisely so a reader could tell a
composite built from five factors from one built from eight. Until now nothing
said it out loud, which is nearly the same as having dropped it.

### The new cards found a false sentence in shared code

`test_no_card_is_presented_short[single_name_breach]` went red on the first run:

```
Buy ₪63,070 of AGG — ... Fund it with ₪70,078 by selling 188.8887 HUGE.
Estimated tax on the sale: ₪7,008. That still leaves ₪0 short.
```

`shortfall_ils` is stored to the agora, so any residue under half a shekel
formats as `0` while staying truthy — and `describe_funding` narrated it. The
sentence contradicted the rest of the line **and** the card's own `est_amount`.
That test had asserted this all along; **no existing producer had ever hit the
round-to-zero case.** Now thresholded at `MIN_TRADE_ILS`: below the minimum
tradeable amount there is nothing to act on, so there is nothing to report. The
fix lands in shared code, so every buy card gets it.

**Gate:** SQLite **1482 passed / 0 failed**; Postgres **1482 passed / 0 failed**;
ruff clean.

### Known remaining
* The other nine ranked names, the scores and the coverage counter are still
  API-only. That is deliberate — they are diagnostics, not decisions — but if
  the coverage number is ever to be read by a human it needs a home that is not
  Today.
* Routing the secondary provider through `guarded_quote` (carried from PR 28).

---

## PR 28 — the suite was asserting against the live market, and the chart charged a bad snapshot to performance

Two unrelated-looking reds, one shape: **something outside the test was writing
while the test believed it had stopped everything.**

### 1. The chart said -24.91% on a book that had moved +0.78%

Reported live 2026-08-31: the account-history chart read **-24.9137%** while the
summary card on the same screen read +6.67%. The whole of it is ONE step, from
the live payload:

| date | NAV | positions |
|---|---|---|
| 08-17 | 21,168.33 | 10 |
| 08-22 | **28,496.54** | **5** | ← +34.6% while HALF the book left |
| 08-24 | 21,369.63 | 5 | ← reverts, no shape change |

`21369.63 / 28496.54 - 1 = -25.0097%`. Every other day in the window is
ordinary (+0.77, +0.03, +2.66, -3.24).

**Root cause: `nav_history.py:154`, `prev = (d, nav, n_now)`.** PR 22 taught this
module that a NAV move with a position-count change and a silent ledger is not
performance, and it fixed the step **into** such a move — 08-22 scores 0.00%,
correctly. It left the step **out** of it alone: the phantom +7,328 was removed
from the numerator of one period and left sitting in the denominator of the next.

**PR 22's test asserts the flagged step is 0.0%. It never asserts what the NEXT
step does.** That is the entire gap.

**The fix, and the wrong turn on the way to it.** My first attempt stripped the
attributed flow from the next period's base as well. It produced **+0.7807%** —
*exactly* the raw NAV change. That looked like confirmation and was the opposite:
matching the simple change to the decimal is the tell that it had cancelled
**every** flow adjustment, so a genuine deposit would have stopped being excluded
from performance. It also re-charged the real ₪63.41 removal as a -0.30% "loss"
the next day. A number that agrees with you is not evidence.

What shipped instead: a snapshot whose attributed flow is **reversed by the next
step with no further shape change** is a bad row, not a flow. Money that really
arrives stays in the book; a bad reading reverts. Such a row is dropped as an
anchor *and* from the plotted series, and named in `suspect_snapshots` — dropping
it silently would be PR 22's own mistake in a new place.

The 08-17 → 08-24 boundary is then **abstained**, not scored: five holdings left
and the total rose ₪201.30, and performance across that boundary is
`(21369.63 + W - 21168.33) / 21168.33` where `W` is what the five were worth. The
snapshot stores a count and a total, never `W`. Scoring it +0.95% would assert
`W = 0`. Ground or abstain.

**-24.9137% → +0.1281%**, against a simple NAV change of +0.78%.

⚠️ **The 08-22 row is still in the database.** The code stops it poisoning the
return, but the chart will keep *plotting* a ₪28,496 spike until the row goes.
Deleting it is an ops action, not a code change.

ℹ️ The summary card's **+6.67%** will still differ, and should: that is gain vs
contributions since inception (₪1,334 on ₪20,000 put in), not a one-month
time-weighted return. What was broken was -24.9% against +6.67%.

### 2. The suite was asserting against TEVA.TA's live share price

`test_a_row_already_stored_in_agorot_is_repaired` failed on two local machines
**and both CI jobs**, with a different number every time:

```
10800.00   10810.00   10820.00   10830.00   10850.00      expected 11030.00
```

Not five bugs and not flakiness. **TEVA.TA traded at 10,820 agorot that
afternoon** and the figures track it. Two separate writers, both fixed:

**a. `pricing_service.py:115` built a real provider outside the resilience tier.**

```python
yahoo = None if primary.name == "yahoo" else YahooMarketDataProvider()
...
if q is None and yahoo is not None:
    q = yahoo.get_quote(tk)          # NOT guarded_quote
```

The test patches `registry.guarded_quote` **and** `pricing_service.guarded_quote`
— exactly what the house rule from PR 21 prescribes — and still could not stop
this, because **nothing named `guarded_quote` is on that path at all**. The "no
quote available" branch the test exists to exercise was never reached: a quote
*was* available, by a route the test could not see. Now behind
`fallback_provider()`, a stubbable seam.

**b. The scheduler runs during every test.** `enable_scheduler` defaults to
`True` and `conftest.py` never turned it off, so every `TestClient(m.app)` called
`start_scheduler()` — and `scheduler.py:140` passes
`next_run_time=datetime.now(utc)`, priming a live price refresh **immediately**.
A worker thread then wrote real quotes into the test database, at whatever moment
it finished, including after a test's `finally` had restored the provider.

That is what `test_preview_is_read_only_and_names_its_anchors` was actually
catching: NAV moved between two GETs, so a read-only preview was accused of
writing. It fired in one of two jobs on the identical commit — a race, correctly
reported, wrongly attributed.

**The repo had already seen this and muted it.** STATUS records 183 warnings from
"APScheduler's price job fires during `TestClient` startup", filtered by coroutine
name in PR 21. The warning *was* the symptom of this hole; silencing it removed
the last thing pointing at it. Same lesson as `logging.disable(CRITICAL)` hiding
the NAV-conservation diagnostics.

**Measured side effect: the suite went 750s → 227s.** It had been waiting on the
network.

**Gate:** SQLite **1473 passed / 0 failed**; Postgres **1473 passed / 0 failed**;
`ruff check app tests` clean. All 7 new history tests confirmed failing against
the old code first.

### Known remaining
* Routing the secondary provider through `guarded_quote` so it gets the rate
  limiter, breaker and cache. The seam makes it stubbable; it does not yet make
  it governed. Named rather than smuggled into a bug-fix PR.
* The 08-22 snapshot row (ops action above).
* Buy ideas as Today cards — Eran's call, and the right one: the screener already
  reaches Today through `recommendations.py:2480` and `:2696`, so what is missing
  is *why this name*, not a new section. A second surface proposing trades is
  what the card-claim invariant exists to prevent.

---

## PR 27 — the screener could see again, and three of the numbers were wrong

PR 25 is **verified live**. `/health` serves `7edb624`; measured against
production on 2026-08-31:

| | |
|---|---|
| equity buy ideas | **5** (CVX, JPM, GOOGL, XOM, BAC) — was zero, every scan, for months |
| coverage | `requested 58, ranked 58, unreachable 0, no_data 0` |
| `/screener` | returns equities |
| NAV | ₪21,333.59 across 4 positions, no stale prices |

58 of 58 also closes the token-bucket question for good: it was a sandbox
artifact of the builtin provider being instant, exactly as PR 24 suspected.

**What that exposed is that the engine consuming those numbers had never been
exercised on real data at scale.** It makes the same mistake PR 25 fixed one
layer down — a missing answer and a bad answer rendering identically — and it
makes it three times.

### 1. An implausible P/B was ranked as a bargain, and the card said so

BRK-B ranked **#7 on the live buy list** carrying:

```
"pb": 0.00096701446        value pillar: 98.2  <- the highest in the top 25
reasons: ["Trades near book value (P/B 0.0)", ...]
```

Berkshire's price-to-book is about **1.6**. The 0.00097 is Yahoo dividing the
class-B **price** (~$490) by the class-A **book value per share** (~$507,000) —
off by a factor of ~1,650. P/B is a lower-is-better column, so the broken number
handed BRK-B very nearly the best value percentile in the cohort, and `_reasons`
then **asserted it to the user in plain English**. A caption that vouches for a
number tells the reader the checking has already been done; PR 22 recorded that
exact failure on the history chart.

`_safe_pe` had this instinct already and it was never generalised — the same
"a guard protects the path it is on, not the behaviour" lesson this file records
against the NAV-inflation bug.

**`_safe_pb` returns `None`, not the worst value, and that is deliberate.** A
P/E of zero means something specific — no earnings — so `_safe_pe` can map it to
the worst score honestly. An implausible P/B says nothing about the *direction*
of the error: Berkshire's real ratio is mid-cohort, and the minor-unit case runs
the other way (PR 25 measured TEVA 5.48 against TEVA.TA **1657.9**). Scoring it
worst would be as wrong as scoring it best. Ground or abstain.

This also disarms the trap PR 25 left for the Tel Aviv work: a `.TA` P/B is now
rejected rather than ranked, so adding those listings cannot quietly import a
300x error.

### 2. A missing factor had two opposite meanings

```
_mean([50.0, None])  -> 50.0   dropped   (quality has 3 slots)
_mean([None])        ->  0.0   the WORST (income has 1 slot)
```

The only thing deciding which is how many factors happen to share the pillar.
Both were live:

| | |
|---|---|
| **JPM #2**, **BAC #5** | `debt_to_equity: null` — quality scored without leverage, for a bank the one factor you would most want |
| **MCD #11** | `pb`, `roe_pct` **and** `debt_to_equity` all null — value from P/E alone, quality from profit margin alone, and **quality 78.3, the highest of the names checked** (JPM 61.0, CVX 47.8) |
| **BRK-B, AMZN, CHKP, MNDY** | `income: 0.0` — "Yahoo did not say" rendered identically to "worst payer in the cohort" |

**8 of the top 25 carried at least one missing factor and not one of them said
so.** MCD's only visible trace was that its `reasons` list had two entries where
its neighbours had four, which is a coincidence, not a disclosure.

`_pillar` scores an unmeasured slot neutral in both directions: a name cannot
win a factor nobody measured and cannot be convicted on one. `data_gaps` names
every factor that did not contribute and why — `not reported` (chase the
provider) versus `implausible` (chase the field) — and a gapped name is flagged
`partial-data`. It is a **label, not a second penalty**; docking a name for its
provider's silence is the mirror image of the bug. `metrics` keeps the raw
value, because a silently nulled field cannot be diagnosed.

`_mean` is **unchanged** — `rank_commodities` and `rank_bonds` depend on it and
neither has the pillar problem. A test pins that, so a later cleanup does not
merge the two and silently move the bond rankings.

### 3. A company that pays nothing answers zero. The adapter filed it as unknown

Measured against Yahoo, 2026-08-31:

```
BRK-B  dividendYield {}   trailingAnnualDividendYield {"raw": 0.0}
AMZN   dividendYield {}   trailingAnnualDividendYield {"raw": 0.0}
JPM    dividendYield {"raw": 0.0168}
```

Yahoo drops `dividendYield` entirely for a non-payer while still reporting, on
the same object, that the trailing yield is zero. Reading only the first field
filed a **measured zero** as "unknown" — the conflation PR 25 removed at the
transport layer, surviving in the field mapping.

**It produced the right ranking by luck.** `_mean([None])` collapsed to 0.0 and
0.0 was the truth. The moment `_pillar` starts scoring an unmeasured factor
neutral — as it must — every non-payer would be handed a middling income score
it has not earned. Right answer, wrong route, and the route is what breaks when
the data shifts. That is why this lands in the same commit as `_pillar`.

### 4. The coverage pin vanished in the case it exists to explain

`_pin` was attached only to equity picks, so a scan that ranked nothing fell
through to `audit_block`, which has no `coverage` key. PR 25's own smoke script
names it: *"PR 24 attached the coverage pin only to equity ideas, so it
disappears in exactly the case worth diagnosing."* Reproduced against PR 25's
bug shape — the agent held `{"requested": 58, "ranked": 0}` and the payload said
nothing at all. `_buy_ideas` now publishes the pin through a caller-supplied
dict, filled before anything can shorten the list — a dict rather than a module
global, so two concurrent scans cannot hand each other the wrong coverage.

### Two of my own tests passed against the bug before I caught them

Worth recording, because it is the failure mode this repo keeps finding.

The obvious test — three names varying only in leverage — **goes green on the
old engine**. Dropping a factor does not score it 50; it scores it at the mean
of whatever else is in the pillar, so with the other factors tied the dropped
slot lands on 50 by arithmetic accident and the ordering is already correct.
Tying the unmeasured name at the top of the cohort does not help either, because
`_pct_ranks` gives ties the **mean** of the ranks they span, so the "perfect"
score is 83.3 and the assertion passes on both engines. The defect only appears
when the surviving factors are top-of-cohort **and untied**; then the old pillar
returned `mean(100, 100)` — a flat 100 for a balance sheet nobody looked at.

**11 of the 14 new tests were confirmed to fail against the old code** before
being accepted. The three that pass are deliberate no-change guards.

**Gate:** SQLite 1465 passed / 1 failed; Postgres 1465 passed / 1 failed; `ruff
check app tests` clean. Both PowerShell scripts parse-checked with pwsh 7.4.6,
pure ASCII, no backticks.

### The one failure, and why it is not this PR's

```
tests/test_minor_unit_currencies.py::test_a_row_already_stored_in_agorot_is_repaired
AssertionError: NAV is 10910.00; the repair did not run when the quote failed
assert 10910.0 == 11030.0 +- 1.1e+00
```

**ROOT CAUSE: UNKNOWN. CONFIDENCE: nothing** — same treatment as the `trading_rules`
StaleDataError and the +739.2 leak. What is established:

* It fails **identically on unmodified HEAD**, in a control run taken in the
  same window, on **both** backends. Not caused by this PR.
* It fails **deterministically when run alone**, three times out of three, on
  old and new code alike.
* It **passed** in one earlier full run in the same sandbox — the only
  environment delta being that `py-vapid`/`pywebpush` were installed between the
  two runs.
* Not the shared `/tmp/iw_test_app.db` (deleted, still fails) and not network
  (fails with `MARKET_DATA_PROVIDER=builtin`). 10910 is 109.10 x 100 shares; the
  builtin quote for TEVA.TA is 230.97, so it is not simply a fresh quote leaking
  through the patch either.
* PR 26 gated green at 1436/0/0 on both backends six days ago, so this most
  likely does not reproduce on CI or on the Windows box. **Worth one run there
  to confirm before believing that.**

A test that passes only when something earlier in the suite has run is the
`a-skip-is-never-a-pass` family: it reports a pass for a condition it did not
establish. Recorded, not fixed here.

---

## 🔴 PR 26 — two credentials were published, and one of them could not be changed

The repository was **public** until 2026-08-30. Two credentials were readable by
anyone who opened it, and both worked.

Measured against production on 2026-08-30, before the fix:

| probe | result |
|---|---|
| `GET /api/v1/portfolio` with no key | `401` |
| `GET /api/v1/portfolio` with a wrong key | `401` |
| `GET /api/v1/portfolio` with the key committed in 33 scripts | **`200`** |
| `POST /api/v1/auth/token` with the default password in `config.py` | **`200`**, SUPERADMIN token |

**Authentication was working correctly.** A wrong key and no key were both
refused. The credentials were simply public.

### The password half was worse, because the documented remedy was inert

`PRODUCTION.md` section 8 said to set `AUTH_PASSWORD`.
`auth_service.ensure_superadmin_credential` only ever ran an `INSERT` —
`if res.scalar_one_or_none() is None` — so the bcrypt hash was written **once**,
at first boot, from whatever the setting was then. Every later change to the
environment variable was ignored, and there is **no password-change endpoint
anywhere in the app**. The published password could not be retired by any
configuration change at all. That is why it survived five months of the item
sitting in the backlog labelled "known".

### Why it was silent

Nothing failed. Nothing logged. The test that covered this path *asserted the
hole*: `test_ensure_credential_and_login` logged in with the default value and
passed on every run, because the test hard-coded the same literal the config
did. **A test written against a default cannot notice that the default is
published.**

### Fixed

* `auth_password` has no default. Blank means password login is **unavailable**,
  not open — fail-closed. Google sign-in is a separate path and is untouched.
* `AUTH_PASSWORD` is now authoritative: a stored hash that does not verify
  against it is replaced. This is the app's only means of changing the SuperAdmin
  password, and it did not exist before.
* A password in `auth_service.PUBLISHED_PASSWORDS` is refused even if set
  deliberately, and an existing row still holding one is scrambled to a random
  value. `/api/v1/auth/token` awaits that **before** it checks credentials, so
  the first request after the deploy retires the old hash — including a request
  from someone arriving to use it.
* The key literal is gone from all 33 scripts; they call `Get-AgentKey`.
  `agent-key.ps1` lost its third resolution candidate — "read the literal back
  out of `smoke-p0.ps1`" — which would have quietly undone the migration, and
  gained a SHA-256 fingerprint check that warns when the retired key is still in
  use. A hash, not the value.
* `ROTATION.md` is the runbook. `PRODUCTION.md` section 8 now states that its own
  instruction used to do nothing.

### The check that would have caught it

`tests/test_no_secret_is_committed.py` reads the files git would carry —
tracked **and** newly added un-ignored ones — and fails on credential shapes, on
the two burned values by SHA-256, and on a default reappearing in `config.py`.

My first version of it used plain `git ls-files`. I planted the retired key in a
new script, ran the suite, and **it passed**: the file was untracked, so the
check never saw it. It would have gone red in CI one commit too late, which is
the same "found it afterwards" the test exists to end.
`--others --exclude-standard` is the load-bearing half.

### What is still true

* **Nothing is rotated until `AGENT_API_KEY` and `AUTH_PASSWORD` are set on
  Railway.** This PR makes rotation possible; it does not perform it.
* The old values remain in git history forever. Rotation makes them useless; it
  does not remove them. Rewriting history is not worth it for two dead
  credentials.
* Making the repo private closed *new* exposure. It did not un-publish what was
  already out, and both values were also pasted into chat transcripts.
* The agent key still grants SUPERADMIN to any request carrying it
  (`app/core/auth.py:115`). By design.

**Gate:** SQLite 1436 passed / 0 skipped / 0 warnings; Postgres 1436 passed / 0
skipped / 0 warnings; ruff clean. Baseline was 1428 — the +8 is four auth tests
and four guard tests. All four new auth tests were confirmed to **fail against
the old code** before being accepted.

---

## 🔴 PR 22 shipped a pin that could not resolve, and main stopped building

CI run **#182** failed **all three jobs** and the Railway build for `c52cb17d`
failed with them, both at `pip install`, before a single line of app code ran:

```
ERROR: Cannot install -r requirements.txt (line 19), -r requirements.txt
(line 25) and cryptography==44.0.0 because these package versions have
conflicting dependencies.
    pywebpush 2.3.0 depends on cryptography>=2.6.1
    py-vapid 1.9.4 depends on cryptography>=46
```

Measured from the published wheel metadata, after the fact:

| package | `Requires-Dist: cryptography` |
|---|---|
| py-vapid **1.9.2** | `>=2.5` |
| py-vapid **1.9.4** | `>=46` |

`requirements.txt:15` pins `cryptography==44.0.0`. **The pin was unsatisfiable
the moment it was written.** Production is still serving the pre-PR-22 image.

**Fixed** by dropping to `py-vapid==1.9.2`, which is safe for a reason rather
than by luck: its one deprecated curve-as-class call is at
`py_vapid/__init__.py:200`, inside `generate_keys()` — the single function
`push_service._generate_vapid` stopped calling in PR 22. The path still in use,
`Vapid01.from_raw`, already passes `ec.SECP256R1()` instantiated.

### Why it was not caught, which is the part that needed fixing

The pin was chosen by reading the version installed in the **development
sandbox**, which had **cryptography 50.0.1** — not the 44.0.0 this repo pins.
`pip install py-vapid==1.9.4` succeeded there and proved nothing about the
project. The 1382-test green run proved nothing either: a dependency-resolution
failure happens *before* pytest is reachable, so no test inside the suite can
ever observe it.

Same shape as the FX 3.71 incident — a value read out of the local environment
and written down as if it were the project's.

So the suite cannot check resolution, and the hotfix does not pretend to. It
checks the assumption underneath the mistake:
`tests/test_the_pins_are_installable_and_installed.py` fails the moment the
running interpreter holds a version `requirements.txt` does not ask for, naming
the package and both numbers. **Run in the sandbox that chose 1.9.4, it fails on
cryptography: pinned 44.0.0, installed 50.0.1** — the fact that was missing.

### How to tell whether PR 22 actually reached production

A failed deploy is invisible from outside. The app answers, the pages render,
nothing looks wrong — it is simply old. So
`scripts/smoke/smoke-pr22-is-actually-live.ps1` does not ask whether the app is
up. It asks whether `/api/v1/portfolio/nav-history` returns
**`unexplained_flows`**, the key PR 22 added at `nav_history.py:362`. Present
means the new code is running; absent means it is not, whatever the sha and the
deploy dashboard say.

---

## ✅ PR 23 — the agent map, and it found four things

`docs/AGENTS.md` is Eran's item 1. Every claim in it carries a `file:line`.

**The headline.** `war_room.py:18` declares **seven** agents; **five** classes
exist under `app/agents/`; the overlap is **two** (Research, Adversary). Alpha,
Risk, Tax and Decision are *engines* rendered as dialogue; UX is a render step
with no class at all. The doc recommends renaming the four engine speakers
rather than promoting five engines — a large change bought for a labelling
problem.

**Four findings that are not documentation problems:**

1. **"What's moving" reads a four-row fixture, unconditionally** — item 5's root
   cause. `registry.py:63` returns `BuiltinEconomicDataProvider()` with no
   settings branch, and `app/providers/live.py` has **no economic provider** to
   switch to. Four is all there will ever be until one is wired in.
2. **That list is captioned with the price-refresh clock.** `index.html:2791`
   renders "Updates hourly · last refreshed N" from `last_refreshed`, which
   `market.py:44` fills from `market_status()`. A static list under a ticking
   clock — same class of defect as the +35% caption PR 22 removed.
3. **The Allocation Agent's veto is unreachable from the app.** The only veto
   raised *before* a card is built. Its sole caller is an ANALYST-role endpoint
   the front end never calls, behind two request fields that both default to
   `None`. `recommendations.py` never imports it.
4. **No agent reads your strategy when choosing what to buy** — item 2.
   `recommendations.py:2698` calls `screen_equities(top_n=5)` with no weights;
   the only weight input in the codebase is a query string nothing builds. Grow
   and Preserve rank identically.

Findings 1, 3 and 4 are pinned as **defects** in
`tests/test_the_agent_roster_is_what_actually_runs.py`: the tests pass while the
bug is present and **fail when it is fixed**, telling you to update the map.

---

## ⚠️ PR 24 — the objective reaches the screener, and no tilt rides in with it

Item 2, half fixed on purpose, and the half that is missing is the interesting
part.

**The bug.** `recommendations.py` called `screen_equities(top_n=5)` with no
weights, so `ScreenerEngine` used `DEFAULT_WEIGHTS` for every book. Grow and
Preserve ranked the identical candidates in the identical order. The objective
influenced *how much* was bought and never *what*.

**What shipped.** The wiring. Both call sites pass the objective, `/screener`
defaults to it, the pin travels with every buy idea — and **`OBJECTIVE_WEIGHTS`
is empty**, so every book still ranks exactly as before. The ranking is
unchanged for everybody, deliberately.

### Why the table is empty

Eran, before deploying the first cut: *"I don't want anything to be hardcoded —
everything must be calculated."* The first cut carried four hand-chosen vectors
behind `OWNER_REVIEWED = False`. That flag admits the problem; it does not solve
it. The numbers would still have reordered real buy recommendations.

The one derivation available was tried: each objective's strategies carry a real
`basket`, so the factor signature of those baskets could *be* the tilt,
calculated from live fundamentals. Computed against 69 tickers:

| objective | value | growth | quality | income |
|---|---|---|---|---|
| Grow | .241 | .292 | .253 | .214 |
| Balanced | .213 | .224 | .278 | .285 |
| Income | .229 | **.322** | .252 | **.196** |
| Preserve | .262 | .238 | .212 | .288 |

**Income derived to growth-highest, income-lowest** — the exact contradiction
the tilt exists to remove. Cause: `builtin.py:56-81` seeds every fundamental off
a sha1 of the ticker, so SCHD's "dividend yield" is a hash. The method cannot be
validated on the only data this environment has, and on real data it may still
be weak — the Income and Preserve baskets are almost entirely ETFs, whose
pass-through P/E and ROE are poor inputs for exactly the objectives that matter.

So: ship the wiring, ship the counter, leave the table empty. **Item 2 stays
open with a proven reason rather than an oversight**, and a defensible tilt is
four numbers away whenever one exists.

---

## ✅ RESOLVED — the token bucket is a sandbox artifact, not a production bug

Found while building PR 24 — the first cut of its tests passed alone and failed
intermittently in the suite. The cause was not the test. `_fundamentals_for`
fires one provider call per candidate in a tight loop and the tier's bucket is
`TokenBucket(20, 20)`:

```
58 equity candidates in, 20 ranked, 38 RateLimitedError
```

The survivors are literally the **first twenty entries** of `equity_universe()`,
AAPL through PFE. MRK, ABBV, PG, KO, PEP, COST, WMT, HD, MCD, NKE, DIS, XOM and
twenty-six more were **never scored at all** — so "the best buys in the universe"
meant "the best of whichever names the bucket allowed".

**All seven Israeli names are among the 38.** A second, previously unknown cause
for item 3, independent of the known one (all seven `TASE_STOCKS` entries are
NYSE/NASDAQ listings; there is still not one Tel Aviv listing in the universe).

This is the bug `screener_agent.py` already documents twice — `screen_commodities`
and `screen_bonds` both act on the unreachable/no-data distinction and log it.
`screen_equities` never got it. **It has it now**, and `coverage` rides in the
recommendations payload.

**ANSWERED, 2026-08-30, and the answer was no.** Production logs show **zero**
`UNREACHABLE` lines — every one of the 58 was filed under *no fundamentals*.
Provider health confirms it: fundamentals tier CLOSED, 0 failures, 61 succeeding
keys. Per-call latency on Yahoo lets the bucket refill exactly as hoped, so
**the token-bucket problem is a sandbox artifact of the builtin provider being
instant. No limiter fix is needed.**

The counter did its job anyway — it pointed at the real cause, which was worse:
Yahoo had gated the fundamentals endpoint entirely. See PR 25.

---

## 🔴 A Postgres run went red once on `trading_rules`, and has not repeated

Recorded because a red run that is re-rolled away is a red run nobody ever
diagnoses.

```
test_c4_per_sleeve.py::test_two_sleeves_wanting_one_ticker_produce_ONE_drift_card
sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table 'trading_rules'
expected to update 1 row(s); 0 were matched.
```

plus an `SAWarning` on the same table from `test_c3_funding` — both rows
vanishing between load and write.

**Not reproduced.** It passed alone, passed paired with `test_c3_funding`,
passed with the new test file added, and the next full Postgres run on the
**identical tree** was clean. Ordering is deterministic here — no randomising
plugin — so a deterministic fault would have repeated.

| | |
|---|---|
| **What is true** | The three previous Postgres runs never mentioned `trading_rules`, and this appeared on a tree carrying PR 24. |
| **What is also true** | Three clean runs do not disprove a race, and `build_recommendations` does concurrent work through `_offload` with `trading_rules` writes inside it — a shape a timing shift can *expose* rather than cause. |

**ROOT CAUSE: UNKNOWN. CONFIDENCE: nothing.** Same family as the +739.2 ILS
leak. No speculative fix shipped. **CI can go red on it** — if it does, send the
block.

---

## ✅ PR 25 — the equity screener had been returning nothing, for months

Found by the counter PR 24 added, on the **first production scan after it
deployed**:

```
equity candidates the provider says have no fundamentals: AAPL, ABBV, ADBE,
AMD, AMZN, AVGO, BA, BAC, BRK-B, CAT, CHKP, COST, CRM, CVX, DIS, ESLT, GE,
GLOB, GOOGL, HD, HON, JNJ, JPM, KO, LLY, MA, MCD, META, MNDY, MRK, MSFT, NEE,
NICE, NKE, NVDA, ORCL, PEP, PFE, PG, QQQ, SCHD, SMH, T, TEVA, UNH, V, VNQ,
VOO, VTI, VXUS, VYM, VZ, WIX, WMT, XLE, XLF, XLV, XOM
```

All 58, every scan. The buy ideas were three commodities and three bonds. **Not
one stock.**

### Root cause, proven

```
GET /v10/finance/quoteSummary/AAPL -> 401 {"code":"Unauthorized",
                                           "description":"Invalid Crumb"}
GET /v8/finance/chart/AAPL         -> 200
```

Yahoo gated `quoteSummary` behind a session cookie plus a matching crumb.
**Prices come from `chart`, which is not gated** — which is exactly why NAV, the
refresh job and every price on screen looked perfectly healthy while the
screener produced nothing.

**Ruled out by observation:** the rate limiter (a bucket rejection logs as
`UNREACHABLE`; there were zero such lines), the circuit breaker (CLOSED, 0
failures, 61 succeeding keys), anything ticker-specific (AAPL, MSFT, NVDA fail
identically).

### Why it survived, which is the half worth reading

`get_fundamentals` returned `None` for a gated endpoint, a network error **and**
a symbol Yahoo genuinely has nothing for. Its own docstring said so: *"Returns
None (rather than raising) ... so the screener can simply skip the name."*
`_fundamentals_for` dropped the candidate either way, with no counter and no
log, until PR 24. **The app was designed to fail quietly here, and it did.**

It also had **no test**. Added 2026-06-22; every fixture in
`test_market_data_live.py` is a `chart` payload, and `test_screener.py`
exercises the builtin provider whose fundamentals are a sha1 of the ticker. This
PR is the first test that code path has ever had.

### Two fixes, and the second matters more

1. The cookie + crumb handshake — fetched once, reused across a scan, re-fetched
   only on a 401.
2. **A transport failure and an empty symbol stop being the same answer.** Auth
   and transport failures now **raise** (counted as `unreachable`, whole set
   logged); `None` means only "Yahoo answered, nothing here". Without this, (1)
   works until Yahoo changes the handshake and then fails as silently as before.

Verified live through the real provider code: `AAPL pe=36.62`, `TEVA pe=60.73`,
`CHKP pe=13.65`, `NICE pe=15.29`, `QQQ pe=30.63`, `TEVA.TA pe=60.60`.

### FMP was evaluated and rejected on evidence

Eran supplied a key and asked for a registry split. FMP's free tier answers
*"Special Endpoint : This value set for 'symbol' is not available under your
current subscription"* for TEVA, CHKP and QQQ, and covers 13 of 15 US mega-caps
(AVGO and PG blocked). **It cannot see one Israeli name, one ETF, or one Tel
Aviv listing.** Yahoo covers all three. The key is not committed anywhere and
belongs in Railway env only.

### Measured while testing — handle before adding `.TA` listings

| | P/E | P/B |
|---|---|---|
| TEVA | 60.73 | 5.48 |
| TEVA.TA | 60.60 | **1657.9** |

P/E survives the minor unit because it is a same-currency ratio. **P/B does
not** — it is off by FX × 100 (≈303×). Item 3's remaining half must handle this.

---

## Where the seven-day audit stands

| PR | What | State |
|---|---|---|
| 1 | Stop the app inventing and destroying money | ✅ shipped |
| 2 | Ledger, honest gain, dead snapshot job | ✅ shipped |
| 3 | Reconcile the book | ✅ shipped |
| 4 | Cards you can actually see | ✅ shipped |
| 5 | History provider + lead with gain vs deposits | ✅ shipped |
| 5b | Four delisted ETNs out of the buy catalogue | ✅ shipped |
| 6 | Sleeves that mean 64% | ✅ shipped |
| 6b | Fractional sells | ✅ shipped |
| 7 | Surface the core choice (+ the DDL column that hid every backtest) | ✅ shipped |
| 7b | The core picker says what each family is FOR | ✅ this entry |
| 8 | Delete `plans.strategy` / `plans.strategy_sleeve_pct` | ✅ this entry |
| 9 | One target, scoped cards, no self-contradiction | ✅ this entry |
| 10 | The app could not buy a bond | ✅ this entry |
| 11 | T5 — the war room reads the solver | ✅ shipped |
| 12 | The job that lied, the card that was missing, the severity that never moved | ✅ this entry |
| 13 | T6b — the solver varies the core | ✅ this entry |
| 13b | T6b — discover the sleeve count | ✅ this entry |
| 14 | The ceiling went stale and nothing noticed | ✅ this entry |
| 14b | Two Accept buttons, two answers; the split pinned at the cap | ✅ this entry |
| 15 | W1 — walk-forward parameter sweeps | ✅ this entry |
| 15b | W2 — the `vol_target` regime-gate re-decision | ✅ this entry |
| 15c | W3 — contribution modelling | 🔴 not started |

---

## ✅ PR 22 — the history chart called an intake +35%, and the caption vouched for it

Reported live 2026-08-22, open since: the account-history chart showed **+35%**
on the day a holding was added through the Holdings screen, under a caption
reading *"No deposits or withdrawals in this window, so this is simply what your
money did."*

Both halves false. **The second is the worse one** — a caption that vouches for a
number tells the reader the checking has already been done.

**Root cause.** `time_weighted` takes its flows from the **contributions
ledger**. Adding a holding on the Holdings screen writes no ledger row, so
`net_flow_between` returned `0.00` and the entire jump in NAV compounded as
return.

**The signal was already on the row and nothing read it.**
`NavSnapshot.positions` is stored on every snapshot, and its own class docstring
says why: *"a NAV that jumped because money arrived is not a return, and without
the deposit total on the same row a later reader cannot tell the two apart."*
`series_for` did not even pass it to `time_weighted`.

**Measured on the shape of the incident** — a flat book at ₪10,000, a fourth
holding entered at ₪13,500, flat after:

| | total_pct | flagged |
|---|---|---|
| before | **+35.0%** | — |
| after | **0.0%** | the 2026-08-22 step, +₪3,500, 3 → 4 holdings |

**The fix, and the judgement inside it.** When the position count changes and the
ledger says nothing, the unexplained delta is treated as an external flow — the
same thing the app already does for a deposit. That *is* an attribution: the
snapshot records a count and a total, never which holding arrived or what it was
worth. So it is reported in `unexplained_flows` rather than folded in silently,
and the caption names the date, the direction and the amount, and says what to
do about it. **Guessing quietly is how the +35% happened; guessing quietly in the
other direction is not the fix.**

The ledger stays authoritative where it has something to say, so a deposit made
on the same day a holding was entered is not counted twice.

### Also in here: web push was one dependency bump from breaking

Eran's gate showed `1371 passed, **2 warnings**` — both
`CryptographyDeprecationWarning: Curve argument must be an instance of an
EllipticCurve class ... This will be an exception in a future version`, from
`py_vapid`'s `generate_keys()`.

Not cosmetic, and further along than the message says. **Measured on
`cryptography 50.0.1`, that exact call already raises**:

```
TypeError: curve must be an EllipticCurve instance
```

The repo pins `cryptography==44.0.0`, where it still only warns. So the fuse has
already burned down in the current release and the pin is the only insulation.
On the bump, VAPID key generation raises and **web push stops**, for a reason
nobody would connect to notifications.

**And it did not reproduce here, which is the second half of the defect.**
Whether it fires depends on *which* `py_vapid` is installed — 1.9.4 passes an
instance, older releases pass the class — and `pywebpush==2.3.0` requires only
`py-vapid>=1.7.0`: an open lower bound in a file where every other line is `==`.
Two machines legitimately resolved two different libraries.

Both halves fixed. `requirements.txt` pins `py-vapid==1.9.4`, and
`_generate_vapid` builds the keypair with `cryptography` directly — which the two
lines after it were already doing. A VAPID key is an ordinary EC P-256 keypair;
py_vapid was being asked for one call it does not need to own. Four tests,
including the round trip proving py_vapid still derives the same public key from
our private one — a mismatch there is the silent 403 that kills every existing
subscription.

**Gate:** SQLite 1365 passed / 0 skipped / 0 warnings; Postgres 1365 passed / 0 skipped / 0 warnings; `ruff check app tests`
clean, **zero skips, zero warnings**. All ten new tests fail on the old code.

**Ship:** `scripts/deploy/ship-pr22-an-intake-is-not-performance.ps1`
**Smoke:** `scripts/smoke/smoke-history.ps1` (existing) — plus the eyes check in
the ship script, which is the only thing that can see the caption.

Not financial advice.

## ✅ PR 21 — "N skipped" was scenery, and one of the numbers was a real hole

Eran, reading a green gate: *"why do we have those skipped tests on the flow? if
they can be skipped → remove them, if they can't be skipped → why they are not
running as expected?"*

The suite reported **4 skipped on SQLite and 12 on Postgres**, and nobody had
read either number in a long time. Both were run down to their causes. There
were exactly two, and they are not the same kind of thing.

### 1. Eight Postgres skips were a real coverage hole — in the direction that matters

`test_backtest_store_unavailable.py` sabotages the `strategy_backtests` table to
prove that an **optional side table cannot take down the app**. That is a
reproduction of a real production incident: migration 0011 added
`last_error` / `last_error_at`, the deploy landed before `alembic upgrade head`
ran, every SELECT hit an undefined column, and `/strategies`,
`/strategies/backtests` and the recommendations pipeline all went with it.

It did the sabotage with `sqlite3.connect()`, and skipped on anything else. So
those four tests **never ran on Postgres — which is what production is.**
Verifying a production incident only on the backend production does not use is
close to not verifying it, and it was reported as eight skips nobody read.

Nothing about the sabotage needed SQLite; only the *connection* did. `_PRE_0011`
is valid DDL on both backends. It now runs through a throwaway `NullPool` async
engine in its own loop — the reason `conftest.own_session` exists — and passes
**4/4 on Postgres and 4/4 on SQLite**.

### 2. Four skips on both were noise, and noise is where a real skip hides

`test_no_card_funds_itself_by_selling_a_sleeve` was parametrised over all five
adversarial books and skipped the four that have no sleeves. **It was doing the
right thing** — it ran, and passed, on `equities_overweight`, the one book built
for it. But a test announcing four times per run that it does not apply is how
"4 skipped" became scenery.

*(I told Eran this test "has never run, on any book, ever" before I had run it.
That was wrong — `1 passed, 4 skipped` — and it is the same error he had just
caught me in one message earlier: a claim ahead of the observation.)*

It is now parametrised over `SLEEVE_BOOKS`, and an empty sleeve set is a
**failure** rather than a skip: the book declares its sleeves, so if
`sleeve_targets` stops resolving them the test would otherwise pass vacuously
forever.

### 3. The check that stops the next one — a skip is never a pass, enforced

The smoke scripts have said this for months; the suite did not. `conftest.py`
now fails the run on **any skip whose reason is not registered** in
`_ALLOWED_SKIPS`, printing the node id and the reason. The list is currently
**empty**: after the two fixes above, both backends run with **zero skips**.

Registering one is a decision someone makes and writes a reason for, which is
the whole difference from a count that drifts. Verified: a deliberately
unregistered skip exits **1**.

### 4. A fixture that believed it pinned prices, and did not

`test_accept_conserves_nav.py::pinned_prices` patched `registry.guarded_quote`
and nothing else. But `pricing_service` and `strategy_service` each do
`from app.providers.registry import guarded_quote` at import time, so they bind
the function once and a patch on the registry never reaches them. **Measured:
`pricing_service.guarded_quote is _quote` → `False`.**

Both of those modules *write prices* (`refresh_all_positions`, `buy_into_book`),
so the fixture left two writers quoting the real synthetic provider on a book it
was holding still. A test that thinks it controls its inputs and does not is the
same failure as one that skips: it reports a pass for something it never held.

### 5. The one place the diagnostic was needed was the one place it was off

`test_accept_conserves_nav.py` called `logging.disable(logging.CRITICAL)` —
which silences the `NAV CONSERVATION BREACH` line **PR 17 added for exactly this
failure**, naming every position that moved. Two CI sightings were diagnosed
from a bare number as a result. It now disables only `WARNING` and below, and
the assertion carries the API NAV, PR 17's in-session drift, the apply spec,
every position that changed and the full emitted card list.

### The +739.2 ILS leak is NOT fixed by this, and PR 20 did not fix it either

CI #180 failed on the **SQLite** job with `+739.22 ILS` on the same card, with
PR 20 applied. Evidence block as it stands:

```
RULED OUT   _rebalance_to's own writes - its guard compares total NAV over all
              positions to 1.0 ILS with the formula /portfolio uses.
            build_recommendations mutating inside the accept bracket - six
              consecutive GET /recommendations moved NAV by exactly 0.0000.
            Card ORDER - emitted, reversed and rebalance-last; worst 0.12 ILS.
            PR 20's currency flip - NEWLY RULED OUT: PR 20 is applied in #180
              and the leak still fired. That candidate was named "leading" and
              deliberately not claimed as proven. It was wrong.
ROOT CAUSE  UNKNOWN.
CONFIDENCE  Nothing - several unfalsified candidates, no observation separating
            them.
```

This PR ships the instrumentation that makes the next occurrence self-explaining
and **no speculative fix**. If the in-session drift is ~0 while the API NAV
moved, the change did not come from the handler at all, and that single line
halves the remaining candidates.

**Gate:** SQLite 1355 passed / 0 skipped; Postgres 1355 passed / 0 skipped; `ruff check app tests`
clean. **Zero skips on both backends**, for the first time.

**Ship:** `scripts/deploy/ship-pr21-a-skip-is-never-a-pass.ps1`
**Smoke:** none — this PR changes tests and fixtures only; no app behaviour
changes, so there is nothing on the deployed app to check. The gate is the proof.

Not financial advice.

## ✅ PR 20 — a price refresh redenominated a holding, and that was the CI leak

**CI run #178 red on `main` at `9e2dd08`, `test-postgres` only. It is CI run
#169's failure, with the identical assertion — `192360.0 <= 41000.0 + 1.0`.**
Two sightings, months apart, never reproduced from the number alone. The row
dump a previous session added to that assertion is what located it:

```
BBB qty=10.0   basis=100.0 invested=3710.0
AAA qty=107.0  basis=100.0 invested=39697.0
CASH qty=148953.0 basis=1.0 invested=148953.0
```

Both holdings are seeded on **TASE at 100 ILS**, so BBB's invested should read
1,000. It reads 3,710 — exactly 1,000 × **3.71**, which is
`BuiltinFXProvider._BASE[("USD","ILS")]`, the hardcoded rate the test
providers use. (The real market rate is ~2.98; 3.71 is a fixture constant, and
the multiplier in production would be ~2.97. The defect is the same size
either way — roughly triple.)

**Root cause.** The provider decides the market from the **ticker**, not from the
holding. `BuiltinMarketDataProvider.get_quote` is literally

```python
market = "TASE" if ticker.upper().startswith("TA") else "NYSE"
```

(`builtin.py:37`) and returns that market's currency. `pricing_service` stamped
`price_currency` straight from the quote, so **the provider's guess overruled the
market the user declared in the holdings form.**

**Measured on that exact book, one refresh:** NAV 41,000 → 235,687 and invested
41,000 → 152,110, both exactly ×3.71. And the CI dump's cash figure reconstructs
to the shekel — 293 shares at the builtin's synthetic 149.37, times 3.71, less
25% CGT on the gain = **148,953.0**, which is what CI printed.

**Why it was intermittent.** It needs a refresh to land *inside* the test. The
scheduler primes one on startup and Postgres runs are slow enough for the timing
to differ. The arithmetic was always deterministic — only the timing was not,
which is why "reproduce it, don't reason about it" kept failing on the number
alone and succeeded the moment the rows were printed.

**Why it was silent.** No exception, and NAV and invested move *together*, so
gain% stays plausible. A book that has quietly quadrupled looks like a book that
has done well.

**This is live in production**, not a test artefact. Any Tel Aviv or London
holding entered under a ticker the provider reads as American is multiplied by
the FX rate on the next refresh. Same family as PR 18's agorot bug: a provider's
opinion about currency overriding the book's own.

### The fix

A quote whose currency contradicts the declared market **is not a quote about
that holding**. So the refresh refuses it — and refuses the **price** too, not
just the currency stamp. There is no arithmetic that recovers the right number
from a quote about a different instrument, which is the same rule already applied
to a stale quote, for the same reason.

- the position's `market` is the user's **declaration**; `fx.MARKET_CCY` gives
  the currency it implies. A disagreement is reported, never applied.
- a market not in `MARKET_CCY` (`OTHER`) has no declaration to contradict, so the
  quote is trusted — better than nothing.
- **rows already redenominated are repaired**: the declared currency is restored
  (removing the ×3.71) and the row is flagged `price_unverified`, because its
  price was written by a provider answering about something else. The app says
  which part of the total it does not stand behind rather than picking a number.
- `/api/v1/portfolio` carries `price_currency_conflict` and `price_unverified`;
  the refresh reports `currency_conflicts` and `repaired_currency_rows`.

**This is almost certainly the +₪739.21 `rebalance_to_objective` leak as well** —
a card built while the rows read ILS and accepted after a refresh flipped them to
USD moves NAV between the two reads, with `_rebalance_to`'s own guard passing
because it measures a single consistent snapshot. Not asserted as proven: that
sighting was never reproduced and no BREACH line (PR 17) has been captured yet.
It is the leading candidate, and PR 17's reporting is what would confirm it.

### And a second finding, from Eran: the FX default is a frozen table

He noticed the 3.71 immediately — *"the USD/ILS rate is not 3.7 but 2.98"* — and
he is right. 3.71 is `BuiltinFXProvider._BASE[("USD","ILS")]`, a constant written
once and never revisited; the market was **2.98** on 2026-08-28.

That is correct for a test fixture and would be a **~24% valuation error** on a
real book. And `config.py:105` **defaults `fx_provider` to `"builtin"`**, so a
deployment that does not set `FX_PROVIDER` converts every foreign holding at that
table with nothing on screen to say so.

**Production is fine** — checked directly against the live app on 2026-08-30,
which reports an implied USD/ILS of **2.9728** across all three USD holdings, so
`FX_PROVIDER` is set there. But a fresh environment, or a lost variable, would
silently inherit the frozen table. `registry.fx_provider()` now logs a warning,
once per process, naming the hardcoded rate and how to fix it. The constant is
left alone deliberately: changing it to 2.98 only moves the day it goes stale,
and tests must not depend on a live rate.

*The wording is corrected throughout this file and in the new tests: 3.71 is the
multiplier the defect applied under the test providers, never a market rate. In
production the multiplier would be ~2.97 — roughly triple, either way.*

### Two test fixtures were incoherent, and are fixed rather than the check relaxed

`test_p0_safety.py` built holdings on **TASE** (`_pos(..., market=Market.TASE)`)
and quoted them in **USD** (`_q(..., market="US", currency="USD")`) — incoherent
from the start, and harmless only while nothing compared the two. Those tests are
about *freshness*, so the quote fixture is made coherent rather than the currency
check weakened; the currency behaviour is pinned by its own file.
`test_cash_pricing_guard.py`'s `_Row` stub had no `market` at all, modelling a row
that cannot exist (`market` is NOT NULL on `Position`). The refresh also reads
the field defensively now — a reprice must not 500 the app because one row is
odd, and no market means no declaration to contradict.

**Gate:** SQLite 1355 passed / 4 skipped; Postgres 1351 passed / 12 skipped; `ruff check app tests`
clean. All four tests in
`tests/test_price_refresh_keeps_the_declared_currency.py` fail on the old code.

**Ship:** `scripts/deploy/ship-pr20-declared-currency-wins.ps1`
**Smoke:** `scripts/smoke/smoke-minor-units.ps1` (extended — it already reads the
per-position currency and now checks the conflict flags too).

Not financial advice.

## ✅ PR 19 — two cards contradicted each other and rule 2 could not see either leg

Eran, 2026-08-29, on the deployed app: one card sold ~₪1,071 of Equities
(*"Rebalance toward your target mix"*, BOOK scope) while another bought ~₪3,139
of SOXL (SLEEVE scope). **SOXL is Equities.** Accepting both moves the mix the
wrong way. `contradictions.py` rule 2 exists for exactly this and reported
nothing — measured on the real pair: **0 contradictions found, 0 notes on either
card.**

**Two holes, both live at once, and the pair went through both.**

- `:111` — the sell side required
  `str(r.get("title","")).lower().startswith("sell")`. `_reconcile` merges the
  rebalance legs and renames the survivor *"Rebalance toward your target mix"*,
  which does not start with "sell".
- `:109` — `if not cls: continue`. The SOXL sleeve top-up is ticker-scoped and
  carries no `asset_class` at all, so the card was skipped before its direction
  was ever considered.

**The deeper defect, which the plan did not anticipate.** The merge does not just
rename the card — it *destroys the structure the check needs*. After
`_reconcile`, `apply.asset_class` names whichever leg sorted first and every
other leg survives only as a **title string** in `meta.merged_legs`. So a card
that sells Equities *and* buys Commodities presented itself to every consumer as
one card about Equities. There was no data to read even if rule 2 had wanted to.

**Fixed structurally on both sides, per the card-claim rule already in force**
(*"verify by applying the card's spec, never by asserting on card text"*):

1. the generator records `apply.direction` instead of leaving it to be read back
   off the title;
2. `_reconcile` writes `apply.legs[]` — one entry per leg with its own class,
   direction and amount — so the merged card describes itself as data;
3. `contradictions._class_moves` derives `{(asset_class, direction)}` from
   `apply.legs[]` → `apply.asset_class` + kind → `allocation_mix.classify` on
   every ticker the card names. **No branch of it reads a title.** A card with
   no `asset_class` is not a card with no asset class.

A card is never flagged against itself: a merged rebalance sells one class and
buys another in one decision, and flagging that would turn the fix into a
permanent false alarm on every rebalance — a fast way to get a real warning
ignored.

Still **reported, not suppressed**. Both cards are defensible alone and the
choice is Eran's; what was unacceptable was being told nothing. Each card now
carries a note naming the other.

**Measured, old code vs new, on the live pair:**

| | contradictions | `buys_and_sells_one_class` | notes on either card |
|---|---|---|---|
| before | 0 | 0 | none |
| after | 1 | 1 (`Equities`) | both |

**Gate:** SQLite 1351 passed / 4 skipped; Postgres 1347 passed / 12 skipped; `ruff check app tests`
clean. `tests/test_contradictions_read_structure.py` asserts on the reported
contradiction and on `_class_moves`, never on card text — including a test that
renames the card to five different titles and requires the answer not to move.

### Also in here: the key helper had the defect it was written to describe

`scripts/agent-key.ps1` shipped in PR 18 and Eran hit its failure within the
hour. `Get-AgentKey` preferred `$env:IW_AGENT_KEY` **unconditionally**, so an env
var holding a *wrong* value silently beat a working fallback — and the smoke
scripts then printed *"/portfolio did not answer"* when the server had answered
perfectly clearly with a **401**. Two very different failures rendered
identically, which is the class of defect these scripts exist to catch.

- `Get-AgentKey` now **tests** each candidate against the live API and falls
  through on a 401/403, naming the source it skipped. Only 401/403 counts as a
  rejection: a timeout or a 5xx says nothing about the key, so the key is kept
  rather than discarded for the wrong reason.
- The smoke `Api` helper prints the status code and says in words that a 401 is
  a rejected key, not an outage.
- The request splat is `$reqArgs`, never `$args` — `$args` is a PowerShell
  **automatic** variable holding the function's own unbound arguments. Same
  family as the `$h` that once clobbered the auth headers.

Verified against the live deploy from the sandbox: with `IW_AGENT_KEY` set to the
placeholder string, `Get-AgentKey` reports the 401, skips it, resolves the
committed key, and `Test-AgentKey` confirms that key authenticates while a
deliberately wrong one does not.

**Every `.ps1` written this session is now parse-checked with the PowerShell
language parser rather than eyeballed** — 0 parse errors across all seven ship
and smoke scripts. The ASCII-only and no-backtick rules were being enforced by
hand until now; a parser catches what a reader does not.

**Ship:** `scripts/deploy/ship-pr19-contradictions-read-structure.ps1`
**Smoke:** `scripts/smoke/smoke-contradictions.ps1` (read-only).

Not financial advice.

## ✅ PR 18 — agorot and pence were valued as shekels and pounds

Found while asking why no card ever names a Tel Aviv listing. It is a separate
defect from that one, it is a **money** defect, and it needs no TASE feature work
to reach a real book: the holdings form already offers Tel Aviv and London
(`index.html:267`).

**Root cause.** Several exchanges quote in a **minor unit** and say so in the
*currency code* rather than in the price. Verified live against the same Yahoo
endpoint `YahooMarketDataProvider` calls, 2026-08-29:

| symbol | quoted currency | quoted price | actually |
|---|---|---|---|
| `TEVA.TA` | `ILA` | 11030.0 | ₪110.30 |
| `POLI.TA` | `ILA` | 7761.0 | ₪77.61 |
| `ESLT.TA` | `ILA` | 211190.0 | ₪2,111.90 |
| `VOD.L` | `GBp` | 118.45 | £1.1845 |
| `AAPL` | `USD` | 319.70 | $319.70 — no trap |

`pricing_service` stamped `price_currency` straight from the quote, so the
position carried `"ILA"` and a price a hundred times too big. Nothing downstream
knew that code, and **both FX providers answered anyway**:

- the **builtin** provider has no `ILA` pair, so `get_rate` falls through to
  `rate = 1.0` and returns it **without erroring** — indistinguishable from a
  legitimate peg;
- the **live** provider raises, and `fx.fx_rate` caught everything and returned
  1.0, which its own docstring called *"failing safe"*.

Either way agorot were valued as shekels. Measured in the new test: a book of 100
Teva shares worth **₪11,030 reads as ₪1,103,000**. NAV, gain %, the allocation
mix, the concentration caps and every card sized against NAV inherit it — with no
exception and no log line.

**Fixed at the boundary, not at the thirteen call sites.** `fx.to_major_units`
converts a quoted price into its major unit and `pricing_service` calls it once,
before stamping. A normalisation each consumer has to remember is one a new
consumer silently opts out of — the same lesson as the funding path.

**The trap inside the trap: `"GBp".upper() == "GBP"`.** Pence upper-case to the
major currency they are one hundredth of, so uppercasing before the lookup turns
the detection into a no-op for exactly the currency that most needs it. The raw
code is matched first and `GBP` is excluded from the case-insensitive pass. There
is a test for this alone.

### The 1.0 fallback was not safe, and is no longer a default

It was reached in two very different situations and only one is survivable:

- **the provider wobbled on a currency it knows.** Falling back to 1.0 values a
  USD book at **~34%** of its worth at today's ~2.98 USD/ILS. The last rate that
  actually resolved is remembered per pair and served with a warning.
- **the code is one the provider has never heard of** — how `ILA` got valued at
  par. That is a wrong input, not a wobble, and it now says so at ERROR.

It still **returns** rather than raises: thirteen call sites do arithmetic on the
result. What it no longer does is fail *silently*.

**And the status had to become a separate thing from the value.**
`performance_service` decided a rate was missing by testing `r == 1.0` — the only
signal there was. That cannot tell a genuine parity from a failure, nor either
from a real rate that simply is not current. `fx_rate_with_status` returns
`live | stale | unresolved`; `unresolved` still reports
`unconverted_holdings`, `stale` reports the new `stale_fx_holdings`, and both
mark the payload `degraded`. `fx_rate` keeps its old signature for callers that
only want the number.

*The existing `test_a_missing_rate_is_reported_not_assumed` went red on this and
was **not** weakened to suit the change — it now clears the cache to pin the
never-known branch, and a second test covers the stale branch.*

### Repair for rows already written

A position stamped in a minor unit is renormalised at the **top** of the refresh
loop, before the quote. Deliberately: a ticker whose quote fails or is stale
keeps the price it already has, so on exactly the runs where the row is least
likely to be rewritten, the 100× value would survive another cycle. Same shape
and same reason as `repair_cash_row`.

The refresh reports `minor_unit_quotes` and `repaired_minor_unit_rows`, and the
position payload carries `price_currency_quoted` — so the normalisation is
auditable from outside the process rather than inferred. The whole failure was
that it did not happen and nothing said so either way.

**This is the prerequisite for PR 24** (real Tel-Aviv candidates). Adding `.TA`
names on top of the un-normalised path would have multiplied the exposure.

### Also in here: `scripts/agent-key.ps1`

The smoke script needed a key and there is no create-a-key endpoint —
`config.py:140` reads one `AGENT_API_KEY` and `auth.py:115` compares the header
to it. A new key means changing the Railway variable, which instantly breaks the
**34 committed scripts** that bake the old one in. This helper is what makes
rotating it a one-line change instead of a 34-file change: `$env:IW_AGENT_KEY`,
then a git-ignored `scripts/.agent-key`, then the literal already committed —
saying out loud when it falls that far. **It does not rotate the key.**

**Gate:** SQLite 1342 passed / 4 skipped; Postgres 1338 passed / 12 skipped; `ruff check app tests`
clean. All eleven tests in `tests/test_minor_unit_currencies.py` fail on the old
code.

**Ship:** `scripts/deploy/ship-pr18-minor-unit-currencies.ps1`
**Smoke:** `scripts/smoke/smoke-minor-units.ps1` (read-only; `-AllowRefresh`
triggers the reprice that repairs a row already stored in agorot).

Not financial advice.

## ✅ PR 17 — accepting a rebalance spent the whole cash balance

Found while instrumenting the intermittent NAV leak (STATUS-V2 §1), not by
reading the code. A different defect, deterministic, and live in production.

**Root cause.** `_book_target` returns **investable** weights only — Grow is
`{Equities .80, Fixed Income .10, Commodities .10}` and **no objective has a
`Cash` key at all**. The CASH row classifies as the asset class `"Cash"`, so
`_rebalance_to` looked up `target.get("Cash", 0.0)`, got `0.0`, computed a
desired value of zero, and set the balance to zero — spreading the money across
the equity legs.

Measured on the seeded USD book: **₪621.79 of cash → ₪0.00, in one accept.** It
also drove the balance straight through the app's own cash floor for that book
(`funding_service.cash_floor_ils` → ₪419.69), a policy this path never consulted.

**Why nothing caught it.** NAV is conserved by the move, so `_rebalance_to`'s own
guard passed and so did every test. What it actually did was **buy ₪621.79 of
equities** — no trade legs, no ledger entry, no tax — under a card whose text
says only *"Sell about ₪X of Equities to move toward 80%"* and never mentions
cash. That is the card-claim invariant on the accept path: a card may not
produce a change it does not claim.

**Fix.** A rebalance is a reshuffle of *invested* value between investable
classes. Turning cash into securities is a purchase and belongs to the
funded-buy path, which writes legs and says what it bought. Cash is now excluded
from both the classes being scaled and the NAV they are scaled against. The
proposed legs are consequently **smaller** than before, because the target
weights now apply to invested value rather than invested value plus cash.

### Two things that made it undiagnosable, fixed in the same pass

**1. Six decimal places written into a four-decimal column.** Every MONEY column
is `Numeric(18, 4)`; the writers handed it 6dp. Postgres rounds on write, the
identity-mapped ORM object keeps all six, so a conservation check reading the
ORM object measured a book the database never had. SQLite keeps whatever it is
given and shows nothing — which is why only `test-postgres` could ever have seen
it. Measured: writing `4.712276` shares reads back `4.7123`.

`app.models.tables.money()` now quantises to the column's own scale — read off
`MONEY.scale`, not repeated as a literal — and every writer goes through it:
`upsert_positions`, `buy_into_book`, `update_position`, `pricing_service`,
`reconcile_service`, `strategy_service`. Patch the boundary, not the call sites.

**2. The conservation invariant was enforced in one handler out of twelve.**
`_rebalance_to` checked itself; the other eleven actionable kinds changed money
with nothing checking them. The only thing standing behind them in production
was a test sweeping whatever cards the engine happened to emit that run — and
which cards it emits varies with process-global provider state, so a card shape
that never appears in a run is a handler nothing has ever checked.

Every accept now returns `nav_before_ils`, `nav_after_ils`, `nav_drift_ils`, and
logs a `NAV CONSERVATION BREACH` line at ERROR — kind, card, drift, and every
position that moved — when the drift exceeds the tax the card reported.

It **reports** rather than **refuses**, deliberately: several handlers commit
part-way through (`update_position` commits; the funding paths commit), so by
the time the drift is measurable there is nothing left to roll back. Making an
accept atomic is a change to the commit boundaries and was not smuggled in here.
**That is the follow-up**, and it is the only thing that would turn this from an
alarm into a gate.

### The +739.21 leak is NOT fixed and is not claimed to be

It did not reproduce. The full Postgres suite ran green as a control before any
change (1322 passed, 7m13s) and again after. Ruled out **by observation**, so
nobody repeats them:

- **Not `_rebalance_to`'s position writes.** Its guard compares total NAV before
  and after across *all* positions to a 1.0 ILS tolerance, using the same
  formula `/api/v1/portfolio` uses. A 739 ILS move cannot pass it.
- **Not `build_recommendations` mutating the book inside the accept bracket**
  (it is called at the top of `apply_recommendation`, before `rows` is loaded,
  so anything it changed would be attributed to the card). Six consecutive
  `GET /recommendations` moved NAV by exactly 0.0000.
- **Not card order.** The sweep was re-run in emitted, reversed and
  rebalance-last order. Worst drift 0.12 ILS — and that 0.12 *was* this cash
  bug, now zero.

The reporting above is what turns the next occurrence into evidence instead of a
bare number. **Confidence on the remaining leak: INFERRED, nothing.** The
observation that would settle it is a `BREACH` line from a live accept.

**Gate:** SQLite 1330 passed / 4 skipped; Postgres 1326 passed / 12 skipped;
`ruff check app tests` clean. The four new tests in
`tests/test_rebalance_leaves_cash_alone.py` all fail on the old code.

**Ship:** `scripts/deploy/ship-pr17-rebalance-leaves-cash-alone.ps1`
**Smoke:** `scripts/smoke/smoke-rebalance-cash.ps1` (read-only; `-AllowExecute`
accepts one rebalance card on the live book and checks the balance survives it).

Not financial advice.

## ✅ PR 15b / W2 — the gate's threshold was never a measurement, so it is yours

**Shipped 2026-08-25.** No migration, but **`ENGINE_VERSION` a4 → a5**.

`MAX_CAGR_GIVEUP_PCT = 1.0` has always carried the comment that it is *"decided,
not discovered"*. On this catalog it is also decisive —
`btm_vol_target_tqqq`, reproduced live and matching `STATUS.md:2911`:

```
ungated   29.80%/yr at -63.51%
gated     28.09%/yr at -55.19%
deltas    -1.71%/yr for -8.32 points of drawdown
```

It fails **only** because 1.71 > 1.0. `STATUS.md:2931` already put the question:
*"on a 3x leveraged sleeve, 8 points of drawdown for under 2%/yr is arguably a
trade worth taking… whether vol_target should be switched on is Eran's call."*

### Three changes, none of them a new measurement

**The limit is a setting.** `gate_verdict` takes `max_giveup_pct` and reports
which limit produced the verdict *and whether it is still the shipped one* —
`measure()` always passes the setting, so an `is None` check would have read
False even when nobody had changed anything.

**A gate that lost on cost alone reports where it passes.**
`would_improve_at_limit_pct` is **1.71** for `vol_target`, with a note naming
what that limit buys. Offered **only** when the gate reduced drawdown and lost
on cost — a gate that failed on *drawdown* cannot be rescued by any limit, and
offering one would invite raising the limit until something passes.

**The opt-in is per strategy and still off by default.** The rule is unchanged:
the gate ships off until a human has compared the numbers. What changed is that
saying so is no longer a code edit.

### Why the engine version moved

An opted-in strategy's **headline** numbers are the gated run. A gated CAGR in
the same field as an ungated one, with nothing to tell them apart, is worse than
not having the gate — so every row records `regime_gate_applied` and the bump
forces a recompute rather than a silent mix. An opted-in strategy whose gated run
*abstains* keeps its ungated numbers and says so loudly in
`opt_in_not_honoured`.

### And the comparison is finally visible

`backtest_service` has run every strategy gated and ungated on each nightly
refresh since the gate was written, and stored both. Before this change the only
reader of `robustness` in `index.html` was `out_of_sample` — **the regime block
was computed nightly and rendered nowhere.** A measurement nobody can see cannot
be the human comparison the ship-off rule is waiting for.

**Verification:** 17 new tests. SQLite 1270 / 4 skipped; Postgres 16 1266 / 12
skipped; `ruff check app tests` clean.

---

## ✅ PR 15 / W1 — walk-forward parameter sweeps, and not one strategy earns a win

**Shipped 2026-08-25.** No migration. Plan: `PHASE_W_PLAN.md`.

The spec calls these *"the only untested source of additional measured edge in
the family, and a bigger lever on excess than any sleeve size"*
(`BEAT_MARKET_TARGET_SOLVER_PLAN.md:936`). Measured across all six sweepable
strategies:

```
btm_trend_tqqq       INCONCLUSIVE           3/6  mean  +8.49   chose 150 in 4/6
btm_trend_soxl       FITTING_DOES_NOT_HELP  1/6  mean -21.58   chose 250 in 4/6
btm_vol_target_tqqq  INCONCLUSIVE           4/6  mean  +3.23   chose  45 in 3/6
btm_swing_dip        FITTING_DOES_NOT_HELP  3/6  mean  -0.11   chose   5 in 3/6
btm_swing_breakout   FITTING_DOES_NOT_HELP  3/6  mean  -6.62   chose  30 in 3/6
btm_factor_stack     NO_SWEEP                              buy_hold, no parameter
btm_dual_momentum    INCONCLUSIVE           4/6  mean  +5.35   chose 315 in 4/6
```

**Not one reaches `FITTING_HELPS`.** And nothing is stable: the modal value is
chosen in 3 or 4 folds out of 6 everywhere.

### Three of those would have been wins on the mean alone

`btm_trend_tqqq` averages **+8.49** while winning **three folds of six** — one or
two folds carrying the whole figure. That is why the bar has two halves
(`MIN_WIN_FRACTION` **and** `NOISE_FLOOR_PCT`) and why both are module constants
set *before* any number was measured. With six folds it is trivial to decide
afterwards what counts as a win, and that is how a sweep manufactures an edge.

### The question

Not *"which value wins"* — that always has an answer, the answer is always the
in-sample winner. It is **does fitting beat not fitting**, so every fold tests
the fitted value against the value **already in the catalog**: the counterfactual
that existed had nobody swept. The baseline is the default, not the grid's
median, because the median is a value nobody was running.

### Two corrections to the spec

Sweeps exist on **6 of 7** entries, not "every overlay" — `btm_factor_stack` is
`buy_hold` and abstains `NO_SWEEP` **by name**, because a strategy that silently
vanished would read as one measured and found wanting.

And they **are** run — nightly, full-sample, stored in
`StrategyBacktest.robustness`, and **read by nothing**. `robustness.sweep` has
zero consumers anywhere. The compute was already paid and discarded; what was
missing is the split and the selection.

### Geometry in sessions, not years

Second time this floor has decided a design. `_MIN_OBSERVATIONS = 260`
(*"below this, CAGR is noise"*) and a calendar year is ~252 — the 3y/1y geometry
recommended before the floor was found would have abstained on **every fold**.
Rolling, 756-session fit / 260 test / 260 step: **six folds**.

The fit window is fixed length on purpose — it makes the folds comparable, so
"the parameter is stable" is a claim the data can support. It costs the last
**197 sessions**, at the *recent* end, which are in no test window and are named
rather than absorbed.

### Nothing is written back

`backtest_service` already sets the rule that a measured-better variant ships
**off** until a human compares the numbers. A route that tuned a live strategy
from one history would be exactly the overfitting this module detects. The trial
spec is built per measurement and thrown away; a test asserts nothing assigns
into the caller's overlay.

**Verification:** 26 new tests. SQLite 1253 / 4 skipped; Postgres 16 1249 / 12
skipped; `ruff check app tests` clean.

---

## ✅ PR 14b — two Accept buttons, two questions, and a winner that was clipped

**Shipped 2026-08-25.** No migration.

### They were never the same question

The Target card offers *"Set your sleeves to 65%"* and directly below it *"Best
split found: 40% / 40%"*. Two green Accept buttons, two numbers, both correct:

| | asks | on this book |
|---|---|---|
| T2 `solve()` | the **smallest** sleeve share that **reaches** a target you typed | 65% |
| T6a `solve_split()` | the **largest** excess your ceiling **allows**, no target | 80% |

T2's card already said *"Anything larger buys return you did not ask for at risk
you did not agree to."* T6a's docstring already said *"No `excess_pct`
parameter, deliberately."* Neither said it where a reader sees both numbers at
once — the card-claim invariant broken *across* two cards instead of inside one.
Both payloads now carry a `question` block naming what they asked and pointing
at the other, and **neither calls the other wrong**: they are different
questions, not a right and a wrong answer.

### The split winner was clipped, not chosen

Every one of the top eight splits had **40 in the first column**. Each sleeve
holds a single ticker and the concentration cap is 0.40, so a 40% sleeve *is*
that ticker at exactly the cap. The search ran into a wall and stopped.
`coverage.method` already said *"the best point REACHED, not a proven global
optimum"* — true, and it still reads as "optimised".

`split_solver` now probes the winner's own neighbourhood and reports whether the
optimum sits on the edge of the admissible region — **generically**, because
that module is handed a `measure` callable and knows nothing about caps. Naming
*which* constraint binds is the caller's job, since only the caller knows what it
enforced.

Measured on the real book: winner `[40.0, 40.0]`, 4 of 6 neighbours
inadmissible, binding constraint `concentration_cap` on **QQQ** — the dip-buy
rule's *base* holding, not its leveraged leg. The cap is biting on what the rule
sits in when it is **out**, which is not where anyone would have looked.

The interior case is asserted too. A block that can only ever say one thing is
not a measurement.

### And one thing PR 14 got half-right in production

The ceiling line read *"Your ceiling is 58.6%"* while the input box showed
`43.1`. The input is uncontrolled, so a typed value does not reach `TGT.ceiling`
until Solve reads it — the line was right about the solve it described. It now
says **"Solved against a 58.6% ceiling"**, which removes the contradiction
without pretending to know what is in the box.

**Verification:** 11 new tests. SQLite 1227 / 4 skipped; Postgres 16 1223 / 12
skipped; `ruff check app tests` clean.

---

## ✅ PR 14 — the ceiling went stale, and every answer for a week was about the number

**Shipped 2026-08-25.** No migration.

`index.html:1727` seeds the drawdown ceiling **once**, from
`performance(range=MAX).max_drawdown_pct` — *"the risk the book already
carries"* — and only when it is null. After that it lives in the stored verdict,
and PR 11's nightly job re-runs *"the LAST verdict's own parameters"*, so
whatever was seeded propagates forever.

A **13.3%** ceiling survived on this book from an era when the history provider
returned a short window. The same endpoint now measures **58.59%** over 2,513
sessions. Every answer the solver gave was "no" — because of the number:

| search | at 13.3% | at 58.6% |
|---|---|---|
| T2 | `DRAWDOWN_BOUND` | `REACHED` |
| T6b core | `NONE_ADMISSIBLE`, all 15 breach | `RANKED`, current holdings 3rd of 15 |
| T6b2 count | `BASELINE_BREACHES_CEILING` | `BELOW_NOISE_FLOOR`, count 2 converged |

Nothing about the book changed between those two runs.

### It does not re-seed

Silently moving a risk constraint the user owns is worse than leaving it wrong —
the rule PR 11 already states: *"a constraint strong enough to disable a button
has to come from a number the user actually set."* So `solve()` **measures** the
mismatch and says it; the number stays theirs.

`ceiling_check` rides on **every** exit path of `solve()`, not three of four — a
field present on some outcomes is a field no reader can rely on. `as_constraint`
exposes it with an explicit `known` flag, because "we did not check" and
"checked, nothing wrong" are different claims and a pre-PR-14 verdict can only
say the first.

### The card names one cause, in precedence order

Its action has been rewritten three times in one day — PR 12 said *change the
core*, PR 13 measured that and said *the sleeves*, PR 14 measured that and says
*check your ceiling first* — so it stopped being a branch each time and became
one ordering, in `_ceiling_cause`:

1. **The ceiling**, when the book already breaches it unchanged. It bounds every
   finding underneath, so naming anything else first answers a question the
   reader cannot act on yet.
2. **The sleeves**, when T6b measured every core and none clears.
3. **The core** — the only case where "change the core" is supported.

An *unchecked* ceiling never seizes the headline. The precedence is total and
asserted parametrically: a card that can name two causes will name both.

### My own contribution to the problem

`13.3` was a **default parameter** in three of my smoke scripts, so every
`-Solve` run fed it back into the stored verdict. They now read the ceiling off
the stored verdict and skip loudly when there is none.

**Verification:** 16 new tests. SQLite 1216 / 4 skipped; Postgres 16 1212 / 12
skipped; `ruff check app tests` clean.

---

## ✅ PR 13b / T6b — discover the sleeve count, and say how the search ended

**Shipped 2026-08-25.** No migration, no stored verdict.

T2 resizes a fixed set; T6a sizes each member of that fixed set. Neither can
answer *"should there be a third one at all"* — and this book runs two sleeves
for no measured reason.

### The honesty is in the report, not the algorithm

Greedy forward selection is four lines. What makes it usable is that *"it stopped
at two"* can be **checked**: every round records the candidates it tried and what
each would have added, and the stopping round records the addition it rejected
and by how much it missed.

The stop reason is a first-class field, because these mean different things and a
bare count renders them identically:

| | means |
|---|---|
| `BELOW_NOISE_FLOOR` | converged — the finding |
| `NO_ADMISSIBLE_ADDITION` | the held set is fine, nothing improves it |
| `BASELINE_BREACHES_CEILING` | the held set already breaches |
| `MAX_SLEEVES` | the pool ran out, not the improvement |
| `RUNTIME_BACKSTOP` | a budget stopped it; the count is a **floor** |

`converged` and `count_is_a_floor` are separate booleans for that reason.

### `BASELINE_BREACHES_CEILING` was found by running it

Every candidate third sleeve breached, so the search returned
`NO_ADMISSIBLE_ADDITION` with `count=2` — technically true and badly misleading,
because a reader sees 2 and concludes two is right. The measured truth is that
the two already held measure **44.6% against a 13.3% ceiling**. It's now its own
outcome, checked *before* the first round, and its verdict says in words that the
count is not a recommendation.

### Measured on the live book

```
ceiling 13.3, from the two held  -> BASELINE_BREACHES_CEILING, 1 measure
   "The 2 sleeve(s) you already run measure 44.6% against your 13.3% ceiling"
ceiling 45.0, from zero          -> BELOW_NOISE_FLOOR, count 3
   +1.80 btm_swing_breakout, +4.30 btm_trend_tqqq, +5.14 btm_trend_soxl
   4th rejected at -0.82
```

### Two things it refuses to conflate

**The count is not the size.** Every set is measured at an *equal* split of the
same total sleeve share. A three-sleeve set that wins because the search also
handed it more of the book has not shown that three beats two.

**Greedy is not exhaustive.** The best pair is not necessarily the best single
plus one, so a set this search rejects may beat the set it keeps.
`method.caveat` says so on the payload rather than letting "discovered" imply
otherwise.

**Verification:** 24 new tests. Suite green on SQLite and Postgres 16, plus
`ruff check app tests`.

---

## ✅ PR 13 / T6b — the solver varies the core, and the answer is "none of them"

**Shipped 2026-08-25.** No migration — T6b rows go into the existing
`solver_verdicts` table tagged `solver_version = "t6b"`.

### The finding

All fifteen candidates — the fourteen `strategies.CATALOG` families plus the
current holdings as baseline — measured **inadmissible** against the 13.3%
ceiling. Shallowest drawdown on offer: **31.92%**.

```
                              ex260   dd260  |  dd10y   sessions
Aggressive AI & Semis         65.39   39.35  |  75.43   260/2513
Leveraged Momentum            59.31   40.43  |  77.71   260/2513
Diversified Global Growth     57.52   36.80  |  72.02   260/2513
Dividend Growth               57.37   33.93  |  69.58   260/2513
Your current holdings         54.43   31.92  |  69.46   260/2513
...
High-Yield Income             51.37   34.08  |  69.45   260/1573   <- JEPI, 6.2y
Capital Preservation          47.40   34.46  |  66.87   260/2513
```

With 64% of the book in TQQQ and SOXL the sleeves dominate and no core repairs a
ceiling they have already broken. **Cross-checked independently:** the core alone
at 97% measures 35.12%, and the stored target-solver verdict says 35.1% — two
code paths, one number.

So the payload leads with a `verdict`, not a table. A ranked list of fifteen rows
and no verdict reads as a menu whose top row is a fix.

### It contradicts the card PR 12 shipped two hours earlier

That card's action was *"Change the strategy managing your core."* The
measurement refutes it — the breach is real, the remedy was wrong. The card now
reads the stored core search and drops that advice once refuted, keeping the
original wording when no search has run (the honest thing to say when nothing has
measured the alternatives). A single card giving advice its own measurement
refuted is the PR 9 defect from the inside.

### The window

Ranked on the recent window — Eran's decision of 2026-08-25, taken against the
spec's recommendation with both sets of drawdown figures in front of him. The
spec's table claims the short window has "no bear market"; measured that day it
is false (SOXL −69.42%, TQQQ −37.17%, SPY −9.13% inside it). But BND measured
−4.02% short against −23.96% long, so **both windows are measured for every
candidate** and disagreement is flagged in the payload.

**260 sessions, not 250** — `_MIN_OBSERVATIONS = 260 # ~1 trading year; below
this, CAGR is noise`, and `measure_blend` abstains under it. A 250-session
request returned INSUFFICIENT_HISTORY for all fifteen. The ranking window is the
shortest window this engine will measure at all.

### Two bugs found by running it

**`_fetch` called a rate limit a delisting.** First live run: 18 of 28 tickers
came back "no history" — SPY, VTI, QQQ, MSFT among them — purely from rate
limiting, and every candidate was excluded as unmeasurable. Same defect PR 10
removed from the screener. `_fetch` now retries with backoff and separates
`unavailable` from `no_data`; the legacy two-tuple is unchanged.

**My first concentration check compared basket-internal weights to the cap** and
excluded 8 of 14 families, including Classic 60/40 (VTI is 60% *of the basket*)
and Capital Preservation (BIL 60%). A core that is 36% of NAV holding VTI at 60%
of itself puts 21.6% of the *book* in VTI. It now reads
`peak_weight_pct_by_ticker` off the measurement. The baseline stays exempt.

### Per-blend date axes

JEPI listed 2020-05. One shared price feed would have silently truncated all
fourteen candidates to `inc_high_yield`'s six years, because `align()` intersects
across everything in the feed. Each blend gets its own axis — that's the
`260/1573` row.

### The firewall

investing-discipline §5. Candidates come from `strategies.CATALOG` and nowhere
else. `/plan/target` has `/apply` and `/undo`; `/plan/target/cores` deliberately
has neither and returns no `execution_plan`. The disclaimer ships with the
payload.

### Also

`latest()` now excludes `t6b` rows — it filtered on nothing before, so a core row
would have been handed to the war room as a target-solver constraint, and
NONE_ADMISSIBLE is not in `BLOCKING_OUTCOMES`, so the constraint would have
quietly stopped blocking.

`run_solver_refresh_blocking` loses its swallow, the same correction PR 12 made
to `pricing_service`. I wrote that in PR 11 with the older bug in front of me.

**Verification:** 51 new tests. SQLite 1176 passed / 4 skipped; Postgres 16 1172
passed / 12 skipped; `ruff check app tests` clean.

---

## ✅ PR 12 — three things that reported success, or reported nothing, while wrong

**Shipped 2026-08-25.** No migration.

### 1. The price refresh crashed on every boot and reported success

`scheduler.start_scheduler` primed the job with a bare
`run_price_refresh_blocking()` on the FastAPI startup thread. That thread has a
running event loop; the function calls `asyncio.run()`, which refuses to nest.
Every boot logged it and then returned `{"updated": 0, "failed": 0}` — the same
two numbers a clean run produces when nothing needs repricing, so a crash and a
quiet no-op were indistinguishable downstream. Seen live in the 2026-08-25
16:07:27 Railway log:

```
[WARN] scheduled price refresh failed
  exc="RuntimeError: asyncio.run() cannot be called from a running event loop"
pricing_service.py:256: RuntimeWarning: coroutine '_refresh_all' was never awaited
  return {"updated": 0, "failed": 0}
```

The call was also outside `_guarded`, so `JOB_STATE` never heard about it and
`push_service.blockers` — taught by PR 2 to read job state — could not name it.

Exceptions now propagate to `_guarded`, the one place that records an outcome,
and the prime goes through the **same** registration via `next_run_time` instead
of a second call site. `_guarded` already runs its target in a worker thread,
which is the context `asyncio.run()` needs, so the 30-minute runs were always
fine — only the prime was broken.

### 2. `floor_breaches_ceiling` had no card

PR 11 stored the verdict and let the war room take a one-tap away with it. But
the verdict's biggest statement is not about any single signal: the **core alone**
breaches the ceiling, so no sleeve size repairs it, **including zero**. On this
book that is 35.1% against a 13.3% ceiling, 21.8 points over, and Today showed
five cards, none of which mentioned it.

`_core_ceiling_recs` is deliberately **not** part of `_war_room_recs`: that
function needs war-room signals to attach a constraint to, and this book had one
displayed session which was a Rebalance. A 21-point breach would have gone
unsaid for want of an unrelated BUY.

`apply.kind` is `"none"` permanently — swapping a core is a whole-book change and
`target_solver` is read-only without exception. The card carries the verdict's
age, and a **stale verdict still speaks**: PR 11's rule is "stale stops blocking,
it does not stop speaking", and this card blocks nothing. No verdict at all means
no card; the smoke fails if a card appears with no verdict behind it.

`as_constraint` gains `core_drawdown_pct`. `_headline` already stated it in
prose; a card that has to pull the figure back out of a sentence is one rewording
away from printing nothing.

### 3. A 20-point drift rendered as "nice to have"

`severity` was the literal string `"MEDIUM"`, so a book 3.1 points out of target
and one 20.4 points out looked identical. Four other card types in the same file
already scale by magnitude. `_drift_severity` scales on the **multiple** of
`allocation_drift_threshold` rather than fresh absolute cut-offs — that setting
already decides a drift is worth a card at all. At the default 3%: HIGH at 3×
(9 points), CRITICAL at 6× (18). This book: Equities CRITICAL, Fixed Income HIGH,
Commodities HIGH.

And `_reconcile` now takes the **max** severity of the legs it merges. It rewrote
the merged card's id, title, action, why and meta and never its severity, so it
kept `rebal[0]`'s — whichever leg sorted first, which has nothing to do with which
is worst. Latent only while every leg was hardcoded MEDIUM: fixing the scale
without this would have introduced the bug the scale was meant to fix.

### A process note worth keeping

I told Eran PR 11's deploy had failed. It had not. I probed
`/api/v1/war-room` once, seconds before the new container came up at 16:07:27,
found no `solver_constraint`, and reported a build failure — sending him to `gh`
and Railway logs for nothing. The deploy landed 21 seconds later. **The ship
scripts poll `/health` for eight minutes for exactly this reason and I did not
follow my own script's discipline.** A single probe at a deploy boundary is not
evidence.

**Verification:** 16 new tests. Suite green on SQLite (1141 passed, 4 skipped)
and on Postgres 16 (1137 passed, 12 skipped), plus `ruff check app tests`.

---

## ✅ PR 11 / T5 — the war room reads the solver, and the red team can finally veto

**Shipped 2026-08-25.** Migration `0020_solver_verdicts`.

`STATUS.md:79` said T5 was *"the solver becomes a constraint the Risk / Decision
/ Adversary agents read; not an eighth agent, not the decision maker."* Reading
the code first turned up two things the plan did not know.

### 1. The Adversary could not veto anything on the card path

`build_war_room()` runs `vet` / `optimize` / `rank` / `display` individually and
appended the Adversary's findings as transcript **prose**, branching on nothing.
`adversary_enforce_veto` defaults to **True** and was honoured in exactly one
function — `StateMachine.cross_examine` — reachable only from
`/api/v1/decision-feed`. **The route that produces the cards people actually see
had no veto at all.** A `Severity.BLOCK` finding was a sentence.

`test_war_room_includes_cross_examination_lines` did not catch it. It asserts
the Adversary lines exist and carry a severity field — true the whole time, and
silent on whether anything acts on them. Coverage that looks like coverage.

The rule now lives in **one** place, `StateMachine.blocks_now()`. `cross_examine`
had it inline four times as `enforce and note.blocks`; the war room had it
nowhere. Both call the same function, so the two paths cannot drift apart again —
the same argument as PR 8's columns and PR 9's target.

### 2. Nothing stored a solver verdict, so nothing could act on one

`GET /plan/target` recomputed from scratch on every request: a coarse-then-fine
grid sweep, each point one simulation of ten years of daily closes. Meanwhile:

| | cost |
|---|---|
| war room | **6,726 ms** on `/recommendations` |
| every other agent | **< 50 ms** |

So "the agents consult the solver" could not mean "call the solver" — that puts
a multi-second sweep on the slowest path in the app. The solver now **writes**
(`solver_verdicts`) and the war room **reads**.

Two properties make a stored verdict safe to act on:

- **Age travels with the answer.** A verdict is a statement about one book at one
  moment and this one can disable a button, so `as_constraint()` returns the age
  and every card that acts on it prints it.
- **Stale stops blocking, it does not stop speaking.** Past 36 hours — about 1.5
  nightly runs, so a missed job — the constraint keeps its finding and drops its
  teeth. Letting a week-old measurement go on refusing things is how an app
  starts saying no for reasons nobody can reconstruct.

Only `UNREACHABLE` and `DRAWDOWN_BOUND` block. `REACHED_ABOVE_CAP` deliberately
does not: it means *reachable, past your concentration cap*, which is a different
conversation and not one that should disable anything.

### What a blocked card does

**It keeps the card and loses the one tap.** `apply.kind` is demoted to `"none"`,
which is the same mechanism every advisory card already uses — `_is_actionable`
reads it, so nothing new can forget to check a new flag. The finding, and the
verdict's age, land in `notes` on the card.

Suppressing it was the alternative and it is the PR 4 failure mode: eleven cards
went missing there, and a card deleted because of a background measurement the
user cannot see is strictly worse than one that explains itself and refuses.

**Scoped to signals that ADD risk.** A trim, a hedge or a de-risking rebalance is
exactly what a book that cannot reach its target should be doing; blocking those
would turn a safety feature into a trap. Keyed off `action_type`, not the path
label — `path` is "Bulletproof" for everything that is not a BUY, and that label
has already been misread once (PR 4).

**And the transcript says the constraint was consulted even when it does not
bite.** Silence is indistinguishable from "nobody asked", which is the state this
whole phase exists to end.

### The nightly re-solve

04:10, after the 03:30 backtest refresh, because a re-solve reads the
measurements that job writes — solving first would answer from yesterday's
numbers and stamp today's timestamp on them.

**It does not invent a question.** A solve needs a target excess and a drawdown
ceiling; neither is stored on the plan (the UI passes them as query parameters,
so they exist only in the answer). The job re-runs the **last verdict's own
parameters**. A book whose owner has never opened the target card has no stored
question, gets no verdict, and its war room says *"no solve on record"* rather
than being handed a ceiling nobody chose. A constraint strong enough to disable a
button has to come from a number the user actually set.

Its own `NullPool` engine inside its own loop, per `pricing_service` — handing an
APScheduler thread a connection from the app's shared pool is the cross-loop
asyncpg rejection that has already cost this repo three incidents.

---

## ✅ PR 10 — the app could not buy a bond, on any book, ever

**Shipped 2026-08-25.** No migration.

`universe.full_universe()` returned 83 candidates: **64 Equities, 19
Commodities, zero Fixed Income** — on an app whose objective presets have wanted
bonds since the first commit (Grow 10%, Balanced 30%, Income 50%, Preserve 60%).
So `_redeploy_cash_recs` searched `_buy_ideas` for a Fixed Income candidate,
never found one, and filed the money under `unplaced` with the reason *"you hold
no Fixed Income and no candidate was available"* — accurate, and reported on
every scan, for every book, forever.

PR 9 is what made it urgent rather than merely wrong. The live core targets 40%
Fixed Income of a 36% core = **14.4% of the book, ~₪3,054**, and the rebalance
card finally asked for the right number while the app still could not name one
thing to buy.

**Now:** `Redeploy ₪3,115 of idle cash → ₪1,917 into BND; ₪1,198 into CANE`,
with `unplaced` empty. Verified end to end on a book rebuilt to match his.

### What a bond catalogue has to carry that an equity one does not

**Duration**, because it is the entire risk — SGOV moves ~0.1% when rates move
1%, TLT moves ~16%. **Credit**, separately, because credit risk is equity risk
wearing a bond's name: high yield fell *with* stocks in 2008, 2020 and 2022, so a
book buying ballast against a 64% leveraged-equity sleeve and getting handed HYG
has bought more of what it was hedging. And **caveats** — every fund is USD-
denominated, so on an ILS book the "safe" asset carries FX risk.

All 24 entries verified against the live Yahoo provider on 2026-08-25: 251 daily
bars each, ending on the previous close. The stated durations correlate with
measured annualised volatility at **r = 0.94** — evidence the metadata is sane,
and the reason it is still never quoted to the user *as* a measurement.

### Three bugs found by building on the existing code

**1. Eleven bond ETFs would have been counted as Equities.** `classify` falls
through to "Equities" for anything it does not recognise, and
`FIXED_INCOME_TICKERS` was missing VGSH, VGIT, VGLT, USFR, SCHP, VTIP, VCSH,
VCIT, USHY, EMB and VTEB — every Vanguard and Schwab ticker, which is to say all
the cheap ones. The leg would have looked filled while the gap it was meant to
close went untouched. Exactly the commodity `classify` bug PR 4 fixed, one asset
class over.

**2. `_pct_ranks` did not handle ties, and it is shared by every screen.**
`sorted` breaks ties by list position, so eight funds all charging 0.03% were
handed percentiles from 100 down to 70 *by their order in a data file*. Moving a
line changed the recommendation. Fixed to share the mean rank across each run of
equal values; `rank_equities` and `rank_commodities` inherit the fix.

**3. A rate-limited call was treated as a delisted ticker.** PR 5b's liveness
fix dropped any candidate whose history call raised — but `CircuitOpenError` and
`RateLimitedError` say nothing about the ticker. Reproduced while building this:
ranking bonds three times in one process rate-limited the later calls, twenty of
twenty-four candidates silently vanished, and all three rankings came back
**identical and wrong**, including a 6-year fund recommended for a 0.3-year
mandate. The ranking changed with transport luck and nothing said so. The
resilience tier already draws the line (`is_no_data` — a `ValueError` means the
provider answered); this stops throwing that answer away, in both screens.

### The ranking, and why it is not the commodity ranking

`rank_commodities` scores 2:1 on trailing momentum. Copying that was the obvious
move and it is backwards: a bond fund's trailing return is almost entirely *what
rates did*, so ranking on it buys the most duration right after the move that
rewarded it — maximum sensitivity, worst entry.

The first attempt scored duration fit against cost as percentiles, 2:1. That
failed on a real case: **SGOV, a near-exact match for a cash-like mandate, lost
to a fund with nineteen times its rate sensitivity, on six basis points of fee** —
because percentile ranks discard magnitude, so 6bp cost the same 42 points a
46bp difference would. Every weight ratio that fixed it was reverse-engineered
from the answer I wanted.

The structure was wrong, not the weights. Missing the duration is a *risk* error;
paying more is a *return* error; for ballast the risk error is the entire point
of holding the thing. So **duration is a gate and cost ranks what survives it**,
with fit as the tiebreak. A mandate nothing matches widens the band and flags
every pick `outside-mandate-band` rather than silently returning a 6-year fund
for a 30-year ask.

| mandate | pick | why |
|---|---|---|
| ~6y (his core) | BND / AGG | the aggregate funds, 0.03% |
| ~2y | VGSH | short Treasury, not a 6-year fund |
| ~0.3y | SGOV | T-bills — the case that forced the redesign |
| ~15y | VGLT | same job as TLT at 0.03% vs 0.15% |

**US municipals are withheld from a non-USD book.** VTEB charges 0.03% at 6.5
years, so it beats most Treasuries on every factor this ranker measures. What it
sells is exemption from US federal income tax — and that exemption is priced
*into the yield*, so a non-US holder takes the lower yield and gets nothing back.
Gated on `plan.currency`, not on an assumption about who is reading.

**NOT fixed here:** the commodity screen is still trend-ranked and knows nothing
about the core's basket, so with All-Weather chosen (IAU 15% + DBC 10%) the
redeploy card offered **CANE** — sugar. It is a live commodity and the screen is
working as designed; the design is what is wrong. Backlog below.

---

## ✅ PR 9 — the app stopped arguing with itself

**Shipped 2026-08-25.** Three things Eran asked to check before the next PR.
All three were real, and all three turned out to be **one root cause with three
faces**: nothing forced a card to say which part of the book it was talking
about, so cards were computed at the wrong scope and nobody could see it.

Everything below was measured on the live book, not reasoned about:

```
NAV        21,205        sleeves  btm_swing_dip  38.4% -> TQQQ  (held 38.6%)
holdings   97.0% Eq               btm_trend_soxl 25.6% -> SOXL  (held 25.7%)
            3.0% Cash    core     bal_all_weather, 36% of book, 35/40/25
```

### 1. "Max weight 15% · Triggered · now 69.01" — that 69.01 was the share price

The API was right the whole time: `current: 38.6, unit: pct_of_portfolio,
level: 15.0`. The rules screen has a unit-aware renderer built for exactly this
bug — its own comment cites SOXL's $137.61 being compared to a 10% cap — and the
**triggered** branch was never converted. So a weight cap read its own weight
correctly right up until it fired, then started quoting dollars. The one branch
that matters most was the one left behind. The target column had the same
problem: `15.00`, indistinguishable from a price on a row sitting under a
stop-loss reading `65.70`. Both now read in points of book.

### 2. The rebalance card was out by exactly 2.78×

```python
mix,    _ = current_mix(rows)                    # the WHOLE book
target    = await _core_target(session, user, objective)   # the CORE's mix
report    = AllocationEngine().compute(target, mix, nav=nav)   # sized on FULL nav
```

The core governs **36%** of the book. Its target was measured against 100% of it
and sized off full NAV, so every leg was overstated by `1/0.36`:

| the card said | correct | |
|---|---|---|
| Sell Equities ₪12,139 | ₪4,324 | 2.8× |
| Buy Fixed Income ₪8,469 | ₪3,054 | 2.8× |
| Buy Commodities ₪5,293 | ₪1,908 | 2.8× |

**And choosing a core made it worse**, which is the opposite of what choosing one
should do. Before that, `_core_target` fell back to `OBJ_TARGET[objective]` —
Grow, 80/10/10 — which at least reads as a whole-book target, so the error looked
like ordinary drift. Choosing All-Weather (35/40/25) on 2026-08-25 turned a
₪3,600 equity sale into a ₪12,139 one, on a book whose sleeves are 64% equity
**by design** and cannot be sold without abandoning the strategies.

`app/services/book_target.py` replaces it. The book's target is the two ADDED:
the sleeves' per-ticker targets classified and summed (`{"Equities": 0.64}`),
plus the core's mix scaled to the share the sleeves left
(`0.36 × 35/40/25`) — giving `76.6 / 14.4 / 9.0`, which sums to 1.0 **by
construction**, because the core's share is derived as the remainder rather than
read from a second place that could disagree. `AllocationEngine.compute` assumes
a target covering 100% of NAV and never checks; `_rebalance_to` renormalises
downstream, which hid the problem at the point it would have been caught.

`_rebalance_to` — the function **Accept** runs — reads the same helper. Fixing
only the card would have been worse than fixing neither: the screen would say
"sell ₪4,324" and the button would sell ₪12,139.

### 3. Two targets for one asset class, in one payload

`recommendations.py:2111` read the objective's commodity target while the
rebalance card three cards above read the core's. Live: *"Buy Commodities
~₪5,293"* (25%) beside *"Add a commodities sleeve — ₪2,120 of USO … against your
10% target"*. Both cards internally consistent; the pair nonsense. Seven readers
of `OBJ_TARGET` and one of `_core_target` were competing to define "your target
mix"; the ones that answer *what should the book be* now all read `book_target`,
and the ones that are genuinely per-class **budgets** keep `OBJ_TARGET` as a
documented fallback.

Also deleted: a private copy of `OBJ_TARGET` and a private `_classify` in
`api/routes/plan.py`, both with zero readers and both already drifted — the local
`_classify` had none of the commodity or bond ticker sets, so it called GLD, IAU
and DBC Equities. A dead fork of a lookup table is the PR 8 failure one layer up.

### 4. The 15% cap that fought a 38.4% sleeve

The loudest card on Today, HIGH severity, was *"TQQQ is 39% of your portfolio —
Trim 24.231 TQQQ"* against a sleeve **targeting 38.4% and sitting on it**.

Traced to source: `cap = max(15, 5*ceil(weight/5))` appears in **two** places —
`rules_service._suggestions_from` and `recommendations._momentum_recs` — and both
derive the level from the position's *current* weight, knowing nothing about
sleeves. The cap was suggested while TQQQ was small, armed by an *"uptrend,
protect your gains"* card, and the sleeve grew to 38.4% afterwards. Note every
rule on the live book carries `strategy_id: null`, so nothing could re-level it.

The bug only exists in the gap between arming a cap and resizing a sleeve — run
the same formula at today's 38.6% and it gives 40, which is why it was invisible.

Fixed at both sources (a suggested cap is floored *above* the sleeve target — a
cap exactly *at* the target fires the moment the sleeve is correctly funded,
which is the same bug with a friendlier number), plus a HIGH card for the caps
already armed. That card re-levels in one tap via `_arm_sleeve_caps`, which
already did exactly the right thing and had simply never run against caps armed
after the last apply. Behind a tap rather than on the read, because
`/recommendations` is a GET and a GET that quietly writes makes "does this book
have a stale cap?" depend on whether anyone opened the page.

### 5. Every card now names the strategy it serves

`app/services/card_scope.py`, three kinds: **sleeve** (the card is arguing with a
strategy you chose, and had better have a reason), **core** (the part no sleeve
claims), **book** (your goal, fees, cash floor — belongs to no strategy and says
so rather than pretending to be scoped). Derived from what cards already carry —
`apply.strategy_id`, `apply.ticker`, `apply.legs[].ticker`, `apply.rules[]`,
`meta.ticker` — and stamped once after reconciliation, so a merged card is scoped
from what it ended up containing.

A card naming *some* sleeve tickers and some not is **book**, deliberately: a
tax-harvest card listing four losers of which one is sleeved acts on the book,
and calling it a sleeve card would license it to argue with that sleeve's target.

### 6. Cards can no longer contradict each other silently

`app/services/contradictions.py`. `_reconcile` already existed and is about cards
saying the SAME thing twice; this is about cards saying OPPOSITE things once
each — every card individually defensible, the set incoherent. Four rules:

- **A card may not sell a holding at or below its sleeve target.** Suppressed,
  and the stale rule behind it named, because the rule is what needs fixing. A
  sleeve genuinely *over* target is still trimmed — the guard is scoped to the
  hazard, not the feature.
- **Buy and sell of one asset class** (live: *"Sell Equities ₪12,139"* beside
  *"Buy ₪2,120 of VXUS"*). Flagged on both, suppressed on neither — which to drop
  is the user's call.
- **Two targets for one class.** Reported loudly, never suppressed: this is a bug
  in a generator, not something to ask the user to arbitrate.
- **Cards that together exceed the book** (live: ₪5,003 + ₪12,139 + ₪2,180 =
  ₪19,322 of selling out of ₪20,567 held — each card funds itself and none knows
  the others exist).

Every suppression appends to `notes` on the surviving card and appears in
`contradictions[]` on the payload. A card that vanishes silently is how eleven
cards went missing in PR 4.

**Verified end-to-end** on a book rebuilt to match his: `/mix` and Today now
quote the same `76.6 / 14.4 / 9.0`, every card carries a scope chip, and arming a
15% TQQQ cap raises the HIGH re-level card. Full suite green on both backends
(1085 SQLite / Postgres 16 locally) plus ruff on `app` and `tests`.

**NOT fixed here, deliberately** — rule notes freeze the price they were created
at and present it as today's: *"Caps downside ~15% below today's 77.30"* when
TQQQ is 69.01 and the stop is 4.8% away. Cosmetic next to the above, but it is
the same class of staleness and it has its own backlog line below.

---

## ✅ PR 8 — one sleeve was never enough, and the spare copy was lying

**Shipped 2026-08-25.** Migration `0019_drop_plan_strategy_columns`.

**WHAT WAS ACTUALLY WRONG.** `plans.strategy` + `plans.strategy_sleeve_pct` can
express exactly ONE sleeve. `plan_sleeves` (migration 0014) replaced them with N
rows and C1–C4 moved every reader across. What was left behind was not a spare
copy but a **second answer**, and nothing kept the two in step.

Measured on the live book the day this was written:

```
plans.strategy   = btm_swing_dip @ 15.0%     <- stale
plan_sleeves     = btm_swing_dip @ 38.4%
                   btm_trend_soxl @ 25.6%
```

**23 points out of date, and still consulted.** Three fallbacks read it: the tax
harvester's sleeve-exclusion set, the strategy-signal service, and the
sleeve-drift card. Every one of them existed to keep the protection *"never
weaker than it was in C1"*. By the end they made it weaker, because they would
have computed from 15% a book that actually ran 38.4% — leaving a third of the
sleeve outside harvest protection, and measuring drift against a target nobody
set.

**WHAT WENT.** Readers in `recommendations.py` (×2), `strategy_signal_service`,
`strategy_service.load_basket`; writes in the `plan_service` whitelist,
`apply_strategy` and `retire_sleeve`; the `legacy` block and both fields on
`/plan`; the `_appliedIds()` fallback in `index.html`; both one-shot backfills
(`backfill_once`, `backfill_core_once`) and the startup call; the model columns;
and the startup DDL now **DROPs** rather than ADDs.

Both backfills had already been spent on every live database — production logged
*"0 sleeve(s) created"* on every boot. Their KV keys are deliberately **kept**:
they are the record that the migration happened, and the only thing that would
stop a resurrected backfill re-running on a book that has since had sleeves
removed by hand.

**THE TEST THAT HAD TO CHANGE SHAPE.** `test_startup_ddl_covers_migrations.py`
asserts every migration-added column is mirrored in `main.py`'s startup DDL —
because alembic does not run against production and that list is the actual
mechanism. It had no concept of a DROP, so it would have demanded these two
columns forever. It now walks migrations in order, lets a later `drop_column`
cancel an earlier `add_column`, ignores `downgrade()` bodies, and gained the
mirror-image assertion: **a column a migration drops must be dropped by the
startup DDL too.** Otherwise the DDL merely stops adding it and every existing
database keeps it — which is exactly the stale-second-answer failure 0019 exists
to end.

**~26 assertions were re-premised, not deleted.** Each one that read
`plans.strategy` was asserting something real through the wrong window; the
property moved to `/plan/sleeves` and the assertions moved with it. The clearest
example: `test_removing_the_named_sleeve_moves_the_legacy_pointer` guarded a
*repair* — re-point the column at some arbitrary survivor. It is now
`test_removing_a_sleeve_leaves_no_second_answer_anywhere`, which asserts the
thing that repair was standing in for.

Smoke scripts followed: `smoke-c1.ps1` C1.3/C1.5, `smoke-c2.ps1` C2.3,
`smoke-p4.ps1` drift sizing, `smoke-beat-market.ps1` active-strategy pick. Each
now asks the live deploy the one question only it can answer — *are the columns
actually gone from THIS database* — since the DDL, not alembic, is what runs.

---

## ✅ PR 7b — the core picker says what each family is FOR

**Shipped 2026-08-25**, on top of PR 7.

Eran: *"I want indication on the core strategy about which strategy group is it
(i.e. grow/balance/income/preserve) — it will help me decide."*

Fourteen names arrived with no way to tell **Leveraged Momentum** from
**Low-Volatility Defensive** except by squinting at the mix. The goal group was
already on every family in `strategies.py` — the catalogue is literally keyed by
it — and `/plan/core/options` had always returned it. The picker just never drew
it. Now each row leads with the group, and the four words are defined once on the
panel, because a chip whose word you have to guess is decoration.

**And the picker became reachable a second time.** `Clear` was the only way out
of a core you had already chosen, and between pressing Clear and pressing Use the
book has **no core at all** — every rebalance card silently falls back to
measuring drift against the objective. The first core is chosen before you have
seen what it does, so changing it is the normal case, not the exception. It gets
a `Change` button that does not pass through "unmanaged", and the family already
in use shows as `In use` rather than being offered again.

The panel also states the size the choice actually governs: the sleeves claim
64% and this choice cannot touch them. Both halves matter — picking a Grow core
does not make the book much more aggressive, and picking a Preserve one does not
rescue it.

**Still deliberately absent: the app does not recommend a core.** It cannot
honestly, yet. Every "worst fall" on that panel is an `est.` from the catalogue
profile, not a measurement, and a modelled number wearing the authority of a
measured one is the failure this app keeps having. The measured figures only
became readable again with PR 7's `benchmark_ticker` DDL fix; the ranked answer
is **T6b** (*"the solver varies the core"*), which is where a recommendation gets
a measurement behind it.

---

## 🔴 BACKLOG — the commodity screen does not know what the core wants

**NOT STARTED.** Found 2026-08-25 verifying PR 10.

With `bal_all_weather` chosen, the core's commodity leg is **IAU 15% + DBC 10%**
— gold and a broad basket. The redeploy card offered **CANE**. Sugar.

Nothing is malfunctioning: `screen_commodities` ranks 2:1 on six-month trend
against cost across all 19 candidates, and sugar had the trend. The design is
what is wrong — the screener has no idea a core was chosen or what basket it
names, so it answers "which commodity has momentum" when the question was "what
fills my core's commodity allocation".

PR 10 solved the same problem for bonds by making the *mandate* a gate. The
equivalent here is to prefer the core's own basket tickers when a core names
them, and fall back to the screen only for the part the basket does not cover.
That is a change to what the commodity card is FOR, so it gets its own change
rather than riding along in a bond PR.

---

## 🔴 BACKLOG — a rule note freezes its creation-day price and calls it "today"

**NOT STARTED.** Found 2026-08-25 in the same live pull as PR 9.

```
TQQQ stop_loss  "Caps downside ~15% below today's 77.30"   TQQQ is 69.01
V    stop_loss  "Caps downside ~8% below today's 357.75"   V is 382.41
SCHD take_profit "Takes some off the table if it climbs ~10% more"  0.6% away
SCHD max_weight "It's 28% of your book"                    SCHD is 2.5%
```

Every one of those was true when written and none is true now. The *levels* are
correct — the rules screen computes distance live and showed TQQQ's stop as
"-5% to trigger", which is right. It is the frozen **prose** that lies, and it
lies in the direction that matters: a stop described as 15% away is 4.8% away.

The fix is not to rewrite notes on a schedule (that would make the audit trail
unreadable). It is to store the note in two parts — the *reason*, which is
durable, and the *reference price*, which is a datum — and render the distance
live the way the progress bar already does.

---

## 🔴 BACKLOG — `alembic upgrade head` cannot run from scratch

**NOT STARTED.** Found 2026-08-25 while verifying PR 8's migration against a real
Postgres 16, which is the first time the whole chain had been applied to an empty
database in a long while.

```
$ alembic upgrade head
sqlalchemy.exc.ProgrammingError: DuplicateColumnError:
    column "subject" of relation "transactions" already exists
[SQL: ALTER TABLE transactions ADD COLUMN subject VARCHAR(255)]
```

**WHY.** `0001_initial` runs `Base.metadata.create_all`, so on a fresh database
every table is built from the **current** model — including columns that later
migrations then try to add. `0018_transaction_ledger` adds seven of them to
`transactions` unguarded, so it dies on the first. Migrations 0013, 0014, 0015
and 0019 all guard against exactly this; 0018 does not.

**WHY IT HAS NOT BITTEN.** This app does not run alembic against production —
the `ALTER TABLE ... IF NOT EXISTS` list in `main.py` is the real mechanism, and
`create_all` covers a fresh database. So the chain being unrunnable costs
nothing today and costs everything the day someone needs to rebuild from it, or
point a new environment at it.

**THE FIX** is the `_present(bind, col)` guard 0019 uses, applied to 0018's seven
`add_column` calls and two `create_index` calls. Deliberately **not** done inside
PR 8: quietly editing a shipped migration in an unrelated commit is how a chain
becomes untrustworthy. It wants its own change, and a CI job that runs `alembic
upgrade head` against an empty Postgres so this cannot silently return.

**Verified working meanwhile** (2026-08-25, Postgres 16, by hand): stamping at
0018 and running 0019 drops both columns when present; `downgrade` recreates the
shape and re-upgrading drops them again; and booting the app's own lifespan
against a database holding both columns drops them — which is the path
production actually takes.

---

## 🔴 BACKLOG — a card may not name a ticker that is not trading

**NOT STARTED.** Opened 2026-08-25 from a live defect. The immediate symptom is
fixed; the gate that would have prevented it does not exist.

**SYMPTOM.** Eran: *"I'm always getting the COW recommendation, though the COW
price is irrelevant and not updating for more than 6 months."* Both halves true,
and the second explains the first.

**WHAT WAS ACTUALLY WRONG — two independent defects that happened to compose.**

1. **Four delisted ETNs sat in the buy catalogue.** JJN, JO, NIB and COW are
   iPath ETNs Barclays has redeemed. Checked against Yahoo on 2026-08-25: the
   other 25 catalogue entries returned 251 daily bars ending on the previous
   close; those four returned **none**. `commodities.py` opens with the words
   *"real, live-priced, retail-investable instruments"* — a promise nothing
   verified. They are the same four symbols that opened the history circuit
   breaker (see PR 5), so one dead-ticker problem produced two unrelated-looking
   outages.

2. **Missing data scored as strength.** `rank_commodities` scores 2:1 trend
   against cost; `_trend_pct` returns `None` with no history; and `_mean`
   **drops** the `None`s rather than penalising them. So a candidate with no
   price data is scored **on its expense ratio alone**. COW's 0.45% is among the
   cheapest in the catalogue, so a dead ETN beat every live commodity that
   happened to be falling — deterministically, every scan. That is why it was
   *always* COW.

**SHIPPED NOW (the symptom).** The four are removed from `CATALOG` and recorded
in a `DELISTED` map with reasons, guarded by a test so re-adding one fails.
`screen_commodities` drops candidates the provider cannot price instead of
scoring them, so the *next* ETN to die is caught without anyone noticing. And
`_commodity_recs` abstains when nothing can be ranked rather than falling back
to a hardcoded `DBC` — a card that names a ticker chosen by no process reads
exactly like one that was screened.

**A SECOND GAP, FOUND FROM THE SAME CARD.** The redeploy-idle-cash card reads:

> ⚠ ₪1,224 could not be placed — you hold no Fixed Income and no candidate was
> available

It is not that no *good* bond was found. **There are no bond candidates at all.**
`universe.full_universe()` returns 58 Equities and 25 Commodities and **zero**
Fixed Income. So a Grow plan targeting 10% bonds can never place that leg, on any
book, ever — the money silently stays in cash and the card reports an
unplaceable remainder every single time. The catalogue has a curated commodities
list and a curated equity list and simply never grew the third one.

Fixing it is not "add BND": the same liveness question applies, the ranking
factors for bonds are duration and credit and yield rather than trend and
expense ratio, and `rank_commodities` / `rank_equities` have no third sibling.
Deciding what a bond candidate list is scored on is the work.

**NOT SHIPPED (the gate), and why it is its own piece of work.**

- **Liveness is not checked anywhere, for any asset class.** The equity universe
  has the same exposure: 58 hand-maintained tickers, no freshness check. The fix
  is one rule applied at the boundary — *no card may name a ticker without a
  close within N trading days* — which needs a decision on N, a cheap way to
  ask (a quote is cheaper than a 252-day history), and a policy for what a
  candidate list does when it thins out.
- **`_mean` dropping `None` is a scoring bug wherever it appears**, not just in
  commodities. `rank_equities` uses the same helper across fundamentals that are
  routinely missing for some names. Whether the right answer is a neutral
  penalty, exclusion, or a confidence weight per candidate is a real modelling
  decision, and it changes every equity score in the app — so it does not get
  made inside a bug fix.
- **`strategy_profile._COMMODITY` still lists COW** on purpose. Holding a dead
  ETN and being *sold* one are different claims: a legacy position must still
  classify as a commodity for allocation and risk, or it gets risk-scored as a
  32%-volatility single name (that note is already in the file). Tested.

## ✅ T6a — size each sleeve on its own axis, ranked out-of-sample

**Status:** in the working tree, **pending QA**. `ship-t6a.ps1` / `smoke/smoke-t6a.ps1`.
33 tests in `tests/test_split_solver.py`.

**T6a IS THE FIRST QUARTER OF PLAN-T6.** It does the per-sleeve split search. It
does **not** add strategies not currently held, vary the core, or discover the
sleeve count — those are **T6b**, and nothing in the code or the card pretends
otherwise. Eran chose this split deliberately, so that the Accept button stays
legitimate: there is still ONE answer, not a ranked list, and the plan's *"no
auto-apply, ever, from a ranked list"* is not yet in tension with it.

**WHAT T2 LEFT.** `_split()`'s own docstring: *"Sizing each independently is a
wider search, not this one. That search is T6."* T2 sweeps the TOTAL at the ratio
the book already runs; this sweeps the simplex.

**THE RISK IS NOT THE ARITHMETIC.** Picking the best of ~55 splits scored on one
history is in-sample optimisation. Search hard enough over one window and
something always wins, and that it won says little about whether it wins again —
and the output ends at a green button where a curve-fit looks exactly like a
finding. Every guard below is from the plan, applied rather than paraphrased:

- **Ranked out-of-sample** on `backtest_service.OOS_SPLIT`, **imported not
  restated** so two answers to "which window is out of sample" cannot drift. When
  no OOS figure exists it falls back and *says* it fell back.
- **The caveat travels.** `OOS_SPLIT`'s own comment is *"the only real bear market
  these instruments have seen."* One bear market is a sample of one, and the card
  says so instead of borrowing the authority of the phrase "out of sample".
- **Fit/test gap on every row**, with the winner's gap beside the **median** gap.
  If every candidate decays alike, that is the instruments; if the winner decays
  more, it was learned from the fitting window.
- **Spread** — best against median. Inside the noise floor the "optimum" is a coin
  landing, and **the Accept button is disabled**.
- **Coverage** — how many splits, what grid, whether the grid was coarsened, and
  in the *payload* (not a comment) that the result is the best point **REACHED**,
  never a proven global optimum. A grid too large is **coarsened, never
  truncated**: truncating searches a corner and reports the winner as though the
  whole space had been tried.

**THE APPROXIMATION, DECLARED.** The sweep judges the ceiling on the worse
half-window — a **lower bound**, since a fall spanning the split is larger than
either half sees. The winner is re-measured over the whole window before it is
shown, the same pattern `_verdict` already uses; if it breaches there, the payload
says so and the button stays disabled.

**REUSE, NOT A SECOND PATH.** `tgtAcceptSplit` delegates to `tgtAccept` — same
endpoint, same staleness refusal, same confirm text. A second apply path would be
a second set of guards, and they would diverge. `ship-t6a.ps1` fails if
`tgtAcceptSplit` ever calls `fetch` directly.

**COST.** Two simulations per grid point instead of one. That is why it is an
explicit button and not something the card runs on load.

### The plan's last open question — resolved

*Which window leads the card headline?* Open since 14 Aug, and **Phase N changed
the answer rather than settling the argument**: the 250-day backfill was only ever
a proxy for "how am I doing", and that question now has a real measurement.

- The solver card leads with the **ten-year** window — it asks "what would it
  take", which is a question about the rules and the blend.
- **"How am I doing" belongs to the Today card**, which as of Phase N is actual
  recorded account history.
- The 250-day backfill **headlines nowhere**. It lives on the Performance tab,
  labelled.

### Plan status after this phase

| phase | state |
|---|---|
| T0–T4, N | shipped |
| N2, A | in tree, pending QA |
| **T6a** | **this entry** |
| **plan-T5 — the war room** | **NOT STARTED.** The solver becomes a constraint the Risk / Decision / Adversary agents read; not an eighth agent, not the decision maker. *What shipped earlier under the name "T5" was the recommendation block, which is not a numbered phase in the plan and took the label from work still outstanding.* |
| **T6b** | not started — catalog strategies not held, varying the core, discovered sleeve count, top-N ranking |
| **Phase W** | not started — walk-forward sweeps, the `vol_target` regime-gate re-decision, contribution modelling |

## ✅ PHASE A + N2 — the button that acts, and the card that stops reconstructing

**Status:** in the working tree, **pending QA**. `ship-a.ps1` / `smoke/smoke-a.ps1`.
22 tests in `tests/test_target_apply.py`. Migration `0017_plan_applications`.

### PHASE A — the first write in the T line

**SYMPTOM.** Eran: *"the card is very good but still missing the most important
thing — the button that takes the action and follows the recommendation."* The
card could say "set your sleeves to 65%" and then leave him to type it.

**WHY IT WAS READ-ONLY.** T0–T5 wrote nothing, deliberately: *a return target one
tap from a book change is the C5 slider bug with higher stakes.* Relaxing that
is the risky part of this phase, so what makes it safe is enumerated rather than
assumed.

**THE BOUNDARY, UNMOVED.** `plan_sleeves` only — intended PERCENTAGES. No order,
no quantity, no price, no broker. `investing-discipline` §5. Asserted on the
**import graph** (`test_the_apply_module_cannot_reach_a_broker`): a module that
imports nothing capable of execution cannot execute. *The first version of that
check was a text search for "order" and it failed on this module's own
docstrings — sentences denying that it places one. A check that fires on its own
safety notes is one someone deletes for being noisy, and then nothing guards the
boundary.*

**THE FOUR FAILURE MODES, each handled before any row is touched:**

1. **Half-applied writes.** `(40,40) → (70,20)` applied in the solve's own order
   raises the first sleeve to 70% while the second still holds 40% → 110% →
   `sleeve_service.validate` refuses **that call** → the book is left at
   `(40,20)`. Neither plan, no error anyone asked for, and **every individual
   call behaved correctly**, which is exactly why nothing else in the repo
   catches it. `plan_apply` orders every DECREASE before every INCREASE, then
   *walks its own step list* and refuses if any intermediate total would breach
   — proving the rule rather than trusting it.
   → `test_a_raise_before_a_drop_would_half_apply`
2. **A plan solved against a book that has since moved.** The quietest one: the
   card's numbers describe a book changed in another tab, so "accepting" is
   really "overwrite with an answer computed for something else". No exception,
   entirely reasonable-looking sizes. `from_pct` is echoed from the solve
   **verbatim** so the server can compare it to the live row and refuse.
   → `stale_against`, `test_a_plan_solved_against_a_different_book_is_detected`
3. **A write with no way back.** `plan_applications` stores before/after
   **snapshots, not diffs** — a diff needs a base, and the base is exactly what
   a later reader will not have. `undo` is a read of the last row.
4. **A write nobody asked for.** `confirm=true` required; a refusal `rollback`s
   explicitly, because `apply_target` can return `ok=False` *after* flushing
   steps and only a rollback makes "nothing was written" true rather than
   intended.

**BLAST RADIUS.** New: `target_apply.py`, `0017`, `PlanApplication`, three routes
(`apply` / `undo` / `applications`). `index.html` gains `tgtAccept` / `tgtUndo`.
Nothing existing changed behaviour.

### N2 — the Today card stops reconstructing

**SYMPTOM.** Third time asked: *"the graph on Today is still not representing MY
ACCOUNT."*

**EVIDENCE.** `nav-history` → `ok=false, points=1, needs=3`. `index.html:732`
falls through to the backfill exactly as written.

**RULED OUT.** `drawTodayReal` missing — smoke N.7 found it in the served shell,
3/3 markers. Fallback unlabelled — it rendered *"This is not your account
history"* and *"1 of 3 days"*. History could start earlier — `Transaction(` still
has zero writers outside the models; past NAV is **unrecoverable**, not merely
unqueried.

**ROOT CAUSE.** Not a defect: one recorded day, `MIN_POINTS_FOR_SERIES = 3`. But
the card was still wrong to draw anything. **A drawn curve outranks the caption
under it** — so it answered "what would this book have done" while appearing to
answer "what did my money do", and the disclaimer lost that argument three times
running.

**FIX.** The Today card draws NOTHING until `nav_snapshots` can produce a real
series; it shows the days recorded, a "record today now" button, and why history
cannot be backfilled. The reconstruction stays on the **Performance tab**, where
it is the actual subject, and on the solver seed, which needs the ten-year
window — deleting either would over-apply the decision. `ship-a.ps1` asserts both
survived.

### THE LINT GATE — why CI went red twice

`ship-*.ps1` ran pytest and `py_compile`. **Neither is a linter.** CI runs
`ruff check app tests`, which no ship script had ever invoked — so #148 and #149
pushed red while ship-n and ship-t5 both reported green on a 899-test suite. Two
`E702`s (semicolon-joined statements) in `nav_history.time_weighted`.

`ship-a.ps1` now runs **`ruff check app tests` first**, the same command
`ci.yml` runs. It caught an unused `pytest` import in this phase's own test file
before the push, which is the shortest possible argument for its existence.

## ✅ T5 — the card ends in an instruction

**Status:** in the working tree, **pending QA**. `ship-t5.ps1` / `smoke/smoke-t5.ps1`.
28 tests in `tests/test_target_recommend.py`.

**SYMPTOM.** Eran, looking at the shipped card: *"I see the card and the
explanation, I don't understand what is the agent's recommendation and the
action item I need to do."* Every figure on it was correct. It still did not
tell him what to do.

**EVIDENCE.** His card, 2026-08-16: `DRAWDOWN_BOUND`, sleeves solved to **0%**,
core 97% / cash 3%, measured **−2.88%/yr over SPY at a 31.89% drawdown**, excess
at equal risk **−2.07%/yr**, ceiling **13.3%**, footer `1729 sessions ·
2019-09-26 to 2026-08-14`, seed note `over 250 days, 13.26% drawdown`.

**ROOT CAUSE — three, compounding.**

1. **Window mismatch in the seed.** `_seedTarget()` called
   `POST /portfolio/performance` with no `range`, which defaults to **252 days**
   (`intake.py:287`). The solver measures **~1729 sessions**. A ceiling seeded
   from a calm twelve months, then judged against a window containing 2020 and
   2022, can only come back breached. The card was reporting a *window
   difference* as though it were a fact about the book.
2. **Tautological target seed.** `TGT.excess` was seeded from
   `p.excess_cagr_pct` — the excess he already earns. The first solve therefore
   asked "what would it take to stand still". Answer: nothing, 0% sleeves. That
   is not advice, and it is why both sleeves read 0%.
3. **No instruction existed.** Five outcomes was the right model of the
   *answer*; it is not an answer to *"so what do I do"*.

**WHY SILENT.** Each defect produces a *plausible* card. A breached ceiling and
a 0% sleeve are both legitimate outputs, so nothing looked broken — the card was
answering a different question from the one being asked, in a voice that sounded
like it had answered.

**THE FIX.** `target_solver.recommend(v, *, benchmark)` — a **pure function over
the verdict**, so the sentence the user acts on is testable without a database, a
price feed or a browser. It computes nothing: every figure traces to something
the solve already measured, and
`test_the_recommendation_invents_no_figure_the_solve_did_not_measure` enforces
exactly that by extracting every numeral from the rendered text and requiring it
to be in the allowed set.

Two failure modes are held **by name**, because an instruction carries more
authority than a diagnosis:

- **Never point at the slider when the core is what binds.** When
  `floor.breaches_ceiling` is true, the core alone exceeds the ceiling, so no
  sleeve size — *including zero* — can help. Recommending a resize there points
  at a control that provably cannot move the outcome.
  → `test_it_does_not_send_him_back_to_the_sleeve_slider`
- **A ceiling is a permission, not a return.** Raising it adds no return; it
  stops the solver refusing to show options above it. The copy must say so in
  those words. → `test_a_raised_ceiling_is_described_as_a_permission_not_a_return`

Excess at equal risk, when negative, renders **above** the instruction. A book
behind per unit of risk has a problem no sleeve size solves, and answering the
smaller question first would bury the larger one.

**CAUGHT DURING THE BUILD (would have been a regression, not a fix).**
`recommend()` raised `TypeError` on a verdict with a missing figure — every
`{x:g}` interpolation. It runs at the *end* of every solve, so a solve that
measured perfectly well would have returned **500** and the user would have lost
the whole card. Strictly worse than the missing instruction. Fixed with `_g()` /
`_signed()` (render `?`, never raise), `_ceil_pct(None) → None`, and actions
with no computable value **dropped rather than rendered disabled** — a greyed-out
button still reads as "there is a lever here". Six parametrised totality tests
now cover it.

**STILL READ-ONLY, WITHOUT EXCEPTION.** `tgtApply()` refills this card's own two
inputs and re-solves. It issues no `POST`/`PUT`/`DELETE`/`PATCH`, and
`set_sleeves` is deliberately **inert** — it is the Phase A handoff, and a button
that looks like it resizes a sleeve while doing nothing is worse than no button.
Both properties are asserted against the *served* shell in `smoke-t5.ps1`.

**BLAST RADIUS.** `target_solver.py` (+~180 lines, additive), `index.html`
(+~70), `sw.js` v22→v23. No existing figure changed; the solve path is untouched.

**OPEN.** The seed window fix assumes `performance?range=MAX` (2600 days) and the
solver's ~10-year fetch land within 10% of each other. `smoke-t5.ps1` T5.1
measures both and fails if they diverge — **INFERRED until that runs against
production.**


## ✅ PHASE T0–T4 + N — the target solver, and the day history started

**Shipped:** `b2d5e98` (T0.3) → `87a47a0` (T0) → `0315b47` (T1) → `df0d85e` (T2)
→ `211682a` (T3) → `0df481f` (T3 fix) → `6edebc5` + `10d8b40` (T4). Phase N and
the T4 follow-up are in the working tree, **pending QA**.
Suite 856 passed / 4 skipped, ruff clean.

The Plan screen could offer a sleeve size but never said what the size bought.
`BEAT_MARKET_TARGET_SOLVER_PLAN.md` is the plan; this is what it cost.

### The framing that changed everything downstream
The target is **excess over the benchmark at a bounded drawdown**, not an
absolute return and not raw excess. Raw excess is bought with leverage — anyone
beats SPY holding 1.3× SPY — and every sleeve in this family is leveraged or
concentrated, so a solver maximising it returns the most leveraged admissible
blend every time. The drawdown ceiling constrains the ADMISSIBLE SET; it is not
a figure reported afterwards.

### T0.3 — the performance backfill was adding shekels to dollars
**Root cause:** `performance()` projected positions to `(ticker, quantity)`,
dropping `market` and `meta` — the only two inputs `price_currency` takes. The
worker was not ignoring FX; it had been handed no way to apply it. The
projection exists to keep ORM objects off the worker thread (MissingGreenlet):
**the safety fix caused the currency bug.**
**Reproduced** on the real function: 1 TASE holding + 10 MSFT gave a start value
64.3% low and an excess vs SPY of 8.33% against a true 12.62%.
**Ruled out:** "design choice, not defect" — nine other modules FX-normalise
through the same two helpers; only this one did not, while labelling its output
`_ils`.
**Why silent:** no test called `_performance_from`, and `test_performance.py`
uses two NYSE tickers — a single-currency book, where the defect cannot appear.
Rate is spot held constant (`guarded_fx` has no historical series), reported as
`fx_basis`, and a missing rate now reports `degraded:["fx"]` instead of passing
1.0 off as parity.

### T0 — two benchmarks, provenance, staleness
A strategy is measured against **the thing it levers** (`base`: QQQ for the TQQQ
sleeves, SMH for SOXL) as well as against the book benchmark, in separate
fields. A rule can beat its base while the book still loses to SPY — roughly the
current picture — and one number cannot show both. Every payload carries
`{start, end, sessions, kind}`. `strategy_backtests.benchmark_ticker` (migration
0015) makes `_is_stale` fire on a benchmark change: an excess rendered under a
benchmark it was not measured against is a *wrong* number, not a stale one.
`ENGINE_VERSION` a3 → a4.

### T1 — blend by simulating, not by averaging
`0.7×core + 0.3×sleeve` is not the CAGR of the blend, and for drawdown it is
worse: averaging two max-drawdowns assumes they never bottom on the same day,
and a leveraged sleeve bottoms on exactly the same days as its core. Instead the
daily target vectors are summed and the existing `_simulate` runs once, so the
true correlation falls out of the simulation. **The identity test is the
load-bearing one:** a blend of one component at full weight reproduces `bt.run`
exactly. `excess_at_equal_risk_pct` is the leverage-proof scoreboard, solved by
bisection because drawdown is not linear in exposure.

### T2 — five outcomes, three of which are "no"
`REACHED`, `REACHED_ABOVE_CAP`, `DRAWDOWN_BOUND`, `UNREACHABLE`,
`NOT_MEASURABLE`. Read-only without exception; `smoke-t3` fingerprints sleeves,
positions and cash either side of a solve and compares.
**Two defects the tests found, both real:**
1. The concentration cap was judged **book-wide**, so an already-concentrated
   core vetoed every sleeve size *including zero* — an answer that says nothing
   about the decision and hides the sleeve actually breaching. Now judged
   against `blend.claimable_tickers` (basket + base + risk_off; a gate ticker is
   not a position).
2. "Nothing admissible" meant two opposite things. A `floor` block now reports
   the drawdown at zero sleeve and whether it already breaches the ceiling, so
   *shrink the sleeve* and *raise the ceiling* stop looking identical.

### T3 — what it costs, and the conversion that would have been silent
Drawdown in shekels with its recovery figure (down 45% needs +82%), the
distribution with **median beside mean**, and the tax to stay.
**The subtle part:** `SimulationEngine` draws `exp((mu - sigma^2/2)T + ...)`, so
`mu` is the arithmetic drift and the MEDIAN lands at `exp((mu - sigma^2/2)T)`. A
measured CAGR is a *geometric* return, so it belongs on the median. Feeding it
in as `mu` would shift every percentile down by the volatility drag — by more
the more volatile the blend — and nothing on the card would look broken.
`geometric_to_arithmetic_pct` converts explicitly; a test pins it.
On a 45%-vol blend over 10 years the median lands ~64% below the mean.

### T3 fix — three defects, one caught live by the smoke
**Root cause 1 (PROVEN, observed on the deployed app):** `bt.run()` adds
`gross_cagr_pct` / `tax_drag_pct` / `cgt_rate_pct` **after** `_metrics` returns,
and `measure_blend` calls `_metrics` directly — so the blend path never had
them. **Why silent:** a null tax figure renders blank, and blank reads as "pays
no tax" rather than "never measured".
**Root cause 2 and 3 (mechanism proven in code; impact on the live book
INFERRED):** `solve_for` summed every priced position *and* added `get_cash()`
— cash is a real position row with `current_price = 1`. And `CASH` would have
entered the core basket, where `is_cash_position`'s own docstring warns it is
also a real listing (Pathward Financial, NASDAQ) — so the fetch would have
**succeeded** and backtested the shekels as a US bank stock. NAV now comes from
`strategy_service._snapshot`, the app's single source; cash is excluded by the
app's own predicate rather than a ticker string. Unpriced holdings now report
`degraded:["price"]` instead of vanishing from NAV.
`BLEND_ENGINE` b1 → b2.

### T4 — the card, and the repo hygiene that made it verifiable
Two inputs, **never a slider** — a slider implies every position on it is
attainable. Both defaults are measured (current excess, current drawdown); if
that read fails the fields stay empty rather than filling with a plausible
number. `UNREACHABLE` and `DRAWDOWN_BOUND` render `warn`, never `bad`.
**T4.0:** `frontend/node_modules` had **2,463 files tracked** with no ignore
rule, and `.gitattributes` declares `*.ps1` checkout-CRLF, so every
`node_modules/.bin/*.ps1` was re-flagged on every git operation. `git status`
timed out at 40s — and, the expensive part, `git status --short` returned an
**empty view while five files sat staged**. A clean tree was very nearly
reported on that basis. `.gitattributes` already said EOL noise is "part of why
`git add -A` is banned in this repo"; that ban was treating the symptom.
After: `git status` 0.2s.
**Also in T4:** a Today chart of % change with server-side ranges
(1W/1M/1Q/1Y/MAX). The range is a **request parameter, not a client-side slice**
— `index_series` normalises to the first value in the series it fetched, and a
downsampled ten-year series is one point per ~16 sessions, so "last week" would
be two points sixteen sessions apart drawn as a week.

### 🚧 PHASE N — what the money actually did (in tree, PENDING QA)
**The finding that decides the design:** `grep` for `Transaction(` across `app/`
outside the models returns **zero hits** — the trade ledger has never been
written — and `whs_snapshots` stores health scores, not value, and is never
written either. **Past NAV cannot be recovered, only started.** Every day
without a snapshot is a day of real history that will never exist.

**The correctness issue that dominates it:** deposits are not performance. Put
₪5,000 into a ₪20,000 book and a naive endpoint-to-endpoint percentage reads
+25% — the chart announcing a great day when there was a bank transfer.
`nav_history.time_weighted` chains sub-period returns across every cash-flow
boundary using the dated `contributions` ledger. Measured on the real function:
a ₪5,000 deposit followed by genuine +5% growth reports **5.00%** where the
naive figure says **31.25%**. Without this the feature is *worse* than the
backfill it replaces — same wrongness, more authority, erring in the flattering
direction.

`nav_snapshots` (migration 0016), a daily 22:10 job, `GET
/portfolio/nav-history`, and a manual `POST .../snapshot` so history starts on
deploy day rather than tomorrow. The Today chart prefers real history and falls
back to the backfill, saying which it drew and how many days remain.
**The job also writes `whs_snapshots`** — the table already existed for it and
nothing wrote it, which is why "my score was 92 last week, what changed?" has no
answer. The row now carries the **caps** alongside the score, because a stricter
yardstick and a worse book need opposite responses.

### Known sharp edges added this batch
- **PowerShell is the unverified surface.** Five script defects this batch
  (`git commit -m` shattering, `Measure-Object -Line` counting blank lines as
  zero, piping a here-string to `python -`, `git add -u` after `git rm --cached`,
  a truncation floor that went stale once the bulk shipped) against three code
  defects. The code is executed before delivery; the `.ps1` cannot be. Four are
  now in `ship-it`'s failure playbook.
- Engine a4 marks every stored backtest stale on purpose — cards read *stale*,
  not wrong, until the 03:30 job reruns.

### Open questions / pending QA
- **Health score 92 → 82 (unexplained).** Mechanism located: both caps come from
  `effective_caps(plan)` at `workflows.py:43`, so `plan.risk_tolerance` moves the
  score with no change to the book. High → Medium tightens concentration
  0.40 → 0.25 = 37.5 diversification points × weight 0.25 = **−9.4 total**,
  which is the observed drop almost exactly. **INFERRED** — needs the four
  component chips and `GET /plan.risk_tolerance` to confirm.
- Phase N + the T4 follow-up are in the tree, not yet shipped.

---

## ✅ CARD CLAIMS — a card may not claim a change it does not produce

**790 passed, 4 skipped (was 731 before this work), ruff clean on `app` and
`tests`. Shipped as `f127d25` → `453188e` → `ddd0d81`, merged `cb2b72b`,
`30a3752`, `98c8b26`.**

Eran opened Today and asked one question — _"how do these recommendations align
with my strategies? isn't it contradict the logic?"_ — about three cards that
each proposed **buying Equities to move Equities from 97% toward an 80% target,
funded by selling Equities**. Net class movement: zero.

### Root cause
The prose and the arithmetic were assembled in different places, so nothing
forced them to agree. Six defects followed from that one gap:

1. The buy gate asked *"does the plan hold this class"* (`target_w <= 0`), not
   *"does the plan want more of it"*. 97% against an 80% target passed it.
2. `size_purchase(nav, ticker_weight, class_target, cap)` took two floats both
   named "...weight", so a TICKER weight was measured against an ASSET CLASS
   target. Confirmed on the live book: `class_gap_ils` = 0 while `name_room_ils`
   = 6,149 — the card sizes were pure cap headroom.
3. `describe_funding`'s honesty clause was dead code on this path — the
   parameter had a default and Today never passed it.
4. `impact` was rendered from trade size (`buyable/nav`), never a gap delta.
5. Funding sold **sleeve holdings**: the cards raised money from TQQQ and SOXL,
   which are `btm_swing_dip` and `btm_trend_soxl` on this book. C3 forbade
   exactly this for sleeve funding; Today was ignoring it.
6. `plan_funding` filled a **gross** target and subtracted tax at the end, so
   nearly every card announced a shortfall it did not have (4,708 − 4,290 = 418
   + 11 tax = the "still leaves ₪430 short" line).

### Ruled out
* **The signal layer.** SCHD was DISPLAYED on the Bulletproof path in production
  throughout; the divergence signal was fine. The failure was entirely in the
  plan-fit narration.
* **The sizing arithmetic itself.** `size_purchase` was internally consistent —
  it was being handed the wrong argument.

### Why it was silent
**Every test in the area asserted on strings.** `test_c3_funding.py:353` guarded
the exact sentence — on the *other* consumer of the same code. The C3 fix had
been applied at the call site rather than at the boundary, so `recommendations.py`
inherited none of it and shipped the same bug through a different door. Text can
agree with itself while the arithmetic disagrees with both.

### What now holds
* `propose_funded_buy()` is the only way to build a funded buy: it sizes from the
  class gap, funds it, **simulates the post-trade mix**, and derives the impact
  sentence from the measured before/after. It refuses a buy into a class at or
  over target, and any buy that increases total drift.
* `describe_funding(fund, buying_class)` — no default. Omitting it is a TypeError.
* `size_purchase` deleted, split into `class_gap_ils` and `name_room_ils`.
* `plan_funding` raises **net** proceeds, spends **one** overweight budget per
  class, and will not sell a class that is already underweight.
* One `FundingLedger` per build, so no two cards spend the same shares.
* `tests/test_card_claims.py` asserts on **portfolio state, not wording** — it
  applies each card's own `apply` spec and re-measures, across five adversarial
  books plus two war-room tests driven end to end.

### The regression, and how it was caught
The first cut of cross-card netting ran *after* every card was built and
**dropped** any card whose funding legs an earlier card had claimed. On the live
book that left Today with two cards and none actionable — the geo card took 2
MSFT and both the war-room and commodities cards were deleted, while ₪7,863 of V
sat untouched. `smoke-p0.ps1` P0.4 and `smoke-beat-market.ps1` §14 both caught it
(`agents degraded: funding`). Fixed by netting at **build** time: a card whose
first choice of funding is gone is re-sourced, never deleted.

Found by running `investigate-issue` against production rather than by reading
code. The six defects above were diagnosed correctly from the source, but the
regression was only visible in the live `/api/v1/recommendations` response.

### Verified live on `98c8b26`
`Swap into SCHD — ₪4,227` / "Does not move your Equities weight. Swaps which
equities you hold." Funded by 4 V. No TQQQ, no SOXL. Shortfall 0. `degraded: []`.

### Known-remaining
* The card quotes the mix **excluding cash** (100% equities) while `/api/v1/mix`
  includes it (96.98%). Both live, both true of different denominators — but two
  screens quoting different numbers for the same thing is the species of
  inconsistency this phase existed to remove. Not yet reconciled.

## ✅ PHASE C6 — the core stops being an anonymous remainder

**743 passed on SQLite (729 + 14 new), ruff clean on `app` and `tests`, every
`<script>` block parses under `node --check`.**

Eran asked one question — _"for the core, why can't I choose from the strategy
cards?"_ — and the answer was **you can, and three things were stopping it**, one
of them able to sell his whole book.

### The three
1. **C5 mislabelled 14 buttons.** Renaming "Apply strategy" to "Add as a sleeve"
   hit *every* card, including the 14 static families, which create no sleeve at
   all. Mine, two days old.
2. **Indication was impossible.** `_appliedIds()` reads `plan_sleeves`; a static
   family wrote no row there, so a core choice could never be ticked. And
   `plans.strategy` holds the most recently applied strategy *of either kind*, so
   it could not name the core either.
3. **"Replace book with this basket" would have sold his sleeves.**
   `_replace_book` deletes every holding and inserts the basket, entirely
   sleeve-unaware. On a two-sleeve book it sells SOXL, MTUM, QUAL and AVUV and
   leaves the `plan_sleeves` rows pointing at nothing — the stale-cap shape
   `retire_sleeve` exists to prevent, at book scale. **Phase C shipped five
   sub-phases past this button and never came back to it.** Now refused, with a
   reason that names the alternative.

### The defect underneath, which no screen could show
`recommendations.py` read the core's target asset mix out of `plans.strategy` in
two places (the rebalance card, and the rebalance-apply handler):

```python
_s = _strat.get(getattr(plan, "strategy", None))
target = _s["target_allocation"] if _s else OBJ_TARGET[objective]
```

Apply `bal_6040` → the book's target became 60/40. Resize **any** sleeve →
`plans.strategy` now named the sleeve, `_strat.get` missed, and the target
silently reverted to `OBJ_TARGET["Grow"]` = 80/10/10. **The book's target asset
allocation depended on which card you pressed last.** C2's own comment called
that column "a pointer, no longer the truth"; these two readers were still
treating it as the truth about the core. `/mix` was a third answer again — it
read `OBJ_TARGET` directly and ignored the family entirely.

All three now go through one `_core_target(session, user, objective)`.

### The model — C1's decision survives intact
`is_core` was reserved in C1 and never written. C6 writes it, for a **narrower**
purpose than the model C1 rejected: the core row records **which** strategy
manages the core, never **how big** it is. `sleeve_pct` on a core row is always
0 and the size stays `remainder_pct`. So the core is still a remainder — it just
has a name now.

`list_sleeves` filters `is_core` out, and that single choke point is what keeps
C6 from touching sleeve behaviour. A core row leaking through would claim a
share of the book in `total_pct` and arm `max_weight` caps across its whole
basket. Two tests pin it from both directions.

### Also fixed, found on the way
- **`applyStrat` never called `loadStrategies()`** — applying refreshed `/plan`
  but not the panel or the ticks, so a card you had just applied still looked
  unapplied until you switched tabs. Invisible before C6 because nothing on the
  card changed; the core tick made it obvious.
- **The apply confirmation claimed a write C2 removed.** It said "your plan is
  now objective Grow, High risk" for *every* strategy — but a sleeve has not
  written objective or risk since C2. It now says which of the two things
  happened.

### Backfill
`backfill_core_once`, its own one-shot KV key (`plan_core_backfilled_v1`),
because C1's has already been spent on every live deploy. A `plans.strategy`
naming a **static** family becomes the core row; a rule-based one is skipped,
because it says nothing about the core.

Deliberately **not** a read-through fallback on `plans.strategy` — that version
needs no key and works for every existing book, right up until you *clear* your
core, at which point the next page load hands it straight back. Same trap the
sleeve backfill's own comment describes; worth a second KV key not to fall into
it twice.

### One flake worth recording
A single full-suite run failed `test_whatif.py::test_drawdown_slider_scales_
scenario_loss` with `no such table: users`, on the shared
`/tmp/iw_test_app.db`. It did not reproduce in isolation, and the next two full
runs (SQLite 743, Postgres) were clean. Logged rather than dismissed: the
`_isolate_db` autouse fixture builds a throwaway NullPool engine per test, and a
cross-test schema disappearance is exactly the kind of thing that gets waved off
three times before it costs a red CI.

## ✅ PHASE C5.1 — two things the finished Plan tab still got wrong

Both found by Eran on the live screen, immediately after C5 shipped. Frontend
only; no Python changed.

### 1. The slider showed the suggestion, never your sleeve
Factor Stack's card read **"Share of your portfolio: 40% (suggested 40%)"** on a
book running that sleeve at **8%**. Two numbers on one card and neither of them
described the book.

`sleeveControl` fell back to `s.sleeve_pct`, which comes off the catalog and is
*the same field as* `sleeve_default_pct` — the suggested size. It has never had
anything to do with the sleeve you run. So every page load re-opened the slider
at the suggestion, and pressing "Resize sleeve" without touching it would have
silently taken the sleeve from 8% to 40%. **That is the real cost of this one:**
not a wrong label, a wrong default on a button that writes.

Fixed three ways:
- the slider is **seeded from `plan_sleeves`**, once per sleeve, so a size you
  dragged to and have not applied yet survives a goal-tab click;
- the label names both numbers when they differ — *"12% · you run 8% today ·
  suggested 40%"*;
- **`step` is 1, not 5.** Worth noting for its own sake: 8 was not reachable
  from this slider at all. Any sleeve size that is not a multiple of 5 was
  written by the API, a script, or the C1 backfill — the UI could not have
  produced it.

Nothing shrinks a sleeve behind your back: `apply_strategy` **refuses** an
over-allocation rather than clamping it, funding never resizes, and
`add_or_resize` is the only writer in the codebase. `sleeve_pct` is whatever was
last written to it.

### 2. The core had no visible strategy
The core row said *"managed to your objective Grow · High risk"* and offered no
way to change it. The control existed — the **"What are you aiming for?"**
dropdown further down the same tab — but nothing connected the two, so the core
read as a leftover that nothing manages.

The objective/risk selects now sit **in the core row**, where the core is named.
They do their own two-field `PUT /api/v1/plan` rather than calling `savePlan()`:
`upsert_plan` drops nulls, so a partial body cannot clear a target amount, and
`savePlan`'s body is read out of a form that `loadPlan()` may still be racing to
populate — `show("plan")` fires both loaders without awaiting either. On success
it writes the same two values back into the form below, so "Save my plan" cannot
put the old objective back.

The panel also now says out loud that its percentages are **targets**, and points
at Fund all sleeves for what you actually hold against them.

### The trade-off, recorded because it will be asked again
Sleeves have first claim on NAV; the core is the remainder. Raising a sleeve
does not add exposure, it **moves** it out of the objective-managed core into a
rule-managed, concentrated one — and the objective still sets the concentration
cap and cash floor for the whole book, sleeves included. Funding the increase
sells from the core at 25% CGT. A 40% Factor Stack sleeve is not "more of the
same book"; it is a book whose bad-year drop is 40% governed by three factor
ETFs and a backtest.

---

## ✅ PHASE C5 — the Plan tab stops claiming there is only one strategy

**729 passed on SQLite, ruff clean, every `<script>` block parses under
`node --check`.** Frontend only; no Python changed.

The banner said, in prose, _"One strategy applies at a time."_ It had said that
since before C2 made it false, and it sat directly above a book running two
sleeves. **This is the last place the app misdescribed what it does.**

### What the Plan tab does now
- **A sleeve panel** replaces the single-strategy banner: every sleeve with its
  size, the **core as the remainder** it actually is, and the total allocated.
  The core row is deliberately un-clickable and says why — it is a remainder, not
  a strategy, so there is no card behind it.
- **`_appliedIds()` returns a Set**, read from `plan_sleeves` with the legacy
  column as fallback. Every card that is one of yours is ticked, not just one.
- **Every goal tab holding one of your sleeves gets a ✓**, where before exactly
  one could.
- **"Apply strategy" now reads "Add as a sleeve"**, or **"Resize sleeve"** on a
  card you already run — the button's old label described the overwrite it used
  to do.
- **Remove**, per sleeve, with a confirm that leads on the thing most likely to
  be misread: **nothing is sold**. The shares stay; the app stops steering toward
  that target and the caps it armed are retired.
- **Fund all sleeves**, preview-then-confirm, reusing the shape "Fund this
  sleeve" already uses. It shows every sleeve's status, every trim with its
  share count and tax, and — when the plan lands short — says so instead of
  reporting success. After executing it reports the **leg scale**, because the
  amounts deliberately will not match the preview to the shekel.

### Why "Fund all" is on the phone after all
I first recommended keeping it script-only, on the grounds that a one-tap
multi-position sell is a lot of consequence for a phone. Eran pushed back and was
right: **"Fund this sleeve" already does exactly that** — it previews every buy
and every sell with tax, then requires a second explicit tap. Fund-all is the
same shape at a larger magnitude, and the confirm dialog already handles
magnitude by naming every line. My "a 6-second Ctrl-C does not exist on mobile"
argument was backwards; an explicit confirm is *better* than a countdown.

The one genuine difference — one tap lets the app choose which sleeves get funded
largest-first and which get skipped — is exactly what the preview shows before
you confirm.

### Not tested by the suite, on purpose
There is no JS test infrastructure here, and `smoke-p2.ps1` already established
the precedent: HTTP cannot see rendering, and asserting anyway would be asserting
a lie. What IS checked mechanically: `node --check` on every script block, and a
reference sweep confirming no stale `_appliedId(` or `MYPLAN.strategy_sleeve_pct`
survived. **The rest is a phone pass.**

---

## 🏁 PHASE C IS COMPLETE

| | |
|---|---|
| **C1** | `plan_sleeves` lands inert, one-shot backfill |
| **C2** | a book runs N sleeves; caps summed per ticker |
| **C3a/b** | one budget across sleeves — previewed, then executed for real |
| **C4** | signals, discipline and drift per sleeve |
| **C5** | the Plan tab tells the truth |

**Six defects were found across Phase C, and not one of them came from reading
code.** A Windows box running the suite. A smoke printing a list next to its own
verdict. CI's Postgres job. Two probes with realistic numbers. A screenshot of
Today. Three separate times **the check was the broken thing** — a smoke that
passed having compared nothing, one that reported "no cap left behind" while
three were disarmed, one that reported FAIL while quietly executing a live trade.

## 🔴 THREE CARDS WERE ARMING THREE TRAILING STOPS ON ONE TICKER

**729 passed on SQLite, 725 / 8 skipped on Postgres, ruff clean, and the suite
now emits ZERO warnings.**

Found by looking at the live Today screen, not by reading code. Three cards, all
on screen at once, all proposing rules on the same tickers:

| card | proposes |
|---|---|
| Arm the discipline for Factor Stack | trailing stop **MTUM 12%** |
| 6 protective rules ready to arm | 6 rules across AVUV, **MTUM**, QUAL |
| MTUM is in an uptrend | trailing stop **MTUM 15%** |

**`create_rule` had no uniqueness rule.** It built a `TradingRule` and added it.
Reproduced: arming 12% then 15% leaves **both active on MTUM**. The tighter one
fires first, so you get stopped out at 12% having decided on 15%.

**Why nothing caught it.** Nothing errors — both rules are valid. Each card
filters only against rules **already armed**, so none of them can see what the
others are proposing on the same render. Three independent producers
(`strategy_catalog.discipline_rules`, `rules_service.suggest_rules_for_holdings`,
`_momentum_recs`) all emit `trailing_stop` specs, and the write path that
receives them had no opinion.

**The codebase had already reached this conclusion once and not generalised it.**
`_arm_sleeve_caps` sums `max_weight` per ticker, and `discipline_rules`
deliberately emits no `max_weight` because — its own docstring — "two max_weight
rules on the same ticker at two different levels" is a duplicate. The protective
stops never got the same guard.

**Fixed at the write, which is the one choke point.** `create_rule` re-levels an
existing **active** rule of the same kind on the same ticker instead of stacking
beside it, and re-arms it (`triggered = False`) so a re-levelled rule cannot be
armed and latched at once.

**What deliberately still stacks**, because these do not contradict each other:
- `price_above` / `price_below` — "tell me at 100 and again at 120" is a real
  thing to want.
- `strategy_signal` — keyed by `(mode, strategy_id)`. An entry and an exit on one
  ticker are both wanted, and **two sleeves may both subscribe to one ticker's
  signal**; collapsing those would silently unsubscribe a sleeve.
- A **retired** rule never blocks a fresh one. History is kept, not overwritten.

**The confirmation stops lying too.** `conflicting_rule_level` is read *before*
the write, so Accept reports `relevelled` with `MTUM trailing stop 15% (was 12%)`
rather than claiming it armed something new.

### 🔇 183 warnings → 0
Every one was the same: APScheduler's price job fires during `TestClient`
startup and calls `asyncio.run()` on a thread that already has a loop, so
`_refresh_all` is created and never awaited. Harmless — in production the
scheduler owns its own thread — but **183 copies of a benign warning is where a
real one hides**, the same argument as gitignoring the noise off `git status` in
Phase A. Filtered by that one coroutine name, not blanket, so the next genuinely
un-awaited coroutine still shows. `asyncio_default_fixture_loop_scope` pinned to
`function` while there, so that deprecation lands as a no-op later.

**Phase A's lint gate earned its keep here**: my own new test file arrived with
nine `E702` semicolon-joined statements and CI would have rejected it.

## 📚 Older detail is in `STATUS-ARCHIVE.md`

PHASE A/B/C, P0–P4, the 2026-08-10 backlog + execution plan, and the 2026-08-03
"Now / Last shipped / Verified live" snapshot were moved verbatim to
`STATUS-ARCHIVE.md` on 2026-08-31 (nothing deleted). They described a HEAD of
`86760ff`, eleven PRs behind `1362ce8`, and were the bulk of a 4,416-line file.

## Corrections worth remembering
- **Measure over a REUSED connection, or you are timing the handshake.** A
  fresh TCP connection per request adds ~0.25s of setup and intermittently
  ~1.2s — larger than most things being measured. Two separate "defects" were
  reported on 2026-08-12 (a residual blocking window, then a ~1s FX stall) and
  **neither existed**: both were `curl` reconnecting. The tell is that
  `/health`, which touches nothing, spikes identically. Use one `curl`
  invocation with the URL repeated, and read `%{time_connect}` and
  `%{time_starttransfer}` — never `%{time_total}` alone. The same applies to
  the smoke scripts, which issue one `Invoke-RestMethod` per check.
- **A control run must be taken in the SAME window as the test run.** The
  residual-blocking claim came from comparing an under-load run against a
  control measured earlier. Re-running the control alongside it is what
  falsified it.
- **A guard protects the path it is on, not the behaviour.** Phase 1 fixed cash-being-quoted-as-NASDAQ:CASH in `refresh_all_positions` and stopped there. `POST /portfolio/refresh-prices` had its own copy of the same loop with no guard, so on 2026-08-10 the identical bug fired again from the manual button and inflated NAV ~9x. When fixing a data-integrity bug, **find every writer of that field first** — the fix is otherwise conditional on which code path the user happens to hit. (Still unaudited: broker sync, CSV intake, `add_holding`, `update_position`.)
- **The AMZN "cap breach" was not a bug.** `risk_tolerance: High` sets `concentration_cap` to 0.40, so AMZN at 29% is within limits — `diversification_score: 100` proves it (that score can only be 100 when `max_weight <= cap`). A smoke test hardcoding 0.25 manufactured the false alarm. Read caps from `/api/v1/plan`, never assume 0.25.
- **Gemini outages were billing, not code**: HTTP 429 "prepayment credits are depleted". `gemini_generate` swallows every exception identically, so a two-minute billing fix looked like a permanent outage. Surfacing the error class is still an open improvement.
- **Smoke tests must not pass on missing data.** A check reported "no competing cash cards" when the API had timed out and returned null. Every check now SKIPs or FAILs on a null payload.
- **PowerShell variable names are CASE-INSENSITIVE.** `$h = Api GET '/api/v1/health-check'` overwrote `$H` (the auth headers), so every subsequent call died at parameter binding in 0.00s — the request never left the machine. The handler printed "NO RESPONSE", which reads as a network fault, and that one hidden error message produced **four** wrong hypotheses (keep-alive reuse, deploy drain, server saturation, DB pool) and a production DB change shipped on false evidence. Headers are now `$ApiHeaders` and `Api` throws if it is ever not a hashtable. **Print the exception type and message on the FIRST failure — never collapse errors into a generic string.**
- **Phase 11 (`pool_pre_ping`) was justified by that false evidence.** Keep it — Railway does close idle connections and `push_service`/`pricing_service` already guard against it — but it fixed nothing that was being observed.

ℹ️ **(historical, now satisfied)** The pre-ship warning on this batch — run suite + ruff, expect `test_*health*` to shift, `rule_events` relies on `auto_create_tables` — was cleared when the phases landed and the smoke run passed. Keep the `auto_create_tables` note in mind if that setting is ever turned off in production.

## Next — standing list (written 2026-08-10, NOT re-prioritised since)
_The live priority order is the **Next** block at the top of this file. This list is kept because
the individual items are still open, but it predates PRs 16–26 and its pre-flight notes are stale._

0. **Identify the one failing check inside `smoke-all`.** The chain reports 74 passed / **1 failed** / 8 skipped; the failure is in the smoke-all section (24/1/0) and was never read this session. Run `.\scripts\smoke\smoke-all.ps1` alone. Likely `subscriptions: 0` — do not assume it. ← blocks calling the chain green
0b. **`smoke-p1.ps1 -Apply`** — proves the cap is really armed at the sleeve size. Every P1.1 live check is currently skipped.
0c. **Pixel QA for P0** (`qa/QA-2026-08-10-p0-safety.md`) + a Plan-tab pass for P1 (preview shows funding legs; Apply shows the armed cap; SW is `iw-v15` so close/reopen the installed app once).
0d. **Then P2** (`BEAT_MARKET_NEXT_PLAN.md`): presentation only — Style + Horizon chips, the tab row wrapping at five tabs (`.stgoal`, `flex-wrap:nowrap; overflow-x:auto`), and "VERY HIGH RISK" breaking across two lines (`.rk` needs `white-space:nowrap`). Then P3 (regime proxy) and P4 (rules + notifications).
1. **Enable notifications on the Pixel 9** and confirm `subscriptions: 1`, then that the 07:00 digest arrives. This is the *only* open item from the 5-issue batch and the single failing smoke check — a device action, not code. ← next up
2. **Offload blocking provider I/O to a thread pool.** `/recommendations` is ~5s warm on a **single uvicorn worker** making *synchronous* calls inside `async def` handlers, so one request blocks every other. Shaving latency further is the wrong fix.
3. **Rotate `AGENT_API_KEY`** (it was pasted into a chat transcript).
4. Pin `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` in Railway — currently DB-generated, so a DB reset would invalidate every push subscription at once.
5. Broker integration — see `BROKER_INTEGRATION_PLAN.md` (unstarted; confirm it's still the next initiative — it has been "next" and unstarted for 3 weeks).

## Pending QA / open questions
- **P0 safety batch (2026-08-10):** `qa/QA-2026-08-10-p0-safety.md` on the Pixel 9 — 8 sections, ~10 min. Pre-flight matters here: the SW went `iw-v13`→`iw-v14`, so close/reopen the installed app once, and force `POST /api/v1/portfolio/refresh-prices` before section 4 or no holding will carry `price_freshness` yet. Automated half: `.\scripts\smoke\smoke-p0.ps1` (read-only; `-Execute` writes).
- **5-issue batch (phases 1–11):** ✅ verified live 2026-08-03 via `smoke-all.ps1` — see "Verified live" above. Only the notification subscription remains.
- **Alignment batch (Phases 0–5, 2026-07-18):** ⚠️ **still unrun on the Pixel 9 after two weeks**, and the SW has since moved `iw-v10`→`iw-v11`, so the update path in the original checklist is stale. Check: cash set/adjust + pinned Cash row + Cash slice in donut; a buy card shows sizing + "How it's funded" legs and executes; Mark-as-done vs Ignore land in separate restore lists; war-room cards match Today; strategy "What changes?" preview renders; no stale shell after SW `iw-v11`. **Decide whether this is still worth running standalone or is subsumed by the 08-03 smoke pass.**
- **Notification ↔ Today alignment (2026-07-12):** `qa/QA-2026-07-12-notification-alignment.md` on the Pixel 9 — blocked on the same "enable notifications on the device" step as Next #1; do them together.
- Confirm on live: accepting a tax-loss/sell rec removes the holding and adds a CASH position for the net proceeds; fee-swap replaces the fund; trim credits cash. (Accept-executes fix.)
- Confirm the 30-min reprice + data-source label shows fresh prices (FMP primary, Yahoo fallback) in production.
- Confirm the live advisor answer for "should I add commodities?" reads as a balanced for/against.

## Known sharp edges
Postgres per-test isolation fixture (throwaway NullPool engine, own event loop); ruff strictness. Windows mount can serve truncated views of file-tool edits — verify large writes on the mount (see `safe-windows-edits`). Commit with `git commit -F COMMIT_MSG.txt`, never a PowerShell here-string (parses as pathspecs, commit silently doesn't happen). Never `git add -A` (`frontend/node_modules` is tracked, CRLF-noisy). See CLAUDE.md.

## Changelog (newest first)
- 2026-09-21 — Weekly status review: **3 commits in 7 days (PR 67 `0965e18`, PR 67.1 `84be80b`, PR 68 `aa7826c`)** — second-busiest of the five repos, behind Job Hunter's 31. Per-PR STATUS discipline held again: PR 67/67.1/68 each wrote their own section on the day. **What had NOT held, for the second time in four weeks, was the spine** — the `Where this repo actually is` block still said "HEAD is PR 66" and was dated 09-14, two PRs stale; corrected to `aa7826c`. That is the exact failure the 08-31 restructure was built to stop, and it recurs because the per-PR sections are written by the PR session while the header is written by nobody. Ops table aged: broker integration 9→**10 weeks and now the oldest item on any board**, VAPID pin and the three unrotated secrets 6→7, Pixel P0 QA 5→6, untracked `candidate_search.py` 2→**3**. Nothing closed. `EXECUTION_PLAN_2026-09-14_v8.md` is now also untracked (mtime 09-14) — a third uncommitted file, and unlike the other two it is the document the "Next" list points at. ⚠️ **A stale `.git/index.lock` was left here again** — the review's first git call used plain `git status` before switching to `--no-optional-locks`. Empty, sandbox cannot delete it: **remove `C:\dev\Investwise-pro\.git\index.lock` before the next commit.** Second week running, same cause; the rule needs to be step 0 of the review, not a remembered note.
- 2026-09-14 — Weekly status review: **one commit in 7 days (PR 66 `fab6e46`, 09-07) — down from 48 and 26 in the two weeks before.** That is not a criticism; it is the datum that makes the ops table legible. For three weeks the honest defence of the untracked `candidate_search.py` was that this repo was shipping faster than it could tidy. This week it shipped one PR, and the file is still untracked — so the item is a decision being deferred, not a queue being drained. Aged: broker integration to **9 weeks** (the oldest item on any of the four boards), VAPID pinning and secrets rotation to **6**, Pixel P0 QA to **5**, untracked code to **2**. Nothing closed. ⚠️ **Two process faults recorded, both mine.** (1) The sandbox's `git status` left an undeletable `.git/index.lock` here (and in AdaptiveFit and WealthOS) — it will block Eran's next commit; the removal command is in the ops table. Future runs use `git --no-optional-locks status`. (2) **This file is 6,412 lines / 364KB.** The 08-31 restructure cut it to ~3,000 by moving PHASE A/B/C and P0–P4 to `STATUS-ARCHIVE.md`; per-PR sections have since doubled it back. Twenty-six PR narratives now sit above the changelog, the oldest (PR 41–46) more than six weeks old and superseded by `EXECUTION_PLAN_2026-09-03_v6.md`. **Flagged, not fixed** — moving ~2,500 lines on this mount is exactly the operation `safe-windows-edits` says can silently truncate, and it should be done deliberately in a session that can verify the result, not by a scheduled job. Next week this becomes an ops row if it has not been done.
- 2026-09-07 — Weekly status review: **48 commits in 7 days (PR 27 → PR 65, `103fa93`…`853c51d`) — nearly double last week's 26, against zero commits in each of the other three repos.** Per-PR STATUS discipline held again: every PR wrote its own section, and the spine did not go stale the way it did in August — the header was one PR behind (said PR 61.1, HEAD was PR 65) and is corrected. **Two ops items closed by shipped work rather than by nagging**, which is new for this board: the ₪618.65 cash question was **decided** in PR 64 (soft 7.5% target, hard ₪150 floor, a mandatory `reserve_reason` on any card that spends into it) four days after it was flagged; and the 20-file untracked pile is down to **2 files** — `candidate_search.py` and its tests, which is the part that was always the real item. Aged the rest: secrets rotation and VAPID pinning to 5 weeks, broker integration to 8, Pixel P0 QA to 4 (now **thirty-eight** PRs stale, so re-scope or void it rather than run it as written). **The pattern worth naming:** this repo closes ops items when the answer is a decision it can ship, and does not close them when the answer is a console visit — the same shape that has Job Hunter's Google OAuth vars open at 8 weeks. Four of the five remaining rows here are console visits.
- 2026-08-31 — Weekly status review: **26 commits in 7 days (PR 16 → PR 26, `315ad1f`…`1362ce8`) — the busiest of the four repos while the other three shipped nothing.** Per-PR STATUS discipline held: every PR wrote its own section. What had NOT held was the file's own spine — the `Now` / `Last shipped` / `Verified live` sections still described HEAD `86760ff` (2026-08-03), **eleven PRs and four weeks stale**, buried under 4,300 lines. Restructured: a **Where this repo actually is** block with a real HEAD, an **Ops actions with age** table (this repo had none) and a live **Next** now sit at the top; PHASE A/B/C, P0–P4 and the 08-10 backlog moved verbatim to `STATUS-ARCHIVE.md`; pre-07-20 changelog entries collapsed. 4,416 → ~3,000 lines. **The finding worth keeping: aging the ops items showed `AGENT_API_KEY` was first flagged 2026-08-03 as "rotate it sometime" and was found published in a public repo on 08-30 — four weeks, three restatements, no rotation.** It is now the first row. Also flagged: 20 untracked files including `candidate_search.py` + its tests and `STATUS-V2.md` itself, and a duplicate-message commit pair (`b48b291`/`f5773c0`, one carrying a BOM) — the same gate/ship-script artefact WealthOS shows.
- 2026-08-16 — **Phase N: history starts.** `nav_snapshots` + a 22:10 daily job + time-weighted returns across the contributions ledger, so a deposit moves the value without moving the line (₪5k into ₪20k then +5% real growth reports 5.00%, not 31.25%). Past NAV is unrecoverable — `Transaction(` has zero writers, `whs_snapshots` was never written — so the job also snapshots the health score WITH its caps, because a stricter yardstick and a worse book need opposite responses. Today's chart prefers real history, falls back to the backfill, and says which. In tree, pending QA.
- 2026-08-16 — **T4: the target card + Today chart.** Two typed inputs and no slider; defaults measured, empty when unmeasurable. T4.0 untracked 2,463 `frontend/node_modules` files that had made `git status` time out at 40s and, once, return an EMPTY view while five files were staged. `git status` now 0.2s. Chart ranges are a server parameter, not a client slice. `6edebc5`, `10d8b40`.
- 2026-08-16 — **T3 fix.** The blend never computed its tax drag (`bt.run` adds it after `_metrics`; `measure_blend` calls `_metrics` directly) — a null renders identically to "pays no tax". Caught live by smoke-t3. Plus cash double-counted in NAV, and `CASH` — also a real NASDAQ listing — would have been backtested as a US bank stock inside the core. `0df481f`.
- 2026-08-16 — **T0–T3: the target solver.** Excess over benchmark at a bounded drawdown, because raw excess is bought with leverage. Blend by simulating rather than averaging (identity test pins it). Five outcomes, three of which are "no". Median beside mean, with the geometric→arithmetic conversion that would otherwise have shifted every percentile down silently. `87a47a0`, `0315b47`, `df0d85e`, `211682a`.
- 2026-08-15 — **T0.3: the performance backfill was adding shekels to dollars.** `performance()` projected away `market` and `meta`, the only inputs `price_currency` takes — the safety fix that keeps ORM objects off the worker thread caused the currency bug. Reproduced at 64.3% understated start value and 8.33% vs a true 12.62% excess. `b2d5e98`.
- 2026-08-15 — **Card claims: a card may not claim a portfolio change it does not produce.** Three Today cards proposed buying Equities to move Equities from 97% toward an 80% target, funded by selling Equities — net movement zero. Root cause: prose and arithmetic assembled in different places. Six defects (direction never checked; ticker weight measured against a class target; the honesty clause dead on this path; impact rendered from trade size; funding raiding sleeve holdings TQQQ/SOXL; a gross-vs-net shortfall on nearly every card). Silent because every test in the area asserted on strings — including one guarding the exact sentence on the other consumer of the same code, where the C3 fix had been applied at the call site instead of the boundary. Now: `propose_funded_buy` simulates the post-trade mix and derives its own claim; `describe_funding` requires `buying_class`; `size_purchase` split into `class_gap_ils` / `name_room_ils`; `plan_funding` raises net proceeds, spends one overweight budget per class, and won't sell an underweight class; one `FundingLedger` per build so no two cards spend the same shares. `tests/test_card_claims.py` asserts on portfolio state by applying each card's `apply` spec and re-measuring. A first cut of the netting DROPPED cards whose legs were taken and briefly left Today with nothing actionable — caught by smoke P0.4 / §14 (`agents degraded: funding`) and fixed by netting at build time so a card is re-sourced, not deleted. 790 passed, 4 skipped. Verified live on `98c8b26`.
- 2026-08-10 — **Beat the Market P0 (safety) written, not yet run.** All four items from `BEAT_MARKET_NEXT_PLAN.md` P0 are in the working tree: (0.1) `load_basket` no longer deletes the book — `mode="fund"` is the new default for the rule-based family and raises only the sleeve's shortfall through the existing funding engine (spendable cash to the objective floor, then worst-fit holdings), abstaining with a stated reason rather than half-executing; `mode="replace"` survives for the four static families behind a confirm that now lists every position and its value; both modes gained `dry_run`. (0.2) `measuredProfile` reads `metrics.cagr_pct` before `ok`, so a card that kept its numbers through a failed refresh shows chips plus "refresh has been failing since X" instead of "Couldn't measure". (0.3) freshness became **three** states, not two — the original bug was that FMP stamps `_now()` on every quote, so a delisted holding looked freshly traded; `Quote.as_of_source` now distinguishes a venue timestamp from a request timestamp, FMP reads its real `timestamp`, and where the primary can't say, Yahoo is asked purely for the trade time. A quote older than 5 **trading** days no longer overwrites the price; the position is flagged, badged in Holdings, reported in `/portfolio.stale_positions`, and surfaced on Today as guidance — never an automatic write-off. (0.4) suggested protective rules reach Today as one card wired to `create_rules`. Plus `smoke-p0.ps1` and two new test files. **The sandbox VM died before any of it could be executed** — see the ⛔ block at the top of this file.
- 2026-08-03 — Weekly status review: 🎉 **busiest week of the four repos — 11 commits, `d42f38c`→`86760ff`.** The entire 5-issue bug batch (phases 1–11) is committed AND verified live (`smoke-all.ps1` 29/30); rewrote the "Uncommitted (2026-08-02)" section as "Last shipped" with a phase→commit table, resolving last week's uncommitted-work flag. Working tree **clean**. Merged the duplicated "Open items" list into "Next" and gave Next a priority order. ⚠️ Flagged: the **Phases 0–5 alignment-batch QA has now gone two weeks unrun** and its SW update path (`iw-v10`) is stale — the app is on `iw-v11`; decide whether the 08-03 smoke pass subsumes it. Broker integration still unstarted after 3 weeks as the "next initiative". The one failing smoke check (`subscriptions: 0`) is a device action, not a defect.
- 2026-07-20→08-03 (weekly reviews, collapsed 2026-09-14, >6 weeks) — The 07-18 alignment batch and the 5-issue bug batch both landed and were verified live (`smoke-all.ps1` 29/30); working trees clean; the duplicate-commit-message artefact and the commit-hygiene rules (no here-strings, no `git add -A`) were folded into **Known sharp edges**, where they still live.
- 2026-06-22→-07-18 (consolidated 2026-08-31, all >6 weeks old) — **Foundations through the alignment batch.** Installable PWA + web push with 7-day-TTL server dismissals; price-provider hardening (FMP + Yahoo fallback); Markets tab, Gemini summaries, risk-on/off regime; the trading-rules engine; 30-min reprice with a truthful source label; NAV/goal alignment and FX normalization; opportunity screener + fundamentals. Then **Accept actually executes** (sells credit net-of-CGT proceeds to a visible CASH holding; fee-swaps replace at a live price), **notification ↔ Today alignment** (a permanent localStorage hide vs a 7-day server dismissal was the real cause of "push says do X, app says nothing to do"), actionable trend cards + per-holding suggested rules, the grouped trading-rules UI, and war-room timestamps. Closing it: the **2026-07-18 alignment batch, Phases 0–5** (`defe8ec`…`9e2cd01`) — cash as a first-class holding, grounded signals, the funding engine, strategy profiles, green gated CI, and the stale-PWA-shell fix that turned out to be the real cause of the reported "Ignore does nothing" bugs. SW `iw-v3`→`iw-v10`; ~75 tests added.
