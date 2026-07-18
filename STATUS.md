# InvestWise Pro — Status

_Last updated: 2026-07-13 by Claude (weekly status review)._
_Seeded from git history + prior transcripts._

## Now (on main, CI green)
- **Trading rules engine**: stop-loss / take-profit / trailing-stop / price alerts / buy-the-dip / max-weight, each raising alerts + to-dos, with a management UI. Triggered rules surface in the daily digest and as a Today-screen alert banner — HEAD `67f7da3`.
- **Actionable Accept**: the Today-view "Accept" on a recommendation now really executes it — sells credit net-of-CGT proceeds to a visible CASH holding, fee-swaps replace the fund at a live price, trims credit the sold portion; Accept returns a "what changed" summary the UI shows.
- **Actionable trend cards + suggested rules**: the momentum downtrend/uptrend cards (previously `apply: none`, so Accept did nothing) now arm a concrete, one-click discipline rule — downtrend → stop-loss at a volatility-derived price; uptrend → trailing stop (+ a max-weight cap on already-large positions) — which becomes a real alert + Today to-do; Accept returns an "Armed …" summary. New per-holding **Suggested rules** panel (`GET /api/v1/rules/suggestions`) proposes a stop-loss / trailing-stop / take-profit / max-weight set with concrete, vol-derived levels; add each or "arm all".
- **Price freshness**: scheduled 30-min auto-reprice of all holdings (FMP → Yahoo fallback) with a truthful data-source/status label (no more silently-stale prices).
- **Markets + AI layer**: Yahoo futures with a risk-on/off regime feeding the agents; Gemini portfolio / holding / macro summaries + grounded deep-research-per-holding; new Markets tab and AI cards.
- **PWA + push**: installable app (manifest, service worker, mobile bottom-nav); web push for recommendations, risk alerts, price moves and daily digest; server-side recommendation dismissals with 7-day TTL so push and Today stay in sync.
- ILS currency normalization across valuation + display; goal target, projections, and allocation mix all derived from live FX-normalized NAV (kept aligned).
- Opportunity screener agent + fundamentals layer + expanded holdings recommendations; expanded commodity catalog (advisor reasons grounded, never invents numbers, "Not financial advice").
- Legacy "Advanced" dashboard restored behind the auth gate.
- Test suite ≈259 passing (5 new for war-room timestamps + benchmark-lag & commodity recs); lint (ruff) + test + test-postgres CI jobs green.

## ⚠️ Uncommitted work on disk (2026-07-18) — Phase 1, needs a commit from Windows
Phase 0 shipped (`defe8ec`, CI green, deployed). **Phase 1 (cash) is written and tested but NOT
committed.** The sandbox cannot commit here: a stale `.git/index.lock` it lacks permission to
remove, plus bash/git serving a *stale* view of `app/static_app/*`. Verify `git status` lists all
7 files before committing, and **never `git add -A`** — `frontend/node_modules` is tracked and
full of CRLF noise.

Files changed: `app/services/intake_service.py`, `app/api/routes/intake.py`,
`app/api/routes/plan.py`, `app/services/recommendations.py`, `app/static_app/index.html`,
`app/static_app/sw.js` (`iw-v4`→`iw-v6`), `tests/test_cash.py` (new),
`tests/test_accept_honesty.py` (new).

**Commit with `git commit -F COMMIT_MSG.txt`** — a PowerShell here-string (`@"…"@`) failed on
2026-07-18: PowerShell didn't parse it, git took each line as a pathspec, the commit never
happened and the follow-up push reported "Everything up-to-date". Don't use here-strings here.

## Next (confirm priority)
- **Phase 1–4 of `PLAN_2026-07-18_alignment.md`** — cash as a first-class citizen; grounding the
  war-room signals in live prices and unifying them with Today; funding/sell recommendations;
  strategy differentiation. Decisions locked: signals grounded before wiring to Today; cash floor
  = % of NAV by objective; all candidates eligible for buy signals; funding = cash first, then
  worst-fit holding.
- Broker integration — see `BROKER_INTEGRATION_PLAN.md` (unstarted; confirm it's the next initiative).
- Live-verify the trading-rules alerts + Today banner fire correctly against real triggers post-deploy.

## Pending QA / open questions
- **Notification ↔ Today alignment (2026-07-12):** run `qa/QA-2026-07-12-notification-alignment.md` on the Pixel 9 post-deploy (PWA update path: SW `iw-v2`→`iw-v3`).
- Confirm on live: accepting a tax-loss/sell rec removes the holding and adds a CASH position for the net proceeds; fee-swap replaces the fund; trim credits cash. (Accept-executes fix.)
- Confirm triggered trading rules render correctly in both the daily digest and the Today-screen banner after deploy.
- Confirm the 30-min reprice + data-source label shows fresh prices (FMP primary, Yahoo fallback) in production.
- Confirm the live advisor answer for "should I add commodities?" reads as a balanced for/against after deploy.

## Known sharp edges
Postgres per-test isolation fixture (throwaway NullPool engine, own event loop); ruff strictness. Windows mount can serve truncated views of file-tool edits — verify large writes on the mount (see `safe-windows-edits`). See CLAUDE.md.

## Changelog (newest first)
- 2026-07-18 — **Stale-shell fix + recommendation coherence pass** (on disk, uncommitted).
  (1) **Deploys weren't reaching clients.** Diagnosed live via Chrome against production: `actBtn`
  and `restoreIgnored` were `undefined` in the running page while the cache key was already
  `iw-v6-shell` — i.e. the SW had bumped version but cached the *old* `index.html` under the new
  key. Two causes: `cache.add(url)` fetches *through* the browser HTTP cache (now
  `new Request(url, {cache:'reload'})`), and `StaticFiles` sent no `Cache-Control` on the shell
  (now a `_NoCacheShell` subclass sending `no-cache, must-revalidate` for `*.html` and `sw.js`).
  Shell HTML is also network-first in the SW rather than cache-first. **The reported "Ignore does
  nothing / cards don't disappear" bugs were this — verified working once the client was forced to
  the deployed build (cards 8→7, dismissals 1→2).** SW `iw-v6`→`iw-v7`.
  (2) **`_reconcile` pass** — the ~10 agents never consulted each other, so production showed
  "Sell Cash" + "Buy Equities" (two legs of one rebalance, both applying the *identical* action),
  "Put idle cash to work" (a third card for the same surplus) and "Markets look risk-off" advising
  a 5-10% *increase* in cash — four mutually impossible cards. Now: multi-leg rebalances merge into
  one card; geographic + currency concentration merge when they describe the same exposure;
  cash-drag is dropped when a rebalance already redeploys the cash; and a risk-off macro card
  alongside a pending rebalance is reworded from "raise cash" to "phase the rebalance in" instead
  of contradicting it. +10 tests (`test_reconcile.py`, `test_shell_cache_headers.py`).
- 2026-07-18 — **Accept no longer pretends to act** (on disk, uncommitted). Reported live: tapping
  Accept on "You're trailing SPY" changed nothing, said "Done — applied.", and parked the card in
  the ignored list. Cause: 10 of the card types (benchmark lag, commodity sleeve, geo/currency/
  liquidity diversification, holding verdicts, sector concentration, cash drag, yield lift,
  contribution) carry `apply.kind == "none"` — plus the macro risk-off card had no `apply` key at
  all — so `apply_recommendation` fell through every branch and the route dismissed them anyway.
  Now: a single `_ACTIONABLE_KINDS` set is the source of truth; every card ships an `actionable`
  flag; the UI tags each card "⚡ The app can do this for you" vs "💡 Guidance — you act on this
  yourself" and swaps the button between **Accept** and **Mark as done**, with confirm/result text
  that admits nothing was traded. A 404 from accept now **removes the card outright** instead of
  leaving it or filing it under ignored. SW `iw-v5`→`iw-v6`. +5 tests (`test_accept_honesty.py`).
  Phase 3's funding work will convert several of these advisory cards into genuinely actionable ones.
- 2026-07-18 — **Phase 1: cash as a first-class holding** (on disk, uncommitted — see above).
  Cash previously materialised *only* as a side effect of accepting a sell, so money already held
  was untrackable and the donut read 100% Equities for a book with real liquidity. Adds
  `set_cash`/`get_cash` + `GET|POST /api/v1/portfolio/cash` (set or adjust; adjust accepts a
  negative for withdrawals and floors at zero; set rejects negatives), a "💵 Set liquid cash"
  control and modal on Holdings, and a pinned green-bordered Cash row showing % of portfolio.
  `/api/v1/mix` now always emits a Cash slice (even at 0%) plus `cash_ils`, so "no cash" is
  visually distinct from "cash not tracked". Cash carries `liquidity_score: 100` and
  `volatility_pct: 0` via a shared `CASH_META`, so it lifts the liquidity health score instead of
  falling back to the generic 70. **Latent bug fixed:** `credit_cash` stored the full proceeds as
  the *per-share* `cost_basis`, so once Phase 0 surfaced invested totals, ₪2,500 of cash reported
  as ₪6.25M invested — basis is now 1.0, and existing rows self-heal on the next credit/set.
  Also FX-normalized the cash-drag rec's weight (raw numerator over an ILS-normalized NAV).
  SW `iw-v4`→`iw-v5`. +7 tests (`test_cash.py`); 60 affected-surface tests green, no new ruff.
- 2026-07-18 — **Phase 0: recommendation alignment groundwork** — shipped as `defe8ec`, CI green,
  Railway deploy Active.
  (1) **FX bug fixed**: `market_impact.annotate` computed exposure as `qty × price` with no FX rate
  while every other surface is ILS-normalized — the "What's moving" panel claimed events touched
  "100% of your portfolio (₪5,680)" for a book worth ₪17,306. Now uses `fx_rate(price_currency(...))`
  like `current_mix`; +2 regression tests. (2) **Silent failures instrumented**: the four bare
  `except: pass` blocks in `build_recommendations` (risk alerts, performance/benchmark, market
  regime, trading rules) now log with `exc_info` and report a `degraded` list to the client, so a
  missing card is distinguishable from "nothing to do". (3) **Honest empty state**: Today now
  separates "nothing to do" from "you ignored N suggestions", surfaces a warning when an agent
  failed, and adds a **Show ignored** control backed by a new `POST /api/v1/recommendations/restore`
  — an Ignore was previously a one-way door for 7 days with no way to see what was hidden.
  (4) **Total portfolio value** gains invested ("you put in"), absolute + % gain, and a liquid-cash
  line; `/api/v1/portfolio` now returns `invested_ils`, `gain_ils`, `gain_pct`, `cash_ils` and
  per-position `invested_ils`/`gain_ils`. SW cache bumped `iw-v3`→`iw-v4`. +4 tests
  (`test_portfolio_totals.py`). 56 affected-surface tests pass locally; no new ruff errors
  (verified by diffing lint output against HEAD); **full suite still gated by CI on push**.
- 2026-07-14 — **War-room timestamps + benchmark-lag & commodity recommendations.** (1) The Agents war room now stamps each run: an 'Analyzed at <date · time>' header plus a per-decision time on every session card (`build_war_room` returns `generated_at`; each session carries `decided_at`). (2) New performance-driven card in Today's What-to-do-now: when the portfolio trails its benchmark by >3% the engine surfaces a grounded 'You're trailing <benchmark>' improvement rec (real excess-return number, points at laggards/fees/drift). (3) Commodities now surface as holdings advice — an 'Add a commodities sleeve' card fires when under-allocated vs the objective's commodity target, naming concrete screener-ranked picks, and `buy_ideas` now includes commodity picks alongside equities. +5 tests (`test_recs_extras.py`).
- 2026-07-14 — **Trading-rules UI redesign (grouped by holding).** The rules list was a flat stack of fat cards — ticker repeated on every row, same-holding rules scattered, no colour coding, duplicates unflagged. Rebuilt as a `Positions | Rules` segmented sub-tab inside Holdings (no new bottom-nav tab): rules now group under one card per holding (ticker + live price header), each rule a compact colour-coded row (stop-loss red / take-profit green / trailing blue / max-weight amber) with a distance-to-trigger bar. Adds filter chips (all/triggered/armed/paused), sort (closest/holding/type), duplicate detection with a one-click remove, and triggered rules pinned + highlighted on top of their group (and their group floated first). Count badge on the Rules tab; Today's rule-alert banner deep-links straight into the Rules pane. Client-side only (regroups the existing `/api/v1/rules` payload) — no backend change.
- 2026-07-14 — **Actionable trend cards + per-holding suggested rules.** The downtrend/uptrend momentum cards were advice-only (`apply: none`) — Accept did nothing user-visible. They now arm a concrete rule: downtrend → stop-loss at a volatility-derived price (≈8–15% below today); uptrend → trailing stop (10–20%) plus a max-weight cap when the name is already a large slice. Accept returns an "Armed …" summary; the rule then fires the normal alert + Today to-do. Added `stop_buffer_pct` (levels grounded in each holding's own realized volatility, never invented), a `create_rules` apply-kind, `rules_service.suggest_rules_for_holdings` + `GET /api/v1/rules/suggestions`, and a "Suggested rules" UI panel (add each / arm-all). +5 tests (`test_trading_rule_suggestions.py`); changed surfaces green locally, full suite gated by CI on push.
- 2026-07-13 — Weekly status review: ✅ **the previously-uncommitted Accept-executes + notification-alignment/why-impact work is now committed** — HEAD `6036e61`→`67f7da3` ("Align push notifications with Today actions + explain recommendations", 2026-07-12), working tree now clean. Resolves last week's uncommitted-work flag. The Pending-QA items below still need live Pixel 9 verification post-deploy.
- 2026-07-06 — **Accept now executes recommendations.** The Today-view "Accept" applied nothing user-visible: sells just deleted holdings (value vanished) and fee/other cards were `apply:none`. Now sells credit net-of-CGT proceeds to a visible **CASH** holding (liquidity you can see/redeploy), `trim` credits the sold portion, and fee-swap cards actually sell the high-fee fund and buy the cheaper equivalent at a live price (falls back to cash if unpriceable). Accept returns a "what changed" summary the UI now shows. Tests: sell→cash added.
- 2026-07-12 — **Notification ↔ Today alignment + why/impact recommendations.** Root cause of "push says do X, app says nothing to do": the app hid cards via a *permanent* `localStorage` list while server dismissals (which gate push) expire after 7 days — so a resurfaced item re-notified while the app hid it forever. Replaced the permanent list with a TTL-matched (7-day) local store keyed by `iw_snoozed_v2`; the server dismissal is now the single source of truth. Notifications are now categorised `action` (maps 1:1 to a Today card) vs `info` (price moves + weekly digest, reworded as FYI, `silent`/no-nag in the SW). Added actionable region/currency/liquidity diversification cards so every pushed alert maps to a card. Every recommendation now carries plain-language `why` + `impact` (rendered in the card); the goal-gap contribution card, the digest and the home "Where this could end up" panel now share one Monte-Carlo projection instead of diverging. SW cache bumped `iw-v2`→`iw-v3`. Ships alongside the previously-uncommitted Accept-executes work.
- 2026-07-06 — Weekly status review: caught STATUS up to 4 shipped commits it was missing (2026-06-28/29). HEAD `8165628` → `6036e61`.
- 2026-06-29 — **Trading rules + digest/banner.** Stop-loss/take-profit/trailing/price-alert/buy-dip/max-weight rules with alerts, to-dos and a management UI; triggered rules surfaced in the daily digest and a Today-screen alert banner.
- 2026-06-29 — **Stale-price fix.** Scheduled 30-min reprice of all holdings (FMP → Yahoo fallback) + truthful data-source/status label.
- 2026-06-29 — **Markets + AI.** Yahoo futures with risk-on/off regime feeding the agents; Gemini portfolio/holding/macro summaries + grounded deep-research-per-holding; Markets tab and AI cards.
- 2026-06-28 — **PWA + push notifications.** Installable app (manifest, service worker, blue-bars icons, mobile bottom-nav); web push for recommendations, risk alerts, price moves and daily digest; price-provider hardening (FMP stable API + keyless Yahoo fallback + transparent errors); server-side recommendation dismissals with 7-day TTL.
- 2026-06-22 — STATUS.md + CLAUDE.md seeded.
- (prior) — NAV/goal alignment, FX normalization, opportunity screener + fundamentals, commodities advisor context, advanced dashboard restore, Postgres test-isolation fix.
