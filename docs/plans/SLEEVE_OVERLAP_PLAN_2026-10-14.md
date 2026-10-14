# Sleeve overlap, attribution and overweight — plan (2026-10-14)

Status: **proposed, awaiting owner approval.** Executed wave by wave; stop and report after each.
Base: `origin/main` @ `c5b8e43` (PR 130).

## Owner asks (2026-10-14)

1. Alert when two or more sleeves share holdings, suggesting to change one. Threshold: shared weight >= **40%** of the
   smaller sleeve's target, editable.
2. Fix the double count in the "What you hold vs your plan" panel (approved).
3. Explain "does not improve your book": say which test failed; make its limits settings.
4. Overweight must get a card too: offer **trim** or **raise the size**, with a recommendation from measurement.

## What the investigation found

- `sleeve_occupancy` (`sleeve_service.py:294`) credits each sleeve with the WHOLE holding of every ticker it targets.
  Volatility-Targeted Leverage and Trend-Filtered Leveraged Nasdaq both hold TQQQ, so TQQQ (38.4%) is counted in
  both: per-sleeve rows add to 97.6% while the sleeves together hold 59.2% against a 60% target. The book total uses
  the union, so it is right; every per-sleeve consumer is wrong:
  - the mix panel (screenshot);
  - `core_realign`'s "sleeve overshoot" (`recommendations.py:6655`) — currently ~39 pts of NAV that do not exist, which
    the core is allowed to sell from the sleeves once the core is underfunded (latent harm);
  - plan health's sleeve score (`plan_health.py:171`);
  - measured expected return weights (`measured_assumptions.py:338`) — leveraged sleeves weighted ~84% instead of ~38%.
- An overweight card already exists: per ticker, against the SUMMED sleeve target, band `sleeve_drift_band_pts`
  (default 5), action "trim" only (`recommendations.py:2557`). It is correctly silent on this book.
- The mix panel's chip counts only under-funding (`index.html` `mixPanel`, `toFund`).
- "Does not improve your book" (PR 129, D93, `book_effect.py`) is computed on target sizes, so the double count does
  not touch it. It fails on a fixed rule: return per unit of volatility +0.02 AND worst drop no more than 2 pts deeper.
  A 3x sleeve at 15-25% nearly always fails the drop test, and two TQQQ sleeves each add little beside the other.
  `RATIO_GAIN` / `DD_TOLERANCE_PTS` are code constants (against "every decision is a setting").

## Wave 1 — PR 131: one holding, split between the sleeves that share it (D95)

Fixes the data every other wave reads. Low risk, no new UI concepts.

- `sleeve_occupancy`: a ticker's held value is split between the sleeves that want it **in proportion to each sleeve's
  target weight on that ticker** (the weights `all_sleeve_targets` already sums). If every weight is zero, split
  evenly. Invariant: sum of per-sleeve `held_ils` == `allocated_held_ils` (the union).
  - Rationale: the holdings carry no lot tags, so ownership is not knowable; pro-rata to target is the only split that
    makes "sleeve X is N pts over" agree with the per-ticker drift card (`over_ticker = sum of sleeves' over`).
  - Each sleeve row also gets `shared_with: {ticker: [strategy_id, ...]}` (read by wave 4; harmless now).
- No change to the four consumers' code: they read `held_pct`, which becomes right. The `core_realign` overshoot drops
  from ~39 pts to the real figure.
- Mix panel: the chip shows both sides — "N pts to fund · M pts over" (or "on plan"); over rows already turn amber.
- Tests (`tests/test_pr131_sleeve_attribution.py`):
  - two sleeves on TQQQ: split by weight; per-sleeve sum == union; book on target -> neither shows over;
  - a ticker only one sleeve holds: unchanged from today;
  - `core_realign` overshoot on that fixture == real excess, not the double count;
  - `measured_assumptions` component weights sum to the union.
- Docs: STATUS entry, D95, DOMAIN.md (occupancy definition), FRONTEND.md (chip).
- Smoke `scripts/smoke/smoke-pr131-sleeve-attribution.ps1`: `Assert-Build`; per-sleeve `held_pct` sum ==
  `allocated_held_pct` ± 0.2 pt; core held unchanged from before; each `held_pct` <= the union. Field names verified
  against production before pushing.

## Wave 2 — PR 132: the book-effect verdict says why, and its limits are settings (D96)

- `book_effect.compare_blocking` returns `failed: ["ratio" | "drop"]` plus the margins it missed by.
- `RATIO_GAIN`, `DD_TOLERANCE_PTS` become settings in `thresholds.py`: `book_effect_ratio_gain` (0.02, range 0-0.5)
  and `book_effect_drop_tolerance_pts` (2, range 0-30); shown in the dials sheet; `test_every_decision_is_a_setting`
  extended.
- Line text, for example: "adds return (+0.05 per unit of risk) but makes the worst drop 9.1 pts deeper — your limit is
  2 (change)". A pass names both margins.
- Tests: each failure branch, a setting change flips a verdict, defaults reproduce PR 129's numbers.
- Smoke: `/strategies/{id}/book-effect` carries `failed` and the settings values; verdict unchanged at defaults.

## Wave 3 — PR 133: an overweight sleeve gets "trim" or "raise the size", with a recommendation (D97)

Extends the existing per-ticker over card (one card per position, never one per sleeve — two cards about one TQQQ is
the P1 duplicate bug).

- Fires as today: ticker more than `sleeve_drift_band_pts` over its summed sleeve target.
- Two actions:
  - **Trim** (existing `trim` apply): sell the excess back to target.
  - **Raise the size** (new apply `resize_sleeves_to_held`): raise each sleeve holding the ticker by its pro-rata share
    of the excess (`sleeve_service.add_or_resize`), so the plan matches what is held. Sells nothing. Refused if sleeves
    + commodities + cash would exceed 100% (the existing `validate`).
- **Recommendation, measured, not guessed:** a new `book_effect.resize_effect(sleeve sizes now -> raised)` runs the
  book at target sizes vs at the raised sizes with the same blend engine and the same wave-2 limits.
  - Recommend **Raise** only if the raised book passes (return per risk not worse by more than the limit, worst drop
    no deeper than the tolerance) and stays within the plan's drawdown ceiling if one is set.
  - Otherwise recommend **Trim**. Not measurable -> recommend Trim and say it could not be measured.
  - Measured on demand (`GET /api/v1/sleeves/resize-effect?ticker=`), filled into the card after Today renders, like
    PR 129's book-effect line — Today never waits on a backtest.
- Card claims (`tests/test_card_claims.py`): apply each action and re-measure — Trim leaves the ticker at its summed
  target; Raise leaves `held_pct == target_pct` for each sleeve, with nothing sold.
- Smoke: card shape on a fixture-free read (no over on the live book is a SKIP, not a FAIL); the endpoint answers.

## Wave 4 — PR 134: two sleeves that hold the same thing (D98)

- Overlap of sleeves A and B = the share of the **smaller** sleeve's live target weight in tickers the other also
  targets. Alert when >= `sleeve_overlap_alert_pct` (new setting, default **40**).
- One card per overlapping pair (or group): "Volatility-Targeted Leverage and Trend-Filtered Leveraged Nasdaq both hold
  TQQQ — 100% of the smaller one. Together they are one 45% bet, not two."
- Suggests which to replace, measured: the sleeve whose removal costs the book least by the wave-2 book-effect rule
  (each run with the other kept). Not measurable -> no pick, both named.
- No one-tap apply (changing a sleeve is a Plan-tab decision); links to the Plan tab. Shown while the overlap holds;
  Ignore mutes it for `ignore_mute_days`.
- Tests: TQQQ+TQQQ trips at 40; a 30% shared leg does not; a setting change flips it; a sleeve in its off-leg (QQQ) is
  compared on its live target.
- Smoke: on the live book the card exists and names both sleeves.

## Every wave

Local SQLite + local Postgres + ruff on `app` and `tests` -> PR branch, CI green -> merge -> smoke with `-Sha` ->
STATUS (with the result), BACKLOG, DECISIONS, doc pages. Stop and report.

## Open questions for the owner

None blocking. Assumptions to confirm or correct:
- A1. Shared holdings split in proportion to target weight (wave 1).
- A2. "Raise the size" raises every sleeve sharing the ticker pro-rata, not just the largest (wave 3).
- A3. The overlap uses live targets (what the sleeves hold now), so two sleeves can stop overlapping when one steps
  back to its base (wave 4).

Not financial advice.
