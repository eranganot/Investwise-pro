# Plan — Eran's six items, 2026-09-28

_Status: **FOR REVIEW. Nothing has been coded.** Production checked live at `e556b64` (PR 71),
schema `0024_plan_cash_target_ils`. Evidence comes from the live API (`/rules`, `/recommendations`,
`/portfolio/position-history`, `/portfolio/ledger/repair` preview, `/plan`, `/plan/projection`,
`/rules/events`, `/api/v1/audit`) plus the code at HEAD. Every claim is labelled **PROVEN**
(observed directly) or **INFERRED**._

---

## 0. One-page summary

| # | Your point | Root cause | Status | PR |
|---|---|---|---|---|
| 1 | Rule triggered, no card | The rule card is built, then **hidden by the contradiction pass**, which runs *after* the banner counts it. Plus: the take-profit **ladder never re-arms** (outcome string mismatch), so the rule re-fires at the same level and the sleeve funder buys straight back. | PROVEN | **A** |
| 2 | Position vs ticker | They're different charts. They look identical because your ledger doesn't reconcile. Cause: **the "rebalance" Accept rewrites share counts of whole asset classes and records no trade.** | PROVEN (Commodities); VTI residual INFERRED | **B** |
| 3 | Cash usage rule | X already exists (**₪1,500**, editable) with the ₪150 hard floor. What's missing is the research: the card only tops up existing gaps. | PROVEN | **D** |
| 4 | Goals keep moving | The target is `current NAV × 1.3⁶`, recalculated live. So **progress is mathematically stuck at 20.7% forever** (1/1.3⁶). Four different bases on one screen. | PROVEN | **C** |
| 5 | Hidden/done cards unlabelled | "show them" calls *restore*, which **erases** the done/ignored state and makes the cards live (and acceptable) again. | PROVEN | **A** |
| 6 | Holding-review agent | A trend check exists, but it only arms protective rules and never proposes a sell. Build a reviewer on top of it, **not a new debating agent**. | Design | **E** |

**Your decisions so far:**
- Rule vs sleeve: the card shows the conflict and you decide. **Sell** lowers the sleeve. **Keep** *updates* the rule (it isn't retired).
- Cash: rank sleeves, core and new screener ideas together.
- Goal: based on money put in, frozen except on deposits or withdrawals.
- Return basis: measured figure as the headline, conservative figure shown beside it.

**Order:** A → B → C → D → E. B starts with one proof step and reports back before any repair is written.

**Six small questions (§8)** need answers before the build. Each has a recommended default.

---

## 1. Findings in detail (the evidence)

### 1.1 Item 1: SOXL take-profit, banner without card (PROVEN)

- Rule `8cd9f1ff`: SOXL `take_profit`, price mode, level **148.52**. `triggered: true`, last fired 2026-09-25 14:33. Live price 151.45.
- `/recommendations` → `rule_banner = {triggered:[8cd9f1ff], carded:[8cd9f1ff]}`. The card **was** built.
- The same payload's `contradictions` has `sells_a_sleeve_at_target`, card `rule_8cd9f1ff`, *"SOXL is 40.1% of the book against a sleeve target of 40.0%"*, `suppressed: true`.
- **Order bug:** in `recommendations.py`, the banner's `carded` list is computed at ~L2593. `contradictions.check()` then runs at ~L2635 and drops the card. The banner counted a card that no longer existed.
- **Nothing on screen shows `contradictions`.** `index.html` never reads that key. The suppression is invisible.
- **Trap to avoid in the fix:** the same block "heals" any triggered rule with no visible card by auto-acknowledging it. If we only move the reconciliation below the contradiction pass, it would **silently retire your SOXL rule**. The fix must turn the conflict into a card, not a heal.

**Second, deeper defect: the take-profit ladder is dead code (PROVEN).**
- `rules_service.resolve_rule` re-arms a take-profit at the next rung (+10%) only when `outcome == "accepted"`.
- The accept path passes `"executed"` for actionable cards (`recommendations.py` ~L4534), and nothing in the app passes `"accepted"`.
- Live `/rules/events` for rule `8cd9f1ff`:
  - 2026-09-22 18:03: `triggered`, then `executed` at 18:40:45, with target still 148.52.
  - 2026-09-25 13:33 and 14:33: `triggered` again, with target **still 148.52**.
- The rule came back at the same level and fired on the next uptick.

**Third: the rule and the sleeve fight, and you pay tax each round (PROVEN from the ledger):**

| when | leg | source |
|---|---|---|
| 09-18 20:08:22 | SOXL **sell** 6.4695 | trim (take-profit `9ed89a5f`) |
| 09-18 20:09:25 | SOXL **buy** 3.9046 | sleeve_fund |
| 09-22 18:40:45 | SOXL **sell** 5.7623 | trim (take-profit `8cd9f1ff`) |
| 09-22 18:41:03 | SOXL **buy** 3.847 | sleeve_fund |

Each round sells into strength, books CGT, and buys back **18–63 seconds later**.

### 1.2 Item 2: "Your position" vs "Ticker price" (answer + root cause)

**The two charts:**
- **Ticker price:** QQQ's own quote over time.
- **Your position:** the ₪ value of the shares you actually held each day (shares × price × FX). It rises when you buy, falls when you sell, and moves with the market in between. It is rebuilt **backwards from today's share count through your trade ledger**.

**Why they look the same.** Undoing every recorded QQQ trade leaves **+0.0441 shares** that no trade explains. The endpoint refuses to draw a share count it knows is wrong and falls back to the price. The screen keeps the "Your position" header, the value number and the % above the price chart, which is misleading. That's its own UI bug.

**Which holdings are affected** (live ledger-repair preview, the `episodes` section). The book holds more shares than the recorded trades explain:

| holding | unexplained extra shares | recorded trades since opening |
|---|---|---|
| UGA | +0.0824 | **exactly one**: buy 2.7956 on 09-22 08:39. Book holds 2.878. |
| USO | +0.0740 | last buy 2.5135 on 09-22 07:52. Book holds 2.5875. |
| QQQ | +0.0441 | 4 buys since 09-18 14:54, total 2.0771. Book holds 2.1212. |
| VTI | +0.0088 | since 09-25 |

**UGA and USO are off by the identical factor ×1.0295.** A single trade can't do that. Something scaled the **whole Commodities class** by one number.

**Candidates ruled out, and the observation that killed each:**

| candidate | ruled out by |
|---|---|
| ✏️ manual holding edit (`PUT /portfolio/position`) writes quantity with no ledger row | Audit log (2,690 entries back to 06-09): **zero** `PUT /portfolio/position` calls |
| `buy_funded` records a different count than it books | Code: `_apply_buy_funded` books `qty` and returns `shares=round(qty,4)`; `_record_apply` writes that same number. Can't differ by 2.95%. |
| rounding in the MONEY column | 4 decimals; can't produce 0.08 shares |
| `reconcile_service` FX-scale repair | writes an `Adjust` leg for every change; none present |
| duplicate legs | preview: `duplicates: 0` |

**The writer that fits exactly: `_rebalance_to` (PROVEN for Commodities).**
- It is the handler behind every "rebalance to target" / "Buy Equities" card.
- It computes one factor per asset class (`desired / current`) and calls `update_position(quantity=…)` on **every holding in every class**.
- It returns no `detail`, so `_record_apply` writes **no ledger legs at all**. It also charges no CGT on the implied sells.
- Audit log: the only rebalance accept after the UGA buy is **`rec_547b55` ("Buy Equities") at 2026-09-26 13:14:49**. The card id matches `sha1("rebal|Equities")[:6]`.
- **Blast radius:** rebalance cards were accepted **16 times** since 08-21, every one without trade records. That's why the ledger can't rebuild SOXL, BND, QQQ, USO and UGA history. The same card (`rec_547b55`, "Buy Equities") is **on your Today screen now**.

**Why it was silent:**
- The ledger is written best-effort ("bookkeeping must not cost a trade").
- NAV is conserved, so the rebalance's own guard passed.
- The only reader that notices is the per-holding chart, which fails politely by falling back to price.

**What is still INFERRED:** Equities.
- QQQ implies a factor of 1.0212.
- Applying that to VTI's recorded count doesn't land on today's 3.2901. VTI is off by about 0.06 in the other direction.
- So VTI has a **second** unrecorded change. The prime candidate is the `core_to_basket` legs: 4 VTI buys recorded with `price: null`, and the same path left "blind" sells.
- B1 below settles this by reproduction before anything is written.

### 1.3 Item 3: cash (PROVEN)

- `plan.cash_redeploy_at_ils = 1500` (your 09-21 decision, migration 0024). `cash_target_ils = 150`.
- You hold ₪1,124, which is below the trigger, so **no card is correct**.
- `_redeploy_cash_recs` fills under-target sleeves and core classes. It asks the screener **only when you hold nothing in a class the plan wants**. It never compares "top up QQQ" against "a better name in the same class".
- The screener (`buy_ideas`) already ranks 72 names live, but its weights are `0.0-no-tilt`, `owner_reviewed: false`.

### 1.4 Item 4: goals (PROVEN)

| where | target | years | return | projection |
|---|---|---|---|---|
| Plan screen / `/plan` | ₪121,386 = **live NAV** 25,148 × 1.3⁶ (`_auto_target`: "derived live from the current NAV") | 6 (horizon) | 24.98% measured | — |
| DB `plan.target_amount` (stored at last save) | ₪113,039 | — | — | — |
| "You're behind" card | **₪113,039** (the stored one) | **5** (2031 − 2026) | **8.5%** objective table | Monte-Carlo on the **static** 8.57% table → ₪31,498 |
| `/plan/projection` | ₪121,386 | 6 | mixed (85.9% measured) | median ₪62,708 |

- **Progress = NAV ÷ (NAV × 1.3⁶) = 0.2072, a constant.** Live `goal_progress: 0.2072`. It cannot move no matter what the market does.
- The "Set a target you'll actually hit" card rewrites the target to the projection. "Give it more time" rewrites the horizon. Both move the goalposts.
- Good news: the contributions ledger already exists and is correct. ₪20,000 on 08-03, one row, and standing orders write dated `deposit` rows. **No migration is needed.**

### 1.5 Item 5: hidden/done cards (PROVEN from code and payload)

- The server already sends `hidden_cards` with `how: "done" | "ignored"` and `hidden_at` for each card (13 today).
- `index.html` "show them" calls `POST /recommendations/restore-completed` (or `/restore`). That **deletes** the marks and re-renders the cards as ordinary live cards with working Accept buttons.
- Audit log: `restore` was called 09-22 18:43, and cards were re-accepted afterwards.

### 1.6 Item 6: what exists today

- `_momentum_recs`: 30- vs 150-day moving average plus 120-day return. Emits "X is in a downtrend / uptrend". Accept **only arms a stop-loss or trailing stop**. It never sells, and it returns at most 3 cards.
- `strategy_signal_service`: the sleeves' own rules. `btm_trend_soxl` is already a trend-filtered strategy on SOXL.
- `rules_service`: price rules (take-profit, stops, max-weight).
- Existing sell executors: `trim`, `sell_position`, both through the funding / FundingLedger path and recorded in the ledger.
- **A caution from your own evidence (STATUS B2):** the timing overlay on TQQQ turned a 40.25%/yr instrument into 17.19%, below plain QQQ, with a deeper drawdown. A trend-based "sell" signal is exactly that kind of overlay. So the reviewer must **measure each signal against simply holding, on that holding's own history, before it's allowed to raise a sell card.**

---

## 2. PR A: the banner matches the cards, conflicts become a decision, history is read-only (items 1 + 5)

**Goal:** a triggered rule always has a card you can act on. A card never disappears silently. Hidden and done cards are browsable with their status, without being resurrected.

### A1. Show the conflict instead of hiding it (`services/contradictions.py`, `services/recommendations.py`)
- `sells_a_sleeve_at_target` stops dropping the card. It **converts** it into a conflict card:
  - `kind: "decide_rule_vs_sleeve"`, same id (`rule_<id>`), `severity: MEDIUM`
  - The title names both sides: *"SOXL hit your take-profit, but it's exactly at your 40% sleeve"*.
  - `why` quotes both numbers (weight vs sleeve target, price vs rule level).
  - **Two actions, both simulated before display**, per the card-claim invariant (the effect is re-measured by applying the spec, not by asserting on text):
    - **Sell & lower sleeve:** trim by the rule's planned slice (`take_profit_fraction`, 25%) and resize the sleeve by the sold weight via `sleeve_service.add_or_resize`. The card shows the new sleeve % and the core % before you tap.
    - **Keep & update rule:** move the rule to its next rung (`next_rung`, +10%, so 148.52 → 163.37) and re-arm it. Nothing is sold. The card shows the new level and lets you type a different one.
- Only rule-driven sell cards and the new reviewer's sell cards (PR E) become conflict cards. Other sells at target stay suppressed as today, but are now **listed** (see A3).

### A2. Fix the ladder and the banner order (`services/rules_service.py`, `services/recommendations.py`)
- `resolve_rule`: re-arm a take-profit on `executed` **and** `accepted`. Today it only checks `accepted`, which nothing sends.
- Declined or ignored still latches off, as designed.
- Move the banner reconciliation **below** `contradictions.check()`. A conflict card counts as a visible card, so the "heal" branch can't retire a rule that has a conflict card.
- Anti-churn guard: a take-profit card on a sleeve ticker whose sleeve funder would re-buy within the same run is always routed through A1, never executed straight.

### A3. "Show them" becomes history, not restore (`static_app/index.html`, `api/routes/recommendations.py`)
- "show them" opens a **read-only list** built from `hidden_cards`.
- Each row has a badge: **✅ Done** / **🙈 Ignored**, then *"on 28 Sep 09:08"*, the title and the amount.
- There's no Accept in this list. Each row has a separate **Restore** button that calls a new `POST /recommendations/{rec_id}/restore` (per card) and asks for confirmation first.
- The old bulk `/restore` and `/restore-completed` stay for API compatibility, but the UI no longer calls them.
- Cards hidden by a conflict (A1's non-rule cases) appear in the same list with a **⚖️ Blocked** badge and the reason.

### Tests (created and run)
- `test_rule_card_survives_its_sleeve_conflict.py`: the SOXL shape (weight 40.1%, sleeve 40%, triggered take-profit) produces exactly one visible `decide_rule_vs_sleeve` card, and the banner `carded` includes it.
- `test_banner_never_counts_a_hidden_card.py`: for every card the contradiction pass drops, the banner doesn't count it.
- `test_take_profit_ladder_rearms_on_execute.py`: executing a take-profit moves the level to `next_rung` and keeps the rule active.
- `test_keep_updates_rule.py` / `test_sell_lowers_sleeve.py`: apply each choice, re-measure the book and sleeves, and assert what the card claimed.
- `test_history_does_not_resurrect.py`: listing history changes no marks. Per-card restore changes exactly one.

### Smoke `scripts/smoke/smoke-pr72.ps1` (read-only)
- `/health` SHA matches.
- `rule_banner.carded ⊆ visible card ids`.
- Every `contradictions[*].suppressed == true` card is either in `hidden_cards` or has become a visible conflict card.
- The SOXL rule shows a conflict card.

### Risk / rollback
- Frontend and backend only. No migration.
- Revert = previous SHA.

---

## 3. PR B: every change to your holdings writes a trade (item 2)

**Gate: B1 is investigation only and reports back to you. B2–B4 start after your OK.**

### B1. Prove the remaining cause (no writes)
- In the sandbox, seed a book shaped like yours (Equities, Fixed Income, Commodities plus cash) and accept a rebalance card. Assert that every holding's ledger sum no longer matches its quantity. That's the reproduction.
- Replay the VTI episode (the 4 `core_to_basket` buys with `price: null`, the 13:14:28 trim, then the 13:14:49 rebalance) and pin down where the second ~0.06-share VTI discrepancy comes from.
- **Report:** the proven cause for VTI, plus every write path that touches `positions.quantity` (full list from a grep plus a runtime hook in tests) and whether each writes a ledger leg.

### B2. Make rebalance a real trade (`services/recommendations.py::_rebalance_to`)
- Each holding's quantity change becomes a recorded **Sell** or **Buy** leg: shares, live price, ₪ value, and CGT on the gain for sells, consistent with every other sell path.
- Proceeds net of tax fund the buys. If tax makes the reshuffle net-negative, the card says so before you tap.
- **This changes the card's economics.** Today a rebalance is tax-free by accident. See question Q3.
- Structural guard: a **test-time hook** fails any test in which `positions.quantity` changes without a matching `Transaction` in the same flush. This covers every writer present and future, so the next silent path is caught by the suite, not by a chart.

### B3. Repair history (preview → your OK → apply)
- Extend `ledger_repair` with an **"unrecorded rebalance"** fix. Only where the answer is exact: an anchored episode where exactly one rebalance sits between the recorded trades and today (UGA, USO, QQQ, plus VTI if B1 proves it).
- Write one `Adjust` leg per holding: +0.0824 UGA, +0.0740 USO, +0.0441 QQQ. Note: *"rebalance rec_547b55 2026-09-26 13:14:49, reconstructed"*.
- Older rebalances (15 before 09-22) remain **reported, not guessed**, as the repair already does for blind sells.
- Also apply the existing, already-built repair for the 28 blind sells where confidence is `estimate` (8), after you review the preview.

### B4. The chart tells the truth when it can't draw (`static_app/index.html`)
- When `value.available == false`, the "Your position" tab shows the reason in one line: *"Can't draw your position yet — 0.0441 shares have no recorded trade"*. The price chart is shown **labelled "QQQ price"**, and the header no longer shows a position value that doesn't exist.

### Tests
- `test_rebalance_writes_legs.py`
- `test_every_quantity_change_has_a_transaction.py` (the hook)
- `test_repair_reconstructs_single_rebalance.py`
- `test_position_tab_never_shows_price_as_position.py`

### Smoke `smoke-pr73.ps1`
- The ledger-repair preview `episodes` has no "write path that records nothing" rows for QQQ, USO or UGA after repair.
- `/portfolio/position-history?ticker=QQQ` → `value.available == true`.

### Risk
- Money-touching (investing-discipline §1c): the repair is dry-run by default and prints every row before and after. Apply needs `confirm=true`.
- NAV and cash are unchanged by the repair; it's bookkeeping, not a trade.

---

## 4. PR C: goals on the money you put in (item 4)

### C1. One goal function (`api/routes/plan.py::_auto_target` → `services/goal_service.py`, new)
- **Target** = Σ over contributions *i* of `amount_i × (1 + ROI)^(years from deposit_i to the deadline)`, read from the `contributions` ledger.
  - Today: ₪20,000 from 2026-08-03 to 31 Dec 2031 at 30%/yr → **≈ ₪82,700**. This replaces the drifting ₪121,386.
  - It changes **only** when a deposit or withdrawal is recorded, or you edit ROI or deadline.
  - Each future standing-order deposit adds its own compounded share.
- **Progress** = NAV ÷ target. Gains now move it.
- Also shown: **"On track line"**, meaning where you *should* be today (contributions compounded at ROI to today) against where you are. That's the honest "ahead/behind" number.
- `/plan`, `/plan/projection`, the behind-goal cards, the digest and `context_service` all call this one function. `plan.target_amount` becomes a cached copy of it, written on save.
- Frontend `recalcTarget()` stops using `PLAN_NAV` and shows the breakdown: *"₪20,000 put in, compounding 30%/yr to 2031 → ₪82,7xx"*.

### C2. One return basis
- The behind-goal card uses `measured_assumptions.book_assumptions()` (the same as `/plan`), not the static table. The headline is measured (~25%/yr).
- The conservative figure (objective table, 8.5%) is shown as a second line: *"at a conservative 8.5%: ₪…"*.
- Years come from the same deadline everywhere. No more 5 on one card and 6 on another.

### C3. Remove the goalpost-movers
- Delete lever D, "Set a target you'll actually hit".
- Lever C, "Give it more time": see Q2.
- Lever A (add a monthly contribution) stays. A contribution *is* the thing that legitimately moves the goal.

### Tests
- `test_goal_ignores_market_moves.py`: NAV ±30% → target unchanged, progress changes.
- `test_goal_moves_only_on_contribution.py`
- `test_one_goal_everywhere.py`: `/plan`, `/plan/projection`, the behind card and the digest quote the same target, years and return.
- `test_no_card_rewrites_the_target.py`

### Smoke
- `/plan.goal_progress` ≠ `1/(1+roi)^n`.
- `/plan.target_amount == /plan/projection.target_amount == behind-card target`.

### Risk
- No migration.
- The target drops from ₪121k to ~₪83k, because it no longer compounds your gains as if they were deposits. That is the point, but it'll look like a big change on screen. The card says why once.

---

## 5. PR D: cash above ₪1,500 gets researched (item 3)

**Unchanged:** trigger ₪1,500 (Plan → cash, already editable), invest down to your ₪150 hard floor, one Accept, funding rules (only buy what the plan has room for, per-name caps, FundingLedger).

### D1. Rank destinations (`services/redeploy_research.py`, new; called from `_redeploy_cash_recs`)
- **Candidates:**
  - (a) each under-target **sleeve**. Its ticker is fixed by the strategy, so the screener never swaps a sleeve's instrument.
  - (b) each under-target **core class** with the name you already hold there.
  - (c) up to 3 **screener names** in each under-target core class, including ones you don't hold.
- **Score** (each part is shown on the card, nothing hidden):
  - plan fit = gap size in ₪
  - screener score, for (c)
  - trend state from PR E's reviewer, so it doesn't buy into a confirmed downtrend
  - room under the concentration cap
  - diversification, where a new name in an over-concentrated class gets a bonus
- The card proposes the **top 1–3 legs**, and lists the runners-up as "also considered" with their score and the reason they lost.
- A new name is proposed only when it beats topping up your existing holding in that class by a set margin. The margin is a config value in the assumption registry, not hard-coded.

### D2. Honesty requirements (investing-discipline §1, §3, §5)
- Every new-name leg carries the screener's own disclosure: the weights aren't owner-reviewed yet (`0.0-no-tilt`). Plus the `as_of` of the data used, and the card ends with "Not financial advice."
- Stale or unpriced candidates are dropped and listed, never scored.
- Accept updates the **tracked book only**. "No brokerage order was placed" is shown as today.

### Tests
- `test_redeploy_prefers_gap_then_score.py`
- `test_redeploy_never_breaches_floor_or_caps.py`
- `test_redeploy_new_name_needs_margin.py`
- `test_redeploy_never_swaps_a_sleeve_ticker.py`
- `test_card_claims.py` extended to the new card

### Smoke
- With cash ≤ ₪1,500: no card.
- A dry-run endpoint, `?simulate_cash=3000`, shows the card read-only without touching the book.

---

## 6. PR E: holdings reviewer, with sell cards (item 6)

**My answer to "new agent or existing?"** Extend what exists, as a **per-holding review module** feeding the same card pipeline. Don't add a separate agent that argues with the others.
- It replaces `_momentum_recs`.
- It reuses the sell executors, funding and ledger, and the PR A conflict card.
- This matches your earlier call for the solver: feed the existing agents rather than add a debater.

### E1. What it measures, per holding you own (cash excluded)
- **Short term:** 20-day vs 50-day average, 1-month return, distance from 20-day high, realised 20-day volatility.
- **Long term:** 50-day vs 200-day average, 6- and 12-month return, drawdown from 52-week high.
- **Position context:** weight vs sleeve or core target, unrealised gain and CGT cost of selling, rules already armed.
- Output state per holding: `Hold` · `Protect` (arm or raise a stop, as today) · `Trim X%` · `Exit`, with every number that produced it shown.

### E2. When it may raise a SELL card (the evidence bar)
- Sell and trim signals are **backtested on that holding's own 10-year history** with the existing `strategy_backtest` engine, net of costs and Israeli CGT, against simply holding.
- A signal may raise an actionable sell card **only if it beat holding on the same window with no worse drawdown**. The measured edge goes on the card.
- Otherwise the card is guidance only: *"trend turned down; historically, selling on this signal did worse than holding QQQ"*.
- This is the lesson from B2 (TQQQ overlay 17.19% vs 40.25% held).
- **Sleeve holdings:** the sleeve's own strategy rule decides. The reviewer reports the sleeve's signal and doesn't add a second opinion. If its view disagrees, the card goes through PR A's conflict flow.

### E3. Card shape
- Title: *"Sell 25% of QQQ — trend broke on both horizons"*.
- Shows ₪ amount, shares, CGT, where the cash lands (redeploy vs above the floor), and the sell-vs-hold backtest line.
- `apply: {"kind": "trim", …}` or `sell_position`, the existing executors, so the ledger records it.
- Accept updates the tracked book. You place the order at your broker.

### E4. Cadence
- Runs in the Today build, cached daily (price history is daily).
- A push notification only when a holding moves to `Trim` or `Exit`.

### Tests
- `test_reviewer_states.py`: fixtures for up, down and choppy.
- `test_reviewer_sell_requires_measured_edge.py`
- `test_reviewer_defers_to_sleeve_rule.py`
- `test_reviewer_card_claims.py`: apply, re-measure.

### Smoke
- `/recommendations` returns a `holding_review` block with one row per non-cash holding.
- No actionable sell without `measured_edge`.

---

## 7. Delivery mechanics (unchanged conventions)

- The shell on your PC can't mount `C:\dev\Investwise-pro` right now (the 8 Sep Windows update issue). I build and test in the cloud sandbox from `git archive` of your HEAD (the PR 31 route).
- Both gates run: `pytest -q` **plus the Postgres job**, and `ruff check app tests`.
- Each PR comes as a `.patch` delivered to your folder, plus a pure-ASCII `ship-prNN.ps1` that names its files, commits with `-F`, pushes, and polls `/health` for the SHA. Then the smoke test runs.
- STATUS.md gets one entry per PR, with the root cause **and what was ruled out**.
- PR numbers are provisional (72–76).

---

## 8. Questions before building (recommended default first)

1. **Q1: Goal deadline vs horizon.** Your plan says deadline 2031 *and* horizon 6y. From today, that's 5.3 years, not 6.
   - **Recommended:** the deadline governs (31 Dec 2031), and "years" is shown as derived from it.
   - Alternative: the horizon governs and the deadline is computed.
2. **Q2: "Give it more time" card.**
   - **Recommended:** keep it as guidance only, with no Accept that edits your plan.
   - Alternative: remove it.
3. **Q3: Rebalance and tax.** Making rebalance a real trade means its sells owe CGT like every other sell, so a rebalance will now show a tax cost.
   - **Recommended:** yes, charge it.
   - Alternative: keep tax-free (not recommended; it isn't what would happen at your broker).
4. **Q4: History repair scope.**
   - **Recommended:** write exact reconstructions only (UGA, USO, QQQ, plus VTI if B1 proves it) plus the 8 "estimate" blind sells, after you see the preview. Leave the rest reported.
   - Alternative: exact ones only.
5. **Q5: Sell-card evidence bar (E2).**
   - **Recommended:** an actionable sell only when the signal beat holding on that name's own history.
   - Alternative: show sell cards on trend alone, flagged "unmeasured".
6. **Q6: "Sell & lower sleeve" size.**
   - **Recommended:** the sleeve drops by exactly the weight sold (40% → ~30% for a 25% slice).
   - Alternative: you type the new sleeve %.

_Not financial advice._
