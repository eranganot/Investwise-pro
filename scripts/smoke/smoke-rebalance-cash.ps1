# SMOKE - PR 17, against the LIVE deploy. READ-ONLY unless -AllowExecute.
#
#   .\scripts\smoke\smoke-rebalance-cash.ps1
#   .\scripts\smoke\smoke-rebalance-cash.ps1 -Sha 1234abc
#   .\scripts\smoke\smoke-rebalance-cash.ps1 -Sha 1234abc -AllowExecute
#
# The claim: accepting "Rebalance toward your target mix" used to set your CASH
# balance to zero and buy equities with it - no trade legs, no ledger entry, no
# tax, and a card that never mentions cash. After this deploy the rebalance
# reshuffles invested value only.
#
# Sections 0-3 are read-only and prove the build is live and describe what the
# cards currently claim. Section 4 is the only real proof of behaviour, because
# the defect is on the ACCEPT path - it needs -AllowExecute and it CHANGES THE
# TRACKED BOOK. It is safe after this deploy and was not before, which is the
# whole point, but it is still a real edit and it is opt-in.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; paired state read in ONE
# call; the exception TYPE printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "",
    [switch]$AllowExecute)

# No key baked in. Every other script in this folder carries the live
# AGENT_API_KEY as a literal - 34 files of it, and rotating that key is an open
# backlog item. This one does not add a 35th.
if (-not $env:IW_AGENT_KEY) {
    Write-Host ""
    Write-Host "  IW_AGENT_KEY is not set. Set it for this session:" -ForegroundColor Yellow
    Write-Host '    $env:IW_AGENT_KEY = "<your agent key>"' -ForegroundColor White
    Write-Host ""
    exit 1
}
$ApiHeaders = @{ 'x-agent-key' = $env:IW_AGENT_KEY }
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

function CashOf($portfolio) {
    if ($null -eq $portfolio) { return $null }
    $row = @($portfolio.positions | Where-Object { $_.ticker -eq 'CASH' })
    if ($row.Count -eq 0) { return $null }
    return [double]$row[0].quantity
}

Write-Host ""
Write-Host "InvestWise Pro - a rebalance stops spending your cash" -ForegroundColor White
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

# Paired state in ONE call: the cash balance and the NAV it sits inside have to
# come from the same read, or a refresh between them makes the pair incoherent.
Sec "1  the book, and the cash the rebalance used to spend"
$portfolio = Api GET '/api/v1/portfolio'
$cashBefore = CashOf $portfolio
if ($null -eq $portfolio) { Bad "/portfolio did not answer" }
elseif ($null -eq $cashBefore) {
    Skip "no CASH row on this book, so there is nothing for a rebalance to spend. SKIP, not PASS."
} else {
    Write-Host "        NAV $([math]::Round([double]$portfolio.nav_ils, 2)) ILS, cash $([math]::Round($cashBefore, 2)) ILS across $($portfolio.positions.Count) positions" -ForegroundColor DarkGray
    Ok "cash balance read: $([math]::Round($cashBefore, 2)) ILS"
}

Sec "2  what the rebalance cards actually claim"
$recs = Api GET '/api/v1/recommendations'
$rebal = @()
if ($null -eq $recs) { Bad "/recommendations did not answer" }
else {
    $rebal = @($recs.recommendations | Where-Object { $_.apply.kind -eq 'rebalance_to_objective' })
    if ($rebal.Count -eq 0) {
        Skip "no rebalance card on the feed right now. SKIP, not PASS - it means untested, not fixed."
    } else {
        foreach ($r in $rebal) {
            Write-Host "        $($r.title)" -ForegroundColor DarkGray
            Write-Host "          action: $($r.action)" -ForegroundColor DarkGray
        }
        # The defect was a claim violation: the card names ONE asset class and a
        # single trade, and the button rebalanced the whole book including cash.
        $mentionsCash = @($rebal | Where-Object {
            "$($_.action) $($_.impact) $($_.why)" -match '(?i)cash' })
        if ($mentionsCash.Count -eq 0) {
            Ok "$($rebal.Count) rebalance card(s), none of which claims to touch cash - so none of them may"
        } else {
            Write-Host "        a card does mention cash; read it before accepting" -ForegroundColor Yellow
            Ok "$($rebal.Count) rebalance card(s) found"
        }
    }
}

Sec "3  the cash floor this book is supposed to keep"
$hc = Api GET '/api/v1/health-check'
if ($null -eq $hc) { Bad "/health-check did not answer" }
elseif ($null -ne $hc.message) { Skip "no book to score - $($hc.message). SKIP, not PASS." }
else {
    Write-Host "        objective $($hc.objective), liquidity $($hc.liquidity_score)" -ForegroundColor DarkGray
    if ($null -ne $cashBefore -and $null -ne $portfolio) {
        # Not the app's own floor arithmetic re-implemented here - just the fact
        # that a book WITH cash is the case the bug applied to.
        if ($cashBefore -gt 0) { Ok "the book holds cash, so this deploy is the difference between keeping it and not" }
        else { Skip "cash is already zero. If a rebalance was accepted before this deploy, that is where it went. SKIP, not PASS." }
    } else { Skip "no cash reading to compare. SKIP, not PASS." }
}

Sec "4  the accept itself"
if (-not $AllowExecute) {
    Skip "read-only run. The defect is on the ACCEPT path and cannot be proved without one."
    Write-Host "        Re-run with -AllowExecute to accept ONE rebalance card and" -ForegroundColor DarkGray
    Write-Host "        check the cash balance survives it. THAT EDITS YOUR BOOK." -ForegroundColor DarkGray
} elseif ($rebal.Count -eq 0) {
    Skip "no rebalance card to accept. SKIP, not PASS."
} elseif ($null -eq $cashBefore) {
    Skip "no cash row, so nothing to protect. SKIP, not PASS."
} else {
    $card = $rebal[0]
    Write-Host "        about to ACCEPT: $($card.title)" -ForegroundColor Yellow
    Write-Host "        this changes the tracked book. Cash before: $([math]::Round($cashBefore, 2)) ILS" -ForegroundColor Yellow
    if ((Read-Host "        proceed? (y/N)") -ne "y") {
        Skip "declined at the prompt. SKIP, not PASS."
    } else {
        # No backtick continuation anywhere in this repo's PowerShell: a
        # backtick is an escape character inside a double-quoted string and a
        # line written with one has broken a script here before. Splat instead.
        $acceptArgs = @{
            Method     = 'POST'
            Uri        = "$BaseUrl/api/v1/recommendations/$($card.id)/accept"
            Headers    = $ApiHeaders
            TimeoutSec = 180
        }
        $res = $null
        try {
            $res = Invoke-RestMethod @acceptArgs
        } catch {
            Write-Host "        $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor DarkGray
        }
        if ($null -eq $res) { Bad "the accept did not answer - book state unknown, check /portfolio by hand" }
        else {
            # 1. The fix: the balance survives.
            $after = CashOf (Api GET '/api/v1/portfolio')
            if ($null -eq $after) { Bad "the CASH row is gone after the accept" }
            elseif ([math]::Abs($after - $cashBefore) -lt 0.01) {
                Ok "cash unchanged at $([math]::Round($after, 2)) ILS - before this deploy it would be 0.00"
            } else {
                Bad "cash moved $([math]::Round($after - $cashBefore, 2)) ILS on a rebalance ($([math]::Round($cashBefore,2)) -> $([math]::Round($after,2)))"
            }
            # 2. The instrument: the accept says what it did to NAV.
            if ($null -eq $res.nav_drift_ils) {
                Bad "no nav_drift_ils on the accept response - this is the OLD build"
            } else {
                Write-Host "        NAV $($res.nav_before_ils) -> $($res.nav_after_ils), drift $($res.nav_drift_ils) ILS" -ForegroundColor DarkGray
                Ok "the accept reported what it did to NAV"
                if ([math]::Abs([double]$res.nav_drift_ils) -lt 1.0) {
                    Ok "and conserved the book to within 1 ILS"
                } else {
                    Bad "NAV drifted $($res.nav_drift_ils) ILS beyond the tax reported - check the Railway log for the BREACH line naming the positions"
                }
            }
        }
    }
}

Write-Host ""
Write-Host "  PASS $($script:Pass)   FAIL $($script:Fail)   SKIP $($script:Skipped)" -ForegroundColor White
if ($script:Skipped -gt 0) {
    Write-Host "  A SKIP IS NOT A PASS. $($script:Skipped) check(s) never ran." -ForegroundColor Yellow
}
Write-Host "  Not financial advice." -ForegroundColor DarkGray
Write-Host ""
if ($script:Fail -gt 0) { exit 1 }
exit 0
