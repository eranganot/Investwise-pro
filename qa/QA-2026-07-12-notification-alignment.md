# QA — Notification ↔ Today alignment + why/impact recs (2026-07-12)

Test on the Pixel 9, installed PWA. Each check is ✅/❌ in under a minute.
Note: this app's UI is English-only, so no Hebrew/RTL check needed.

## Pre-flight (2 min)
- **CI green** on the pushed commit (lint / test / test-postgres). Don't test before it's green.
- **Railway deploy Active** for `investwise-pro-production`. _(I couldn't verify this from here — confirm it's the newest deploy, Active, in Railway before starting.)_
- **PWA picked up the new code.** The SW cache was bumped `iw-v2 → iw-v3`. Fully close the installed app and reopen it **twice** (first reopen activates the new SW, second serves it). If unsure: browser → DevTools → Application → Service Workers shows `iw-v3`.
  - Fail: still on old shell — Why/Impact lines won't appear; everything below is invalid.

## Checks

1. **Recommendations explain themselves.** Home → "What to do now". Each card shows a **Why:** line (muted, above the action) and a highlighted **Impact:** box (blue left-border, below the action).
   - Fail: any card with only the action line, no Why/Impact.

2. **The app is no longer falsely empty.** If you'd been getting "action" notifications while the app said "Nothing to do right now" — it should now show the actual to-dos (e.g. the monthly-contribution card, a region/currency card).
   - Fail: "Nothing to do" while an action notification is sitting in your tray.

3. **Goal-gap number matches the app's own projection.** The "add ~₪X/month" card's projected "you'd reach about ₪Y" equals the home **"Where this could end up → Expected (most likely)"** figure.
   - Fail: two different projected ₪ numbers for the same portfolio.

4. **Ignore stays aligned.** Tap **Ignore** on a card → it disappears and stays gone on refresh; you should not get a fresh push for that same item right after.
   - Fail: card reappears immediately on refresh, or you get re-notified for something you just ignored.

5. **Notifications are honestly categorised.** When a **price-move** or the **weekly digest** arrives, it reads as FYI ("📈 FYI — TICK +x%" / digest) and is quiet/non-nagging. An **action** notification (a real recommendation) opens the app to a matching card in "What to do now".
   - Fail: a price move worded as an action, or an "action" push with no matching card.
   - (To force one now: Settings → toggle notifications / the test ping.)

6. **Every flagged risk has an action.** If "Your biggest risk" shows a region / currency / liquidity risk, there is now a matching actionable card in "What to do now".
   - Fail: a risk shown up top with no corresponding to-do card.

## Looks bad but isn't
- **Old "Ignored forever" cards reappear.** By design — the permanent local hide (`iw_snoozed`) is abandoned for a 7-day store (`iw_snoozed_v2`) that matches the server. Anything the server still considers active will resurface, then respect the 7-day window. Not a bug.
- **Price-move / digest notifications are silent now.** Intentional — they're FYI, so they don't buzz or demand interaction.
- **"Nothing to do" is still correct** when you genuinely have no active action notification — the fix is about them *agreeing*, not about always having a to-do.
