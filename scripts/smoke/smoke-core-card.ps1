# SMOKE - PR 38: is there a card telling you to buy VTI, and did the
#                "Buy Equities" you marked done come back?
#
#   .\scripts\smoke\smoke-core-card.ps1
#   .\scripts\smoke\smoke-core-card.ps1 -Sha 1a2b3c4
#
# READ-ONLY. Every call here is a GET. Nothing is accepted, dismissed or bought.
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
Section "2. THE QUESTION: is a ticker named?"
$recs = $null
try {
    $recs = Invoke-RestMethod -Uri ($BaseUrl + "/api/v1/recommendations") -Headers $ApiHeaders -TimeoutSec 120
    Write-Host ("        " + $recs.count + " live cards") -ForegroundColor DarkGray
    foreach ($c in $recs.recommendations) {
        Write-Host ("        - [" + $c.severity + "] " + $c.title) -ForegroundColor DarkGray
    }
    Write-Host ""
    $core = @()
    foreach ($c in $recs.recommendations) {
        if (($c.title -like "*core*") -or ($c.title -like "*basket*")) { $core += $c }
    }
    if ($core.Count -eq 0) {
        Check "a card names the core's own basket" $false "no core-basket card in the live list"
    } else {
        foreach ($c in $core) {
            Write-Host ("        " + $c.title) -ForegroundColor Green
            Write-Host ("          why:    " + $c.why) -ForegroundColor DarkGray
            Write-Host ("          action: " + $c.action) -ForegroundColor White
            Write-Host ("          amount: " + $c.est_amount) -ForegroundColor DarkGray
        }
        Check "a card names the core's own basket" $true ""

        # The whole point: a TICKER, not an asset class.
        $named = $false
        foreach ($c in $core) { if ($c.action -match "[A-Z]{1,5}\.?$|VTI|BND") { $named = $true } }
        $d1 = "'Buy Equities' can be satisfied by buying more TQQQ - a class is not an instruction"
        Check "the action names an instrument, not a class" $named $d1

        # PR 39: THIS CARD NOW ACTS. The rule is no longer "declare nothing" but
        # "declare only what the app implements". Note that a card with
        # apply.kind = "none" is the app's own GUIDANCE marker, stamped by
        # _ensure_why_impact - not a claim to execute. The PR 38 version of this
        # check treated "none" as a claim and failed a card that was behaving
        # correctly; that was a bug in THIS SCRIPT.
        $acts = $false
        foreach ($c in $core) { if ($c.apply.kind -eq "core_to_basket") { $acts = $true } }
        $d2 = "guidance is not enough - the money is already in the book"
        Check "the card offers an action, not just advice" $acts $d2

        # EVERY LEG, BEFORE THE BUTTON. The How list is generated from the same
        # plan the executor runs, so anything Apply will do has to be visible
        # here first.
        foreach ($c in $core) {
            if ($c.apply.kind -ne "core_to_basket") { continue }
            Write-Host "        WHAT APPLY WOULD DO:" -ForegroundColor Yellow
            foreach ($h in $c.how) { Write-Host ("          - " + $h) -ForegroundColor White }
            $sells = $false; $buys = $false
            foreach ($h in $c.how) {
                if (($h -like "Sell*") -or ($h -like "Trim*")) { $sells = $true }
                if ($h -like "Buy*") { $buys = $true }
            }
            $d4 = "a rebalance that shows only one side is hiding the other"
            Check "the card lists both what it sells and what it buys" ($sells -and $buys) $d4
        }
    }
} catch {
    Check "/api/v1/recommendations answered" $false ($_.Exception.GetType().FullName + ": " + $_.Exception.Message)
}

# --------------------------------------------------------------------------
Section "3. Did the 'done' that never happened come back?"
if ($null -eq $recs) {
    Skipped "a stale done is re-fired with a reason" "no recommendations payload"
} else {
    $refired = @()
    foreach ($c in $recs.recommendations) { if ($c.refired_because) { $refired += $c } }
    $stillHidden = @()
    foreach ($c in $recs.hidden_cards) { if ($c.how -eq "done") { $stillHidden += $c } }

    if ($refired.Count -gt 0) {
        foreach ($c in $refired) {
            Write-Host ("        " + $c.title) -ForegroundColor Green
            Write-Host ("          back because: " + $c.refired_because) -ForegroundColor Yellow
        }
        Check "a re-fired card says why it came back" $true ""
    } else {
        Skipped "a stale done is re-fired" "nothing re-fired - fine only if every done was really done"
    }

    Write-Host ""
    Write-Host ("        still hidden as done: " + $stillHidden.Count) -ForegroundColor DarkGray
    foreach ($c in $stillHidden) {
        Write-Host ("          x " + $c.title + "  (" + $c.est_amount + ")  since " + $c.hidden_at) -ForegroundColor DarkGray
    }
    Write-Host "        READ THAT LIST. Anything there with a real amount that you" -ForegroundColor Yellow
    Write-Host "        did NOT actually do is a card this PR should have returned." -ForegroundColor Yellow
}

# --------------------------------------------------------------------------
Section "4. Do the leg targets agree with the class target?"
# Two answers to one core is the card-claim defect. book_target.core is the
# class-level fold of exactly the arithmetic the leg cards use.
if ($null -eq $recs) {
    Skipped "the leg targets agree with book_target" "no recommendations payload"
} else {
    $bt = $recs.book_target
    if ($null -eq $bt) {
        Skipped "the leg targets agree with book_target" "no book_target block"
    } else {
        Write-Host ("        core " + $bt.core_strategy_id + " at " + $bt.core_pct) -ForegroundColor DarkGray
        Write-Host ("        core contributes " + ($bt.core | ConvertTo-Json -Compress)) -ForegroundColor DarkGray
        Write-Host ("        class target     " + ($bt.target | ConvertTo-Json -Compress)) -ForegroundColor DarkGray
        $d3 = "the leg cards and the class card must be one arithmetic, not two"
        Check "book_target still reports the core's contribution" ($null -ne $bt.core) $d3
    }
}

# --------------------------------------------------------------------------
Write-Host ""
Write-Host ("  " + $script:Pass + " passed / " + $script:Fail + " failed / " + $script:Skip + " skipped") -ForegroundColor White
if ($script:Skip -gt 0) { Write-Host "  A SKIP IS NEVER A PASS. Read each one above." -ForegroundColor Yellow }
if ($script:Fail -gt 0) { exit 1 }
