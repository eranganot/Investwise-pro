# Making InvestWise Pro Simple — Proposal

**For:** you, a hands-on owner who isn't a finance expert.
**The job:** see how your wealth is doing, and be told plainly what to fix.
**Language:** plain by default, the technical "why" available on a tap.

---

## 1. Why it feels complicated today

The current dashboard shows **nine panels at once** — Tax, Risk, Monte Carlo,
Wealth Health, Allocation, Research, plus feeds and calculators. That's the
*engine room* of the system exposed to the driver. A normal user sees inputs and
numbers, but no answer to the three questions that actually matter:

> "How am I doing? What should I do? …and how do I do it?"

It also speaks in jargon — *divergence, Depth 3, Probability of Ruin, Impact
Score, VETOED* — which are correct internally but mean nothing to a person.

The fix isn't more features. It's **hiding the machinery and leading with the
answer.** Four principles drive every option below:

1. **Answer first, details on tap.** Lead with the conclusion; tuck the math behind a "Why?".
2. **One primary thing per screen.** Never make the user choose between nine tools.
3. **Plain money language.** Talk in shekels, plain risks, and clear actions.
4. **Always show the next step.** Every screen ends with something to do (or "nothing to do — you're fine").

---

## 2. Plain-language makeover (used by all options)

| Today (jargon) | Becomes (plain) |
|---|---|
| Wealth Health Score 72 | **Your wealth is Healthy (72/100)** |
| Impact Score 53 | "How much this helps" |
| Probability of Ruin 3% | "Chance of a big drop" |
| VETOED (volatility cap) | "Skipped — too risky right now" |
| Allocation drift / SAA-TAA | "Your mix vs. your plan" |
| Monte Carlo stress | "Stress test" |
| Depth 3 / backbone / divergence | *(hidden — internal signal)* |
| Concentration breach | "Too much in one place" |
| Tax-loss harvesting | "Use a loss to cut your tax bill" |

---

## 3. Three directions to choose from

### Direction A — "Today + Holdings" (a calm 2–3 screen app)
The home screen is **Today**: one health line, then *the 1–3 things to improve*,
then your *biggest risk*. A second tab, **Holdings**, shows what you own. Optional
**Explore** hides the scenarios and deeper tools.

```
┌─ Today ───────────────────────────────────────────┐
│  Your wealth is HEALTHY        72/100   ◴ updated  │
│                                                    │
│  3 things to improve                               │
│  ▸ Trim Teva — it's 28% of your money (too much    │
│    in one stock).            [Why?] [Do it] [Snooze]│
│  ▸ Harvest a ₪3,000 loss to cut this year's tax.   │
│                              [Why?] [Do it] [Snooze]│
│  ▸ You're 100% in one currency — consider spreading.│
│                              [Why?] [Do it] [Snooze]│
│                                                    │
│  Biggest risk: a market crash could drop you ~30%. │
│  [See what a crash would do →]                     │
└────────────────────────────────────────────────────┘
   [ Today ]   [ Holdings ]   [ Explore ]
```
- **Pros:** nails both your jobs (track + act), calm, scannable, little to learn.
- **Cons:** still an app with tabs to navigate.
- **Effort:** Medium.

### Direction B — "Guided setup, then the calm home" (recommended start)
Same end state as A, but the **first run walks you A→B** in three steps:
**1) Add your holdings → 2) "Here's your health" → 3) "Here's what to do."**
After setup it always opens on the simple **Today** home.
- **Pros:** best for a non-expert — removes the "what do I even do here?" moment; clear journey.
- **Cons:** a bit more to build (the 3-step setup).
- **Effort:** Medium–High.

### Direction C — "Ask your money" (conversational home)
The home is just four big plain questions you tap:
*"How healthy is my money?" · "What should I do this week?" · "What if the markets
drop?" · "What am I holding?"* — each opens one simple answer screen.
- **Pros:** zero learning curve, extremely friendly.
- **Cons:** weaker for *tracking over time* (each answer is its own page, less of a persistent dashboard).
- **Effort:** Medium.

---

## 4. My recommendation

**B's guided setup → A's calm "Today" home**, with C's plain questions folded into
**Explore**. Concretely, three screens total:

- **Today** — health in one line + the 1–3 fixes + biggest risk. (Your "tell me what to do.")
- **Holdings** — what you own, value, up/down, add/import. (Your "track my portfolio.")
- **Explore** *(optional, collapsed)* — "what-if" scenarios and the deeper tools, phrased as questions for when you're curious.

Everything else from today's dashboard becomes **"details on tap"**: the scores,
the confidence breakdown, the engine names, the Monte Carlo numbers all live behind
a **"Why?"** on each recommendation — present for trust, invisible by default.

### What changes vs. today
- **Remove from the main view:** the six standalone calculators (Tax, Risk,
  Simulation, WHS, Allocation, Research). They power the answers behind the scenes
  and live in Explore for the curious.
- **Keep, but rephrase:** the decision feed → "3 things to improve"; WHS → "wealth
  health"; risk alerts → "biggest risk."
- **Add:** a friendly empty state ("Add your holdings to begin") and a per-action
  **Why? / Do it / Snooze**.

---

## 5. The whole experience in one sentence
> Open the app → see "your wealth is healthy, here are the 3 things to fix" →
> tap one → read a plain why → press *Do it* (or *Snooze*) → done.

(The powerful engines you already built stay exactly as they are — we're only
changing the *face* of the product, not the brain.)

---

## 6. Next step
Pick a direction (I recommend **B→A**) and I'll build it as a new simplified
front-end served at `/app`, leaving the current detailed dashboard at `/dashboard`
for power use. Nothing on the backend changes.
