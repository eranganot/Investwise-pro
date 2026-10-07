# SMOKE - PR 16, against the LIVE deploy. READ-ONLY.
#
#   .\scripts\smoke\smoke-measured-assumptions.ps1
#   .\scripts\smoke\smoke-measured-assumptions.ps1 -Sha 1234abc
#
# Four claims, all false before this deploy:
#
#   1. Every forward number says where it came from. The banner reading
#      "~10.87%/yr vs your 30%/yr target" never said it was an instrument-table
#      guess about buy-and-hold TQQQ.
#   2. A sleeve that HAS a current measurement is priced from it - and the
#      volatility that drives the Risk score moves with it.
#   3. A sleeve that has NO usable measurement is refused BY NAME and falls back
#      to the table labelled as the table. Never a silent zero.
#   4. The Risk score stays reproducible from the volatility on the payload, so
#      a reader can check the arithmetic rather than trusting it.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; paired state read in ONE
# call; the exception TYPE printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "")

. "$PSScriptRoot\..\agent-key.ps1"
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
$script:Pass = 0; $script:Fail = 0; $script:Skipped = 0

function Sec($m) { Write-Host ""; Write-Host "-- $m" -ForegroundColor Cyan }
function Ok($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Skip($m) { $script:Skipped++; Write-Host "  SKIP  $m" -ForegroundColor Yellow }

function Api($method, $path, $timeout = 180) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $timeout
    } catch {
        Write-Host "        $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor DarkGray
        return $null
    }
}

Write-Host ""
Write-Host "InvestWise Pro - the forward numbers stop guessing" -ForegroundColor White
Write-Host "$BaseUrl" -ForegroundColor DarkGray

Sec "0  the deploy landed"
$health = Api GET '/health' 60
if ($null -eq $health) { Bad "/health unreachable - nothing below can be trusted" }
elseif ($Sha) {
    if ("$($health.commit)" -eq $Sha) { Ok "serving $Sha" }
    else { Bad "serving '$($health.commit)', expected '$Sha' - the rest describes the OLD build" }
} else {
    Write-Host "        serving $($health.commit) (pass -Sha to gate on it)" -ForegroundColor DarkGray
}

Sec "1  the health card names the volatility behind the Risk score"
$hc = Api GET '/api/v1/health-check'
if ($null -eq $hc) { Bad "/health-check did not answer" }
elseif ($null -ne $hc.message) { Skip "no book to score - $($hc.message). SKIP, not PASS." }
else {
    Write-Host "        health $($hc.wealth_health_score), risk $($hc.risk_score), vol $($hc.avg_volatility_pct)% vs cap $($hc.volatility_cap_pct)%" -ForegroundColor DarkGray
    if ($null -ne $hc.volatility_basis) { Ok "volatility_basis = $($hc.volatility_basis)" }
    else { Bad "no volatility_basis - this is the OLD build" }
    if ($null -ne $hc.avg_volatility_pct_static) {
        Ok "the instrument-table figure is still shown for comparison ($($hc.avg_volatility_pct_static)%)"
    } else { Bad "avg_volatility_pct_static missing - the old number is nowhere to check against" }

    # 4. The score must remain reproducible from what the payload states.
    $vol = [double]$hc.avg_volatility_pct / 100.0
    $cap = [double]$hc.volatility_cap_pct / 100.0
    if ($cap -le 0) { Skip "no volatility cap set, so the Risk formula has no budget. SKIP." }
    else {
        $expected = if ($vol -le $cap) { 100.0 - 15.0 * ($vol / $cap) } else { 85.0 * (1.0 - ($vol - $cap) / $cap) }
        if ($expected -lt 0) { $expected = 0 }
        if ($expected -gt 100) { $expected = 100 }
        if ([math]::Abs([math]::Round($expected) - [double]$hc.risk_score) -le 1) {
            Ok "the Risk score reproduces from the volatility on the payload"
        } else {
            Bad "risk_score $($hc.risk_score) does not follow from $($hc.avg_volatility_pct)% vs $($hc.volatility_cap_pct)% (expected ~$([math]::Round($expected)))"
        }
    }
}

Sec "2  measured, or refused by name - never a silent zero"
$asm = $hc.assumptions
if ($null -eq $asm) { Skip "no assumptions block. SKIP, not PASS." }
else {
    Write-Host "        basis $($asm.basis), $($asm.measured_pct_of_book)% of the book measured" -ForegroundColor DarkGray
    if ($asm.basis -in @('measured','mixed','static')) { Ok "basis = $($asm.basis)" }
    else { Bad "unrecognised basis '$($asm.basis)'" }

    if ($asm.basis -eq 'static') {
        Write-Host "        nothing is measured yet. That is a legitimate state - but it must SAY so." -ForegroundColor DarkGray
        if ($asm.note -and $asm.note -match 'instrument table') { Ok "and it says so in words" }
        else { Bad "static basis with no explanation is exactly the silent fallback this PR removes" }
    } else {
        if ([double]$asm.measured_pct_of_book -gt 0) { Ok "$($asm.measured_pct_of_book)% priced from its own strategies" }
        else { Bad "basis says measured but measured_pct_of_book is 0" }
    }

    $known = @('NO_MEASUREMENT','MEASUREMENT_FAILED','ENGINE_VERSION_MOVED',
               'TOO_FEW_OBSERVATIONS','NO_OUT_OF_SAMPLE')
    $bad = @($asm.abstained | Where-Object { $known -notcontains $_.reason })
    if ($bad.Count -gt 0) { Bad "$($bad.Count) abstention(s) with an unrecognised reason - the first is '$($bad[0].reason)'" }
    else { Ok "every abstention carries a recognised reason ($(@($asm.abstained).Count) total)" }
    foreach ($a in @($asm.abstained)) {
        Write-Host "        $($a.id): $($a.reason) - $($a.detail)" -ForegroundColor DarkGray
        if (-not $a.detail) { Bad "$($a.id) abstained with a code and no explanation" }
    }

    if ($asm.caveat -and $asm.caveat -match 'FITTING_HELPS') { Ok "the W1 caveat travels with the payload" }
    else { Bad "no return caveat - a measured CAGR is being presented as a forecast" }
}

Sec "3  the plan banner and the projection both state their basis"
$plan = Api GET '/api/v1/plan'
if ($null -eq $plan) { Bad "/plan did not answer" }
else {
    if ($null -ne $plan.assumption_basis) {
        Ok "plan basis = $($plan.assumption_basis) (expected return ~$($plan.portfolio_expected_roi_pct)%/yr)"
        if ($null -ne $plan.portfolio_expected_roi_pct_static -and
            $plan.assumption_basis -ne 'static') {
            Write-Host "        the instrument table would have said ~$($plan.portfolio_expected_roi_pct_static)%/yr" -ForegroundColor DarkGray
        }
    } else { Bad "no assumption_basis on /plan - this is the OLD build" }
}

$proj = Api GET '/api/v1/plan/projection'
if ($null -eq $proj) { Bad "/plan/projection did not answer" }
elseif ($proj.message) { Skip "nothing to project - $($proj.message). SKIP, not PASS." }
else {
    if ($null -ne $proj.assumption_basis) { Ok "projection basis = $($proj.assumption_basis)" }
    else { Bad "the projection does not state its basis" }
    $joined = ($proj.assumptions -join ' ')
    if ($joined -match 'measured' -or $joined -match 'instrument table') {
        Ok "and the basis is in the assumptions the card prints"
    } else { Bad "assumptions do not mention where the figures came from: $joined" }
}

Sec "4  the two cards agree about the book"
if ($null -ne $plan -and $null -ne $hc -and $null -eq $hc.message) {
    # Both read the same measurement. If they disagree, one of them is stale.
    if ($null -eq $plan.measured_pct_of_book -or $null -eq $hc.measured_pct_of_book) {
        Skip "one side did not report a measured share. SKIP, not PASS."
    } elseif ([math]::Abs([double]$plan.measured_pct_of_book - [double]$hc.measured_pct_of_book) -lt 0.5) {
        Ok "Plan and Health agree that $($plan.measured_pct_of_book)% of the book is measured"
    } else {
        Bad "Plan says $($plan.measured_pct_of_book)% measured, Health says $($hc.measured_pct_of_book)% - two answers for one book"
    }
} else { Skip "not enough state to compare. SKIP, not PASS." }

Write-Host ""
Write-Host "  PASS $($script:Pass)   FAIL $($script:Fail)   SKIP $($script:Skipped)" -ForegroundColor White
if ($script:Skipped -gt 0) {
    Write-Host "  A SKIP IS NOT A PASS. $($script:Skipped) check(s) never ran." -ForegroundColor Yellow
}
Write-Host "  Not financial advice." -ForegroundColor DarkGray
Write-Host ""
if ($script:Fail -gt 0) { exit 1 }
exit 0
