# InvestWise Pro — Status

_Last updated: 2026-07-12 by Claude (weekly status review)._
_Seeded from git history + prior transcripts._

## Now (on main, CI green)
- **Trading rules engine**: stop-loss / take-profit / trailing-stop / price alerts / buy-the-dip / max-weight, each raising alerts + to-dos, with a management UI. Triggered rules surface in the daily digest and as a Today-screen alert banner — HEAD `6036e61`.
- **Actionable Accept**: the Today-view "Accept" on a recommendation now really executes it — sells credit net-of-CGT proceeds to a visible CASH holding, fee-swaps replace the fund at a live price, trims credit the sold portion; Accept returns a "what changed" summary the UI shows.
- **Price freshness**: scheduled 30-min auto-reprice of all holdings (FMP → Yahoo fallback) with a truthful data-source/status label (no more silently-stale prices).
- **Markets + AI layer**: Yahoo futures with a risk-on/off regime feeding the agents; Gemini portfolio / holding / macro summaries + grounded deep-research-per-holding; new Markets tab and AI cards.
- **PWA + push**: installable app (manifest, service worker, mobile bottom-nav); web push for recommendations, risk alerts, price moves and daily digest; server-side recommendation dismissals with 7-day TTL so push and Today stay in sync.
- ILS currency normalization across valuation + display; goal target, projections, and allocation mix all derived from live FX-normalized NAV (kept aligned).
- Opportunity screener agent + fundamentals layer + expanded holdings recommendations; expanded commodity catalog (advisor reasons grounded, never invents numbers, "Not financial advice").
- Legacy "Advanced" dashboard restored behind the auth gate.
- Test suite ≈249 passing; lint (ruff) + test + test-postgres CI jobs green.

## Next (confirm priority)
- Broker integration — see `BROKER_INTEGRATION_PLAN.md` (unstarted; confirm it's the next initiative).
- Live-verify the trading-rules alerts + Today banner fire correctly against real triggers post-deploy.

## Pending QA / open questions
- Confirm on live: accepting a tax-loss/sell rec removes the holding and adds a CASH position for the net proceeds; fee-swap replaces the fund; trim credits cash. (Accept-executes fix.)
- Confirm triggered trading rules render correctly in both the daily digest and the Today-screen banner after deploy.
- Confirm the 30-min reprice + data-source label shows fresh prices (FMP primary, Yahoo fallback) in production.
- Confirm the live advisor answer for "should I add commodities?" reads as a balanced for/against after deploy.

## Known sharp edges
Postgres per-test isolation fixture (throwaway NullPool engine, own event loop); ruff strictness. Windows mount can serve truncated views of file-tool edits — verify large writes on the mount (see `safe-windows-edits`). See CLAUDE.md.

## Changelog (newest first)
- 2026-07-12 — Weekly status review: ⚠️ **the Accept-executes work is uncommitted.** `recommendations.py` (+94), `intake_service.py` (+32), `fee_agent.py`, `static_app/index.html`, `tests/test_recos_apply.py` (+21) and STATUS.md are all modified but never committed — last commit on `main` is still `6036e61` (2026-06-29). Commit + run CI before it drifts or gets lost. (No push/commit done here per policy.)
- 2026-07-06 — **Accept now executes recommendations.** The Today-view "Accept" applied nothing user-visible: sells just deleted holdings (value vanished) and fee/other cards were `apply:none`. Now sells credit net-of-CGT proceeds to a visible **CASH** holding (liquidity you can see/redeploy), `trim` credits the sold portion, and fee-swap cards actually sell the high-fee fund and buy the cheaper equivalent at a live price (falls back to cash if unpriceable). Accept returns a "what changed" summary the UI now shows. Tests: sell→cash added.
- 2026-07-12 — **Notification ↔ Today alignment + why/impact recommendations.** Root cause of "push says do X, app says nothing to do": the app hid cards via a *permanent* `localStorage` list while server dismissals (which gate push) expire after 7 days — so a resurfaced item re-notified while the app hid it forever. Replaced the permanent list with a TTL-matched (7-day) local store keyed by `iw_snoozed_v2`; the server dismissal is now the single source of truth. Notifications are now categorised `action` (maps 1:1 to a Today card) vs `info` (price moves + weekly digest, reworded as FYI, `silent`/no-nag in the SW). Added actionable region/currency/liquidity diversification cards so every pushed alert maps to a card. Every recommendation now carries plain-language `why` + `impact` (rendered in the card); the goal-gap contribution card, the digest and the home "Where this could end up" panel now share one Monte-Carlo projection instead of diverging. SW cache bumped `iw-v2`→`iw-v3`. Ships alongside the previously-uncommitted Accept-executes work.
- 2026-07-06 — Weekly status review: caught STATUS up to 4 shipped commits it was missing (2026-06-28/29). HEAD `8165628` → `6036e61`.
- 2026-06-29 — **Trading rules + digest/banner.** Stop-loss/take-profit/trailing/price-alert/buy-dip/max-weight rules with alerts, to-dos and a management UI; triggered rules surfaced in the daily digest and a Today-screen alert banner.
- 2026-06-29 — **Stale-price fix.** Scheduled 30-min reprice of all holdings (FMP → Yahoo fallback) + truthful data-source/status label.
- 2026-06-29 — **Markets + AI.** Yahoo futures with risk-on/off regime feeding the agents; Gemini portfolio/holding/macro summaries + grounded deep-research-per-holding; Markets tab and AI cards.
- 2026-06-28 — **PWA + push notifications.** Installable app (manifest, service worker, blue-bars icons, mobile bottom-nav); web push for recommendations, risk alerts, price moves and daily digest; price-provider hardening (FMP stable API + keyless Yahoo fallback + transparent errors); server-side recommendation dismissals with 7-day TTL.
- 2026-06-22 — STATUS.md + CLAUDE.md seeded.
- (prior) — NAV/goal alignment, FX normalization, opportunity screener + fundamentals, commodities advisor context, advanced dashboard restore, Postgres test-isolation fix.
