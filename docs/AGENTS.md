# The agents

Every claim here carries a `file:line` you can open. Nothing in this document is
inferred from a name: where a sentence says an agent does something, the line
cited is the line that does it. Where the code and the label disagree, the
disagreement is written down rather than smoothed over.

Read `WHAT THIS MAP FOUND` at the bottom first if you only read one section.

> **Currency note (2026-10-07).** The roster, the veto facts and the 7-vs-5 analysis are still true and are pinned by
> `tests/test_the_agent_roster_is_what_actually_runs.py`. Most other `file:line` references were written at PR 23 (2026-08-30)
> and have **drifted**, so search for the cited symbol. Examples: FeeAgent's call in `recommendations.py` is now near :1944, buy ideas
> near :5164, `guarded_events` at `registry.py:198`. Today's copy of the war room is cached and not narrated (PR 104c.1), so
> the narration latency figures apply only to the Agents tab. This page covers agents only. **The Today pipeline,
> which is mostly plain producer functions rather than agents, is in [DOMAIN.md](DOMAIN.md).** The goal/frontier
> solver and the holding review behave like agents but are not listed here.

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
| Fee Agent | `app/agents/fee_agent.py:26` | agent | no | ✅ fee cards on Today |
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

> **✅ FIXED IN PR 58. It has no data source, and now says so.**
> This section used to read: *"Its data source is a fixture with four rows."*
> `registry.py` returned `BuiltinEconomicDataProvider()` unconditionally — no
> settings switch, no live alternative, `app/providers/live.py` having a market
> provider and an FX provider and no economic provider at all — and `builtin.py`
> returned the same four hardcoded events on every call, on every deployment,
> forever.
>
> Worse, the Explore card captioned them *"🕒 Updates hourly · last refreshed
> {n} minutes ago"*, and that timestamp was `market_status()["last_refreshed"]`
> — **the price refresh clock**, which had nothing to do with the events above
> it. A list that had never changed wore a clock that was genuinely ticking. And
> `market_impact.annotate` wrapped each one in the reader's real holdings, real
> shekel exposure and a *"What to do"* list.
>
> That was the same failure PR 22 fixed on the history chart: a caption that
> vouches for a number tells the reader the checking has already been done.
>
> PR 58 made `get_events()` return `[]`, removed the borrowed clock, and put
> `source` / `live` / `source_note` on the payload instead. The card now says
> *"No market-intelligence source is configured"*. The war-room Research line
> says the same rather than falling silent — a cast list that names a speaker
> who never speaks is the same defect one layer down. `annotate`, `_playbook`
> and the exposure arithmetic are untouched and are the part worth keeping: wire
> a real provider and the whole panel comes back.

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

**What it did not do — item 2's headline, and now fixed.** It did not read your
strategy. `screen_equities` takes a `weights` argument and every caller inside
the app omitted it:

- `recommendations.py` called `ag.screen_equities(top_n=5)` — no weights, so
  `ScreenerEngine` used `DEFAULT_WEIGHTS`.
- `screener.py` took weights **only from URL query parameters**
  (`?value=&growth=&quality=&income=`), and nothing in the app built that URL.

So whether your objective was Grow, Balanced, Income or Preserve, the buy ideas
were ranked by the identical factor mix. The strategy influenced *how much* was
bought (through the target mix and the funding logic) and never *what*.

> **HALF FIXED — the wiring, not the tilt.** `app/services/objective_weights.py`
> carries the objective from the plan to the screener at both call sites, and
> `/screener` defaults to it. **`OBJECTIVE_WEIGHTS` is empty**, so `weights_for`
> returns `None` for every objective, `ScreenerEngine` uses `DEFAULT_WEIGHTS`,
> and **every book ranks exactly as it did before**.
>
> **Why empty, and it is not an oversight.** Eran's constraint is that nothing
> is hardcoded. Four hand-chosen vectors behind an `OWNER_REVIEWED = False` flag
> failed that test — the flag admits the problem, it does not solve it, and the
> numbers would still have reordered real buy recommendations.
>
> The one derivation available was tried: each objective's own strategies carry
> a real `basket`, so the factor signature of those baskets could *be* the tilt,
> calculated from live fundamentals. Grow resolves 14 names, Balanced 5, Income
> 6, Preserve 6. Computed against the full universe (69 tickers), the sandbox
> returned:
>
> | objective | value | growth | quality | income |
> |---|---|---|---|---|
> | Grow | .241 | .292 | .253 | .214 |
> | Balanced | .213 | .224 | .278 | .285 |
> | Income | .229 | **.322** | .252 | **.196** |
> | Preserve | .262 | .238 | .212 | .288 |
>
> **Income came out weighting growth highest and income lowest** — the exact
> contradiction the tilt exists to remove, reproduced by the derivation. All four
> sit inside 0.20–0.33 and would barely differentiate anything.
>
> The cause is `builtin.py:56-81`: the sandbox provider seeds every fundamental
> off a sha1 of the ticker, so SCHD's "dividend yield" is a hash. The method
> cannot be validated on the only data this environment has — and on real data it
> may still be weak, because the Income and Preserve baskets are almost entirely
> ETFs, whose pass-through P/E and ROE are poor inputs for exactly the objectives
> that matter most.
>
> So the table stays empty. **Item 2 remains open, with a proven reason.**

**Its two liveness rules are worth knowing** because they legitimately produce
silence: a candidate the provider cannot price is dropped rather than scored
(`:104-113`, `:164-175`), and when nothing ranks, the commodity card abstains
instead of falling back to a hardcoded ticker (`recommendations.py:2641`). An
empty section here is often the system being honest.

---

## Fee Agent

**Lives at** `app/agents/fee_agent.py:26`.

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

## Allocation Agent (deleted)

**Deleted in PR 113** (plan v3 stage 2, D76) with the decision-feed pipeline that was its only caller. It held the
codebase's one pre-card veto (a `VetoException` on a buy into an over-target class), but it was reachable only from
`POST /api/v1/decision-feed/generate`, which the app never called. The live rule it encoded is enforced on the path
the app uses: funding may only sell what the plan says is over target, and a buy into an over-target class gets no
card (`funding_service.propose_funded_buy`, PR 106's drift gate).

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

> **⚠ Alpha is the first step, not the decision maker.** Worth stating plainly,
> because the name invites the opposite reading. `LagEngine` compares a spot
> price to a listing price and emits a signal if the gap clears a noise floor —
> `lag_engine.py:36-52`. `war_room.py:86` is `for det in lag.scan(observations)`,
> so Alpha's output is what the pipeline *iterates over*. It consults nobody and
> runs before everything. "The ALPHA mandate" in its docstring means only that it
> ranks backbone (Depth 3) divergences above surface ones.
>
> **`DecisionEngine` is the one that synthesises** — Impact from five components,
> Confidence from four, reading Risk's ruin probability and Tax's net gain
> (`decision_engine.py:31-55`). Alpha finds candidates; Decision scores what
> survived. They are opposite ends of the same conveyor.
>
> **And the role that would consult everyone does not exist.** The pipeline is
> Alpha → Risk → Tax → Decision → UX, strictly linear, with Adversary
> cross-examining each stage's output. There is no back-channel: no stage can ask
> another a question, push back, or request a re-run. `StateMachine` runs them in
> order but exercises no judgement — it is plumbing. So the war room's name
> promises deliberation and the code is a conveyor belt. See the Tier 4 decision
> below.

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

My recommendation was (1), on the grounds that promoting five engines is a large
change bought for a labelling problem, and that being an *agent* is not what
makes something strategy-aware — `RiskEngine` is an engine and already flexes
with the plan.

### DECIDED, 2026-08-30: (2), deferred to Tier 4

Eran's call, and the reasoning that settled it is better than either option as
originally framed. The useful line is not *agent or engine*. It is:

> **The agent holds the judgement and the policy. The engine stays underneath as
> pure, reproducible computation.**

Every participant here has both, and today they are welded together — Risk's
veto is `if sigma > cap`, one threshold standing in for a decision that ought to
vary by objective. That resolves the reproducibility objection cleanly: the Monte
Carlo stays a seeded pure function, and what to *do* with its answer moves up a
layer where it can be argued with.

So promoting is not ceremony, **provided each agent holds a real decision**.
`AllocationAgent` felt like ceremony because its entire judgement was one
threshold — an argument for making the judgements richer, not for deleting the
layer.

**Scope of the Tier 4 item:**

- Research, Opportunity, Allocation, Fee, Adversary, Tax, Risk, Decision → all
  agents, each over its engine.
- **UX is not promoted.** It has no class, and inventing one to reach a count of
  seven is the one piece of pure ceremony on the list.
- **Alpha keeps its real job** — the price-divergence scout — and a **new Chief
  agent** is added above the others: the synthesising role that consults each one
  and makes the call. That role does not exist today (see the note above the Risk
  veto), and it is the largest idea to come out of this map.

**Not started.** The four findings below come first.

Whichever shape it lands in, `tests/test_the_agent_roster_is_what_actually_runs.py`
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

4. **No agent reads your strategy when choosing what to buy.** → your item 2.
   **STILL OPEN — half fixed.** The *wiring* is done: both screener call sites
   pass the objective and `/screener` defaults to it
   (`app/services/objective_weights.py`). The *tilt* is not, because
   `OBJECTIVE_WEIGHTS` is empty and nothing in this codebase derives one. Every
   book therefore still ranks on `DEFAULT_WEIGHTS`, exactly as before.

   Hand-chosen weights were rejected as hardcoding. The one available
   derivation — the factor signature of each objective's own strategy baskets —
   was computed and produced **Income weighting growth highest and income
   lowest**, because the sandbox provider seeds fundamentals off a sha1 of the
   ticker. Full working in the Opportunity Agent entry above. The wiring is in
   place, so a defensible tilt is four numbers away whenever one exists.

   **A sharper version of the same defect is still open.** `settings.objective`
   is written into the settings object every engine holds
   (`plan_service.py:55`) and read by **nothing else** — not one engine, not one
   agent. The screener reads the objective from the plan directly rather than
   from that field, so the dead setting stays dead. Measured:

   | participant | reads the plan? | what |
   |---|---|---|
   | Risk · `RiskEngine` | yes | `volatility_cap`, `ruin_probability_cap` (`risk_engine.py:92,96`) |
   | Decision · `DecisionEngine` | partly | `preferred_depth` (`decision_engine.py:40`) |
   | Alpha · `LagEngine` | no | a global noise floor, not plan-derived (`lag_engine.py:37`) |
   | Tax · `TaxEngine` | **no** | nothing — zero settings reads |

   Being an *engine* is not why Tax is blind; `RiskEngine` is an engine and is
   not blind. Nobody wired it. The contract test that would make this impossible
   rather than invisible is the natural companion to the Tier 4 work above.

**Two are still open (as of 2026-10-07).** (1) and (2) were fixed together in
PR 58 (see the Research Agent entry). (3) is open. (4) is half done:
the wiring landed, the tilt is waiting on a basis that survives measurement.
Both open items are tracked in `docs/BACKLOG.md` (P10, P11, L1).

---

*Not financial advice.*
