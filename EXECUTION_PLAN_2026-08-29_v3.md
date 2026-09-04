# InvestWise Pro — Execution Plan V3 (proposed, awaiting approval)

_Written 2026-08-29 against `STATUS-V2.md` at commit `a06a182`. This plan
**merges STATUS-V2's open list with the six new items Eran raised on 2026-08-29**
into one ordered list. Nothing here has been started._

Read `STATUS-V2.md` §0 house rules first. They govern every PR below.

---

## 0. What was decided before writing this

Four scope questions were put to Eran and answered:

| question | answer |
|---|---|
| "STILL BROKEN ON PURPOSE" bucket | Fix the three items it names; **keep the block** as a house convention. It prints "nothing knowingly left broken" when a PR leaves nothing behind. |
| TASE | **Real Tel-Aviv listings**, done safely — the agorot trap fixed first. |
| "What's moving" | **All three**: a live events feed, events that become actionable cards, and a general audit of why the feed is thin. |
| Agent/strategy alignment | Audit against **the live book's active strategies**, read from the deployed app. |

---

## 1. Where each of the six new items actually lives

Findings from reading the code, so the plan is grounded rather than guessed.

### New item 1 — map every agent, with goals and purpose

There is no single agent roster, and the two that exist disagree.

- `app/agents/` holds **four** concrete classes: `AllocationAgent`, `FeeAgent`,
  `ResearchAgent`, `OpportunityAgent` (the screener), plus `adversary.py`, which
  is a module of deterministic invariant checks rather than a class.
- `war_room.py:18` declares **seven**: `["Research", "Alpha", "Risk", "Tax",
  "Decision", "Adversary", "UX"]`.

Alpha, Risk, Tax, Decision and UX are not agent classes at all — they are the
Lag, Risk, Tax and Decision **engines** plus the feed renderer, given agent names
in the war-room trace. So the app narrates a seven-agent pipeline it does not
have, and the two agents that do exist as classes and hold real veto power
(`AllocationAgent.review_buy` raises `VetoException`; `adversary` BLOCK findings)
are not in the roster the UI shows.

**This is not only a documentation gap.** A user reading the war room cannot tell
which named agent is a real actor with veto rights and which is a label on an
engine call.

### New item 2 — are the agents aligned with my strategies?

Two strategy catalogs exist and nothing reconciles them:

- `app/services/strategies.py` — the Plan page presets, keyed by goal
  (Grow / Balanced / Income / Preserve), each with a `target_allocation` and a
  model `basket`.
- `app/services/strategy_catalog.py` — `GOAL = "Beat the Market"`, the
  backtested sleeve strategies with discipline and signal rules.

Meanwhile `OpportunityAgent.screen_equities` (`screener_agent.py:77`) ranks
`universe.equity_universe()` — **the whole universe, with no strategy input at
all**. The screener does not know which strategy is active, what the sleeve
baskets hold, or what the accepted target is. So "aligned with the strategies I
picked" is currently false by construction for at least the idea-generation
agent, and unverified for the rest.

### New item 3 — no TASE recommendation cards, ever

`universe.TASE_STOCKS` (`universe.py:74-82`) is headed *"Tel-Aviv (TASE) names
(Yahoo .TA suffix)"* and contains seven tickers, **all of them US-listed**:
TEVA (NYSE), NICE, CHKP, ESLT, WIX, MNDY (NASDAQ), GLOB (NYSE). There is not one
`.TA` symbol in the candidate universe. No card can name a Tel-Aviv listing
because no Tel-Aviv listing is a candidate. The comment is wrong about its own
list.

**And a money bug sits in front of the fix.** Yahoo quotes most `.TA` symbols in
**agorot** and reports the currency as `ILA` (1/100 ILS). `pricing_service.py:181`
stamps `price_currency` straight from `q.currency`, so the position carries
`"ILA"`. `fx.fx_rate` then asks the FX provider for ILA→ILS, Frankfurter does not
know ILA, the call raises, and `fx.py:37-38` **fails safe to 1.0**. A Tel-Aviv
holding is therefore valued **100× too high**, silently, with the fail-safe
hiding the failure.

This is reachable **today, without any TASE work**: the holdings form already
offers Tel Aviv (`index.html:267`) and London. The same trap applies to LSE,
where Yahoo returns `GBp` (pence).

### New item 4 — no X axis on the Explore performance chart

`index.html:2740`:

```js
drawChart("ch_perf","line",{labels:r.dates,datasets:ds},
  {..., scales:{x:{ticks:{display:false},grid:{display:false}}}});
```

The dates are already passed in as `labels`. The ticks are explicitly switched
off. Smallest item in this plan.

### New item 5 — too few cards from "What's moving"

The Explore panel is fed by `/api/v1/research/events` →
`ResearchAgent().scan()` → `guarded_events()` → `economic_provider()`, which
`registry.py:46-47` hardcodes to `BuiltinEconomicDataProvider`. Its
`get_events()` (`builtin.py:100-110`) returns **four fixed synthetic events**,
one of which is a made-up Israeli surtax note. `live.py` has **no `get_events`
method at all** — there is no live path to switch to.

Separately, those events are display-only: `ResearchEvent` reaches
`market_impact.annotate` and `war_room`, and **never reaches
`build_recommendations`**. So a real market event, even if the feed were live,
could not produce a card you can act on.

### New item 6 — "there are still things broken on purpose"

Not an app banner. It is the ship script's own honesty block —
`scripts/deploy/ship-pr16-measured-assumptions.ps1:166`, a convention repeated
across 15 deploy and smoke scripts. Its three bullets in the current deploy are
exactly **STATUS-V2 §2a, §2b and §2c**. "Fix everything" therefore means closing
those three, after which the block prints that nothing was knowingly left behind.

---

## 2. The consolidated list — STATUS-V2 plus the six new items

Ordered by the only ranking that matters here: **can it silently change what the
book is worth**, then can it make the app lie, then can it make the app useful.

| # | tier | item | source |
|---|---|---|---|
| 1 | **T1 money** | `rebalance_to_objective` leaks ₪739.21 | V2 §1 |
| 2 | **T1 money** | Non-USD quotes valued 100× (`ILA` agorot, `GBp` pence) fail safe to rate 1.0 | new #3, prerequisite |
| 3 | **T2 lies** | Two cards contradict each other about one book | V2 §2a / new #6 |
| 4 | **T2 lies** | Account history reads an intake holding as +37% performance | V2 §2b / new #6 |
| 5 | **T2 lies** | "Find the best split" searches to 97%, ignoring the 75% you accepted | V2 §2c / new #6 |
| 6 | **T3 truth** | The agent roster is fiction: 7 named, 4 real, veto-holders unlisted | new #1 |
| 7 | **T3 truth** | No agent reads the active strategy; two unreconciled catalogs | new #2 |
| 8 | **T4 use** | Zero Tel-Aviv listings in the universe | new #3 |
| 9 | **T4 use** | "What's moving" is four hardcoded strings, and never becomes a card | new #5 |
| 10 | **T4 use** | Explore performance chart has no date axis | new #4 |
| 11 | T5 | Rotate `AGENT_API_KEY` — 34 scripts, 160+ commits, pasted in transcripts | V2 §3 |
| 12 | T5 | Walk-forward geometry spread (5y/1y rolling + expanding) | V2 §3 |
| 13 | T5 | Ceiling re-seed — **a decision for Eran, not a bug** | V2 §3 |
| 14 | T5 | PR 16 follow-up: health-score seam needs a migration | V2 §3 |
| 15 | T5 | `alembic upgrade head` from scratch fails (`0018` adds 7 columns unguarded) | V2 §3 |
| 16 | T5 | Equity universe liveness gate (COW precedent) | V2 §3 |
| 17 | T5 | Rule notes freeze their creation-day price (`rules_service.py:844`) | V2 §3 |
| 18 | T5 | Commodity screen ignores the core's basket | V2 §3 |
| 19 | T5 | `_mean` drops `None` — still live for equities (`screener_engine.py:93-96`) | V2 §3 |
| 20 | T5 | Device QA: notifications on the Pixel 9, then the P0 safety pass | V2 §3 |
| 21 | T5 | Blocking provider I/O — `/recommendations` ~5s warm, blocks the worker | V2 §3 |
| 22 | T5 | Pin VAPID keys in Railway | V2 §3 |
| 23 | T5 | Broker integration — confirm it is still wanted | V2 §3 |

Items 11-23 are unchanged from STATUS-V2 §3 and are **not** scheduled below.
They are listed so this file is the one complete list.

---

## 3. Tier 1 — start here

### PR 17 — the NAV leak

**Goal:** `rebalance_to_objective` conserves NAV, deterministically, or we know
exactly why it cannot.

Per STATUS-V2 §1 the intermittency is almost certainly about *which cards
`build_recommendations` emits* (process-global circuit-breaker state), not about
the arithmetic. So:

1. Build the leaking card **directly from a fixture** — the pinned prices are
   recorded: `MSFT 483.24, SCHD 35.11, TQQQ 71.17, V 371.04` — and assert
   conservation on the handler, bypassing `build_recommendations` entirely.
2. If it reproduces, the intermittency question is closed and this is an ordinary
   bug in `_rebalance_to` (`recommendations.py:2691`). Fix it there.
3. If it does **not** reproduce from a fixture, the defect is in card
   *selection*, not the handler, and the next step is to log the emitted card set
   alongside breaker state on every Postgres run.

**Do not** weaken `test_accept_conserves_nav.py` to make it green.

**Deliverables:** ship script, `smoke-nav-conservation.ps1`, check-with-your-own-eyes block.

### PR 18 — the currency fail-safe that hides a 100× error

**Goal:** no holding is ever valued with a fabricated FX rate.

1. **Normalise minor-unit currencies at the source.** `pricing_service.py:181`
   maps `ILA → ILS ÷ 100` and `GBp/GBX → GBP ÷ 100` before stamping
   `price_currency`, so every downstream consumer sees a major-unit price. This
   is one boundary, not thirteen call sites — the lesson recorded in
   `card_claim_invariant_decision.md`.
2. **Stop `fx_rate` from inventing 1.0.** The fallback (`fx.py:37-38`) turns a
   provider failure into a silent 100× valuation error. It must return an
   explicit *unknown* that the caller surfaces — "this holding could not be
   valued in ILS, here is why" — rather than a number. This is the
   **ground-or-abstain** rule from the `investing-discipline` skill, currently
   violated in the one place where violating it moves NAV.
3. Test with a real Tel-Aviv symbol under `MARKET_DATA_PROVIDER=yahoo`, not the
   builtin synthetic provider.

**This blocks item 8 (TASE candidates) and should ship before it.**

**Risk to name up front:** step 2 changes what happens when FX is down. Today the
app quietly prints a wrong NAV; after this it will say it cannot value part of
the book. That is correct but it is a visible behaviour change, and it will show
up on the Today view the first time Frankfurter has a bad minute.

---

## 4. Tier 2 — empty the "broken on purpose" bucket

### PR 19 — cards may not contradict each other (V2 §2a)

`contradictions.py` rule 2 (`:101`) has two holes and both apply to the live
pair: `:111` matches the sell side on `title.lower().startswith("sell")`, and the
merged card is titled *"Rebalance toward your target mix"*; `:109` does
`if not cls: continue`, and the SOXL sleeve top-up is ticker-scoped with no
`asset_class`.

**Fix:** read `apply.legs[]` and classify **each leg** through
`allocation_mix.classify`. Stop matching on title text — `STATUS.md` already
records that failure mode once ("every test in the area asserted on strings").

Smallest of the three. Card-claim invariant, so not cosmetic.

### PR 20 — the history chart stops calling an intake a return (V2 §2b)

`nav_history.time_weighted` takes flows only from the contributions ledger
(`nav_history.py:304-305` via `net_flow_between` at `:45`), so a holding added
through intake moves NAV without moving `invested_ils` and books as performance.
The chart shows +35% on 22 Aug and the caption at `index.html:820` actively
claims *"No deposits or withdrawals in this window"* — both halves false.

**The signal already exists and nothing reads it:** `NavSnapshot` stores a
`positions` count on every row. When the position count changes between two
snapshots and `invested_ils` does not, the app must not call the step
performance. Fix the series **and** the caption; a corrected line under a caption
that still claims no flows is half a fix.

### PR 21 — the split search respects the target you accepted (V2 §2c)

`target_solver.py:102` / `:921` set `max_total = 100 − cash_floor × 100` = 97%,
and the accepted verdict is never an input. Feed the accepted sleeve total in as
`max_total`. The two cards do ask different questions — PR 14b made that visible
— but **once a target is accepted it is a decision, and the search must be
constrained by it.**

After PR 21 the ship script's bucket is empty for the first time.

---

## 5. Tier 3 — make the app tell the truth about itself

### PR 22 — the agent map (new #1)

Two deliverables, because a document alone would go stale by the next PR:

1. **`docs/AGENTS.md`** — one entry per agent: name, where it lives, what it
   consumes, what it emits, **whether it can veto**, what it is *for*, and what
   it explicitly does not do. Written from the code, with `file:line` anchors.
2. **A test that fails when the map drifts** — `war_room.AGENTS` and the roster
   in the doc must agree with what actually runs, so the seven-versus-four gap
   cannot silently reappear.

Then decide, and record: either the war room stops naming engines as agents, or
the engines are promoted to real agents. Recommendation: **rename in the trace**
(`Alpha · Lag engine`), because promoting five engines is a large change bought
for a labelling problem.

### PR 23 — are the agents actually running my strategy? (new #2)

**Step 1 is a measurement, not a change.** Read the live book's active
sleeves, core and objective through the read-only API and produce
`docs/STRATEGY_ALIGNMENT.md`: for each agent, does it read the active strategy?
If not, what does it use instead, and can that disagree with the strategy?

Known before starting: `OpportunityAgent.screen_equities` ranks the whole
universe with no strategy input, and there are two unreconciled catalogs
(`strategies.py` vs `strategy_catalog.py`).

**Step 2, scoped by what step 1 finds,** is the code change — most likely
passing the active strategy's basket and target allocation into the screener so
ideas are ranked *for the strategy you picked* rather than in the abstract.

I want to show you step 1's findings before writing step 2. The alternative —
guessing the fix now — is how a plan produces a change nobody asked for.

---

## 6. Tier 4 — the remaining new items

### PR 24 — real Tel-Aviv candidates (new #3), **after PR 18**

1. Add genuine `.TA` candidates — TA-35 constituents and Tel-Aviv-listed index
   ETFs — as a properly-labelled group with `market: "TASE"`.
2. Correct the existing group: the seven US-listed names stay, relabelled
   honestly as Israeli companies on US exchanges, and the false `.TA` comment goes.
3. **Liveness gate before shipping.** This is item 16 (the COW precedent) landing
   on a market where it matters more: a Tel-Aviv listing that stops trading is
   less likely to be noticed by eye. No `.TA` name enters the universe without a
   recent-close check.
4. Verify under `MARKET_DATA_PROVIDER=yahoo` with a real rate limit, not the
   builtin provider — otherwise the numbers are fiction that looks real.

**Honest caveat:** TASE names have shorter and patchier Yahoo history than US
listings, and `_MIN_OBSERVATIONS = 260` (`strategy_backtest.py`) will exclude
some of them from anything backtested. They will appear as screener candidates
before they can appear in a backtested strategy. That is a real limit, not
something to paper over.

### PR 25 — "What's moving" becomes real (new #5)

All three parts you asked for, in dependency order:

1. **A live events feed.** Implement `get_events` on the live provider behind the
   existing resilience tier and make `registry.economic_provider()` respect the
   configured provider instead of hardcoding builtin (`registry.py:46-47`). The
   four synthetic events stay as the offline fallback and must be **labelled as
   synthetic in the UI** whenever they are what is being served — a fabricated
   Israeli surtax notice presented as market intelligence is exactly the kind of
   invented number the domain rules forbid.
2. **Events become cards.** Route `ResearchEvent`s that affect tickers you
   actually hold into `build_recommendations`, subject to the card-claim
   invariant: an event card may only claim what it produces, and where the honest
   output is "this is context, not an action", it must say so rather than
   manufacture a trade.
3. **The suppression audit.** Count, per card family, how many candidates were
   built and how many survived to the feed, and why the rest died — dismiss/done
   TTLs (7 and 14 days), circuit-breaker state, funding drop passes, the
   `_mean`-drops-`None` bug (item 19). Report before changing thresholds. The
   feed may be thin because cards are being *suppressed*, and turning up the
   generator would be treating a symptom.

**Sequencing note:** part 3 is a report and can run first — it is cheap, and it
may change what parts 1 and 2 should do.

### PR 26 — the date axis (new #4)

Turn the x ticks back on at `index.html:2740`, thinned so a 5-year range stays
legible, and match the existing chart styling. Ship it alongside PR 25 rather
than spending a deploy cycle on one line.

---

## 7. Order of work

```
PR 17  NAV leak                        T1  money
PR 18  currency fail-safe (100x)       T1  money, blocks PR 24
PR 19  contradicting cards             T2  bucket
PR 20  history vs intake               T2  bucket
PR 21  split respects accepted target  T2  bucket  <- bucket now empty
PR 22  agent map + drift test          T3
PR 23  strategy alignment (report 1st) T3
PR 24  real TASE candidates            T4  needs PR 18
PR 25  What's moving (audit -> live -> cards) T4
PR 26  date axis                       T4  rides with PR 25
```

Each PR carries the three deliverables from STATUS-V2 §0: a
`scripts/deploy/ship-prN-<name>.ps1`, a `scripts/smoke/smoke-<name>.ps1`, and a
check-with-your-own-eyes block. Every PR runs all three CI jobs including
`test-postgres` before pushing, and verifies `/health` `.commit` matches the
pushed SHA before anything is called done.

`STATUS.md` gets an entry per PR; `STATUS-V2.md` shrinks as items close.

---

## 8. What I need from you

1. **Approve or reorder this list.** In particular: PR 18 is not something you
   asked for, and it delays TASE by one PR. I am proposing it because the app can
   value a Tel-Aviv or London holding 100× too high **today**, and adding TASE
   candidates on top of that would multiply the exposure.
2. **PR 23 stops for your review** after the measurement, before any code change.
3. **Item 13, the ceiling re-seed, is still a decision only you can make** — it
   has been open since PR 14 and is not a bug. Should the drawdown ceiling ever
   auto-refresh, or does a stale value stay until you change it by hand?
4. **Item 20, device QA, is a device action** — enabling notifications on the
   Pixel 9 and confirming `subscriptions: 1`. It is the single failing check in
   `smoke-all` and no PR here can clear it.
5. **Item 23, broker integration** — still wanted, or drop it from the list?

---

Not financial advice.
