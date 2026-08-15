# InvestWise Pro — Claude working notes

Personal investing dashboard / wealth system. Python, branch `main`. This is the **best-behaved repo**: sandbox-native git works directly, and CI runs the full suite (≈790 tests, lint/ruff on `app` **and** `tests`, plus a Postgres job) on push.

## How to work in this repo
- **Read `STATUS.md` first**; update after shipping or at session end.
- The sandbox **can** run git here directly — commit, merge, push, and watch CI. Always run the suite locally before pushing: keep it green.
- Use `app-bug-triage` for production issues and `ship-it` for the verify-after-deploy habit (CI is the gate here rather than Railway logs).

## Domain / behavior rules
- The advisor assistant must **never invent numbers**; it reasons from grounded context (holdings, risk, asset-class concentration, available commodity/strategy options) and applies general investing principles. Always ends with "Not financial advice."
- Currency is **ILS-normalized** across valuation + display; goal target, projections, and allocation mix all derive from the FX-normalized portfolio NAV — keep them aligned.
- The legacy "Advanced" dashboard (`/dashboard`) must stay behind the auth gate (`REQUIRE_AUTH`).
- A recommendation card **may not claim a portfolio change it does not produce**. Any "moves X toward target" claim must be computed from the *simulated post-trade mix*, never from trade size. Selling a class to buy the same class is a **swap** and must be labelled one. Every funded buy goes through `funding_service.propose_funded_buy()` — never `plan_funding` plus hand-written prose — and `describe_funding` requires its `buying_class` argument for exactly this reason. Verified in `tests/test_card_claims.py` by applying each card's `apply` spec and re-measuring, not by asserting on its text.
- **Funding may only sell what the plan says you have too much of.** One overweight budget per asset class, spent down as the loop sells; a class at or under target is not a funding source while anything else is over.

## Known sharp edges
- **Postgres test isolation:** per-test fixtures must create a throwaway `NullPool` engine in the test's own event loop — borrowing the app's shared async engine makes `asyncpg` reject the cross-loop connection (SQLite tolerates it, Postgres doesn't).
- Lint is ruff — no stray semicolons / unused imports; CI will fail on them.

## Response style (token-saving)
Short checklist summaries. Don't paste whole CI logs — the failing job + the relevant lines. Edit in place. Explore subagent for broad searches.
