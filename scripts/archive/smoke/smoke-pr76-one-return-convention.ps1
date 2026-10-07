# SMOKE - PR 76: a compound return is projected as a compound return, once.
# (The like-for-like cash rule is covered by tests/test_redeploy_is_like_for_like.py.)
#
#   .\scripts\smoke\smoke-pr76-one-return-convention.ps1 -Sha abc1234
#
# READ-ONLY. Accepts nothing.
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

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

Write-Host "Smoke: PR 76  ($BaseUrl)" -ForegroundColor White

Sec "0  build"
$h = $null; try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
if ($null -eq $h) { Bad "/health unreachable"; exit 1 }
if ($Sha) { if ($h.commit -like "$($Sha.Trim())*") { Ok "serving $($h.commit)" } else { Bad "serving $($h.commit), expected $Sha" } } else { Skip "no -Sha" }
if ($h.schema_revision -eq '0024_plan_cash_target_ils') { Ok "schema unchanged (0024) - PR 76 has no migration" } else { Bad "schema $($h.schema_revision)" }

Sec "1  the projection's median compounds at the stated return"
$pj = Api GET "/api/v1/plan/projection"
if ($null -eq $pj -or $pj.message) { Skip "no projection ($($pj.message))" }
else {
    $a0 = [string]@($pj.assumptions)[0]
    Note $a0
    $roi = $null
    if ($a0 -match '~(-?[0-9.]+)%') { $roi = [double]$Matches[1] }
    if ($pj.return_convention -eq 'compound') { Ok "convention: compound" } else { Bad "no return_convention (old code)" }
    if ($null -ne $roi) {
        $comp = [double]$pj.starting_value * [math]::Pow(1.0 + $roi / 100.0, [double]$pj.years)
        Note ("median {0:N0}; {1:N0} compounded at {2}% for {3}y = {4:N0}; chance of goal {5}" -f [double]$pj.projected_median, [double]$pj.starting_value, $roi, $pj.years, $comp, $pj.probability_meets_target)
        if ([math]::Abs([double]$pj.projected_median / $comp - 1.0) -le 0.05) { Ok "median is within 5% of compounding at the stated rate" }
        else { Bad ("median {0:N0} vs compounding {1:N0} - volatility still counted twice" -f [double]$pj.projected_median, $comp) }
    } else { Skip "could not read the return from the assumptions line" }
}

Sec "2  the behind card uses the same median"
$recs = Api GET "/api/v1/recommendations"
$all = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
$b = @($all | Where-Object { $_.dimension -eq 'goal' -and $_.apply.kind -eq 'set_standing_order' })
if ($b.Count -eq 0) { Skip "no behind-goal card right now" }
elseif ($null -eq $pj -or $pj.message) { Skip "no projection to compare the card with" }
else {
    $c = $b[0]
    Note $c.why
    Note ("asks for {0}/month" -f $c.est_amount)
    $wc = $pj.with_contributions
    $ref = if ($wc -and $wc.available -and $wc.source -eq 'standing_order') { [double]$wc.projected_median } else { [double]$pj.projected_median }
    if ($null -eq $c.projected_ils) { Bad "card carries no projected_ils (old code)" }
    elseif ([math]::Abs([double]$c.projected_ils / $ref - 1.0) -le 0.01) { Ok ("card median {0:N0} = projection median {1:N0}" -f [double]$c.projected_ils, $ref) }
    else { Bad ("card median {0:N0} vs projection {1:N0}" -f [double]$c.projected_ils, $ref) }
}

Sec "3  the page says why the projection moved"
$page = $null; try { $page = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($null -eq $page) { Skip "could not fetch the page" }
elseif ($page -match 'return_convention_note') { Ok "projection card shows the convention note" }
else { Bad "old page" }

Write-Host ""
Write-Host ("PASS {0}   FAIL {1}   SKIP {2}" -f $pass, $fail, $skip) -ForegroundColor White
if ($fail -gt 0) { exit 1 }
