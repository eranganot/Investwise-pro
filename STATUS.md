# InvestWise Pro — Status

_Last updated: 2026-06-22 by Claude (session "review tasks / optimize")._
_Seeded from git history + prior transcripts._

## Now (on main, CI green)
- ILS currency normalization across valuation + display; FMP-keyed data provider.
- Goal target, projections, and allocation mix all derived from live FX-normalized NAV (kept aligned) — HEAD `8165628`.
- Opportunity screener agent + fundamentals layer + expanded holdings recommendations.
- Expanded commodity catalog; advisor can reason about adding commodities (grounded, never invents numbers, "Not financial advice").
- Legacy "Advanced" dashboard restored behind the auth gate.
- Test suite ≈249 passing; lint (ruff) + test + test-postgres CI jobs green.

## Next (confirm priority)
- (No active thread captured — set the next initiative here.)

## Pending QA / open questions
- Confirm the live advisor answer for "should I add commodities?" reads as a balanced for/against after deploy.

## Known sharp edges
Postgres per-test isolation fixture (throwaway NullPool engine, own event loop); ruff strictness. See CLAUDE.md.

## Changelog (newest first)
- 2026-06-22 — STATUS.md + CLAUDE.md seeded.
- (prior) — NAV/goal alignment, FX normalization, opportunity screener + fundamentals, commodities advisor context, advanced dashboard restore, Postgres test-isolation fix.
