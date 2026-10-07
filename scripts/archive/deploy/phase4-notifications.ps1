# PHASE 4 - Notification hardening + diagnostics.
#
# Two weeks of silence with no way to tell why. This phase does NOT claim to
# have found the cause - it fixes the three structural ways this can happen
# silently and adds the endpoint that will name the actual cause next time.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase4-notifications.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/worker/scheduler.py",
    "app/services/push_service.py",
    "app/api/routes/push.py"
)

Invoke-Suite

$msg = @(
    "fix(push): stop notifications failing silently; add GET /push/status",
    "",
    "Two weeks with no notifications and nothing to diagnose it with: every",
    "failure path was a bare logger.warning and no state was persisted, so",
    "'the scheduler died', 'a job is wedged' and 'the pushes are being rejected'",
    "were indistinguishable. Three structural silent-failure modes fixed:",
    "",
    "1. 403 was not treated as a dead subscription. If the DB-persisted VAPID",
    "   keypair was ever regenerated, every push failed 403 forever - the sub was",
    "   never pruned, so the browser never re-subscribed. Permanent, invisible",
    "   outage. 403 now joins 404/410 in DEAD_CODES.",
    "",
    "2. APScheduler's default misfire_grace_time is 1 second, so a job whose",
    "   fire time passes during a restart is dropped silently. On a platform that",
    "   redeploys, a 07:00 daily digest with a 1s grace is a coin flip. Defaults",
    "   raised to 1h, and 6h for the digest (idempotent per calendar day, so a",
    "   wide window can only make it late, never duplicate).",
    "",
    "3. An interval job with max_instances=1 that hangs blocks every subsequent",
    "   run forever. Jobs now run under a 10-minute watchdog that records a",
    "   timeout instead of wedging the slot.",
    "",
    "Also: _send_sync no longer lets an ImportError from pywebpush/py_vapid",
    "escape into the caller's broad except, where it disappeared as one",
    "ambiguous warning.",
    "",
    "New GET /api/v1/push/status reports subscriptions, push-library health,",
    "scheduler job history with last error and next run, the fan-out heartbeat,",
    "and a plain-language 'blockers' list."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nRIGHT AFTER DEPLOY - this is the diagnosis step:" -ForegroundColor Cyan
Write-Host "  1. Open  <your-app>/api/v1/push/status  and read 'blockers'."
Write-Host "  2. POST  /api/v1/push/test   - if it doesn't arrive, re-enable"
Write-Host "     notifications in the app to mint a fresh subscription."
Write-Host "  3. POST  /api/v1/push/check  - forces an evaluation for your user."
Write-Host "`nIf 'subscriptions: 0', that alone explains the two weeks." -ForegroundColor Yellow
