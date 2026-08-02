# Smoke test - PHASES 6 + 7. NON-DESTRUCTIVE by default.
# Verifies: triggered rules clear once acted on, CASH is not a tradeable
# holding, and idle cash gets a sized executable redeployment plan.
# Nothing changes unless you pass -Execute.
#
#   .\scripts\smoke\smoke-phase67.ps1
#   .\scripts\smoke\smoke-phase67.ps1 -Execute    # ACTUALLY buys

param([switch]$Execute)

$ErrorActionPreference = 'Continue'
$BaseUrl = "https://investwise-pro-production.up.railway.app"
$H = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
$pass = 0; $fail = 0; $skip = 0
function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Api($method, $path) { try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $H -TimeoutSec 90 } catch { Write-Host "  HTTP $($_.Exception.Response.StatusCode.value__) on $method $path" -ForegroundColor DarkRed; return $null } }

Write-Host "`n=== PHASE 6+7 SMOKE ===" -ForegroundColor Magenta

# --- 1. retire stale rules ---------------------------------------------------
Write-Host "`n1. Force an evaluation (retires stale rules)" -ForegroundColor Cyan
$ev = Api POST '/api/v1/rules/evaluate'
if ($null -eq $ev) { Write-Host "`nAPI unreachable - stop." -ForegroundColor Red; return }
Ok "evaluation ran (newly triggered: $($ev.triggered.Count))"

# --- 2. PHASE 6a: cash is not a tradeable holding ---------------------------
Write-Host "`n2. Cash is not tradeable" -ForegroundColor Cyan
$rules = (Api GET '/api/v1/rules').rules
$cashRules = @($rules | Where-Object { $_.ticker -eq 'CASH' -and $_.active })
if ($cashRules.Count -eq 0) { Ok "no active rule on CASH" } else { Bad "$($cashRules.Count) active CASH rule(s) still armed" }
$sugg = (Api GET '/api/v1/rules/suggestions').suggestions
if (@($sugg | Where-Object { $_.ticker -eq 'CASH' }).Count -eq 0) { Ok "suggester no longer offers rules on CASH" } else { Bad "CASH still appears in rule suggestions" }

# --- 3. PHASE 6b: the banner reflects only outstanding work ------------------
Write-Host "`n3. Triggered rules clear once acted on" -ForegroundColor Cyan
$trig = @($rules | Where-Object { $_.triggered -and $_.active })
Write-Host "   still triggered: $(if ($trig.Count) { ($trig | ForEach-Object { $_.ticker }) -join ', ' } else { 'none' })" -ForegroundColor DarkGray
$recs = Api GET '/api/v1/recommendations'
$ruleCards = @($recs.recommendations | Where-Object { $_.dimension -eq 'rule' })
if ($trig.Count -eq $ruleCards.Count) { Ok "banner count ($($trig.Count)) matches rule cards on Today ($($ruleCards.Count))" } else { Bad "banner says $($trig.Count) but Today shows $($ruleCards.Count) rule card(s) - they disagree" }

# --- 4. the audit trail records outcomes ------------------------------------
Write-Host "`n4. Audit trail closes the loop" -ForegroundColor Cyan
$events = (Api GET '/api/v1/rules/events?limit=50').events
if ($null -eq $events -or $events.Count -eq 0) { Skip "no rule events logged yet" } else {
  $resolved = @($events | Where-Object { $_.outcome -ne 'triggered' })
  Write-Host "   $($events.Count) event(s), $($resolved.Count) resolved" -ForegroundColor DarkGray
  if ($resolved.Count -gt 0) { Ok "outcomes stamped: $(($resolved | ForEach-Object { $_.outcome } | Select-Object -Unique) -join ', ')" } else { Skip "nothing acted on yet - act on a card, then re-run" }
  $exec = @($events | Where-Object { $_.outcome -eq 'executed' -and $_.action.executed })
  if ($exec.Count -gt 0) { Ok "executed events carry what was done (e.g. $($exec[0].ticker): $($exec[0].action.executed.kind))" } }

# --- 5. PHASE 7: surplus cash has a sized, executable home ------------------
Write-Host "`n5. Idle cash redeployment" -ForegroundColor Cyan
$port = Api GET '/api/v1/portfolio'
$cash = [double]$port.cash_ils; $nav = [double]$port.nav_ils
Write-Host "   cash $([math]::Round($cash,2)) of NAV $([math]::Round($nav,2)) = $([math]::Round($cash/$nav*100,1))%" -ForegroundColor DarkGray
$card = @($recs.recommendations | Where-Object { $_.apply.kind -eq 'redeploy_cash' })[0]
if ($null -eq $card) { Skip "no redeploy card (cash may already be at the floor)" } else {
  Ok "card: $($card.title)"
  Write-Host "   $($card.action)" -ForegroundColor DarkGray
  $legs = $card.apply.legs
  if ($legs.Count -gt 0) { Ok "$($legs.Count) concrete leg(s) named" } else { Bad "card has no legs" }
  $legs | Format-Table ticker, amount_ils, reason -AutoSize | Out-String | Write-Host
  if (@($legs | Where-Object { $_.ticker -eq 'CASH' }).Count -eq 0) { Ok "never proposes buying CASH with cash" } else { Bad "a leg buys CASH - the classify() bug is back" }
  if (@($legs | Where-Object { $_.amount_ils -lt 250 }).Count -eq 0) { Ok "no sub-minimum dust trades" } else { Bad "a leg is under the 250 minimum" }
  $sum = ($legs | Measure-Object amount_ils -Sum).Sum
  if ($sum -le $cash) { Ok "total $([math]::Round($sum,2)) does not exceed available cash" } else { Bad "proposes spending $([math]::Round($sum,2)) of $([math]::Round($cash,2))" }
  $after = [double]$card.meta.cash_after_ils
  $floor = [double]$card.meta.floor_pct * $nav
  if ($after -ge $floor - 1) { Ok "keeps $([math]::Round($after,2)) buffer (floor $([math]::Round($floor,2)))" } else { Bad "would spend below the floor" } }

# --- 6. optional: execute it (DESTRUCTIVE) ----------------------------------
Write-Host "`n6. Execution" -ForegroundColor Cyan
if (-not $Execute) { Write-Host "  skipped - re-run with -Execute to actually buy" -ForegroundColor DarkGray } elseif ($null -eq $card) { Skip "nothing to execute" } else {
  if ((Read-Host "   Type EXECUTE to buy these legs for real") -ne 'EXECUTE') { Write-Host "   aborted" -ForegroundColor Yellow } else {
    $res = Api POST "/api/v1/recommendations/$($card.id)/apply"
    Write-Host "   $($res | ConvertTo-Json -Compress -Depth 4)" -ForegroundColor DarkGray
    if ($res.bought.Count -gt 0) { Ok "$($res.bought.Count) leg(s) bought" } else { Bad "nothing bought - check 'skipped' above" }
    $post = Api GET '/api/v1/portfolio'
    if ([double]$post.cash_ils -lt $cash) { Ok "cash fell $([math]::Round($cash,2)) -> $([math]::Round([double]$post.cash_ils,2))" } else { Bad "cash unchanged" }
    if ([math]::Abs([double]$post.nav_ils - $nav) -lt ($nav * 0.05)) { Ok "NAV roughly unchanged - cash converted to holdings, not lost" } else { Bad "NAV moved from $([math]::Round($nav,2)) to $([math]::Round([double]$post.nav_ils,2))" } } }

Write-Host "`n=== $pass passed, $fail failed, $skip skipped ===`n" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
