# InvestWise Pro — Architecture Audit (Phase 1.1)

**Date:** 2026-06-14 · **Branch:** `feat/audit-phase1` · **Baseline:** 153 tests passing (SQLite).
**Purpose:** ground-truth map of the system before hardening — tech stack, the cross-agent state flow, and the exact typed hand-offs between Risk → Tax → Decision → Allocation that Phase 1.2/1.3 will tighten.

---

## 1. Tech stack (verified)

| Layer | Technology |
|---|---|
| API | FastAPI (async), app factory in `app/main.py`; routers under `app/api/routes/` (21 route modules) |
| Domain | Pure-Python engines in `app/engines/` (13) + agents in `app/agents/` (3) + services in `app/services/` (12) |
| Data model | SQLAlchemy 2.0 async ORM (`app/models/tables.py`), Postgres/asyncpg in prod, SQLite/aiosqlite in tests; 5 Alembic migrations |
| Contracts | Pydantic v2 (`app/schemas/`, 15 modules) + `pydantic-settings` (`app/core/config.py`) |
| Compute | NumPy Monte Carlo (Risk), deterministic scenario tables (Scenario) |
| Async/jobs | Celery + Redis + APScheduler (`app/worker/`) |
| Market data | Provider abstraction + resilience tier (cache→rate-limit→circuit-breaker→retry) in `app/providers/` |
| Security | JWT (asymmetric), bcrypt, audit log (`app/core/auth.py`, `audit.py`, `security.py`) |
| Frontend | Vanilla HTML/CSS/JS + Chart.js (CDN), served at `/dashboard` (`app/static/`) and `/app` (`app/static_app/`) |
| Ops | Dockerfile, docker-compose, Railway (`railway.json`), CI in `.github/workflows/ci.yml` |

The system is a **deterministic** engine+agent pipeline — not LLM agents. "Agent" = a specialist component with veto rights (Allocation, Adversary) or read-only evidence (Research).

---

## 2. The cross-agent state flow

State is carried by **immutable, frozen Pydantic stage models** (`app/schemas/state_machine.py`). Each stage embeds the previous one as a typed `source` field, so a stage cannot be built out of order (constructing `OptimizedSignal` from a `DetectedSignal` raises `ValidationError`). This is the core "no stage can be skipped" guarantee.

```
LagEngine.scan ─▶ DetectedSignal
                      │  RiskEngine.vet
                      ▼
                  VettedSignal ──(veto_flag=True)──▶ VetoedSignal (terminal)
                      │  TaxEngine.optimize
                      ▼
                  OptimizedSignal
                      │  DecisionEngine.rank
                      ▼
                  RankedSignal
                      │  StateMachine.display  (gate: Impact≥20 AND Confidence≥60)
                      ▼
                  DisplayedItem ──▶ Allocation Agent veto? ─▶ Safety check ─▶ Adversary critique ─▶ XAI ─▶ persist
```

Orchestration lives in `app/services/pipeline.py` (`PipelineOrchestrator.generate`); the stage transitions themselves live in `app/engines/state_machine.py` (`vet → optimize → rank → display`). A parallel `app/services/war_room.py` re-runs the lifecycle to emit a human-readable per-agent transcript.

---

## 3. Exact typed hand-offs (the contracts Phase 1.2 will harden)

| Transition | Producer | Output type | Fields added (and their valid domain) | Consumer reads |
|---|---|---|---|---|
| Detect | `LagEngine.scan` | `DetectedSignal` | `depth∈[1,3]`, `divergence_pct∈ℝ`, `gross_gain_ils?`, `prior_taxable_income_ils≥0`, `loss_carry_forward_ils≥0`, `expected_return_pct?`, `volatility_pct?`, `liquidity_score?∈[0,100]` | Risk + Tax + Decision |
| Vet | `RiskEngine.vet` | `VettedSignal` | `probability_of_ruin?∈[0,1]`, `max_drawdown?∈[0,1]`, `volatility?≥0`, `veto_flag:bool`, `risk_critique:str` | Tax (guards on `veto_flag`) + Decision |
| Optimize | `TaxEngine.optimize` | `OptimizedSignal` | `net_gain_delta?`, `actual_tax_cost?≥0`, `tax_saved?`, `tax_deferred?≥0` | Decision (tax sub-score) |
| Rank | `DecisionEngine.rank` | `RankedSignal` | `impact_score∈[0,100]`, `confidence∈[0,100]`, `scores:ImpactScores(5×[0,100])`, `confidence_breakdown:ConfidenceBreakdown(4×[0,100])`, `urgency∈[1,100]`, `complexity_factor∈[1,2]` | Display gate |
| Display | `StateMachine.display` | `DisplayedItem` \| `None` | `path∈{Growth,Bulletproof}`, `title` | Allocation/Safety/Adversary |
| Allocation veto | `AllocationAgent.review_buy` | raises `VetoException` | blocks BUY into an overweight class | pipeline |
| Safety | `SafetyEngine.check` | `SafetyReport` | `verdict∈{ok,warn,block}`, `flags[]` | Adversary (`should_veto`) |
| Adversary | `adversary.critique` | `str` | natural-language red-team note | feed payload / war room |

### Scoring formulas (deterministic — to be surfaced verbatim in Phase 2.1)
- **Impact** = `(0.30·return + 0.25·tax + 0.25·risk + 0.10·liquidity + 0.10·conviction) / ComplexityFactor`, sub-scores clamped to 0–100. `ComplexityFactor ∈ {Trivial 1.0, Easy 1.25, Moderate 1.5, Difficult 1.75, Complex 2.0}` (`app/schemas/scoring.py`, `app/engines/scoring.py`, `decision_engine.py`).
- **Confidence** = `0.40·data_quality + 0.30·model_agreement + 0.20·historical_accuracy + 0.10·market_stability`.
- **Display gate**: `impact_score ≥ min_impact_score (20)` AND `confidence ≥ min_confidence (60)` → else "No Action Recommended".
- **Tax** (`tax_engine.py`): CGT = `cgt_rate·taxable_gain`; marginal surtax above `surtax_threshold_ils`; `losses_applied = min(carry_forward, gross_gain)`. All rates from `Settings`/`.env`.
- **Risk** (`risk_engine.py`): GBM Monte Carlo (`monte_carlo_runs`×`risk_mc_steps`); `P(ruin) = share of paths with max drawdown ≥ max_drawdown_cap`; veto if `volatility > volatility_cap` or `P(ruin) > ruin_probability_cap`.

---

## 4. Where the Adversary sits today (the Phase 1.3 gap)

`app/agents/adversary.py` exposes two functions:
- `critique(path, risk_critique, confidence, impact, safety)` → a single string, called **once**, on the **final displayed item** only.
- `should_veto(safety)` → escalates a Safety `block` verdict into a hard veto.

It does **not** examine the output of each agent as the pipeline advances. There is no per-stage invariant checking, no math-blind-spot detection between Risk→Tax→Decision, and no ability to halt mid-pipeline before a bad number propagates. **Phase 1.3 inserts an `Adversary.examine(stage)` checkpoint after every transition.**

---

## 5. Gaps vs. the requested features (confirmed by audit)

| Requested | Status in repo | Plan item |
|---|---|---|
| Strict cross-agent validation | Typed+frozen, but floats accept NaN/Inf, no `extra=forbid`, few range bounds | 1.2 |
| Adversary after every step | Final-word only | 1.3 |
| "Source Code & Data" accordion | Data exists server-side (XAI, scores, critique); not surfaced as a transparency panel | 2.1 |
| What-If sliders | Pipeline takes a settings/plan object; no live UI control | 2.2 |
| Brokerage sync (Plaid/Yodlee) | `BROKER_INTEGRATION_PLAN.md` design only; **no `app/brokers/` code**; existing plan targets Israeli brokers (order placement), Plaid/Yodlee = read aggregation | 3.1 |
| Fee/expense-ratio optimizer | **Absent** | 3.2 |
| Historical backtesting (validate beta) | `scenario_engine.py` does forward shocks; **no historical replay / beta validation** | 3.3 |

---

## 6. Risks & guardrails noted during audit
- **`strict=True` blast radius:** several engines pass `int` where a `float` field is declared, and construct stages positionally — turning on strict mode will surface these; 1.2 fixes call sites and keeps all 153 tests green.
- **Frozen models:** stages are immutable, so the Adversary cannot mutate a stage to attach a critique — 1.3 will carry critiques in a side-channel (a per-run examination log) rather than mutating frozen objects.
- **Determinism:** Risk uses a seedable RNG (`seed=7` in the pipeline) — keeps tests reproducible; the optional LLM Adversary layer must stay off the deterministic scoring path.
- **No secrets in repo:** tax/risk constants are all config-driven; this audit changes nothing about money movement.
