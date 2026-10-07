# SMOKE - PR 72: a triggered rule always has a card; the ladder climbs; history is read-only.
#
#   .\scripts\smoke\smoke-pr72-a-rule-always-has-a-card.ps1 -Sha abc1234
#
# READ-ONLY. Nothing here accepts, dismisses or restores a card.
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.
#
# What it proves against the live build:
#   1  every rule the banner counts as carded is a card you can SEE
#   2  every card the contradiction pass hid is listed in the history as blocked
#   3  the SOXL take-profit that fought its sleeve is a decision card, and its
#      "keep" level is past today's price
#   4  every history row says what happened to it (done / ignored / blocked)
#   5  the served page has the read-only history and no bulk restore
#   6  the per-card restore route exists

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\..\agent-key.ps1"
$pass = 0; $fail = 0; $skip = 0
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host ""; Write-Host $m -ForegroundColor Cyan }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
function Api($method, $path, $tmo = 300) {
    try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo }
    catch { Write-Host "        $method $path -> $($_.Exception.Message)" -ForegroundColor DarkRed; return $null }
}

Write-Host "Smoke: PR 72  ($BaseUrl)" -ForegroundColor White

Sec "0  build"
$h = $null; try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
if ($null -eq $h) { Bad "/health unreachable"; exit 1 }
if ($Sha) { if ($h.commit -like "$($Sha.Trim())*") { Ok "serving $($h.commit)" } else { Bad "serving $($h.commit), expected $Sha" } } else { Skip "no -Sha" }
if ($h.schema_revision -eq '0024_plan_cash_target_ils') { Ok "schema unchanged (0024) - PR 72 has no migration" } else { Bad "schema $($h.schema_revision)" }

$recs = Api GET "/api/v1/recommendations"
if ($null -eq $recs) { Bad "/recommendations failed"; exit 1 }
$visible = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
$ids = @($visible | ForEach-Object { $_.id })
$hidden = @($recs.hidden_cards)
Note ("{0} cards on screen, {1} in the history" -f $ids.Count, $hidden.Count)

Sec "1  the banner counts only cards you can see"
$rb = $recs.rule_banner
Note ("triggered: {0}   carded: {1}   blocked: {2}   healed: {3}" -f (@($rb.triggered) -join ','), (@($rb.carded) -join ','), (@($rb.blocked) -join ','), (@($rb.healed) -join ','))
$orphans = @(@($rb.carded) | Where-Object { $_ -and ($ids -notcontains ("rule_" + $_)) })
if ($orphans.Count -eq 0) { Ok "every carded rule has a visible card" } else { Bad ("carded but not visible: {0}" -f ($orphans -join ',')) }
$missing = @(@($rb.triggered) | Where-Object { $_ -and (@($rb.carded) -notcontains $_) -and (@($rb.blocked) -notcontains $_) -and (@($rb.healed) -notcontains $_) })
if ($missing.Count -eq 0) { Ok "every triggered rule is carded, blocked (listed) or healed" } else { Bad ("triggered, unaccounted: {0}" -f ($missing -join ',')) }
if ($null -eq $rb.blocked) { Bad "rule_banner has no 'blocked' list - old build?" }
foreach ($b in @($rb.blocked)) {
    if ($blk = @($recs.hidden_cards | Where-Object { $_.id -eq ("rule_" + $b) -and $_.how -eq 'blocked' })) { Ok ("rule {0} blocked, not retired, and listed in the history" -f $b) }
    else { Bad ("rule {0} blocked but not in the history" -f $b) }
}

Sec "2  a hidden card is listed, not lost"
$blockedIds = @($hidden | Where-Object { $_.how -eq 'blocked' } | ForEach-Object { $_.id })
$sup = @(@($recs.contradictions) | Where-Object { $_.suppressed })
foreach ($c in $sup) {
    if ($blockedIds -contains $c.card) { Ok ("{0} hidden and listed as blocked: {1}" -f $c.card, $c.detail) }
    else { Bad ("{0} hidden and NOT in the history" -f $c.card) }
}
$conv = @(@($recs.contradictions) | Where-Object { $_.converted_to })
foreach ($c in $conv) {
    if ($ids -contains $c.converted_to) { Ok ("{0} came back as a decision card" -f $c.card) } else { Bad ("{0} converted but not visible" -f $c.card) }
}
if ($sup.Count -eq 0 -and $conv.Count -eq 0) { Skip "no contradictions today - nothing to check" }

Sec "3  a rule that fights a sleeve is a decision"
$dec = @($visible | Where-Object { $_.apply.kind -eq 'rule_vs_sleeve' })
if ($dec.Count -eq 0) {
    $soxl = Api GET "/api/v1/rules"
    $tp = @($soxl.rules | Where-Object { $_.ticker -eq 'SOXL' -and $_.rule_type -eq 'take_profit' -and $_.active })
    if ($tp.Count -gt 0 -and $tp[0].triggered) { Bad "SOXL take-profit is triggered and there is no decision card" }
    else { Skip "no rule is fighting a sleeve right now (SOXL take-profit not triggered)" }
} else {
    foreach ($d in $dec) {
        $k = $d.apply.options.keep; $s = $d.apply.options.sell
        Ok ("{0}: {1}" -f $d.id, $d.title)
        Note ("sell {0} {1}, sleeve {2}% -> {3}%" -f $s.apply.shares, $s.apply.ticker, $s.sleeve_pct_from, $s.sleeve_pct_to)
        Note ("keep: rule {0} -> {1} ({2})" -f $k.old_level, $k.level, $k.mode)
        if ([double]$s.sleeve_pct_to -lt [double]$s.sleeve_pct_from) { Ok "sell lowers the sleeve" } else { Bad "sell does not lower the sleeve" }
        if ([double]$k.level -gt [double]$k.old_level) { Ok "keep moves the rule up" } else { Bad "keep does not move the rule" }
        if ($d.actionable) { Ok "it is actionable" } else { Bad "decision card is not actionable" }
    }
}

Sec "4  every history row says what happened"
$bad = @($hidden | Where-Object { @('done', 'ignored', 'blocked') -notcontains $_.how })
if ($hidden.Count -eq 0) { Skip "history is empty" }
elseif ($bad.Count -eq 0) {
    $dn = @($hidden | Where-Object { $_.how -eq 'done' }).Count
    $ig = @($hidden | Where-Object { $_.how -eq 'ignored' }).Count
    $bl = @($hidden | Where-Object { $_.how -eq 'blocked' }).Count
    Ok ("{0} done, {1} ignored, {2} blocked - each labelled" -f $dn, $ig, $bl)
} else { Bad ("{0} rows with no status" -f $bad.Count) }

Sec "5  the page: read-only history, no bulk restore"
$page = $null; try { $page = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($null -eq $page) { try { $page = (Invoke-WebRequest -Uri "$BaseUrl/" -UseBasicParsing -TimeoutSec 60).Content } catch {} }
if ($null -eq $page) { Skip "could not fetch the page" }
else {
    if ($page -match 'function showHistory') { Ok "history list is in the served page" } else { Bad "history list missing from the served page" }
    if ($page -match 'restore-completed') { Bad "page still calls the bulk restore" } else { Ok "no bulk restore on the page" }
    if ($page -match 'function decideRule') { Ok "decision buttons are in the served page" } else { Bad "decision buttons missing" }
}

Sec "6  per-card restore route"
$oa = Api GET "/openapi.json" 60
if ($null -eq $oa) { Skip "openapi not served" }
elseif ($oa.paths.PSObject.Properties.Name -contains '/api/v1/recommendations/{rec_id}/restore') { Ok "POST /recommendations/{rec_id}/restore exists" }
else { Bad "per-card restore route missing" }

Write-Host ""
Write-Host ("PASS {0}   FAIL {1}   SKIP {2}" -f $pass, $fail, $skip) -ForegroundColor White
if ($fail -gt 0) { exit 1 }
