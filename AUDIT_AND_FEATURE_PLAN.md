# InvestWise Pro — Architecture Audit & Feature Execution Plan

**Prepared:** 2026-06-14 · **For:** Eran Ganot (SuperAdmin)
**Status:** ⏸ PLAN ONLY — no code written yet. Awaiting your approval & sequencing decision.
**Scope:** the 3-phase brief (state hardening + Adversary routing · UI transparency + what-if sliders · brokerage sync + fee optimizer + backtesting).

---

## 0. Ground truth — important, please read first

You pointed me at `https://github.com/eranganot/Investwise-pro`. I cloned and audited it. **It does not match the copy in your connected folder.**

| | Connected folder (`Investments/investwise-pro/`) | GitHub repo (`eranganot/Investwise-pro`) |
|---|---|---|
| State | **Phase-0 skeleton** | **Mature v1+ system** |
| Engines | Stubs returning `"Awaiting Data"` | 13 real engines (scoring, allocation, scenario, safety, learning, XAI, risk MC, tax…) |
| `app/agents/` | **empty** | `adversary.py`, `allocation_agent.py`, `research_agent.py` |
| Services | none | `pipeline.py`, `war_room.py`, `auth_service.py`, `portfolio_analytics.py`, `recommendations.py`, +7 |
| Providers / worker | none | `app/providers/` (resilience tier) + `app/worker/` (Celery + scheduler) |
| Frontend | bare React/Vite shell | vanilla HTML+Chart.js served at `/dashboard` and `/app` |
| Tests | 2 files | **36 test files** |
| Migrations | 1 | 5 Alembic revisions |

**Consequence:** your brief says "tighten" state between agents and "improve" the Adversary — in the *real* repo those exist, so this is genuinely an audit/hardening job (good). But the folder I have write-access to is the wrong copy.

### Required step 0 (blocking): re-sync the working copy
Before any code is written, we must work against the real repo, not the stale folder. Options:
- **A (recommended):** I clone `eranganot/Investwise-pro` into your connected folder (replacing the stale skeleton) and work there, committing to a feature branch.
- **B:** You re-clone/pull it yourself into the folder, then I take over.
Either way, all work lands on a **branch** (e.g. `feat/audit-phase1`), never straight to `main`.

---

## 1. Verified architecture map (from the real repo)

**Tech stack.** Python 3.12 · FastAPI (async) · SQLAlchemy 2.0 + asyncpg · Pydantic v2 + pydantic-settings · Alembic · NumPy (Monte Carlo) · Celery + Redis + APScheduler (worker) · `cryptography` · JWT auth (asymmetric) · pytest/httpx. Frontend is **vanilla HTML/CSS/JS + Chart.js (CDN)** — no build step. Deployed on Railway; CI in `.github/workflows/ci.yml`.

**State flow (the "multi-agent" pipeline).** It is a **deterministic** engine+agent pipeline, not LLM agents. State moves as immutable, typed Pydantic stage objects (`app/schemas/state_machine.py`), each embedding the previous as a typed `source` field so stages physically cannot be skipped:

```
DetectedSignal → VettedSignal → OptimizedSignal → RankedSignal → DisplayedItem
   (Lag)           (Risk)          (Tax)            (Decision)      (UX feed)
                     └─ veto_flag → VetoedSignal (terminal)
```

Orchestrated in `app/services/pipeline.py` (`PipelineOrchestrator.generate`) which also layers in: Allocation Agent veto (the "Portfolio agent" — `app/agents/allocation_agent.py`), Safety Engine, **Adversary** critique (`app/agents/adversary.py`), XAI explanation (`app/engines/xai_engine.py`), learning personalization, expiry, and DB persistence. `app/services/war_room.py` produces a stage-by-stage transcript.

**Where the Adversary sits today.** It runs **once, at the end** — `adversary.critique(...)` is called on the final displayed item; `adversary.should_veto(safety)` escalates a Safety "block" into a veto. It does **not** critique each agent's output as the pipeline runs. That's the gap your Phase 1.3 targets.

**Scoring (already formalized).** `app/schemas/scoring.py`: 5-component Impact `(0.30·return + 0.25·tax + 0.25·risk + 0.10·liq + 0.10·conviction) / ComplexityFactor`, 4-component Confidence, strict 0–100 normalization. Display gate: Impact ≥ 20 and Confidence ≥ 60.

---

## 2. Recommendation on the "agent" question

Your spec says agents that "critique logic" and "flag mathematical blind spots." My recommendation, given this is **wealth/financial software where reproducibility and auditability matter most**:

**Keep the core deterministic; make the Adversary a deterministic invariant/assertion layer, with an *optional* LLM narrative layer on top.**
- Deterministic Adversary checks (always on, free, reproducible, testable): bounds/range assertions, NaN/Inf guards, monotonicity & sign checks (e.g. selling a gain must produce tax cost ≥ 0; loss carry-forward can't exceed gross gain), cross-engine consistency (Decision's risk sub-score vs Risk engine's drawdown), unit/scale sanity, and "stale/Awaiting Data" detection.
- Optional LLM layer (off by default, behind a flag + API key): turns the deterministic findings into a natural-language red-team paragraph. Never invents numbers.

This satisfies "flag mathematical blind spots" with provable checks rather than a model that might hallucinate confidence. **Confirm you're happy with deterministic-first.**

---

## 3. Phase-by-phase execution plan

Each item lists: what exists → what I'll change → files → verification. Every phase ends at a **green-test gate**.

### PHASE 1 — Core hardening

**1.1 Audit & map (1st deliverable).** Produce a short `ARCHITECTURE_AUDIT.md`: tech stack, state-flow diagram, every agent/engine boundary, and a list of the exact DTO hand-offs between Risk → Tax → Decision → Allocation. *Verification:* you review it; no code risk.

**1.2 Strict cross-agent Pydantic validation.**
- *Exists:* typed frozen stage models, but not maximally strict (floats accept NaN/Inf; no `extra="forbid"`; few range constraints).
- *Change:* add `ConfigDict(strict=True, extra="forbid")` to stage + scoring + allocation schemas; add field validators (probabilities ∈ [0,1], scores ∈ [0,100], finite floats only, ILS amounts not NaN); add an explicit `assert_handoff()` that validates one engine's output literally satisfies the next engine's input contract; centralize so drift can't pass silently.
- *Files:* `app/schemas/state_machine.py`, `scoring.py`, `allocation.py`, `risk.py`, `tax.py`; new `app/schemas/validation.py`.
- *Verification:* new tests proving malformed/NaN/out-of-range hand-offs raise `ValidationError`; all 36 existing tests still green.
- ⚠️ *Architectural-shift flag:* `strict=True` may break callers that pass ints-for-floats etc. I'll fix call sites, but this touches many files — **needs your OK** (§4).

**1.3 Adversarial cross-examination after every step.**
- *Exists:* Adversary critiques only the final item.
- *Change:* refactor the execution graph so after **each** transition (vet, optimize, rank) the state routes through an `Adversary.examine(stage)` checkpoint that runs the deterministic invariant checks for that stage and either (a) annotates the stage with a critique, or (b) raises a hard `AdversaryVeto` that halts the pipeline with a logged reason — *before* the next agent runs. Aggregate critiques into the war-room transcript and the feed payload.
- *Files:* `app/agents/adversary.py` (expand to per-stage examiners), `app/engines/state_machine.py` (insert checkpoints into `run`), `app/services/pipeline.py` + `war_room.py` (collect per-stage critiques), new `tests/test_adversary_routing.py`.
- *Verification:* test that every stage emits a critique; test that an injected bad calc is caught at the *right* stage and blocks progression.
- ⚠️ *Architectural-shift flag:* changes the pipeline control flow — **needs your OK** (§4).

### PHASE 2 — UI transparency & interactive controls
*(Frontend is the vanilla `app/static_app/index.html` — I'll extend it, no framework swap.)*

**2.1 "Source Code & Data" accordion on every recommendation.**
- *Exists:* the data already exists server-side (raw observation, scores breakdown, XAI explanation, adversary critique) but isn't surfaced as a transparency panel.
- *Change:* add a new API field `audit_trail` to each feed item containing: (i) raw portfolio data evaluated, (ii) the **deterministic formulas applied** with the actual numbers plugged in (Impact formula, tax CGT/surtax math, risk MC summary), (iii) the Adversary's per-stage critique from 1.3. Add a collapsible accordion in the feed card rendering it (with a "copy/expand" view).
- *Files:* `app/services/feed_service.py` / `pipeline.py` (assemble `audit_trail`), `app/engines/xai_engine.py` (formula strings), `app/static_app/index.html` (accordion UI).
- *Verification:* API test that `audit_trail` contains all three sections; manual UI screenshot check.

**2.2 Interactive "What-If" sliders.**
- *Exists:* the pipeline already accepts a settings/plan object; no UI to vary it live.
- *Change:* add a control panel with sliders for **Risk Tolerance**, **Tax-Loss-Harvesting target**, **Expected Market Drawdown**. Sliders post to a `POST /api/v1/decision-feed/whatif` endpoint that injects the values into the initial pipeline state and re-runs, returning a fresh feed + updated risk profile. Debounced re-evaluation; show a "recomputed" diff.
- *Files:* new route in `app/api/routes/decision_feed.py`, plumb overrides through `PipelineOrchestrator`, `app/static_app/index.html` (slider panel + re-render).
- *Verification:* test that changing each slider measurably changes outputs (e.g. higher drawdown → more vetoes); UI check.

### PHASE 3 — Advanced wealth optimization

**3.1 Automated brokerage sync (scaffold).**
- *Exists:* `BROKER_INTEGRATION_PLAN.md` already designs a broker-agnostic `BrokerProvider` + mock + Israeli-broker adapters and the `broker_connections/orders/order_events` tables — but **no `app/brokers/` code is built.** Note: your plan targets Israeli brokers (inter-il, IBI); your brief says **Plaid/Yodlee** (US aggregators). These are complementary — Plaid/Yodlee = read-only holdings aggregation; broker adapters = order placement.
- *Change (scaffold only, no real money/keys):* build `app/brokers/base.py` (ABC + DTOs), `mock.py` (deterministic sandbox), `registry.py` wrapped in the existing resilience tier, plus an **`aggregator/` interface for Plaid/Yodlee** (read-only positions sync) with a mock adapter. Add the 3 tables + Alembic migration. Endpoints `POST /broker/connect`, `POST /broker/sync` behind `BROKER_ENABLED=false`. Credentials stored only as encrypted `credential_ref`.
- *Files:* new `app/brokers/`, `app/models/tables.py` (+3 tables), `alembic/versions/0006_broker.py`, `app/api/routes/broker.py`.
- *Verification:* end-to-end sync test against the mock; flag-off by default.
- ⚠️ *Decision needed:* Plaid vs Yodlee vs the existing Israeli-broker direction (§4). Live wiring needs accounts/keys — scaffold runs without them.

**3.2 Fee & expense-ratio optimizer (new sub-agent).**
- *Exists:* nothing.
- *Change:* new `app/engines/fee_engine.py` + thin `app/agents/fee_agent.py`: scans holdings for high management-fee / high-expense-ratio assets, and for each suggests an equivalent **low-fee, highly-liquid index** alternative from a config-driven mapping table (e.g. high-fee active fund → broad index ETF in the same asset class), with the **annual ₪ fee saved** computed deterministically. Surfaces as a new feed category `FEE_OPTIMIZATION` and in the audit trail.
- *Files:* `app/engines/fee_engine.py`, `app/agents/fee_agent.py`, `app/schemas/` (fee DTO), config mapping in `app/services/` or a data file, route hook, tests.
- *Verification:* worked numeric tests (fee delta math); a known high-fee holding produces the expected index suggestion + savings.
- *Note:* the index "equivalents" are config/data-driven suggestions, **not financial advice** — same disclaimer posture as the rest of the system.

**3.3 Historical backtesting engine (validate Risk-Agent beta).**
- *Exists:* `app/engines/scenario_engine.py` runs **forward, deterministic** macro shocks (MARKET_CRASH, INFLATION_SHOCK, …) — but does not replay **real historical** windows or validate beta.
- *Change:* new `app/engines/backtest_engine.py` that replays a proposed portfolio shift against bundled historical return series for named events (2008 GFC, 2020 COVID crash, optionally 2022 drawdown), computes realized drawdown/beta over those windows, and **cross-checks the Risk Engine's beta/drawdown estimates** against the historical realization — flagging (via the Adversary) when the model's beta diverges from history beyond a tolerance, *before* final approval.
- *Data:* bundle canned monthly/weekly index series for those windows (offline, deterministic) so it runs with zero external data; a provider adapter can replace the bundle later.
- *Files:* `app/engines/backtest_engine.py`, a small `app/data/history/*.json`, route `POST /api/v1/backtest`, wire a "beta-vs-history" check into the Adversary/approval path, tests.
- *Verification:* deterministic tests on known series (e.g. a 100%-equity book shows ~-50% over 2008); beta-divergence flag fires on a rigged mismatch.

---

## 4. Decisions I need before building (the "major architectural shifts")

1. **Re-sync (§0):** OK for me to replace the stale folder with a fresh clone of `eranganot/Investwise-pro` and work on a branch? (Blocking.)
2. **Deterministic-first Adversary (§2):** confirm you want deterministic invariant checks as the core, LLM narrative optional/off — vs. full LLM agents (needs Anthropic API key, non-reproducible).
3. **Strict validation blast radius (1.2):** OK to turn on `strict=True`/`extra="forbid"` and fix the call sites it breaks?
4. **Pipeline control-flow change (1.3):** OK to insert per-stage Adversary checkpoints that can halt the pipeline mid-flight?
5. **Brokerage direction (3.1):** Plaid, Yodlee, or stay with the Israeli-broker plan already in the repo? (Scaffold+mock now regardless; live needs keys.)
6. **Fee-optimizer equivalents:** OK that index "alternatives" are a **config-driven mapping you can edit/approve**, presented as options not advice?

## 5. Cross-cutting guardrails
- All work on a feature branch; each phase is independently testable and ends green (36 existing tests stay passing + new tests per item).
- No hardcoded financial constants — everything via `Settings`/config, consistent with the repo's existing convention.
- "Not financial advice" disclaimer posture preserved throughout; nothing moves real money; broker stays flag-off.
- I'll ask again before anything that touches credentials, money movement, or deletes data.

## 6. Suggested sequencing (you decide)
- **Option A (recommended):** Phase 1 fully (audit → strict validation → Adversary routing), verify with you, then Phase 2, then Phase 3. Lowest risk; the Adversary routing underpins 2.1 and 3.3.
- **Option B:** All three phases end-to-end in one pass, gating between each.
- **Option C:** You pick the highest-value items (e.g. fee optimizer + backtesting first) and I sequence around them.

> Reply with your choices for §4 and a sequencing option (A/B/C). I won't write code until you approve.
