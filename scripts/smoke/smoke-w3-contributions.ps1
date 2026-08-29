# SMOKE - PR 15c / W3, against the LIVE deploy. READ-ONLY.
#
#   .\scripts\smoke\smoke-w3-contributions.ps1
#   .\scripts\smoke\smoke-w3-contributions.ps1 -Sha 1234abc
#   .\scripts\smoke\smoke-w3-contributions.ps1 -Rate 24000
#
# Four claims, all false before this deploy:
#
#   1. The card admits it assumes no deposits. The engine has said "no
#      rebalancing or cash flows during the horizon" since Phase 5; plan.py
#      built its own assumptions string and dropped that line.
#   2. A deposit rate can be projected, and the base projection does NOT move
#      when one is asked for - otherwise "with and without" is two different
#      books, not one book two ways.
#   3. An unmeasurable rate is REFUSED BY NAME. Never a silent zero, and never
#      an assumed savings rate.
#   4. The rival lever is on the same card, so Phase W's claim that deposits
#      move terminal value more than any admissible excess is checkable rather
#      than asserted - and can come back "excess".
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; paired state read in ONE
# call; the exception TYPE printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "",
    [double]$Rate = 12000)

$ApiHeaders = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
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

function Money($v) { if ($null -eq $v) { "n/a" } else { "{0:N0}" -f [double]$v } }

Write-Host ""
Write-Host "InvestWise Pro - the projection sees your deposits" -ForegroundColor White
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

Sec "1  the base projection admits it assumes no deposits"
$base = Api GET '/api/v1/plan/projection'
if ($null -eq $base) {
    Bad "/plan/projection did not answer"
} elseif ($base.message) {
    Skip "no NAV to project - $($base.message). This is a SKIP, not a PASS."
} else {
    Write-Host "        median $(Money $base.projected_median) over $($base.years) years" -ForegroundColor DarkGray
    $asm = ($base.assumptions -join ' ')
    if ($asm -match 'cash flows') { Ok "the engine's own cash-flow caveat reaches the card" }
    else { Bad "assumptions do not mention cash flows - the caveat is being dropped again: $asm" }

    if ($null -ne $base.with_contributions) { Ok "with_contributions block present" }
    else { Bad "with_contributions missing - this is the OLD build" }

    if ($null -ne $base.excess_comparison) { Ok "excess_comparison block present" }
    else { Bad "excess_comparison missing - the claim has no rival to be checked against" }
}

Sec "2  an unmeasurable rate is refused BY NAME, never as a zero"
if ($null -eq $base -or $base.message) {
    Skip "no base projection to read. SKIP, not PASS."
} else {
    $wc = $base.with_contributions
    $m = $wc.measured
    if ($wc.available) {
        Write-Host "        a rate WAS measured: $(Money $wc.per_year)/yr from $($m.rows) ledger rows" -ForegroundColor DarkGray
        if ($m.reason -eq $null -and $m.ok) { Ok "measured, and the ledger backs it" }
        else { Bad "available=true but the measurement says reason=$($m.reason)" }
        if ($null -ne $m.per_year_excluding_first) {
            Ok "both readings reported ($(Money $m.per_year) incl. first entry, $(Money $m.per_year_excluding_first) excl.)"
        } else { Bad "only one reading - the opening-capital ambiguity is being hidden" }
    } else {
        $known = @('NO_LEDGER','SINGLE_ENTRY','TOO_SHORT','NOT_POSITIVE')
        if ($known -contains $m.reason) { Ok "refused by name: $($m.reason)" }
        else { Bad "no rate and no recognised reason (got '$($m.reason)') - this is the silent zero" }
        if ($m.detail) { Write-Host "        $($m.detail)" -ForegroundColor DarkGray }
        else { Bad "a reason with no detail is not an explanation" }
        # NO_LEDGER must carry a null total, not 0. That is the None/0 distinction.
        if ($m.reason -eq 'NO_LEDGER' -and $null -ne $m.total_ils) {
            Bad "NO_LEDGER with total_ils=$($m.total_ils) - None has collapsed into a number"
        }
    }
}

Sec "3  an explicit rate projects, and does not move the base"
$with = Api GET "/api/v1/plan/projection?contribution_per_year=$Rate"
if ($null -eq $with -or $with.message) {
    Skip "no projection with a rate. SKIP, not PASS."
} else {
    $wc = $with.with_contributions
    if ($wc.available -and $wc.source -eq 'override') { Ok "the rate you passed is honoured and labelled 'override'" }
    else { Bad "available=$($wc.available) source=$($wc.source) - an explicit rate was not taken" }

    if ($wc.projected_median -gt $with.projected_median) {
        Ok "deposits raise the median: $(Money $with.projected_median) -> $(Money $wc.projected_median) (+$(Money $wc.uplift_median))"
    } else { Bad "deposits did not raise the median - the money went nowhere" }

    $expected = $Rate * $with.years
    if ([math]::Abs([double]$wc.total_added - $expected) -lt 1.0) {
        Ok "total deposited is exactly rate x years ($(Money $wc.total_added))"
    } else { Bad "total_added $(Money $wc.total_added), expected $(Money $expected) - the step count is eating money" }

    # THE ONE THAT MATTERS MOST. If the base moves, "with and without" is not a
    # comparison, and every uplift figure above is meaningless.
    if ($null -ne $base -and -not $base.message) {
        if ($with.projected_median -eq $base.projected_median -and
            $with.projected_low -eq $base.projected_low -and
            $with.projected_high -eq $base.projected_high) {
            Ok "the no-deposit projection is byte-identical with and without the parameter"
        } else {
            Bad "the base projection MOVED when a contribution was requested - $(Money $base.projected_median) vs $(Money $with.projected_median)"
        }
    } else { Skip "no base to compare against. SKIP, not PASS." }
}

Sec "4  the rival lever is on screen"
if ($null -eq $with -or $with.message) {
    Skip "nothing to compare. SKIP, not PASS."
} else {
    $xc = $with.excess_comparison
    Write-Host "        +$($xc.extra_return_pp)pp of return is worth $(Money $xc.uplift_median)" -ForegroundColor DarkGray
    Write-Host "        $(Money $Rate)/yr of deposits is worth $(Money $with.with_contributions.uplift_median)" -ForegroundColor DarkGray
    if ($xc.moves_more -eq 'contributions' -or $xc.moves_more -eq 'excess') {
        Ok "moves_more = $($xc.moves_more)"
    } else { Bad "moves_more is '$($xc.moves_more)' - the comparison did not resolve" }

    # The verdict must FOLLOW the numbers. A field that always says
    # "contributions" would be a slogan, not a measurement.
    $cu = [double]$with.with_contributions.uplift_median
    $xu = [double]$xc.uplift_median
    $should = if ($cu -gt $xu) { 'contributions' } else { 'excess' }
    if ($xc.moves_more -eq $should) { Ok "the verdict follows the arithmetic" }
    else { Bad "moves_more says '$($xc.moves_more)' but the numbers say '$should'" }
}

Sec "5  a zero rate is not a projection with deposits in it"
$zero = Api GET '/api/v1/plan/projection?contribution_per_year=0'
if ($null -eq $zero -or $zero.message) {
    Skip "no answer. SKIP, not PASS."
} elseif ($zero.with_contributions.available) {
    Bad "a rate of 0 produced a with-deposits projection - zero is not a deposit"
} else { Ok "a rate of 0 leaves the projection alone" }

Write-Host ""
Write-Host "  PASS $($script:Pass)   FAIL $($script:Fail)   SKIP $($script:Skipped)" -ForegroundColor White
if ($script:Skipped -gt 0) {
    Write-Host "  A SKIP IS NOT A PASS. $($script:Skipped) check(s) never ran." -ForegroundColor Yellow
}
Write-Host "  Not financial advice." -ForegroundColor DarkGray
Write-Host ""
if ($script:Fail -gt 0) { exit 1 }
exit 0
