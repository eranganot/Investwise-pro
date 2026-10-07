# Verifying the MSFT flap fix

The awkward part: **success is the absence of something**, and the thing being
absent is on a 12-hour clock. So there are two checks — one you can do in a
minute, and one that is just "did it stay quiet".

## Now (one minute)

1. Open the app → **Rules**. MSFT still shows `Max weight 20% -> 20.00` and
   `20.2% of book, cap 20% - over by 0.2 pts`. **That is correct and should
   stay.** The rule is real, the breach is real; what was wrong was being
   *interrupted* about a ₪43 trim.
2. Open **Today**. There should be **no MSFT card with nothing to press**. If a
   card is there, it must carry an actual Accept button with shares on it.
3. Anything with a genuine trade behind it — a stop, a signal flip, a cap
   breached by enough to clear ₪250 — must still be there. The fix suppresses
   noise, and the way to catch it over-reaching is that the real cards vanish
   too.

## Over the next day

4. **No repeat MSFT push.** That is the whole fix. Previously: every few hours.
   Now: never, while the breach stays sub-minimum.
5. The 07:00 digest may still *mention* MSFT. That is intended — the digest is a
   summary you chose to receive, not an interruption.

## If it pushes again anyway

Two possibilities, and they need different fixes, so tell me which:

- **The push says MSFT and has no trade** → the actionable gate did not hold.
- **The push says MSFT and DOES have a trade** → the weight genuinely moved past
  ₪250-worth of breach, the rule is doing its job, and you should trim.

Either way: what time it arrived, and how many times.

## What the fix does not cover

The 20% cap is armed at the sleeve size and MSFT is simply near it. It will keep
sitting there, technically over, until the position or the book moves. If you'd
rather the cap not be a permanent near-miss, the options are to raise it, to trim
MSFT properly, or to let the sleeve grow into it — a product decision, not a bug.
