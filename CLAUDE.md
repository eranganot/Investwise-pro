# InvestWise Pro — Claude working notes

Personal investing dashboard / wealth system. Python, branch `main`. This is the **best-behaved repo**: sandbox-native git works directly, and CI runs the full suite (≈249 tests, lint/ruff, plus a Postgres job) on push.

## How to work in this repo
- **Read `STATUS.md` first**; update after shipping or at session end.
- The sandbox **can** run git here directly — commit, merge, push, and watch CI. Always run the suite locally before pushing: keep it green.
- Use `app-bug-triage` for production issues and `ship-it` for the verify-after-deploy habit (CI is the gate here rather than Railway logs).

## Domain / behavior rules
- The advisor assistant must **never invent numbers**; it reasons from grounded context (holdings, risk, asset-class concentration, available commodity/strategy options) and applies general investing principles. Always ends with "Not financial advice."
- Currency is **ILS-normalized** across valuation + display; goal target, projections, and allocation mix all derive from the FX-normalized portfolio NAV — keep them aligned.
- The legacy "Advanced" dashboard (`/dashboard`) must stay behind the auth gate (`REQUIRE_AUTH`).

## Known sharp edges
- **Postgres test isolation:** per-test fixtures must create a throwaway `NullPool` engine in the test's own event loop — borrowing the app's shared async engine makes `asyncpg` reject the cross-loop connection (SQLite tolerates it, Postgres doesn't).
- Lint is ruff — no stray semicolons / unused imports; CI will fail on them.

## Response style (token-saving)
Short checklist summaries. Don't paste whole CI logs — the failing job + the relevant lines. Edit in place. Explore subagent for broad searches.
