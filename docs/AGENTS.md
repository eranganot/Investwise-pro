# The agents

Every claim here carries a `file:line` you can open. Nothing in this document is
inferred from a name: where a sentence says an agent does something, the line
cited is the line that does it. Where the code and the label disagree, the
disagreement is written down rather than smoothed over.

Read `WHAT THIS MAP FOUND` at the bottom first if you only read one section.

---

## What counts as an agent

The word is used for two different things in this codebase, and conflating them
is how you end up with a screen that lists seven specialists when four exist.

**An agent** is a class under `app/agents/` that decides something: it takes
evidence, applies judgement, and emits either a recommendation or a veto. There
are **five**.

**An engine** is a class under `app/engines/` that computes something: given
inputs it returns a number or a report, the same one every time, with no
discretion. There are **fourteen** (`grep -c '^class' app/engines/*.py` counts
eighteen; four of those are typing protocols and a result model). Engines are
agents' tools, not agents.

`app/services/war_room.py:18` names seven speakers in the transcript the user
reads under *How the decisions are made*. Four of those seven are engines
wearing an agent's name. That is a labelling problem, and it is written up in
`THE SEVEN-VERSUS-FIVE QUESTION` below.

---

## The roster

| Name | Class | Kind | Can veto | Reaches the user |
|---|---|---|---|---|
| Research Agent | `app/agents/research_agent.py:15` | agent | no | ✅ Explore → *What's moving*; war-room line 1 |
| Opportunity Agent | `app/agents/screener_agent.py:22` | agent | no | ✅ buy ideas + the commodity sleeve card |
| Fee Agent | `app/agents/fee_agent.py:25` | agent | no | ✅ fee cards on Today |
| Allocation Agent | `app/agents/allocation_agent.py:22` | agent | **yes** | ❌ **unreachable — see below** |
| Adversary | `app/agents/adversary.py:116` | agent | **yes** | ✅ war-room + card suppression |
| Alpha | `app/engines/lag_engine.py` | engine | no | speaks in the war room |
| Risk | `app/engines/risk_engine.py` | engine | **yes** | speaks in the war room |
| Tax | `app/engines/tax_engine.py` | engine | no | speaks in the war room |
| Decision | `app/engines/decision_engine.py` | engine | no | speaks in the war room |
| UX | *not a class* | renderer | no | the Decision Feed line |

---

## Research Agent

**Lives at** `app/agents/research_agent.py:15`.

**Purpose.** Emit market events as structured evidence for everything
downstream. Its own docstring states the invariant: *"it never recommends an
action or mutates portfolio state - it only emits ResearchEvent evidence."*

**Consumes** `guarded_events()` — `registry.py:139` → `economic_provider()` →
`registry.py:63`.

**Emits** `list[ResearchEvent]`, sorted by relevance descending
(`research_agent.py:29`).

**Veto** none. It is read-only by construction.

**Reaches the user** two ways:
- `GET /api/v1/research/events` → `market.py:40`, which is what the Explore
  page's *What's moving* card fetches (`index.html:2790`).
- The opening line of every war-room transcript (`war_room.py:83, 90`).

**What it does not do.** It does not rank against your holdings, does not
produce cards, and does not know what you own. The *exposure* and *what to do*
lines on the Explore card are added afterwards by `annotate(...)` in the route
(`market.py:46`), not by the agent.

> **⚠ Its data source is a fixture with four rows.**
> `registry.py:63-64` returns `BuiltinEconomicDataProvider()` **unconditionally**.
> There is no settings switch and no live alternative — `app/providers/live.py`
> has a market provider and an FX provider and no economic provider at all.
> `builtin.py:113-130` returns the same four hardcoded events on every call, on
> every deployment, forever.
>
> The Explore card captions that list *"🕒 Updates hourly · last refreshed
> {n} minutes ago"* (`index.html:2791`). That timestamp is
> `market_status()["last_refreshed"]` — **the price refresh clock**
> (`market.py:44`), which has nothing to do with the events above it. So a list
> that has never changed is captioned with a clock that is genuinely ticking.
>
> This is the same failure PR 22 fixed on the history chart: a caption that
> vouches for a number tells the reader the checking has already been done. It
> is also the direct answer to *"I'm not getting enough from What's moving"* —
> you are getting exactly four items because four is all there are.

---

## Opportunity Agent

**Lives at** `app/agents/screener_agent.py:22`.

**Purpose.** Answer *of everything I could buy, what is most attractive on
fundamentals right now?* — the only agent that generates buy ideas rather than
reacting to what you already hold.

**Consumes** `universe.equity_universe()`, `commodity_candidates()`,
`bond_candidates()`, plus `guarded_fundamentals` and `guarded_history` per name.

**Emits** `list[ScreenPick]` from `ScreenerEngine`, via `screen_equities`
(`:75`), `screen_commodities` (`:83`), `screen_bonds` (`:150`), `top_ideas`
(`:186`).

**Veto** none.

**Reaches the user**
- `recommendations.py:2696` → `_buy_ideas` → the buy ideas on Today, and the
  Fixed Income leg of the redeploy-cash card.
- `recommendations.py:2630` → the *Add a commodities sleeve* card.
- `GET /api/v1/screener` → `screener.py:32`. **The app never calls this
  endpoint** — zero references in `index.html`.

**What it does not do — and this is item 2's headline.** It does not read your
strategy. `screen_equities` takes a `weights` argument and every caller inside
the app omits it:

- `recommendations.py:2698` calls `ag.screen_equities(top_n=5)` — no weights, so
  `ScreenerEngine` uses `DEFAULT_WEIGHTS`.
- `screener.py:31` takes weights **only from URL query parameters**
  (`?value=&growth=&quality=&income=`), and nothing in the app builds that URL.

So whether your objective is Grow, Balanced, Income or Preserve, the buy ideas
are ranked by the identical factor mix. The strategy influences *how much* is
bought (through the target mix and the funding logic) and never *what*.

**Its two liveness rules are worth knowing** because they legitimately produce
silence: a candidate the provider cannot price is dropped rather than scored
(`:104-113`, `:164-175`), and when nothing ranks, the commodity card abstains
instead of falling back to a hardcoded ticker (`recommendations.py:2641`). An
empty section here is often the system being honest.

---

## Fee Agent

**Lives at** `app/agents/fee_agent.py:25`.

**Purpose.** Turn `FeeEngine` findings into `FEE_OPTIMIZATION` cards with a
complete audit trail.

**Consumes** position dicts → `positions_to_holdings` (`:15`) → `FeeEngine.scan`.

**Emits** cards with `apply.kind == "fee_swap"` (`:50`), each carrying three
substituted formulas: current annual fee, alternative annual fee, annual saving
(`:59-67`).

**Veto** none.

**Reaches the user** `recommendations.py:1272` — unconditionally, on every
recommendations build. Also `GET /api/v1/fees` (`fees.py:21`), which the app
does not call.

**What it does not do.** No strategy input, and correctly so — a fee is a fee
under every objective. It also does not check whether the alternative it names
is *tradeable for you*: the swap target comes from `FeeEngine`'s catalogue, and
nothing filters it by your market access.

---

## Allocation Agent

**Lives at** `app/agents/allocation_agent.py:22`.

**Purpose.** The only agent class holding a programmatic veto. Its docstring:
*"a high-scoring action that would push an already-overweight asset class
further out of policy is blocked with a VetoException before it can reach the
user's feed."*

**Consumes** an `asset_class` and an `AllocationReport`.

**Emits** nothing on success; raises `VetoException` (`:14`) on a buy into a
class whose drift exceeds `settings.allocation_drift_threshold`
(`allocation_engine.py:57-59`).

**Veto** **yes** — the strongest one in the codebase, because it is raised
before the card is built rather than annotated onto it.

> **⚠ It never runs.**
> Its one caller is `pipeline.py:86, 102`, inside `PipelineOrchestrator.generate`.
> That runs from exactly one place: `POST /api/v1/decision-feed/generate`
> (`decision_feed.py:81-85`), which requires the ANALYST role. **The app never
> calls it** — `index.html` has zero references to `decision-feed`.
>
> And even on that endpoint the veto is doubly conditional: `pipeline.py:80`
> needs `req.allocation` to be non-null, and `pipeline.py:97` needs
> `req.asset_class_map` to be non-empty. Both default to `None`
> (`schemas/feed.py:20-21`), so an unparameterised POST does not reach it either.
>
> The cards you actually see are built by `app/services/recommendations.py`,
> which never imports `AllocationAgent`. **Nothing stops a recommendation from
> buying further into an overweight class on the path the app uses.** Policy
> drift is instead handled by the rebalance cards and the target mix — a
> different mechanism, with no veto.

---

## Adversary

**Lives at** `app/agents/adversary.py:116` (class), with module-level helpers
`critique` (`:243`) and `should_veto` (`:255`).

**Purpose.** Cross-examine each stage's output for internal contradictions —
not "is this a good idea" but "do these numbers disagree with each other".

**Consumes** the four stage objects, one examiner each: `examine_detected`
(`:123`), `examine_vetted` (`:137`), `examine_optimized` (`:150`),
`examine_ranked` (`:167`).

**Emits** an `AdversaryNote` per stage carrying findings and a `Severity`
(`:42`), plus `blocks` (`:57`). `narrate` (`:182`) adds optional Gemini prose,
and is the reason the war room was measured at 5.4s cold / 6.7s **warm** — it
is a live LLM call per signal on the event loop (`war_room.py:55-62`).

**Veto** **yes**, and it fires. `war_room.py:135, 147, 163` collect the first
blocking note; `war_room.py:166-178` turns it into `outcome: "VETOED"` and the
signal never becomes a card. Before that wiring existed the block was prose and
nothing read it — the comment at `war_room.py:167-169` says so.

**What it checks, concretely** — these are the honest examples, not a summary:
a positive expected return with zero volatility (`:129`); a return/volatility
ratio above 3 (`:132`); a loss carry-forward more than five times the gain
(`:134`); a ruin probability computed with no max drawdown (`:139`).

**What it does not do.** It does not check the *content* of a thesis, only its
arithmetic consistency. A perfectly coherent bad idea passes.

---

## The four engines that speak as agents

These have no class under `app/agents/`. They are engines whose output
`war_room.py` renders as dialogue, which is why the screen says seven.

| War-room name | What actually speaks | Line |
|---|---|---|
| **Alpha** · Strategist · Lag | `LagEngine.scan` — finds Depth-1/2/3 divergence signals | `war_room.py:86, 95` |
| **Risk** · Red-Team | `RiskEngine` via `StateMachine.vet` — 10,000-path Monte Carlo | `war_room.py:102, 118` |
| **Tax** · Tax optimizer | `TaxEngine` via `StateMachine.optimize` — net-after-tax | `war_room.py:138` |
| **Decision** · Scorer | `DecisionEngine` via `StateMachine.rank` — impact/confidence | `war_room.py:150` |
| **UX** · Decision Feed | `StateMachine.display` + a string. No class at all. | `war_room.py:180, 200` |

**Risk holds a veto** (`war_room.py:103-117`: `veto_flag` → `outcome: "VETOED"`,
`outcome_label: "Skipped — too risky"`), so one of the four is not merely
cosmetic. It is still an engine — the discretion lives in `StateMachine`, not in
a Risk agent, because there is no Risk agent.

**The target solver is deliberately not on this list.** `war_room.py:63-70`
spells out why: it is a constraint the Risk and Adversary lines consult, not an
eighth agent and not a decision maker. A signal it blocks keeps its card and
loses its one-tap Accept (`war_room.py:220-232`) — because *"a card deleted
because of a background measurement nobody can see is worse than one that
explains itself."*

---

## THE SEVEN-VERSUS-FIVE QUESTION

`war_room.py:18` declares seven agents. Five classes exist under `app/agents/`,
and only two of those five (Research, Adversary) appear in the seven. The
overlap between "the agents that exist" and "the agents the user is shown" is
**two**.

Two ways to make the label true:

1. **Rename in the transcript.** `Alpha · Lag engine`, `Risk · Risk engine`,
   `Tax · Tax engine`, `Decision · Scorer`, and drop `UX` (it is a render step,
   not a participant). Cost: a handful of strings and the `AGENTS` constant.

2. **Promote the engines to agents.** Give each a class under `app/agents/`
   with its own judgement layer. Cost: five new classes, five new test surfaces,
   and a real risk of ceremony around code that is already correct.

**Recommendation: (1).** Promoting five engines is a large change bought for a
labelling problem. The engines are not deficient; they are accurately named
where they live and inaccurately named where they are displayed.

Whichever is chosen, `tests/test_the_agent_roster_is_what_actually_runs.py`
fails when this document and the code drift apart.

---

## WHAT THIS MAP FOUND

Four things that are not documentation problems:

1. **`ResearchAgent` reads a four-row fixture, unconditionally.**
   `registry.py:63` has no live branch and `app/providers/live.py` has no
   economic provider. *What's moving* cannot show a fifth item today no matter
   what the market does. → your item 5.

2. **That list is captioned with the price-refresh clock.**
   `index.html:2791` reads `last_refreshed` from `market_status()`
   (`market.py:44`). A static list, timestamped as if it were live. Same class
   of defect as the +35% caption PR 22 removed.

3. **The Allocation Agent's veto is unreachable from the app.** Its only caller
   is an ANALYST-role endpoint the UI never calls, behind two request fields
   that both default to `None`. Nothing on the live path prevents a buy into an
   overweight class.

4. **No agent reads your strategy when choosing what to buy.**
   `recommendations.py:2698` calls `screen_equities(top_n=5)` with no weights;
   the only weight input in the codebase is a query string nothing builds.
   Grow and Preserve are ranked identically. → your item 2.

None of the four is fixed by this document. Each is a PR, and (1) and (2) are
the same PR.

---

*Not financial advice.*
