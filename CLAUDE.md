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
- **Cross-loop sessions:** anything opening a session from a loop the app did not create must build a throwaway `NullPool` engine in *that* loop — borrowing the shared async engine makes `asyncpg` reject the connection (SQLite tolerates it, Postgres doesn't). This covers per-test fixtures, a test running beside `TestClient`, and any APScheduler job using `asyncio.run()`. `pricing_service.py` and `nav_history.py` both build their own for this reason.
- **Run the Postgres job before pushing. It IS reproducible locally** — `apt-get install -y postgresql`, `service postgresql start`, create the `investwise` role + database, point `DATABASE_URL` at `postgresql+asyncpg://investwise:investwise@127.0.0.1:5432/investwise`, `pytest -q`. postgres:16 is already in the image. This file used to say the job could not be run locally, and that claim is why `5a6dfef` shipped with three cross-loop failures only `test-postgres` could see, while `test` and `lint` were green. The SQLite job proves less than it looks like it does.
- **CI is not an enforcing gate.** Railway deploys on push regardless of the workflow result: `5a6dfef` was live and serving while `test-postgres` was red. Treat a red run as an incident, not a queue — the bad build is already out.
- **A job nothing calls is a job nobody has tested.** `run_nav_snapshot_blocking` imported a name that does not exist and failed 7 nights out of 7 from the day it shipped; the import was inside the function, so neither import time nor ruff saw it, and no test invoked it. Every scheduled job needs one test that actually runs it.
- Lint is ruff — no stray semicolons / unused imports; CI will fail on them.

## Response style (token-saving)
Short checklist summaries. Don't paste whole CI logs — the failing job + the relevant lines. Edit in place. Explore subagent for broad searches.

## Ship rules (added by setup-claude-local.ps1)
- Read STATUS.md first; update it last.
- Before commit: run the repo's checks (see the ship-it skill / README). Never commit failing typecheck or tests.
- Commit messages via git commit -F <file> written WITHOUT a BOM. One commit per round; check git log -1 so a re-run never duplicates a commit.
- Never force-push. Never commit secrets (.env, agent keys, tokens).
- Deploys happen on push to main (Railway). After pushing, read the deploy logs and confirm a line only the new code emits.
- **Every PR ships with a smoke test** (Eran, 2026-10-05). Write `scripts/smoke/smoke-prNN-<slug>.ps1` in the same commit, built on `scripts/smoke/_smoke-common.ps1` (`Assert-Build` first: pushed SHA serving + schema revision). Read-only. It checks what THIS PR claims, plus that what it claims not to change did not. After the push, run it with `-Sha <short-sha>` and record PASS/FAIL/SKIP in STATUS. A FAIL is an incident (CI does not gate the deploy). Before pushing, verify every endpoint and field the script reads against production; a smoke built on guessed field names reports false FAILs.
