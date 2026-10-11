# Operations: deploy, production, secrets, runbook

> How production runs and how to keep it healthy. Replaces the old `PRODUCTION.md` (now in
> `docs/archive/`; its start-command advice is the PR 47 outage pattern, so do not follow it). The detailed
> procedures stay in [`ROTATION.md`](ROTATION.md) and [`scripts/ops/OPS_RUNBOOK.md`](../scripts/ops/OPS_RUNBOOK.md).
> Written 2026-10-07.

---

## 1. Production at a glance

| | |
|---|---|
| Host | **Railway**: one service built from `Dockerfile` (`.railway/railway.ts`, infrastructure as code since PR 119), plus the Railway Postgres plugin |
| URL | `https://investwise-pro-production.up.railway.app` (UI at `/app/`) |
| Image | `python:3.12.14-slim-bookworm`, non-root `appuser` (uid 10001), ships `app/` + `alembic/` only (`.dockerignore` excludes tests, scripts, docs, `.env*`, keys) |
| Process | `uvicorn app.main:app --host 0.0.0.0 --port 8000`: one process, **one replica** (the scheduler assumes this) |
| Schema | `preDeployCommand: alembic upgrade head && alembic current` (300 s timeout) |
| Healthcheck | `/health/ready` (DB `SELECT 1`, 503 when down), 300 s; Railway keeps the old deployment until the new one is ready |
| Restart | ON_FAILURE, max 3 |
| Providers | Yahoo prices, Frankfurter FX, Gemini (keyed). See [ARCHITECTURE.md §6](ARCHITECTURE.md#6-market-data-fx-and-llm) |
| Users | One (Eran), Google sign-in with an email allowlist; scripts use `X-Agent-Key` |

## 2. Deploy flow

1. Push to `main`. **Railway deploys on every push, whatever CI says.** CI is not a gate: a red run means the bad build
   is already serving. Branch protection can't be enforced on this private free-plan repo; the local `pre-push` hook is the substitute.
2. Railway builds the image and runs the **preDeploy** container: `alembic upgrade head`. If it fails, the deploy fails and the old container keeps serving.
3. The new container boots: scheduler, then the warm loop (Today is warm ~20 s after boot).
4. Railway swaps traffic once `/health/ready` is 2xx.
5. **Verify:** run the PR's smoke with `-Sha <short-sha>`. `Assert-Build` polls `/health` until `commit` equals the SHA, then checks
   `schema_revision`. **Never trust Railway's "Deployment successful" alone** (PR 47).
6. On a 500: `scripts/get-error-log.ps1` pulls Railway logs (to the git-ignored `scripts/railway-error.log`). Search by the `X-Request-ID` header.

`GET /health` example:
```json
{"status":"ok","service":"InvestWise Pro","version":"22.1","commit":"96427f5",
 "environment":"production","schema_revision":"0028_plan_thresholds","builds_own_schema":false}
```
`version` is hand-edited and meaningless; **`commit` is the real version.**

## 3. Required production env

```
ENVIRONMENT=production   DEBUG=false   REQUIRE_AUTH=true
DATABASE_URL=${{Postgres.DATABASE_URL}}
MARKET_DATA_PROVIDER=yahoo   FX_PROVIDER=frankfurter
GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / GOOGLE_OAUTH_REDIRECT_URI   ALLOWED_EMAILS
JWT_SECRET                   (stable sessions across deploys)
AGENT_API_KEY                (scripts; iwk_ + token_urlsafe(36))
VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY   (pin the pair the DB already holds; see §6)
GOOGLE_API_KEY               (Gemini)
```
`AUTO_CREATE_TABLES` must be unset/false: boot logs an ERROR if it is on in production. `ENABLE_SCHEDULER` defaults to true.

## 4. Observability

- **Logs:** JSON lines to stderr (`app/core/logging_config.py`), with `X-Request-ID` on every response.
- **Metrics:** `GET /metrics` (Prometheus: request count and duration).
- **Audit:** every write request becomes an `audit_log` row (sha256 of the payload); read it at `GET /api/v1/audit`.
  Browser (cookie) writes are logged with role "open", a known quirk.
- **Jobs:** `GET /api/v1/push/status` shows the scheduler's `JOB_STATE` (runs, failures, last error per job), push subscriptions and
  the VAPID pin state. `JOB_STATE` is in memory and resets on deploy.
  Four jobs (`backtest_refresh`, `strategy_signals`, `push_evaluate`, `push_digest`) still swallow exceptions and record success; see BACKLOG.
- **Data freshness:** `GET /api/v1/data-status` (live vs illustrative, last price source) and `/api/v1/providers/health` (breakers).
- **Today timing:** the `/recommendations` payload carries `timings_ms` per stage and a `degraded` list.
- Sentry is wired but the SDK is not installed.

## 5. Secrets and rotation

- **Never commit secrets.** `tests/test_no_secret_is_committed.py` scans tracked files for key-shaped strings and the SHA-256 of the
  two credentials published while the repo was public (until 2026-08-30).
- The agent key lives in the git-ignored `scripts/.agent-key` or `$env:IW_AGENT_KEY`. `scripts/agent-key.ps1` (`Get-AgentKey`) resolves
  and tests it, and warns if it is the burned key.
- Rotation procedure for `AGENT_API_KEY` / `AUTH_PASSWORD`: [`ROTATION.md`](ROTATION.md). Generate, set on Railway,
  redeploy, update `scripts\.agent-key`, then verify with `scripts/smoke/smoke-the-published-credentials-are-dead.ps1`.
- **Postgres password:** rotate **only for a reason, never on a schedule**. It is the one ops item whose failure mode is an outage.

## 6. Ops runbook (owner-only actions)

Run `scripts/ops/ops-check.ps1` before and after. It is read-only and reports DONE / TODO / MANUAL / UNKNOWN for each item.
Full steps: [`scripts/ops/OPS_RUNBOOK.md`](../scripts/ops/OPS_RUNBOOK.md).

1. **Branch protection:** not enforceable on the free private plan. Install the hook instead: `cp scripts/ops/pre-push .git/hooks/pre-push`.
2. **Rotate the GitHub PAT.**
3. **Pin the VAPID keypair.** **Done:** verified live 2026-10-08, with `vapid_pinned_by_env: true` and `vapid_env_matches_db: true` on
   `/api/v1/push/status`. Keep checking it after any DB rebuild. Without the pin, the pair lives only in a `kv_settings` row,
   and a DB rebuild mints a new pair and **silently kills every push subscription**. The procedure, if it ever has to be redone:
   1. `verify-vapid-pin.ps1 -Before`
   2. Read the pair with `scripts/pin-vapid-from-db.sql` (Railway console) and set both env vars.
   3. `verify-vapid-pin.ps1 -After -TestPush`
   4. On a mismatch, delete both vars immediately.
4. **Rotate the Railway token** (one project-scoped token).
5. Check whether Railway Postgres exposes `DATABASE_PUBLIC_URL`. The internal host doesn't resolve from a laptop.
6. Postgres password: see §5.
7. **Railway infrastructure as code (PR 119).** The deploy is defined in `.railway/railway.ts`, which replaced `railway.json`
   (Config as Code ends 2026-12-01). It carries the Dockerfile builder, the pre-deploy `alembic upgrade head` (300 s), the
   `/health/ready` healthcheck (300 s) and restart-on-failure (3 retries) in Railway's typed `build`/`deploy` objects.
   **Railway does not read it on deploy.** A change takes effect only through `railway config plan` (review) then
   `railway config apply`. After any edit, `railway config plan` must show no pending changes, so the file is the truth.
   `tests/test_the_deploy_is_infrastructure_as_code.py` pins its contents. Do not re-add `railway.json`. **On Windows** run plan/apply from PowerShell with `$env:_` set to the real exe
   (`node_modules/@railway/cli/bin/railway.exe` under the npm global dir): the `railway` SDK checks the CLI version via `$_`,
   which Git Bash and npm shims break ("requires Railway CLI 5.42.1 or newer"). Variables are `preserve()`d in the file:
   add a variable's NAME there before creating it in Railway, or the next apply deletes it. The one-time
   switch was: `railway config migrate --apply --force` (clears the Config-as-Code setting), restore the reviewed
   `railway.ts`, `railway config plan`, `railway config apply`, confirm with `railway config pull --json`, and only then
   merge the commit that deletes `railway.json`.
8. Delete the orphan `redis-volume` (150 MB, attached to nothing; a Celery-era leftover). `railway volume list` shows it.

Current state of these items: [BACKLOG.md § Ops](BACKLOG.md#ops-owner-only).

## 7. Backups and recovery

**Status 2026-10-11 (owner, Railway dashboard › Postgres › Backups):**
- A manual volume backup exists: `2026-10-11 22:55`, 243 MB. An older "Pre-Security-Patch Backup" (231 MB, about a
  month earlier) is also there.
- **No backup schedule is set**, and point-in-time recovery (PITR) is **off**.
- To finish W0-1, set a schedule under "Edit schedule" (daily is plenty for one user) and note the retention here.
  PITR is optional (it redeploys Postgres once).
- No restore has been drilled yet (O3).
The Railway CLI cannot show backup status. `railway volume list` shows `postgres-volume` (243 MB of 5 GB, 2026-10-08).
Check Railway dashboard → Postgres → **Backups**, enable a schedule, and record the schedule and retention here.
Then add `scripts/ops/backup-db.ps1` (`pg_dump` via `DATABASE_PUBLIC_URL`, if exposed) and do **one restore drill into a scratch DB,
never into production**. At stake: `nav_snapshots`, the `transactions` ledger and `kv_settings`. The VAPID pair also lives in the
Railway env.
What exists today:
- `nav_snapshots` and the `transactions` ledger are the durable history. Earlier NAV (before 2026-08-16) is unrecoverable.
- History repairs are preview-first: `GET/POST /api/v1/portfolio/ledger/repair` (`confirm`), plus the one-shot scripts in
  `scripts/archive/repair/`.
- A bad deploy is rolled back by reverting the commit and pushing (PR 48 reverted PR 47). Migrations were add-only until
  **0029 (PR 114b), which drops tables and `positions.bucket_id`**. A build from before PR 114a (`523bbcf`) still maps that
  column and would fail on today's schema. Never roll back past `523bbcf` without first downgrading 0029, which recreates
  the tables empty.

## 8. Incident checklist

1. Is prod serving what you think? `GET /health` (`commit`, `schema_revision`).
2. Is the DB up? `/health/ready`.
3. Are the jobs running? `/api/v1/push/status` → `JOB_STATE`.
4. Are prices live? `/api/v1/data-status`, `/api/v1/providers/health`.
5. Did Today degrade? The `degraded` list and `timings_ms` in `/api/v1/recommendations`.
6. Logs: `scripts/get-error-log.ps1`.
7. Use the `app-bug-triage` / `investigate-issue` skills. Prove the cause before you fix it, and **find every writer of the bad field**.
8. Write the incident into STATUS.md, and into [HISTORY.md § Incidents](HISTORY.md#incidents-that-became-rules) if it produced a rule.
