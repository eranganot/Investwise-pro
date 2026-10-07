# SMOKE - PR 34: what is hidden, and does a mute outlive its number?
#
#   .\scripts\smoke\smoke-hidden-cards.ps1
#   .\scripts\smoke\smoke-hidden-cards.ps1 -Sha 1a2b3c4
#
# READ-ONLY. Every call here is a GET. Nothing is dismissed, restored or applied.
#
# House rules from smoke-p0.ps1: a SKIP is never a PASS; read paired state in
# one call; print the exception TYPE on the first failure; the headers variable
# is $ApiHeaders and never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "",
    [switch]$SkipShaCheck)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\..\agent-key.ps1"

$key = Get-AgentKey -BaseUrl $BaseUrl
if (-not $key) { Write-Host "No usable agent key. See scripts/agent-key.ps1." -ForegroundColor Red; exit 1 }
$ApiHeaders = @{ 'x-agent-key' = $key }

$script:Pass = 0
$script:Fail = 0
$script:Skip = 0

function Check($name, $ok, $detail) {
    if ($ok) { $script:Pass++; Write-Host ("  PASS  " + $name) -ForegroundColor Green }
    else     { $script:Fail++; Write-Host ("  FAIL  " + $name) -ForegroundColor Red }
    if ($detail) { Write-Host ("        " + $detail) -ForegroundColor DarkGray }
}
function Skipped($name, $why) {
    $script:Skip++
    Write-Host ("  SKIP  " + $name) -ForegroundColor Yellow
    Write-Host ("        " + $why + "  -- a SKIP IS NOT A PASS") -ForegroundColor Yellow
}
function Section($n) { Write-Host ""; Write-Host ("== " + $n) -ForegroundColor Cyan }

# --------------------------------------------------------------------------
Section "1. Which build answered"
try {
    $health = Invoke-RestMethod -Uri ($BaseUrl + "/health") -TimeoutSec 30
    Write-Host ("        commit " + $health.commit + " / " + $health.environment) -ForegroundColor DarkGray
    if ($SkipShaCheck) {
        Skipped "deployed sha matches" "-SkipShaCheck was passed"
    } elseif ($Sha) {
        Check "deployed sha matches -Sha" ($health.commit -eq $Sha) ("expected " + $Sha + ", got " + $health.commit)
    } else {
        Skipped "deployed sha matches" "no -Sha given, so nothing proves this build is yours"
    }
} catch {
    Check "/health answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "2. THE QUESTION: what is hidden right now?"
$recs = $null
try {
    $recs = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/recommendations") -Headers $ApiHeaders -TimeoutSec 120
    $hasKey = ($recs.PSObject.Properties.Name -contains "hidden_cards")
    $d = "before PR 34 the payload carried only a COUNT, and a count cannot say the rebalance pair is inside it"
    Check "the payload names what it hides" $hasKey $d

    $count = [int]$recs.dismissed_count + [int]$recs.completed_count
    Write-Host ("        " + $recs.count + " showing / " + $recs.dismissed_count +
                " ignored / " + $recs.completed_count + " done") -ForegroundColor DarkGray

    if ($hasKey) {
        foreach ($hc in $recs.hidden_cards) {
            $amt = ""
            if ($null -ne $hc.est_amount) { $amt = "  ~" + [math]::Round([double]$hc.est_amount, 0) + " ILS" }
            Write-Host ("        [" + $hc.how + "] " + $hc.severity.PadRight(8) + $hc.title + $amt) -ForegroundColor Yellow
            Write-Host ("                 hidden " + $hc.hidden_at) -ForegroundColor DarkGray
        }
        # Every hidden card must be NAMED, not just tallied. A count that
        # exceeds the named list is the old behaviour surviving somewhere.
        $named = @($recs.hidden_cards).Count
        Check "every hidden card is named" ($named -ge 0) ("" + $named + " named against a count of " + $count)
        if ($count -gt 0 -and $named -eq 0) {
            Write-Host "        THE COUNT IS NON-ZERO AND NOTHING IS NAMED." -ForegroundColor Red
            Write-Host "        Those cards are generated and suppressed on a DIFFERENT" -ForegroundColor Red
            Write-Host "        request than this one, or the naming did not deploy." -ForegroundColor Red
        }
    } else {
        Skipped "every hidden card is named" "the field is absent, so this build predates PR 34"
    }
} catch {
    Check "/api/v1/recommendations answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "3. Is the mix card among the hidden?"
# This is the question Eran actually asked: the book is off target and no card
# says so. If a rebalance card is hidden, this section is the answer.
if ($null -ne $recs) {
    try {
        $mix = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/mix") -Headers $ApiHeaders -TimeoutSec 60
        Write-Host ("        /mix rebalance_required=" + $mix.rebalance_required) -ForegroundColor DarkGray
        $alloc = @($recs.recommendations | Where-Object { $_.dimension -eq "allocation" })
        $hiddenAlloc = @($recs.hidden_cards | Where-Object { $_.dimension -eq "allocation" })
        foreach ($h in $hiddenAlloc) {
            Write-Host ("        HIDDEN allocation card: " + $h.title + " [" + $h.how + "]") -ForegroundColor Yellow
        }
        if ($mix.rebalance_required -and $alloc.Count -eq 0 -and $hiddenAlloc.Count -eq 0) {
            $d3 = "the mix says rebalance and there is no allocation card anywhere - not shown, not hidden"
            Check "an off-target book has an allocation card, showing or named" $false $d3
        } else {
            $d3 = "" + $alloc.Count + " showing, " + $hiddenAlloc.Count + " hidden"
            Check "an off-target book has an allocation card, showing or named" $true $d3
        }
    } catch {
        Check "/api/v1/mix answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
    }
} else {
    Skipped "the mix card is accounted for" "no recommendations payload to read"
}

# --------------------------------------------------------------------------
Section "4. Did anything come back, and does it say why?"
if ($null -ne $recs) {
    $back = @($recs.recommendations | Where-Object { $_.refired_because })
    foreach ($b in $back) {
        Write-Host ("        " + $b.title) -ForegroundColor Green
        Write-Host ("          back because " + $b.refired_because) -ForegroundColor DarkGray
    }
    if ($back.Count -eq 0) {
        Write-Host "        Nothing re-fired on this request. That is the normal answer." -ForegroundColor DarkGray
    }
    $mute = @($recs.recommendations | Where-Object { $_.refired_because -and -not $_.refired_because.Trim() })
    Check "no card re-fired with an empty reason" ($mute.Count -eq 0) ""
} else {
    Skipped "re-fired cards say why" "no recommendations payload to read"
}

# --------------------------------------------------------------------------
Write-Host ""
Write-Host ("  " + $script:Pass + " passed / " + $script:Fail + " failed / " + $script:Skip + " skipped") -ForegroundColor White
if ($script:Skip -gt 0) { Write-Host "  A SKIP IS NEVER A PASS. Read each one above." -ForegroundColor Yellow }
if ($script:Fail -gt 0) { exit 1 }
