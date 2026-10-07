# Development guide

> How to run, test, change and ship InvestWise Pro. This page replaces the setup sections of the old
> README and `PRODUCTION.md`, and the house rules in `STATUS-V2.md`. Written 2026-10-07 at PR 104d.1.

---

## 1. Prerequisites

- **Python 3.12** (CI and the Docker image use 3.12; the image is pinned to `python:3.12.14-slim-bookworm`).
- **Postgres 16**, for the Postgres test job and for running against a real DB (`docker compose up db` is easiest).
- **PowerShell** for the scripts in `scripts/` (written for Windows PowerShell 5.1: ASCII only, no backtick continuations).
- Optional: a `GOOGLE_API_KEY` (Gemini) for the AI features; the app runs without it.

## 2. Run it locally

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                                    # then edit; see §3
```

**Option A, everything in Docker:**
```bash
docker compose up --build        # API on :8000 (/docs), Postgres 16 on :5432
```
Compose sets `AUTO_CREATE_TABLES=true`, so the app builds its own schema with `create_all`. Production never does
this; there Alembic owns the schema.

**Option B, local Python against a Postgres:**
```bash
docker compose up -d db
alembic upgrade head             # build the schema the production way
uvicorn app.main:app --reload --port 8000
```
Open `http://localhost:8000/app/`. With `REQUIRE_AUTH=false` (the default) you are a synthetic SUPERADMIN, and no login is needed.
With an empty book you land on **Setup**, where you can load the sample portfolio.

Market data defaults to **`builtin`**, which is deterministic fake prices. Set `MARKET_DATA_PROVIDER=yahoo` and
`FX_PROVIDER=frankfurter` for real data (no keys needed). The UI shows an amber "illustrative" banner while on builtin.

## 3. Configuration

All settings are in `app/core/config.py` (pydantic-settings; the env var is the field name in UPPER_CASE; values are
whitespace-stripped). The ones that matter:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | local Postgres (asyncpg) | `postgres://` is normalised to `postgresql+asyncpg://` |
| `ENVIRONMENT` / `DEBUG` | development / true | `production` enables boot warnings |
| `AUTO_CREATE_TABLES` | **false** | true only for dev/compose/tests |
| `ENABLE_SCHEDULER` | true | Tests set false |
| `MARKET_DATA_PROVIDER` | builtin | `yahoo` / `fmp` (+ `FMP_API_KEY`) |
| `FX_PROVIDER` | builtin | `frankfurter` for real ECB rates |
| `REQUIRE_AUTH` | false | **true in production** |
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` / `_REDIRECT_URI`, `ALLOWED_EMAILS` | | Google sign-in + allowlist |
| `JWT_SECRET` | "" | Set it, or every restart logs everyone out |
| `AGENT_API_KEY` | "" | `X-Agent-Key` → SUPERADMIN, for scripts |
| `AUTH_PASSWORD` | "" | Password login; blank = off. Published old values are refused |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | "" | Web push. If blank, a pair is generated and stored in DB; pin it in production |
| `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) | | Gemini. Read outside Settings |
| `WAR_ROOM_CACHE_TTL_SEC` | 600 | Tests set 0 |

The ~60 tuning constants (tax rates, Monte Carlo runs, risk caps, provider TTLs, push thresholds) are listed in
`config.py`. **User-facing decisions do not belong in `config.py`.** They are plan settings
([§7](#7-adding-a-user-setting)).

> `.env.example` is minimal. Copy the table above for anything you need. `API_KEY` is dead code: nothing reads it.

## 4. Tests

```bash
pytest -q                        # SQLite (the conftest default). Exactly what CI's `test` job runs
ruff check app tests             # exactly what CI's `lint` job runs
```

**Run the Postgres job before pushing.** Only Postgres catches cross-loop bugs (incident `5a6dfef` shipped with three
failures only `test-postgres` saw).
```bash
docker compose up -d db          # or in a Linux sandbox:
# apt-get install -y postgresql && service postgresql start
# sudo -u postgres psql -c "create role investwise login password 'investwise' createdb;" -c "create database investwise owner investwise;"
DATABASE_URL=postgresql+asyncpg://investwise:investwise@127.0.0.1:5432/investwise pytest -q
```

Facts about the suite:
- **~2,290 tests in 254 files**, organised by claim, not by module. File names are sentences
  (`test_a_card_may_not…`, `test_the_cash_target_is_soft_and_the_floor_is_not.py`). Name new ones the same way.
- `tests/conftest.py`:
  - Sets `AUTO_CREATE_TABLES=true`, `ENABLE_SCHEDULER=false`, `WAR_ROOM_CACHE_TTL_SEC=0`.
  - Gives each test a fresh schema through an autouse `_isolate_db` (a throwaway **NullPool engine in the test's own loop**).
  - Provides the **`own_session`** fixture. Use it whenever a test opens a session next to a `TestClient`.
  - **A skip is never a pass.** Any skip whose reason isn't in `_ALLOWED_SKIPS` (empty) fails the run.
- Shared helpers: `tests/_cards.py`, `_redeploy_fixtures.py`, `_review_fixtures.py`.
- No test may hit the live market. PR 28 removed every such assertion.
- **Every new test must fail on the current HEAD** before you accept it as proof of a fix.
- The test areas that guard the domain invariants are listed in [DOMAIN.md §8](DOMAIN.md#8-invariants-and-the-tests-that-guard-them).
- On Windows, the SQLite default path `/tmp/iw_test_app.db` resolves to `<drive>:\tmp\`. Set `DATABASE_URL` if that directory doesn't exist.

## 5. Code conventions

- **Lint is ruff with default rules** (no config). It runs on both `app` and `tests`: no unused imports, no stray semicolons.
- **Money:**
  - Write MONEY columns through `models.tables.money()`; use `core.money` for display rounding.
  - Value holdings through `portfolio_analytics.compute_snapshot` (ILS), never `qty * price`.
- **Funded buys** go through `funding_service.propose_funded_buy()`; pass `buying_class` to `describe_funding`.
- **Blocking I/O** (providers are sync urllib) is called via `asyncio.to_thread` / `offload`. The event loop must stay free.
- **Anything run from another loop** (a scheduler job, `asyncio.run`, a test beside `TestClient`) builds its **own NullPool engine** in that loop.
- **Every scheduled job has a test that actually runs it.** `run_nav_snapshot_blocking` imported a non-existent name inside the
  function and failed 7 nights out of 7 unseen.
- **A guard protects the path it is on.** When fixing a data bug, find *every* writer of the field first (the CASH-as-a-stock incidents).
- Comments carry the *why*, often with the incident date and the owner's words. Keep that habit: it is how this codebase explains itself.
- `app/static_app/index.html`: bump `sw.js` `VERSION` in the same commit as any shell change ([FRONTEND.md §8](FRONTEND.md#8-service-worker-swjs)).

## 6. Database migrations

Migrations are owned by Alembic (`alembic/versions/`), linear and numbered `00NN_<slug>.py`. **Production runs them as Railway's
preDeployCommand**, never at app boot. PR 47 tried boot-time migrations and took the site down for two hours.

The house pattern (from PR 47; used in 0025, 0026, 0027, 0028):
1. **A migration ships alone** in its own PR, e.g. "PR 104d.0: migration 0028 alone".
   - It is add-only and nullable.
   - It is guarded: inspect before you add, because `0001` runs `create_all` and the column may already exist.
2. Mirror the DDL into the startup `ALTER … IF NOT EXISTS` list in `app/main.py` (kept for dev/tests).
   `tests/test_startup_ddl_covers_migrations.py` and `test_the_migrations_are_the_only_thing_that_builds_the_schema.py` check it.
3. Nothing reads the column until the **next** PR. Its smoke confirms that `/health.schema_revision` equals the new head and that nothing changed.
4. Then the feature PR reads it.

`tests/test_the_migration_chain_runs_from_nothing.py` runs a real `alembic upgrade head` on scratch SQLite.
Do **not** use `alembic revision --autogenerate` blindly: 0001 is `create_all` of the current models, so autogenerate sees no diff.

## 7. Adding a user setting

Owner rule ([DECISIONS.md D64](DECISIONS.md)): a limit or bar the owner decides must be a setting with his value as the
default, visible where it acts, and editable in the app.
1. **Card threshold** (a number that gates a card): add it to `SPEC` in `app/services/thresholds.py`, with a default read from the owning
   constant, bounds, label and detail. Read it with `thresholds.get(key)`. It appears in the Card-thresholds sheet automatically,
   and `tests/test_every_threshold_is_a_setting.py` will hold you to it.
2. **Plan-level setting:** a nullable `plans` column (via a migration shipped alone; NULL = default), `plan_service`
   read/write, `PUT /plan` validation, and a row/sheet in Plan › Goal.

## 8. Shipping a PR

The full rules are in [`CLAUDE.md`](../CLAUDE.md) "Ship rules"; this is the sequence:

1. **Read `STATUS.md` first.** Check `git log -1` so a re-run never duplicates a commit.
2. Make the change and add tests that fail on HEAD. Bump `sw.js` if the shell changed.
3. Write **`scripts/smoke/smoke-prNN-<slug>.ps1`** in the same commit (see below).
4. Run the gate locally: `pytest -q` (SQLite), `pytest -q` against Postgres, and `ruff check app tests`. All green.
5. Commit **one commit per round**:
   - `git add <explicit paths>`. Never `git add -A`, because CRLF noise and other sessions' files are in the tree.
   - `git commit -F <msgfile>`, with the message file written **without a BOM**.
   - Never amend a pushed commit, never force-push. The `scripts/ops/pre-push` hook refuses force-pushes to main.
6. Push to `main`. **Railway deploys on push regardless of CI.** A red CI run means the bad build is already live: treat it as an incident.
7. Run the smoke against production: `.\scripts\smoke\smoke-prNN-<slug>.ps1 -Sha <short-sha>`. Record PASS / FAIL / SKIP in STATUS.md.
   A FAIL is an incident.
8. If the change is user-visible, write a Pixel 9 QA list in `qa/QA-YYYY-MM-DD-<slug>.md` ([FRONTEND.md §11](FRONTEND.md#11-testing-the-ui)).
9. **Update `STATUS.md`** with a new `## PR NN — <what changed, in plain words>` entry at the top: what shipped, why, how it was verified,
   and what is still broken on purpose. Update [BACKLOG.md](BACKLOG.md) if an item closed or opened.

### Writing a per-PR smoke

Every PR ships one, built on `scripts/smoke/_smoke-common.ps1`.

```powershell
param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR NN"                                  # builds the x-agent-key header via Get-AgentKey
Sec "0. the build"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "..."   # polls /health until commit == Sha, then checks schema_revision
Sec "1. what this PR claims"
$p = Api GET "/api/v1/plan"                          # returns $null on error and logs it
if ($p -and [double]$p.some_field -eq 5) { Ok "..." } else { Bad "..." }
Sec "2. what it claims NOT to change"
# ...
Stop-Smoke                                           # prints PASS/FAIL/SKIP; exit 1 on any FAIL
```

Rules:
- **Read-only.**
- ASCII only.
- No backtick line continuations.
- Compare numbers as `[double]`.
- Missing data is SKIP/FAIL, **never a pass**.
- PowerShell variables are case-insensitive (`$h` is `$H`).
- **Verify every endpoint and field name against production before pushing.** A smoke built on guessed field names reports false FAILs.

Good examples: `smoke-pr104d-thresholds.ps1` (a feature) and `smoke-pr104d0-migration.ps1` (a migration-only PR).

## 9. Working with Claude on this repo

- `CLAUDE.md` is the agent's standing brief. Skills referenced there: `session-kickoff` (start of session), `ship-it` (push + verify),
  `app-bug-triage` (production issue), `investigate-issue` (prove before acting), `mobile-qa` (Pixel QA list), and `safe-windows-edits`.
- Owner preferences are recorded in [DECISIONS.md](DECISIONS.md) (D31, D64, D65): every PR ships a smoke, every decision is a setting,
  and no Hebrew/RTL step in QA lists unless asked.
- Plans are **re-validated against production before work starts**. A plan row is PROVEN (seen live) or INFERRED (read from code),
  and inferred understanding stops for the owner before acting.
