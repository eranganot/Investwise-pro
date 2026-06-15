# InvestWise Pro — Honest Review

**Context:** personal wealth tool (just you). Lenses weighted: financial correctness & trust, UX & polish, architecture & scalability. Reviewed the full repo (13 engines, 3 agents, 12 services, providers, worker, ~600-line dashboard, 195 tests) and the live Railway deploy.

---

## TL;DR

You've built something genuinely impressive as an **engineering artifact**: clean architecture, a type-safe pipeline, 195 passing tests, CI, transparency features, and a plain-language UI. It is *not yet* a tool you should trust to make real money decisions, for one reason above all: **it runs on synthetic data and a partly contrived signal model.** The scaffolding is excellent; the "intelligence" inside is mostly illustrative. The single highest-leverage move is to replace the made-up inputs with real ones.

Rated honestly: **architecture 8.5/10, engineering hygiene 8/10, UX 7/10, financial substance 4/10** (because of synthetic data + heuristic signals, not because the code is bad).

---

## What's genuinely good

- **Type-safe state machine.** `DetectedSignal → Vetted → Optimized → Ranked → Displayed`, each embedding the prior as a typed `source`, frozen + strict. A stage literally can't be skipped or drift. This is better than a lot of production fintech code.
- **Clean layering & DI.** `schemas / engines / agents / services / api / providers / worker` with constructor-injected `Settings`. Easy to test, easy to swap a provider.
- **Transparency is a real differentiator.** The Adversary cross-examination, the "Source Code & Data" accordion (raw data + substituted formulas + critique), and the War Room transcript make the system *legible*. Most consumer finance apps are black boxes; this isn't.
- **Tax engine is the strongest domain piece.** Real Decimal money math, CGT + marginal surtax above threshold, loss carry-forward with a proper counterfactual for `tax_saved`. Config-driven rates.
- **Testing & CI.** 195 tests, deterministic (seeded Monte Carlo), GitHub Actions running SQLite + Postgres + ruff. Worked numeric examples in the tax/risk tests.
- **Plain-language UX intent.** The `SIMPLIFY_PROPOSAL.md` thinking ("answer first, details on tap") and the `/app` dashboard show real product sense.
- **Honesty primitives already exist.** "Awaiting Data" instead of fabricated numbers, and disclaimers that it's not advice. Good instincts.

---

## Financial correctness & trust — the hard truths

1. **The market data is fake.** `providers/builtin.py` generates prices from a hash of the ticker (`50 + sha1(ticker) % 45000/100`), FX is 3 hardcoded pairs, and the "Research" economic events are a static list. So every quote, divergence, and "what's moving" item is synthetic. → *Fix: wire one real provider behind the existing `MarketDataProvider` interface (e.g. a free-tier vendor for EOD prices + FX). The abstraction is already there; this is the #1 unlock.*
2. **The core "alpha" signal is contrived.** The Lag engine's edge is `divergence = (listing_price − spot_price)/spot_price` with a Depth 1–3 label. For almost every real asset, listing == spot, so the signal is ~0; "Depth" is hand-assigned, not derived. This isn't a recognized market-microstructure edge. → *Fix: either ground it in a real phenomenon (ADR vs local-listing arbitrage, index-vs-constituent divergence, dual-listed TASE/NYSE names) with real dual quotes, or drop it and lead with allocation/tax/risk, which are sound.*
3. **Risk is per-position, not portfolio.** The Monte Carlo simulates one asset's GBM path; "probability of ruin" is for that single trade, with **no correlations or covariance** across holdings. Real portfolio risk is dominated by how positions move together. → *Fix: compute a covariance matrix from historical returns; report portfolio volatility, VaR/CVaR, and a real beta. This also makes the backtesting beta-validation non-circular.*
4. **Missing inputs quietly bias the output.** When `expected_return_pct` is absent it defaults to 0; unknown sub-scores default to 25 (`score_unknown_default`). A portfolio with sparse data gets systematically pessimistic, arbitrary scores. → *Fix: propagate "unknown" explicitly (don't fold into a number), and lower confidence rather than inventing a 25.*
5. **Scoring constants are arbitrary.** `Impact = (0.3R+0.25T+0.25Risk+0.1Liq+0.1Conv)/complexity`, `decision_return_scale=5`, complexity factors 1.0–2.0 — all reasonable-looking but unvalidated magic numbers. They produce a number that *looks* precise (44.2/100) but isn't empirically anchored. → *Fix: be honest in the UI that these are heuristic priorities, not measured probabilities; consider deriving weights from your own accepted/ignored history (the learning loop already exists).*
6. **Tax model is simplified.** It omits Israeli CPI/inflation indexing of cost basis, instrument-specific rates, and exemptions. Good enough for planning estimates, but don't file on it. → *Fix: add inflation indexing and a clear "estimate — confirm with accountant" stance (already partly there).*
7. **Scenarios & backtests are illustrative.** Scenario shocks are fixed tables; the backtest series for 2008/2020/2022 are hand-entered approximations and the model is single-factor (`portfolio = beta × market`). Fine as "what-if" intuition, misleading if read as forecasts. → *Fix: label them clearly and, later, drive them from real historical returns.*

**Bottom line on trust:** today the app is a beautifully instrumented *simulator*. Treat its numbers as illustrative until real prices and real holdings flow in.

---

## UX & professional polish

- **Two dashboards exist** (`/dashboard` legacy + `/app`). Consolidate to one; delete the old.
- **Single 600-line `index.html` with inline JS.** Fine at this size and dependency-light, but it has no build step, no component reuse, and no front-end tests. As features grow this will get painful.
- **Strengths:** plain-language makeover, the collapsible audit accordion, the What-If sliders, the War Room narrative, sensible empty/loading states, ₪ formatting.
- **Gaps to close:** a *prominent* "illustrative data" banner until real data is wired (trust!); consistent error states; mobile layout pass (it's close); a single coherent "Today → Why → What to do" spine per the SIMPLIFY proposal; surfacing the new Gemini narrative and the backtest/fees panels in the UI (the endpoints exist but aren't all on screen yet).

---

## Architecture & scalability

- **Test isolation is the weakest engineering spot.** Tests share one SQLite file and the singleton SuperAdmin, so state leaks between tests (we hit exactly this — a synced GOLD position broke a delete test). → *Fix: a per-test transaction-rollback fixture or a fresh DB per test in `conftest.py`.*
- **`auto_create_tables=True` in production.** The deploy logs even warn about it. You have Alembic — use it on deploy and turn auto-create off in prod to avoid schema drift.
- **Heavy compute in the request thread.** 10k-path Monte Carlo runs synchronously inside the HTTP handler. Fine for one user, but it adds latency and there's no caching. → *Fix: cache risk results by (vol, μ, caps); or move to the Celery worker you've already scaffolded (this is what the Redis question was really about).*
- **Security on a public URL.** `require_auth=False` and `API_KEY` unset means anyone who learns the Railway URL can read and modify your portfolio. Even for a personal tool that's your real financial data exposed. → *Fix: set a single-user password / API key, or put Railway behind access control. Low effort, high value.*
- **Provider/worker abstractions are ready but idle.** Real market-data adapters and the async worker are stubbed/eager. Good seams, just not filled.
- **Magic numbers** are config-driven (good) but undocumented as to *why* each value. A short "modeling assumptions" doc would help future-you.

---

## Features that would make it meaningfully better (prioritized)

**Tier 1 — turns the simulator into a real tool**
1. **Real market data** behind the existing interface (EOD prices + FX). Single biggest credibility jump.
2. **Real holdings sync** — finish the Plaid/broker scaffold, or a clean broker-CSV import, so it analyzes *your* portfolio, not demo tickers.
3. **Portfolio-level risk** — covariance/correlation, portfolio volatility, VaR/CVaR, real beta from returns.
4. **Performance history** — persist daily snapshots; show actual P&L, time-weighted return, and progress vs goal over time (you have the tables; add the time series).

**Tier 2 — professional polish**
5. **Single-user auth** for the public deployment.
6. **Scheduled digest** — a weekly "state of your wealth + top 3 actions" email/Telegram (pairs perfectly with the scheduler already running, and the Gemini narrative you just enabled).
7. **Benchmarking** — compare your portfolio vs TA-125 / S&P 500 / a 60-40, after fees and tax.
8. **Exportable reports** — a clean PDF/Excel "wealth report" (you literally have skills for this).

**Tier 3 — genuinely differentiated**
9. **Natural-language Q&A over your portfolio** using the Gemini integration ("what's my biggest tax-loss opportunity and why?") grounded in the deterministic engines.
10. **Real tax-lot tracking** (per-lot cost basis, holding periods, wash-sale-style windows) to make the tax engine truly actionable.
11. **Goal-based Monte Carlo** at the *portfolio* level (probability of reaching your target wealth by date X) — you have the MC machinery; aim it at the goal, not single trades.

---

## What I'd do next, in order
1. Set a password on the public deployment (30 min, protects real data).
2. Wire one real market-data + FX provider (turns everything from fake to real).
3. Fix test isolation + switch prod to Alembic (engineering hygiene).
4. Add portfolio-level risk (correlations) — the biggest correctness upgrade.
5. Then pick from Tier 2/3 based on what you'd actually use.

The foundation is strong enough that all of the above are additions, not rewrites — which is the real compliment here.
