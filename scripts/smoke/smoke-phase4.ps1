# PHASE 4 smoke - notifications. This is a DIAGNOSTIC as much as a test: the
# point of the phase was that two weeks of silence produced no evidence, so the
# most valuable output here is the 'blockers' list, not a pass count.
#
#   $env:IW_AGENT_KEY = "<AGENT_API_KEY>"
#   .\scripts\smoke\smoke-phase4.ps1
#
# Sends ONE real test notification to your devices (that's the end-to-end proof).
# Pass -NoPush to skip it.

param([switch]$NoPush)

$ErrorActionPreference = 'Continue'
$BaseUrl = "https://investwise-pro-production.up.railway.app"
$H = @{}
if ($env:IW_AGENT_KEY) { $H['x-agent-key'] = $env:IW_AGENT_KEY } elseif ($env:IW_TOKEN) { $H['Authorization'] = "Bearer $($env:IW_TOKEN)" } else { Write-Host "Set `$env:IW_AGENT_KEY first." -ForegroundColor Yellow }
$pass = 0; $fail = 0; $skip = 0
function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Api($method, $path) { try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $H -TimeoutSec 60 } catch { Write-Host "  HTTP $($_.Exception.Response.StatusCode.value__) on $method $path" -ForegroundColor DarkRed; return $null } }

Write-Host "`n=== PHASE 4 SMOKE: notifications ===" -ForegroundColor Magenta

# --- 1. the diagnostics endpoint itself exists ------------------------------
Write-Host "`n1. Diagnostics endpoint deployed" -ForegroundColor Cyan
$st = Api GET '/api/v1/push/status'
if ($null -eq $st) { Write-Host "`n/push/status unreachable - phase 4 is not live. Stop here." -ForegroundColor Red; return }
Ok "GET /api/v1/push/status responds"

# --- 2. THE ANSWER: why were there no notifications -------------------------
Write-Host "`n2. Blockers (this is the actual diagnosis)" -ForegroundColor Cyan
foreach ($b in $st.blockers) { Write-Host "   -> $b" -ForegroundColor Yellow }
if ($st.blockers.Count -eq 1 -and $st.blockers[0] -like 'Nothing obviously broken*') { Ok "no structural blocker reported" } else { Write-Host "  INFO  $($st.blockers.Count) blocker(s) above - that is the root cause" -ForegroundColor DarkGray }

# --- 3. the three things that must all be true ------------------------------
Write-Host "`n3. Preconditions" -ForegroundColor Cyan
Write-Host "   subscriptions=$($st.subscriptions)  library_ok=$($st.push_library_ok)  vapid_pinned=$($st.vapid_pinned_by_env)" -ForegroundColor DarkGray
if ($st.subscriptions -gt 0) { Ok "$($st.subscriptions) live push subscription(s)" } else { Bad "ZERO subscriptions - this alone explains the silence. Re-enable notifications in the app on your Pixel." }
if ($st.push_library_ok) { Ok "pywebpush / py_vapid importable" } else { Bad "push library unavailable: $($st.push_library_error)" }
if (-not $st.vapid_pinned_by_env) { Write-Host "  INFO  VAPID keypair is DB-generated, not env-pinned. If the DB was ever reset, every existing subscription would 403 - which is exactly the failure phase 4 now prunes. Consider pinning VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY in Railway." -ForegroundColor DarkGray }

# --- 4. scheduler is alive and its jobs are registered ----------------------
Write-Host "`n4. Scheduler" -ForegroundColor Cyan
$sched = $st.scheduler
if ($sched.scheduler_running) { Ok "scheduler running in this process" } else { Bad "scheduler NOT running - no job can fire" }
$jobIds = @($sched.jobs | ForEach-Object { $_.id })
foreach ($want in @('push_evaluate','push_digest','price_refresh')) { if ($jobIds -contains $want) { $nr = @($sched.jobs | Where-Object { $_.id -eq $want })[0].next_run; Ok "job '$want' registered (next run $nr)" } else { Bad "job '$want' missing" } }

# --- 5. job outcomes - a wedged or failing job is now visible ---------------
Write-Host "`n5. Job history" -ForegroundColor Cyan
if ($null -eq $sched.history -or $sched.history.PSObject.Properties.Count -eq 0) { Skip "no job has completed since the last restart (deploy was recent - re-run in ~5 min)" } else { foreach ($p in $sched.history.PSObject.Properties) { $h = $p.Value; if ($h.last_ok) { Ok "$($p.Name): ok at $($h.last_run) ($($h.runs) runs, $($h.failures) failures)" } else { Bad "$($p.Name): FAILED - $($h.last_error)" } } }

# --- 6. fan-out heartbeat ---------------------------------------------------
Write-Host "`n6. Fan-out heartbeat" -ForegroundColor Cyan
if ($st.last_fanout_run) { Ok "last fan-out: $($st.last_fanout_run)" } else { Skip "no fan-out recorded yet - the hourly job has not run since deploy" }
Write-Host "   dedupe_days=$($st.dedupe_days)  severities=$($st.notify_severities)" -ForegroundColor DarkGray
if ($st.recent_notifications.Count -gt 0) { Ok "$($st.recent_notifications.Count) notification(s) in the dedupe ledger (most recent $($st.recent_notifications[0].at))" } else { Skip "dedupe ledger empty - nothing has been sent recently" }

# --- 7. END-TO-END: does a push actually arrive on the phone ---------------
Write-Host "`n7. Live delivery" -ForegroundColor Cyan
if ($NoPush) { Skip "-NoPush set" } elseif ($st.subscriptions -eq 0) { Skip "no subscription to send to" } else { $t = Api POST '/api/v1/push/test'; if ($null -eq $t) { Bad "test push call failed" } elseif ($t.sent -gt 0) { Ok "test push accepted by $($t.sent) device(s) - CHECK YOUR PIXEL NOW" } else { Bad "sent=0 - the subscription exists but the push was rejected. Re-check blockers above." } }

# --- 8. force a real evaluation --------------------------------------------
Write-Host "`n8. Forced evaluation" -ForegroundColor Cyan
$chk = Api POST '/api/v1/push/check'
if ($null -eq $chk) { Skip "check endpoint unavailable" } else { Write-Host "   result: $($chk | ConvertTo-Json -Compress)" -ForegroundColor DarkGray; if ($null -ne $chk.sent) { Ok "evaluation ran (sent=$($chk.sent)$(if ($chk.reason) { ", reason=$($chk.reason)" }))" } else { Bad "unexpected response" } }

Write-Host "`n=== $pass passed, $fail failed, $skip skipped ===" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
Write-Host "The number that matters is section 2. A green run with 0 subscriptions still means you get no notifications.`n" -ForegroundColor DarkGray
