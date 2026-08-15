# Fix plan — funded-buy cards must not claim a weight change they don't produce

**Repo:** Investwise-pro · **Branch:** `fix/card-claim-invariant` off `main`
**Written:** 2026-08-15

---

## 0. The one-paragraph version

Three Today cards told you to buy Equities to move Equities from 97% toward an 80%
target, funded by selling Equities. The claim is arithmetically impossible. The root
cause is not a formula — it is that **the prose and the arithmetic are assembled in
different places**, so nothing forces them to agree. This plan makes the honest card
the *only constructible* card: one shared builder computes the before/after weights
and emits the sentence, `buying_class` becomes a required argument so a call site
cannot silently omit the disclaimer, and a cross-cutting test asserts every card's
claim by **simulating its `apply` spec and re-measuring the portfolio** rather than
by reading its strings.

---

## 1. Why it happened, precisely

The same defect was found and fixed in **October's C3 work** — in the *sleeve funding*
path. `funding_service.describe_funding()` grew an optional `buying_class` parameter
and an honesty clause:

```python
# funding_service.py:206-215
sold = {s.get("asset_class") for s in fund.get("sells", []) if s.get("asset_class")}
if buying_class and sold and sold == {buying_class}:
    line += (f" This does not change your {buying_class} weight — the proceeds buy "
             f"{buying_class} again. It swaps which {buying_class.lower()} you hold.")
```

`strategy_service.py` passes it (lines 582, 589, 749, via `_buying_class(legs)`), and
`test_c3_funding.py:353` guards it. **`recommendations.py` does not pass it at any of
its three call sites** (524, 769, 1936). The parameter defaults to `None`, the clause
silently never fires, and the Today path went on shipping the exact sentence C3 was
written to prevent.

Four defects follow from the same structural gap:

| # | Defect | Location |
|---|---|---|
| D1 | Buy gate asks *"does the plan hold this class"* (`target_w <= 0`), not *"does the plan want more of it"* | `recommendations.py:501` |
| D2 | **Ticker** weight sized against the **class** target — a 0%-weight name gets sized to the 40% cap | `recommendations.py:503` |
| D3 | Honesty clause is dead code on this path | `recommendations.py:524`, `:769`, `:1936` |
| D4 | `impact` string is trade size (`buyable/nav`), never a gap delta | `recommendations.py:525` |

Two more surfaced while tracing:

| # | Defect | Location |
|---|---|---|
| D5 | **Cross-sleeve raiding.** `exclude={tk}` only. The cards sell SOXL and TQQQ (both catalog sleeve strategies) to buy QUAL and AVUV (both Factor Stack members). C3 forbids this — `strategy_service.py:556` uses `exclude = set(targets) \| await sv.sleeve_tickers(...)` | `recommendations.py:506`, `:511` |
| D6 | **Structural shortfall.** `plan_funding` fills `remaining` with *gross* proceeds, stops below `MIN_TRADE_ILS`, then subtracts tax from `funded`. So `shortfall ≈ leftover + tax` on nearly every card. Observed: 4,708 − 4,290 = 418 + ₪11 tax = **₪430** | `funding_service.py:159-183` |

And the correct implementation already exists twelve hundred lines away, in the
idle-cash redeploy path:

```python
# recommendations.py:1681 — this is right
gap_ils = max(0.0, (float(tw) - float(mix.get(cls, 0.0)))) * nav
```

---

## 2. Design principle

> **A card may not claim a portfolio change it does not produce.**
> The claim is *computed from the simulated post-trade state*, never composed by hand.

Three enforcement layers, in order of how hard they are to bypass:

1. **Signature** — `buying_class` becomes a required keyword arg. Omitting it is a
   `TypeError` at call time, not a missing sentence in production prose.
2. **Single builder** — no call site assembles funded-buy prose anymore. One function
   owns sizing, funding, direction classification, and narration together.
3. **Behavioural invariant test** — walks *every* card `build_recommendations` emits,
   simulates its `apply` spec, and re-measures. Fixture-driven, so a card type added
   next month is covered the day it's written.

---

## 3. Execution plan

Six phases. Each ends green (`pytest -q` + `ruff check .`) and is independently
committable. Phases 1–2 are the correctness fix; 3–4 are the structural guarantee;
5–6 are the regression wall and the paper trail.

### Phase 1 — Make the shortfall real, not an artifact `funding_service.py`

The phantom ₪430 has to go first, because every downstream size depends on it and it
currently forces a double `plan_funding` call at each site.

- Rework the sell loop in `plan_funding` to target **net** proceeds: accumulate
  `tax_reserve` as sells are appended and loop on `remaining_net = amount - from_cash
  - (gross_so_far - tax_reserve)`.
- `funded_ils` then genuinely equals `amount_ils` whenever it can be met, and
  `shortfall_ils` becomes a real signal ("this book cannot pay for it") rather than
  rounding noise.
- Delete the double-call pattern at all four sites once this holds.

**Tests:** extend `tests/test_funding_service.py`
- `test_a_fully_fundable_buy_reports_no_shortfall` — cash-rich book, `shortfall_ils == 0`
- `test_the_shortfall_is_the_money_actually_missing_not_the_tax` — sale-funded book,
  assert `shortfall_ils < MIN_TRADE_ILS` when the book *can* pay
- `test_an_unfundable_buy_still_reports_a_real_shortfall` — thin book, non-zero

---

### Phase 2 — Split sizing into two named, non-interchangeable functions `funding_service.py`

`size_purchase(nav, current_weight, target_weight, cap)` is the D2 trap: both
arguments are floats named "weight", so passing a ticker weight where a class weight
belongs type-checks fine and reads fine. Replace it with two functions that cannot be
confused:

```python
def class_gap_ils(nav, mix, cls, class_target) -> float:
    """How much the PLAN wants added to an asset class. Zero if at/over target."""
    return max(0.0, float(class_target) - float(mix.get(cls, 0.0))) * nav

def name_room_ils(nav, ticker_weight, cap) -> float:
    """How much of ONE NAME can be added before the concentration cap. Never a
    statement about the plan's intent."""
    return max(0.0, float(cap) - float(ticker_weight or 0.0)) * nav
```

Then `size_purchase` is **deleted**, and its four call sites (`recommendations.py:503,
1706, 1742, 1766`) are rewritten. Note 1706/1742/1766 already pass `cap` as the target
— they *want* `name_room_ils` and get it verbatim. Only line 503 changes meaning.

**Tests:** `tests/test_funding_service.py`
- `test_a_class_at_or_over_target_has_no_gap` — `mix=0.97, target=0.80` → `0.0`
- `test_name_room_is_capped_headroom_not_a_plan_gap`
- `test_size_purchase_is_gone` — `assert not hasattr(funding_service, "size_purchase")`
  (cheap, but it stops the old name being reintroduced by muscle memory)

---

### Phase 3 — One builder for every funded buy `funding_service.py` (new)

```python
@dataclass(frozen=True)
class FundedBuy:
    ticker: str
    buying_class: str
    amount_ils: float
    fund: dict
    class_before: float          # measured
    class_after: float           # simulated post-trade
    class_target: float
    kind: str                    # "toward_target" | "within_class_swap" | "refused"
    summary: str                 # funding sentence, clause already applied
    impact: str                  # derived from (class_after - class_before)
    apply_spec: dict

def propose_funded_buy(*, rows, snap, plan, objective, cap, ticker, buying_class,
                       requested_ils, cash_ils, exclude,
                       allow_within_class_swap: bool = False) -> FundedBuy | None:
```

Behaviour:

- Sizes as `min(class_gap_ils(...), name_room_ils(...), funded)` for a toward-target
  buy. **Fixes D1 and D2 together**, because a class at 97% against an 80% target
  returns `class_gap == 0` and the function returns `None`.
- Computes `class_after` by applying the buy *and every sell* to a copy of the mix.
  **This is the number `impact` is rendered from — D4 dies here.** A swap renders
  "Does not move your Equities weight (97%). Swaps 3x leveraged exposure for factor
  exposure." — never "~38% closer to target".
- Classifies `kind`. `within_class_swap` (every sell is in `buying_class`) is only
  returned when the caller passes `allow_within_class_swap=True`; otherwise `None`.
- Calls `describe_funding(fund, buying_class)` — **positionally required**, see Phase 4.

### Phase 4 — Close the boundary `funding_service.py`

```python
def describe_funding(fund: dict, buying_class: str | None) -> str:
```

`buying_class` loses its default. `None` stays a legal *value* (mixed-class buys make
no claim, per `_buying_class`'s existing contract) but omitting the argument becomes a
`TypeError`. Update `strategy_service.py:582, 589, 749` — already passing it, so this
is a no-op there and confirms the signature change is safe.

Move `_buying_class()` from `strategy_service.py:507` into `funding_service` so both
consumers share one definition instead of Today growing a second, subtly different one.

**Tests:** `tests/test_funding_service.py`
- `test_describe_funding_requires_a_buying_class` — `pytest.raises(TypeError)`
- Port the three C3 narration tests (`test_c3_funding.py:353, 371, 392`) down to the
  `funding_service` level so they guard the *shared* function, not one consumer's route

---

### Phase 5 — Rewrite the three consumers `recommendations.py`

**5a — war-room buy path (lines 495–540).** Collapse to a `propose_funded_buy` call
with `allow_within_class_swap=True` (a trend signal inside an overweight class is a
legitimate *swap* — see the open decision in §5), and:

```python
exclude = {tk} | await sleeve_service.sleeve_tickers(session, user)   # fixes D5
```

**5b — geo-diversification path (line 762).** Same builder. VXUS is Equities funded by
selling Equities, so the honesty clause *should* fire here and currently doesn't.

**5c — commodities path (line 1928).** Same builder. Buying Commodities funded by
selling Equities, so the clause correctly stays silent — this is the control case that
proves the clause isn't over-firing.

**5d — cross-card netting.** `build_recommendations` currently calls `plan_funding`
once per card against the same untouched snapshot, so three cards each "spend" the
same 13 TQQQ shares. Add a single reservation pass after candidates are built:

```python
def allocate_funding(candidates, rows, snap, plan, objective, cap, cash_ils):
    """One inventory of sellable shares and spendable cash, drawn down in severity
    order. A candidate that cannot be funded from the remainder is dropped, not
    shown short."""
```

This is the same "one budget, not N" property `test_c3_funding.py:79
(test_the_cash_above_the_floor_is_only_spent_once)` already enforces for sleeves —
reused rather than reinvented.

---

### Phase 6 — The regression wall `tests/test_card_claims.py` (new)

The four defects shipped because every existing test asserts on **strings**. This file
asserts on **portfolio state**, and is the reason this class of bug does not return.

```python
BOOKS = {
    "equities_overweight":  # the live book: 97% equities, 80% Grow target,
                            # TQQQ + SOXL + SCHD, cash at the floor
    "equities_underweight": # 55% equities, 80% target, room to buy
    "cash_heavy":           # 30% idle cash
    "single_name_breach":   # one position over the 40% cap
    "thin_book":            # NAV small enough that every leg is under MIN_TRADE_ILS
}
```

Six invariants, each running against **every card** `build_recommendations` returns for
**every** book — so new card types are covered automatically:

1. **`test_no_card_claims_a_move_it_does_not_make`** — for any card whose text matches
   `closer to target|toward your .* target|lifts .* to`, simulate `apply` and assert
   the named class weight moved ≥ 0.5pp in the claimed direction. *Catches D1, D2, D4.*
2. **`test_a_within_class_purchase_says_so`** — if every sell's `asset_class` equals
   the buy's, assert `"does not change your"` is present. *Catches D3.*
3. **`test_no_card_funds_itself_by_selling_a_sleeve`** — assert no `sells[].ticker` is
   in `sleeve_tickers()`. *Catches D5.*
4. **`test_no_card_is_presented_short`** — assert `shortfall_ils < MIN_TRADE_ILS` on
   every rendered card. *Catches D6.*
5. **`test_the_card_set_can_be_accepted_in_full`** — apply *all* cards in sequence
   against one book; assert no negative share counts and cash never breaches the floor.
   *Catches the netting bug.*
6. **`test_every_buy_card_moves_the_book_toward_the_plan_or_says_it_does_not`** — the
   general form. Simulate, measure total absolute drift from `OBJ_TARGET` before and
   after; assert drift decreased **or** the card is labelled a swap.

Plus one narrow reproduction, kept for the story:

- `test_the_august_three_card_contradiction_cannot_recur` — seed the exact book, assert
  no card both buys Equities and claims Equities moves toward an 80% target.

**Extend** `tests/test_live_contradictions.py` with a pointer comment to the new file —
that module's docstring ("*an agent that is individually correct, giving advice that is
wrong once you know what the rest of the app is doing*") is exactly this bug's genus,
and the two files should reference each other.

---

## 4. How it does not happen again

| Layer | Mechanism | What it stops |
|---|---|---|
| Signature | `buying_class` required | A fourth consumer silently omitting the disclaimer |
| Structure | `propose_funded_buy` is the only way to build a funded buy | Prose and arithmetic drifting apart |
| Naming | `class_gap_ils` / `name_room_ils` — no interchangeable float pair | Ticker weight passed where class weight belongs (D2) |
| Behaviour | Claims verified by simulating `apply`, not by regex | Every string-level assertion's blind spot |
| Coverage | Fixture-driven over *all* cards × 5 adversarial books | New card types shipping uncovered |
| Docs | `CLAUDE.md` domain rule + `STATUS.md` entry + project memory | The next session re-deriving all of this from scratch |

Proposed `CLAUDE.md` addition, under **Domain / behavior rules**:

> - A recommendation card **may not claim a portfolio change it does not produce**. Any
>   "moves X toward target" claim must be computed from the simulated post-trade mix,
>   never from trade size. Selling a class to buy the same class is a *swap* and must be
>   labelled one. Verified in `tests/test_card_claims.py` by applying the card, not by
>   reading its text.

---

## 5. Decisions — settled 2026-08-15

**Overweight-class signals → show as an honest swap.** When an approved signal fires on
a name whose class is already overweight, the card renders as a swap rather than being
suppressed: *"Swap ₪4,708 of 3x leveraged exposure (TQQQ, SOXL) for SCHD. Does not
change your 97% Equities weight."* Phase 5a passes `allow_within_class_swap=True`, and
`propose_funded_buy` forces `kind="within_class_swap"` narration in that branch — the
card cannot opt out of the label once the classifier has set it.

Rationale: the underlying trade has real merit (shedding 3x leveraged exposure de-risks
a 97%-equity book), so suppressing it loses a good signal. The failure was never the
trade — it was the label.

**Delivery → two PRs.**

- **PR 1 — shared layer.** Phases 1–4: `funding_service.py` (net-proceeds funding, split
  sizing, `propose_funded_buy`, required `buying_class`), `_buying_class` moved down from
  `strategy_service`, the three no-op `strategy_service` call-site updates, and the
  `test_funding_service.py` additions. Reviewable on its own; `recommendations.py` is
  untouched, so Today's behaviour is unchanged and the suite stays green.
- **PR 2 — consumers + wall.** Phases 5–6: rewrite the three `recommendations.py` call
  sites onto the builder, add sleeve exclusion and cross-card netting, add
  `tests/test_card_claims.py`, update `CLAUDE.md` and `STATUS.md`.

---

## 6. Sequencing and effort

| Phase | Scope | Risk |
|---|---|---|
| 1 — net-proceeds funding | `funding_service` + 3 tests | Low, well-isolated |
| 2 — split sizing | `funding_service` + 4 call sites | Low, mechanical |
| 3 — `propose_funded_buy` | new, ~120 lines | Medium — the design core |
| 4 — required `buying_class` | signature + 3 no-op updates | Low |
| 5 — rewrite 3 consumers + netting | `recommendations.py` | Medium — largest diff |
| 6 — `test_card_claims.py` | new, ~250 lines | Low, additive |

Roughly a day and a half of focused work. Phases 1–4 could ship as one PR and 5–6 as a
second if you'd rather review the shared-layer change before the consumer rewrite.

**Verification before merge:** full suite green locally (≈249 tests + the ~15 new),
`ruff check .` clean, CI green including the Postgres job, then seed the live book
locally and eyeball the three cards render as swaps with correct arithmetic.
