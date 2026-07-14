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
- Test suite ≈254 passing (5 new for actionable trend cards + rule suggestions); lint (ruff) + test + test-postgres CI jobs green.

## Next (confirm priority)
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
