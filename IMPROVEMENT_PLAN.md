# InvestWise Pro — Improvement Plan (from APP_REVIEW.md)

**Goal:** turn the well-built *simulator* into a tool whose numbers you can trust, secured behind Google sign-in, on a path that scales to multiple users later.
**Status:** PLAN ONLY — nothing built yet. Approve / adjust, then I execute phase by phase (branch → tests green → push → you review), same cadence as before.
**Decisions locked:** Google login (you only now, multi-user-ready) · free US/global market data first · broker-CSV + better manual holdings · full phased roadmap.

---

## How each phase works
Every phase: its own branch, all 195+ tests stay green plus new tests, pushed to GitHub, synced to your folder, and a short review gate before the next. Where a phase needs something from you, it's called out in **▶ You provide** and the phase can't ship without it (but I can build/scaffold around it).

---

## Phase A — Secure the deployment with Google sign-in  *(do first — fast, protects real data)*
Your portfolio is currently world-readable on the public URL. This closes that and replaces password login with "Sign in with Google."

- Add Google OAuth (Authlib) `/auth/google/login` → `/auth/google/callback`; on success, issue the app's existing JWT/session for that identity.
- **Allowlist** via config `allowed_emails` — only `eran.ganot@gmail.com` now; adding a user later = add an email (no code change).
- Flip `REQUIRE_AUTH=true` in production so every read/write endpoint requires a logged-in, allow-listed user.
- **Multi-user ready by design:** the app already scopes data per `User → Entity → Account`, and `acting_user` already maps an identity (email) to a `User`. I'll audit every query to ensure it filters by the authenticated user, so flipping on multi-user later is config + a sign-up flow, not a rewrite.
- Keep the old password/JWT path available behind a flag for local dev/tests.

**▶ You provide:** a Google Cloud OAuth 2.0 **Client ID + Secret** and one **authorized redirect URI**. Steps:
1. Google Cloud Console → APIs & Services → OAuth consent screen → External, app name "InvestWise", add yourself as a test user.
2. Credentials → Create Credentials → OAuth client ID → Web application.
3. Authorized redirect URI: `https://investwise-pro-production.up.railway.app/auth/google/callback`.
4. Paste the Client ID + Secret to me (or set as Railway vars `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`). I'll also set `SESSION_SECRET`, `ALLOWED_EMAILS`, `REQUIRE_AUTH=true`.

*Verification:* logged-out users get a login screen; only your Google account is admitted; a non-allow-listed Google account is rejected.

---

## Phase B — Real market data (US/global prices + FX)  *(the #1 credibility upgrade)*
Replace the synthetic `builtin` provider (prices from a hash of the ticker) with a real one behind the existing `MarketDataProvider` / `FXProvider` interfaces — so nothing downstream changes.

- **Prices (free, US/global EOD):** Stooq (no key) as default, with an Alpha Vantage / Twelve Data adapter as a keyed alternative. Daily close + recent history.
- **FX (free, no key):** Frankfurter (ECB) or exchangerate.host for USD/ILS/EUR.
- Wrap calls in the existing resilience tier (cache → rate-limit → breaker → retry); cache aggressively (EOD data changes once/day).
- Keep `builtin` as the offline/test provider; `MARKET_DATA_PROVIDER=stooq` in prod.
- **TASE gap:** free tiers barely cover Tel Aviv. For Israeli holdings, fall back to your manually-entered price (clearly labelled) until you decide to pay for TASE — I'll flag each holding's data source + "as of" time.

**▶ You provide:** nothing for the no-key default. Only if we pick a keyed provider: one free API key.

*Verification:* a known ticker (e.g. AAPL) returns a real, current-ish close; USD/ILS matches reality within rounding; a "data source · as of" badge shows in the UI.

---

## Phase C — Make the app honest about what's real  *(correctness/trust)*
- Add a prominent **"Live data" vs "Illustrative"** banner driven by whether a real provider is active per holding.
- Stop folding *unknown* inputs into a fake `25` sub-score — propagate "unknown" and lower **confidence** instead, so sparse data reads as "less certain," not "pessimistic."
- Re-frame the contrived **Lag "divergence" signal**: either repurpose it to a *real* phenomenon (dual-listed TASE/NYSE or ADR-vs-local divergence, which needs two real quotes) or demote it and lead the feed with the sound engines (allocation, tax, risk). My recommendation: demote it now, revisit if you want the dual-listing version.
- Replace or label the hardcoded "what's moving" economic events (optional: a free economic-calendar/news feed).

*Verification:* tests prove unknown inputs reduce confidence rather than inventing scores; the UI never presents synthetic numbers as live.

---

## Phase D — Portfolio-level risk  *(biggest correctness upgrade after real data)*
Today risk is per-trade with no correlations. With real price history (Phase B) we can do it properly.

- Build a returns history → **covariance/correlation matrix**; compute **portfolio volatility, VaR & CVaR**, and **real beta** vs a benchmark.
- Feed the **backtest beta-validation** real beta (removes the current circularity).
- Add **goal-based portfolio Monte Carlo**: "probability of reaching ₪X by <date>" using the whole book, not single positions.

**▶ You provide:** your goal target + date (you may already have these in Plan).

*Verification:* a concentrated book shows higher portfolio vol than a diversified one; VaR/CVaR sane on known inputs; goal probability moves with contributions/risk.

---

## Phase E — Performance history & benchmarking
- Persist **daily snapshots** (NAV, holdings, WHS) via the scheduler already running; compute **time-weighted return** and P&L over time.
- **Benchmark** your portfolio vs S&P 500 / TA-125 / a 60-40, after fees & tax. Charts on the dashboard.

*Verification:* after N days, a real performance curve vs benchmark renders; numbers reconcile with holdings.

---

## Phase F — Real holdings in
- **Broker CSV / statement importer** with configurable column mapping (most practical for Israeli brokers) — captures quantity, cost basis, fees/expense ratio, asset class.
- **Manual-entry improvements:** bulk edit, fund fee field, asset-class picker (some already added in Phase 3.2).
- Plaid (US) stays optional via the scaffold for any US accounts.

**▶ You provide:** a sample export (CSV/statement) from your broker so I match its exact format. Strip account numbers — I only need the column layout.

*Verification:* your real holdings import cleanly and flow through the pipeline.

---

## Phase G — Engineering hygiene
- **Test isolation:** per-test transaction-rollback / fresh-DB fixture in `conftest.py` (kills the shared-SQLite state leaks we hit).
- **Migrations in prod:** run Alembic on deploy, set `AUTO_CREATE_TABLES=false` in production (stops schema drift).
- **Performance:** cache Monte Carlo by (μ, σ, caps); optionally move heavy compute to the **Celery worker + Redis** you already have provisioned (this is what the `redis-volume` is for) so the API stays snappy.
- **Consolidate** the two dashboards (`/dashboard` legacy → delete; keep `/app`).

**▶ You provide:** decision on async jobs — turn on Redis/worker now or defer (the volume is ready either way).

*Verification:* tests pass in isolation and in any order; prod boots on migrations; p95 latency drops with caching.

---

## Phase H — Professional polish & differentiation
- **Weekly digest** ("state of your wealth + top 3 actions"), written by the now-working **Gemini** narrative, delivered on a schedule.
  **▶ You provide:** delivery channel — an email service (e.g. a free SendGrid key) or a Telegram bot token.
- **Exportable report:** a clean PDF/Excel "wealth report."
- **Natural-language Q&A** over your portfolio (Gemini + deterministic grounding): "what's my biggest tax-loss opportunity and why?"
- **Real tax-lot tracking** (per-lot basis, holding periods) + Israeli **CPI/inflation indexing** for the tax engine.
- **Mobile / PWA** pass; optional front-end componentization if it keeps growing.

---

## Recommended order & effort
| # | Phase | Impact | Effort | Needs you |
|---|---|---|---|---|
| 1 | A — Google sign-in | High (privacy) | Low | OAuth creds |
| 2 | B — Real market data | Very high | Med | — (or 1 free key) |
| 3 | C — Honesty cleanup | High | Low | — |
| 4 | D — Portfolio risk | High | Med | goal+date |
| 5 | E — History & benchmark | Med-High | Med | — |
| 6 | F — Holdings import | High (for you) | Med | sample CSV |
| 7 | G — Hygiene | Med | Low-Med | Redis decision |
| 8 | H — Polish/AI | Med | Med-High | email/Telegram |

**My recommendation:** do **A → B → C** first (secure + real + honest = the app becomes trustworthy), then **D**, then pick from E/F/G/H by what you'll actually use. F (your real holdings) can jump earlier if you want to analyze your actual portfolio sooner.

---

## Everything I need from you (consolidated)
1. **Google OAuth** Client ID + Secret (+ I set the redirect URI above). *(Phase A — blocking for auth)*
2. (Optional) a **free market-data API key** if you'd rather use a keyed provider than the no-key default. *(Phase B)*
3. Your **goal amount + target date** if not already in Plan. *(Phase D)*
4. A **sample broker CSV/statement** (column layout only). *(Phase F)*
5. **Redis/worker on or off** for async jobs. *(Phase G)*
6. A **digest delivery channel** — email key or Telegram bot token. *(Phase H)*

Reply with what to start on (default: Phase A) and send the Google OAuth credentials whenever ready.
