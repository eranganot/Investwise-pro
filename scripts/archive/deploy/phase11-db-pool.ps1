# PHASE 11 - the API stops hanging after ~5 requests.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase11-db-pool.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/core/database.py",
    "tests/test_db_pool_health.py",
    "scripts",
    "STATUS.md"
)

Invoke-Suite

$msg = @(
    "fix(db): pool_pre_ping on the shared engine - stops requests hanging forever",
    "",
    "Symptom: in a full smoke run, five API calls succeeded and the sixth and",
    "every call after it returned NO HTTP STATUS AT ALL - no response, not a 4xx",
    "or 5xx, both attempts, 60s each.",
    "",
    "Ruled out by measurement, not assumption:",
    "  * keep-alive reuse    - -DisableKeepAlive changed nothing",
    "  * a deploy draining   - a 5/5 health preflight passed, cliff still hit",
    "  * server saturation   - /health, /health/ready and /plan all answered in",
    "                          0.1s immediately after two 6s /recommendations",
    "  * a rate limit        - 15 identical /plan calls in a row all returned in",
    "                          0.09s; the app has no rate-limit middleware",
    "",
    "Cause: Railway's Postgres closes idle connections. The shared engine was",
    "built without pool_pre_ping, so SQLAlchemy handed a dead connection to",
    "asyncpg, which then waited for a reply that never came - a hang with no",
    "status, which is why nothing was ever logged as an error. It surfaced on",
    "roughly the sixth request, once pooled connections had gone stale.",
    "",
    "Corroboration: push_service and pricing_service ALREADY create their own",
    "engines with pool_pre_ping=True. The lesson had been learned in the",
    "background jobs and never applied to the engine every request uses.",
    "",
    "  pool_pre_ping=True   validate before handing out",
    "  pool_recycle=300     retire connections before the far end does",
    "  pool_timeout=30      exhaustion raises instead of blocking forever",
    "  pool_size=10, max_overflow=20",
    "",
    "Also: echo=settings.debug logged every SQL statement in production, because",
    "`debug` defaults to True and DEBUG is not set. Now gated on environment.",
    "",
    "+4 tests (test_db_pool_health.py). Also commits scripts/ so the deploy and",
    "smoke tooling survives a fresh clone."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nWait for Railway to go Active, then:" -ForegroundColor Cyan
Write-Host "  .\scripts\smoke\smoke-all.ps1"
Write-Host "`nExpect 0 skips this time - the preflight guards against a mid-deploy run." -ForegroundColor Cyan
