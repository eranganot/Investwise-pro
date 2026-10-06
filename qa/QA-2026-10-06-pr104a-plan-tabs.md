# QA — PR 104a: the Plan tab's three sub-tabs (Pixel 9)

_Step (a) of PR 104 (Execution plan v2.2, D6 = A + C): Goal · My mix · Explore, with today's blocks moved in.
The edit sheets (b) and the risk-dials sheet + removals (c) come next — the old Goal settings form is still there
on purpose (see "looks bad but isn't")._

## 0. Pre-flight (2 min)
- Production serves this build: open `/health` → `commit` is the SHA in the PR 104a STATUS entry. (Checked by Claude
  at deploy time.)
- PWA: close and reopen the installed app once so the new shell loads (service worker `iw-v45`).

## 1. Sub-tabs
- Plan tab → a **Goal · My mix · Explore** switch sits under "Plan". Tap each: only that sub-tab's content shows.
- Leave Plan, come back → the sub-tab you were on is still selected.
- Fail: two sub-tabs' content at once, or the switch missing.

## 2. Goal sub-tab — the summary
- Card "YOUR GOAL": **₪82,692 by Dec 2031**, a green progress bar, "₪… today / …% of the way".
- Big number = the chance **with your protective rules as they are** (today ~0.7%) and chips "… % without them" and
  "needs 26%/yr · expected ~18%".
- An "off track" chip at the top right.
- Fail: the chance differs from Today's "Your goal" box, or "NaN"/"undefined" anywhere.

## 3. Goal sub-tab — ways to close the gap
- Under "WAYS TO CLOSE THE GAP": the **Set a target this book can reach** card (same as on Today, with Accept and
  Details), then a one-line **Retune your protection: …** row with "My mix ›".
- Tap that row → you land on **My mix**.
- Fail: the retune card appears in full here (it belongs on My mix), or the row does nothing.

## 4. My mix — what you hold vs your plan
- Rows: **Semiconductors sleeve, Nasdaq sleeve, Core · Classic 60/40, Commodities, Cash**, each with a "held% /
  target%" chip and a bar with a white target tick. Sleeves add up with core + commodities + cash to ~100% target.
- A chip "… pts to fund" (or "funded") at the top. Buttons **Fund all sleeves** and **Change core** below.
- Tap **Change core** → the core options open under the buttons; tap again (Close) → they fold.
- Fail: a bar wider than the card, a target tick off the bar, percentages that don't match Holdings.

## 5. My mix — your protective rules
- "YOUR PROTECTIVE RULES" with a chip "cost your goal 28 pts".
- **SOXL** (Semiconductors sleeve): profit-protect / trailing stop / stop-loss with their levels; **QQQ** (Nasdaq
  sleeve): trailing stop / stop-loss; **Drawdown guard · whole book 10%**.
- The **Retune your protection** card (Accept · Details) sits inside this section.
- The link **Holdings › Rules** opens the Holdings tab on Rules.
- Fail: take-profits, caps or buy-back rules listed here (they belong on Holdings › Rules), or a rule for USO/BND/GOOGL.

## 6. Explore
- "STRATEGY CATALOG" with the group pills (Beat the market / Grow / …) and the strategy cards; your sleeves are
  ticked.
- At the bottom, folded: **What would it take? · size your sleeves to beat SPY**. Tap to open → the Beat-by /
  drawdown fields and Solve work as before.
- Fail: "What would it take?" open by default or at the top.

## 7. Accept from the Plan tab
- On Goal, press **Details** on the target card → it expands. (Don't press Accept unless you mean it.)
- If you do accept something here: the Plan tab refreshes in place (it used to jump back to Today's data).

## Looks bad but isn't
- **"Goal settings" (the old form) is still on the Goal sub-tab**, with "Years invested", Kelly and trust dials, and
  one "Save my plan" button. Step (b) replaces target/deadline/deposit with the Edit-goal sheet; step (c) replaces
  the dials with the risk sheet and removes "Years invested".
- The objective/risk selector that used to sit next to your core is gone from My mix: it duplicated the one in Goal
  settings (the plan's "objective + risk appear twice").
- "Edit rules" is not there yet (step b). Change rules on Holdings › Rules for now.

```
Reply with just:
✅/❌ 1. Sub-tabs
✅/❌ 2. Goal summary
✅/❌ 3. Ways to close the gap
✅/❌ 4. My mix bars
✅/❌ 5. Protective rules
✅/❌ 6. Explore
✅/❌ 7. Accept from Plan
For any ❌ — one line: what you saw.
```
