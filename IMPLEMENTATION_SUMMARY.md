# InvestWise Pro — Implementation Summary (audit + 3-phase build)

**Branch:** `feat/audit-phase1` (pushed to `github.com/eranganot/Investwise-pro`) · **Date:** 2026-06-14
**Tests:** 153 baseline → **194 passing** (+41), deterministic across runs.
**Guardrails honoured:** all work on a feature branch; every constant config-driven; no money moved; broker stays flag-off; credentials never persisted; "not financial advice" posture intact.

## Phase 1 — Core hardening
- **1.1 Audit** — `ARCHITECTURE_AUDIT.md`: tech stack, cross-agent state flow, exact typed hand-offs.
- **1.2 Strict cross-agent validation** — `app/schemas/validation.py` (constrained types: `FiniteFloat`, `NonNegFloat`, `UnitFraction`, `Score`, `ComplexityFactor`). Internal stage models are now `strict + extra="forbid" + frozen`; `assert_handoff` guards every transition; veto-must-have-a-reason invariant. Input boundaries stay coercible by design. (+16 tests)
- **1.3 Adversary after every step** — `app/agents/adversary.py` deterministic per-stage examiners (implausible Sharpe, risk-output inconsistency, net-after-tax > gross, negative tax-saved, impact-score drift). `StateMachine.cross_examine` routes state through the Adversary after each agent; a `BLOCK` becomes a hard veto before the next agent runs. **Optional LLM narrative behind an off-by-default toggle** (`adversary_llm_enabled` + `ANTHROPIC_API_KEY`) — deterministic checks always run, LLM only narrates. Surfaced in War Room + feed payload. (+6 tests)

## Phase 2 — UI transparency & interactive controls
- **2.1 "Source Code & Data" accordion** — every recommendation carries an `audit_trail`: raw portfolio data, the deterministic formula with the real numbers substituted, and the Adversary critique. Collapsible accordion in `app/static_app/index.html`. (`app/services/audit_trail.py`, +3 tests)
- **2.2 Interactive What-If sliders** — `POST /api/v1/whatif` re-runs the pipeline with overridden initial state for **Risk Tolerance**, **Tax-Loss-Harvesting target**, and **Expected Market Drawdown**; live slider panel re-evaluates the risk profile, vetoes, scenario loss and TLH on the fly. (`app/services/whatif.py`, +4 tests)

## Phase 3 — Advanced wealth optimization
- **3.1 Brokerage sync (scaffold)** — vendor-agnostic `AggregatorProvider` (`app/brokers/`): deterministic **mock** adapter + **Plaid/Yodlee stubs** (inactive until `BROKER_ENABLED=true` + keys). `broker_connections` table + migration `0006`; `POST /api/v1/broker/connect` & `/sync` reconcile holdings into the portfolio via the existing intake path. Credentials are never stored — only a `credential_ref`. (+5 tests)
- **3.2 Fee & expense-ratio optimizer** — `app/engines/fee_engine.py` scans high-fee holdings and suggests an equivalent **low-fee, highly-liquid index** (editable `LOW_FEE_ALTERNATIVES` map) with annual ₪ saved; `FeeAgent` surfaces `FEE_OPTIMIZATION` recs (with audit trail) + `GET /api/v1/fees`. Intake now captures `expense_ratio_pct`. (+3 tests)
- **3.3 Historical backtesting engine** — `app/engines/backtest_engine.py` replays the book against bundled **2008 GFC / 2020 COVID / 2022 bear** series, computes structural beta + realized drawdowns, and **validates the Risk Agent's volatility-implied beta** (flags divergence beyond `backtest_beta_tolerance`). `POST /api/v1/backtest` + a `risk_validation` block on `/recommendations`. (+4 tests)

## New configuration toggles (all in `app/core/config.py`, env-overridable)
| Setting | Default | Purpose |
|---|---|---|
| `adversary_enabled` | `True` | route every stage through the Adversary |
| `adversary_enforce_veto` | `True` | a BLOCK finding becomes a hard veto |
| `adversary_llm_enabled` | `False` | optional LLM narrative (needs `ANTHROPIC_API_KEY`) |
| `adversary_llm_model` | `claude-sonnet-4-6` | model for the narrative |
| `broker_enabled` | `False` | gate real aggregators (mock always works) |
| `aggregator_provider` | `mock` | `mock` / `plaid` / `yodlee` |
| `fee_high_threshold_pct` | `0.50` | flag holdings above this expense ratio |
| `backtest_beta_tolerance` | `0.25` | beta-divergence flag threshold |
| `backtest_market_vol_pct` | `16.0` | market vol for the implied-beta calc |

## New endpoints
`POST /api/v1/whatif` · `POST /api/v1/broker/connect` · `POST /api/v1/broker/sync` · `GET /api/v1/fees` · `POST /api/v1/backtest`

## Run it
```
pip install -r requirements-dev.txt
pytest -q                         # 194 passing
uvicorn app.main:app --reload     # API + /app dashboard
```

## Open follow-ups (your call)
- Pick the live brokerage direction (Plaid vs Yodlee vs the existing Israeli-broker order path) and provide sandbox keys to activate beyond mock.
- Provide an `ANTHROPIC_API_KEY` if/when you want to switch the LLM Adversary narrative on.
- Optionally replace the bundled backtest series with a real market-data vendor feed.
- Merge `feat/audit-phase1` to `main` once reviewed; **rotate the GitHub token shared in chat.**
