# Execution plan v2: true numbers, actionable cards, short cards, an organised Plan tab

_v2.2, 2026-10-06 (v2.1 + the Plan-tab layout confirmed). **Nothing has been coded.** Updated after PR 98 shipped (`a603aa9`, live on
production) and after Eran's review of v1. Every number below was measured on production or by running
the app's own code on real data. Plan-tab illustrations: the **"InvestWise Plan tab options"** canvas._

## What changed since v1

**PR 98 is live, and it fixed one thing this plan raised, partly.** The Plan screen and the projection now quote the
goal model's numbers (17.9%/yr expected, 50.3% volatility), so the projection's chance is **37.3%** against the
goal model's **38.1%**, close where it was 42.6% vs 33%. It did **not** touch:

- the goal levers (F1): still "680/month → 70%" and "2039 → 70%";
- your protective rules in "chance today" (F2): the projection says 37%, the goal card says 33% on its headline and
  1% with your rules armed;
- dividends (F3), the SOXL claim (F4), "ILS to move" (F5), the SPY label (F6), the account-history copy (F7).

New on Today since v1: a "QQQ is closing on your take-profit" guidance card. PR 102 covers it (row 5 of its table).
**Every v1 finding still stands.** PR 98 removed one inconsistency, so PR 100 no longer needs its projection step.

## Your decisions (recorded)

| # | Decision | Your answer |
|---|---|---|
| D1 | What the goal cards size their suggestions on | **(b)**: after you accept "Retune your protection" |
| D2 | Where the Heads-up list goes | **Bottom of Today, above the Holding review** |
| D3 | Near-cap card | **Add the Act option** (raise the cap to target + band) |
| D4 | Minimum size for an actionable card | **OK**: ₪50 for tax-harvest cards; ₪100 or 0.5% of the book for the rest |
| D5 | "What would it take?" | **Folded at the bottom of Explore** (part of the confirmed layout) |
| D6 | Plan tab layout | **Confirmed 2026-10-06: A + C combined, protective rules in My mix** |
| D7 | Dividend fix (PR 101) | **First** |

**Shipping order:** PR 101 → 99 → 100 → 102 → 103 → 104. Each PR ships alone, with its smoke test written and
run, and STATUS.md updated with the root cause.

---

## PR 101 (ships first): history prices include dividends

**What I'll change.** The app downloads 10 years of daily prices for every backtest (the holdings review, your
sleeves' measured returns, the goal model, the solvers). It reads the **plain closing price**, which leaves out
dividends. It will read the **dividend-adjusted price** instead, the same series Yahoo uses for "total return".
Live prices, your holdings' values and the price charts stay exactly as they are.

**Why.** A fund that pays you a dividend looks, on plain prices, as if it is slowly losing value. For a bond fund
the dividend is most of the return, so the app concludes that holding it loses money.

**Measured on the real 10-year history (2016-10 → 2026-10), each ticker's return per year if held:**

| Ticker | Plain prices (today) | With dividends (after) | Does the sell signal still "beat holding"? |
|---|---|---|---|
| BND | **−1.76%** | **+1.25%** | **Yes → No** (both signals) |
| VTI | 13.08% | 14.90% | No → No |
| SPY | 13.61% | 15.42% | No → No |
| SOXL | 48.21% | 49.63% | No → No |
| QQQ | 20.31% | 21.13% | No → No |
| GOOGL | 24.03% | 24.15% | No → No |
| UGA, USO | unchanged (no dividends) | | USO short signal stays Yes |

**How it affects you.**
- **The "Sell all of BND" card disappears.** Measured properly, holding BND returned more (1.25%/yr) than the
  signal (1.02%/yr), so it fails your rule "sell only when the signal beat holding". BND stays in your 60/40 core.
- **Every measured return goes up a little** (0 to 1.8 points a year), so your goal odds rise slightly. Your sleeves'
  measured returns and SPY all rise by roughly the same amount, so comparisons against SPY hardly move.
- For one night the cards that depend on backtests go quiet: the old measurements are marked out of date and
  re-measured, and no card acts on an out-of-date number.

**Impact.** No more sell recommendations on bond and dividend funds that rest on missing dividends. All
backtest-based numbers become total-return numbers, as a broker statement shows them.

---

## PR 99: the goal cards only promise what Accept delivers

**What's wrong today, in one line:** two goal cards promise a 70% chance, but accepting them would make your goal
bigger, so the real chance after accepting is **lower than today's**.

Your goal is defined as *30% a year on every shekel you put in* (your 09-28 decision), so:
- **a monthly deposit raises the target**: each deposit has to grow at 30% too;
- **a later deadline raises the target**: 30% more for every extra year.

The cards computed "70%" with the target frozen at ₪82,692. Measured with the app's own probability engine (PR 98's
goal model: 17.9%/yr, 50.3% volatility):

| If you accept... | The card says | Your goal becomes | Real chance |
|---|---|---|---|
| nothing (today) | | ₪82,692 | 38.1% |
| "Add 680/month" | 70% | **₪173,172** | **30.4%** |
| "Move your deadline to 2039" | 70% | **₪674,543** | **26.9%** |
| "Set a target of 9%/yr" | 70% | ₪31,926 | 70% ✓ (this card is right) |

Root cause: the lever code (`frontier_service.py` ~604-650) never recomputes the goal for the change it proposes.
The engine reproduces the cards' own 70.2% when the target is frozen, which confirms the diagnosis.

**What I'll change.**
1. Every goal card computes the goal **as it would be after you press Accept**, using the same goal formula as the Plan
   tab, and shows a card only if that goal reaches 70%.
2. Your book is expected to grow ~18%/yr and the goal asks 30%, so extra deposits and extra years both widen the gap.
   **The deposit card and the deadline card will disappear.** In their place, the goal summary says once:
   *"Adding money or time doesn't help this goal: it asks 30%/yr on every shekel, and the book is expected to make about
   18%."*
3. **D1 (b):** the remaining suggestions are sized on your book **after** "Retune your protection" and say so. If you
   ignore that card, they are sized on your rules as they are.
4. The goal's headline becomes one honest number: **your chance with your rules as they are**, with "without the
   rules" beside it, the same figure everywhere it appears.

**How it affects you.** Today: 4 goal cards, 2 of them promising something false. After: **2 goal cards**,
"Retune your protection" and "Set a target this book can reach", both true, plus one line explaining why money or time
won't close the gap.

**Impact.** You can press Accept on any goal card and get what it says.

**Decided 2026-10-05:** PR 99 goes ahead as written, and the goal definition stays as it is today. Reviewing the
definition (should deposits or extra years raise the target at all?) is on the backlog in STATUS.md: "🔴 BACKLOG —
review the goal definition".

---

## PR 100: every number says what it measures, without making cards harder to read

**Your note: don't make the cards more complicated.** The rule for this PR: **no card gets a new sentence.** Each
fix either deletes a false sentence or replaces it with a shorter true one.

| Fault | Today | After |
|---|---|---|
| SOXL card says trimming "moves your portfolio closer to your plan" | SOXL is 33% vs a 40% target, and your buy-back rule says the plan wants ₪1,673 more | No trim suggestion while SOXL is under its target. It becomes a one-line Heads-up: "SOXL +36% since you bought: ₪2,155 of gain. Your take-profit at 183.17 covers it." Over target, it becomes an actionable "Trim to target" card. |
| Generic "Why / Impact" text on cards with nothing specific to say | "This helps your portfolio track your plan." | Removed. The card shows nothing rather than a claim it didn't compute. |
| "You marked this done … still ₪187 / ₪2,155 to move" | Appears on cards that never asked you to move money | Only cards that asked for a trade can say this. |
| Benchmark chart: "(vs SPY +38.95%)" | Reads as SPY's return; SPY did +15.3% | "You +54.5% · SPY +15.3%". Title: "If you had held today's holdings for the past year", since this chart is a reconstruction, not your account. |
| Account history: "Recorded every trading day" next to "Not recorded 08-17 to 08-24"; 4 decimals; a list of 13 dates | | "Recorded since 16 Aug (one gap: 17–24 Aug)". 2 decimals. The date list moves behind "Details". |

**How it affects you.** Fewer sentences on the screen, and none of them false.

---

## PR 102: every card can be acted on (approved)

- A **card** asks you to do something, and **Accept does it**. No Accept means no card.
- Knowledge without an ask becomes a **Heads-up**: a collapsed list of one-liners at the **bottom of Today, above the
  Holding review (D2)**. Tap to expand, swipe to dismiss. No "Mark as done", and it never re-fires.
- The 12 card types that today give advice without an Accept:

| # | Card | After |
|---|---|---|
| 1 | Trim {ticker} | Card: Accept sells the trim |
| 2 | Diversify your currency exposure | Heads-up |
| 3 | Hold more liquid assets | Heads-up |
| 4 | Holdings review Hold / Trim / Buy more | Trim / Buy: card, only when measured (same bar as sells). Hold: Heads-up |
| 5 | Closing on your take-profit / trailing stop | Heads-up (the rule itself raises the actionable card at the level) |
| 6 | {ticker} is up X% | Over its target: card ("Trim to target"). Otherwise: Heads-up |
| 7 | Nearly at your cap | Heads-up **plus** an Act option "Raise the cap to target + band" when the cap sits within one band of the target (**D3**) |
| 8 | Reduce sector concentration | Card if a funded swap exists, else Heads-up |
| 9 | Put idle cash to work | Removed: the actionable cash-redeploy card already does this |
| 10 | Lift your portfolio yield | Heads-up |
| 11 | You're trailing {benchmark} | Heads-up |
| 12 | Tax loss to harvest | Card only when it saves ≥ ₪50 (**D4**); today's ₪5 card disappears |

- A test makes this permanent: any card without a working Accept fails CI.

---

## PR 103: shorter cards (approved; word limits are guidelines)

- **Goal cards:** one "Your goal" summary at the top of the goal group (approved), and each goal card keeps its
  claim, what Accept does, and before → after chips; everything else sits behind "Details".
- **Card shape (guideline, not a hard limit):** title with its number → one or two plain sentences → "Accept will…" →
  before → after chips → Details. A test flags a card that grows far past this (about 2x), not one that is a few words
  over.
- **Before → after examples (approved):**
```
Retune your protection: goal odds 1% -> 16%
After a stop fires, the app keeps SOXL and QQQ out until the price falls back below the
sale price, so in rising markets they sit out. That costs your goal 28 points.
Accept will pause 5 stop rules and move the drawdown guard 10% -> 15%. Nothing is bought
or sold; one tap re-arms them.
[odds 1% -> 16%] [halving 0% -> 0.6%] [median 31,968 -> 36,055] [worst 5% 22,169 -> 17,061]
Details v
```

---

## PR 104: the Plan tab — A + C combined (confirmed 2026-10-06)

The **"InvestWise Plan tab options"** canvas shows today's tab and three options as phone screens, using your real
numbers, with pros and cons beside each:

- **Today:** one long scroll, with the problems marked: objective + risk appear twice, "Years invested" does nothing,
  risk is set in 5 places, there are two solvers, and a single Save button sits at the bottom.
- **A · Three sub-tabs (Goal · My mix · Explore):** tap the sub-tabs on the frames.
- **B · One page, four numbered sections (Goal · Mix · Protection · Settings).**
- **C · Summary dashboard + edit sheets:** tap "Edit" on the Goal row. My recommendation.

All three remove the duplicate objective/risk controls and the unused horizon field. B and C give your protective
rules a place on the Plan tab; they are what's costing your goal the most today (1% vs 29%). D5 ("What would it
take?") follows from the option you choose: in A it lives in Explore, in C under Strategies → Browse, in B on the
catalog screen.

**Confirmed 2026-10-06.** The layout to build: **A's three sub-tabs, each built the C way, with your protective rules in
My mix.** The reference screens are the canvas row "Suggested: A's sub-tabs, built the C way…" (AC-Goal, AC-Mix,
AC-Explore, AC-EditGoal, AC-Protect); options A, B and C stay on the canvas for the record only.

| Sub-tab | What's on it | How you change things |
|---|---|---|
| **Goal** | Goal summary (target, progress, chance with/without your rules); rows for Target, Monthly deposit, Risk dials; the goal lever card(s) | Edit opens a small sheet with its own Save and a live odds preview |
| **My mix** | Held vs target for sleeves, core, commodities, cash; Fund all / Change core; **Your protective rules**: each sleeve's stops, the drawdown guard, what they cost the goal, the Retune action | Retune Accept on the page; "Edit rules" opens the rules sheet with an odds preview |
| **Explore** | Strategy catalog; "What would it take?" folded at the bottom (**D5**) | Add a strategy from its card |

Removed in all sub-tabs: the duplicate objective/risk control, and "Years invested". The protective-rules section
shows only stops and the guard; take-profits, caps and buy-back holds stay on Holdings › Rules. **Build order inside
PR 104:** (a) the three sub-tabs with today's blocks moved in, (b) the Goal and Rules sheets, (c) the risk-dials sheet
and the removals. Each step ships and is QA'd on the Pixel separately.

**Nothing left to decide for PR 104.** It is built from the canvas screens above, in the three steps listed, after
PR 103. Each step ends with a Pixel-9 QA list and a smoke test, written and run.

---

## Appendix: the evidence (unchanged from v1)

- **F1 goal levers.** `goal_service.goal_from()` on your ledger (₪20,000, 2026-08-03, 30%/yr): today ₪82,692; deadline
  2039 → ₪674,543; +680/month → ₪173,172. Engine `frontier.goal_probability` at the PR 98 inputs: 38.1% today (matches
  live), 30.4% and 26.9% after the levers, and 70.2% with the target frozen (reproduces the card).
- **F2 chance today.** Live card: 33.1% "today", 1% "with your protective rules as they work today", 28.7% rules off;
  projection 37.3% (after PR 98).
- **F3 dividends.** `providers/live.py` reads `indicators.quote.close`; `holding_review.measure_ticker()` on BND: close →
  0.05 vs −1.76 (edge True, the live card says 0.06 vs −1.78); adjclose → 1.02 vs 1.25 (edge False).
- **F4** `recommendations.py:358-360` fallback text on dimension `holding`; SOXL 33.2% vs 40% target; buy-back rule
  status "plan wants ₪1,673 more".
- **F5** `recommendations.py:496` reads `est_amount`: QQQ 186.75 = 0.76% headroom × NAV; SOXL 2,154.67 = unrealised gain.
- **F6** `/portfolio/performance`: benchmark_return_pct 15.33, excess_return_pct 39.19.
- **F7** `/portfolio/nav-history?range=1Q`: 38 dates, 7 of them weekends, 30 trading days; gap 08-17 → 08-24.
- **Checked and correct:** SOXL gain arithmetic, the QQQ 20% cap (your own max-weight rule), BND sale arithmetic,
  required 26%/yr, projection percentiles, sleeve sums, max drawdown 35.06%, the account-history chart series.
