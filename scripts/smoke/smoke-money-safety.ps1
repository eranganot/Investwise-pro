# SMOKE - PR 1: accepting a card may not create or destroy money.
#
#   .\scripts\smoke\smoke-money-safety.ps1 -Sha <sha>      # READ-ONLY (default)
#   .\scripts\smoke\smoke-money-safety.ps1 -SkipShaCheck
#   .\scripts\smoke\smoke-money-safety.ps1 -AllowExecute    # ACCEPTS A REAL CARD
#
# READ-ONLY by default. Every check below is a GET. Nothing is bought, sold,
# dismissed or completed unless you pass -AllowExecute, and that flag is the only
# way this script writes anything.
#
# WHAT THIS EXISTS TO CATCH. On 2026-08-21 two Accept taps ten seconds apart
# raised NAV by roughly 4.8% each with no trade and no cash movement, because
# `_rebalance_to` summed quantity*price with no FX and wrote the resulting class
# factors straight back into share counts. A third path, `_apply_buy_funded`,
# went the other way: buying more of a ticker you already held REPLACED the
# position with just the shares bought. Both were invisible from outside - the
# book simply had different numbers in it afterwards. So the read-only checks
# here are structural: does the book still add up, and does every card the app is
# offering describe a trade it could actually fund.
#
# Rules carried over from the other smoke scripts, each learned the hard way:
#   * A SKIP is never a PASS.
#   * Never assert a guess - read the plan's own caps, never assume them.
#   * Read paired state in one breath (one /portfolio call feeds every check).
#   * Print the exception TYPE and message on the FIRST failure.
#   * Headers are $ApiHeaders, never $h - PowerShell variable names are
#     case-insensitive and a stray $h clobbered them once.

param(
    [string]$Sha = "",
    [switch]$SkipShaCheck,
    [switch]$AllowExecute,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
$ApiHeaders = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
$pass = 0; $fail = 0; $skip = 0
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }

function Api($method, $path, $body = $null, $tmo = 90) {
    try {
        if ($null -ne $body) {
            $json = ($body | ConvertTo-Json -Depth 6)
            return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -ContentType 'application/json' -Body $json -TimeoutSec $tmo
        }
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $($_.Exception.GetType().Name): $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

Write-Host ""
Write-Host "SMOKE - money safety (PR 1)" -ForegroundColor White
Write-Host "  $BaseUrl" -ForegroundColor DarkGray
if ($AllowExecute) {
    Write-Host "  -AllowExecute IS SET. M6 will accept a real card against your live book." -ForegroundColor Yellow
}

# --------------------------------------------------------------------------- #
Sec "M0. The build under test is the one serving"
# --------------------------------------------------------------------------- #
$health = Api GET "/health"
if ($null -eq $health) {
    Bad "M0.1 /health did not answer - nothing below is meaningful"
} else {
    Ok "M0.1 /health answered, serving $($health.commit) ($($health.environment))"
    if ($SkipShaCheck) {
        Skip "M0.2 -SkipShaCheck set"
    } elseif (-not $Sha) {
        Skip "M0.2 no -Sha given (pass -Sha <sha> to prove the deploy landed)"
    } elseif ($health.commit -eq $Sha.Substring(0, [Math]::Min(7, $Sha.Length))) {
        Ok "M0.2 serving the commit under test"
    } else {
        Bad "M0.2 serving $($health.commit), expected $Sha - Railway has not finished, STOP"
    }
}

# --------------------------------------------------------------------------- #
Sec "M1. The book adds up"
# --------------------------------------------------------------------------- #
# One /portfolio call feeds every check below, so no two assertions can be made
# against two different snapshots of a moving book.
$pf = Api GET "/api/v1/portfolio"
if ($null -eq $pf) {
    Bad "M1.0 /api/v1/portfolio did not answer - skipping M1 and M2"
} else {
    $sum = 0.0
    foreach ($p in $pf.positions) { $sum += [double]$p.value_ils }
    $navGap = [Math]::Abs($sum - [double]$pf.nav_ils)
    if ($navGap -lt 1.0) {
        Ok ("M1.1 positions sum to NAV ({0:N2})" -f $pf.nav_ils)
    } else {
        Bad ("M1.1 positions sum to {0:N2} but NAV says {1:N2} - a gap of {2:N2}" -f $sum, $pf.nav_ils, $navGap)
    }

    # The duplicate-row bug: `upsert_positions` scoped its lookup to ONE account,
    # so a buy landing in a different account created a second row for the same
    # ticker and NAV counted it twice. `buy_into_book` looks across the whole book.
    $dupes = $pf.positions | Group-Object ticker | Where-Object { $_.Count -gt 1 }
    if ($dupes) {
        Bad "M1.2 duplicate position rows: $(($dupes | ForEach-Object { "$($_.Name) x$($_.Count)" }) -join ', ')"
    } else {
        Ok "M1.2 one row per ticker ($($pf.count) positions)"
    }

    $neg = $pf.positions | Where-Object { [double]$_.quantity -lt 0 -or [double]$_.value_ils -lt 0 }
    if ($neg) {
        Bad "M1.3 negative quantity or value: $(($neg | ForEach-Object { $_.ticker }) -join ', ')"
    } else {
        Ok "M1.3 no negative quantities or values"
    }

    if ([double]$pf.cash_ils -lt 0) {
        Bad ("M1.4 cash is negative ({0:N2}) - a buy overdrew the balance" -f $pf.cash_ils)
    } else {
        Ok ("M1.4 cash is not negative ({0:N2})" -f $pf.cash_ils)
    }
}

# --------------------------------------------------------------------------- #
Sec "M2. Cost basis against contributions"
# --------------------------------------------------------------------------- #
# Not a PR 1 assertion - PR 2 is what makes these two agree. Printed here as the
# running measure of how much phantom capital is still on the book, so the number
# is visible every time this runs rather than only when someone goes looking.
if ($null -ne $pf) {
    $basis = 0.0
    foreach ($p in $pf.positions) { $basis += [double]$p.invested_ils }
    $contrib = [double]$pf.invested_ils
    $phantom = $basis - $contrib
    Write-Host ("        cost basis {0:N2} | contributions {1:N2} | gap {2:N2}" -f $basis, $contrib, $phantom) -ForegroundColor DarkGray
    if ([Math]::Abs($phantom) -lt 1.0) {
        Ok "M2.1 cost basis and the contributions ledger agree"
    } else {
        Skip ("M2.1 {0:N2} of cost basis is not backed by a recorded deposit (PR 2 + PR 3 close this)" -f $phantom)
    }
}

# --------------------------------------------------------------------------- #
Sec "M3. No card proposes a trade it cannot fund"
# --------------------------------------------------------------------------- #
# The readable half of PR 1: a funded buy must be payable out of its own named
# sells plus the cash it claims. A card whose buy exceeds its funding is one the
# apply handler would have to invent the difference for.
$recs = Api GET "/api/v1/recommendations"
if ($null -eq $recs) {
    Bad "M3.0 /api/v1/recommendations did not answer"
} else {
    if ($recs.degraded -and $recs.degraded.Count -gt 0) {
        Write-Host "        degraded agents: $($recs.degraded -join ', ')" -ForegroundColor DarkYellow
    }
    $funded = @($recs.recommendations | Where-Object { $_.apply -and $_.apply.kind -eq 'buy_funded' })
    if ($funded.Count -eq 0) {
        Skip "M3.1 no buy_funded card on the board right now"
    } else {
        foreach ($r in $funded) {
            $want = [double]$r.apply.amount_ils
            $raise = [double]$r.apply.from_cash_ils
            foreach ($s in @($r.apply.sells)) {
                $raise += ([double]$s.value_ils - [double]$s.tax_ils)
            }
            if ($raise + 1.0 -ge $want) {
                Ok ("M3.1 '{0}' funds {1:N0} with {2:N0}" -f $r.title, $want, $raise)
            } else {
                Bad ("M3.1 '{0}' buys {1:N0} but only raises {2:N0} - short {3:N0}" -f $r.title, $want, $raise, ($want - $raise))
            }
        }
    }

    # A funded buy into a ticker already held is the exact shape that used to
    # overwrite the position. Name it so the reviewer knows the risky card is live.
    if ($null -ne $pf) {
        $held = @($pf.positions | ForEach-Object { $_.ticker })
        foreach ($r in $funded) {
            if ($held -contains $r.apply.ticker) {
                Write-Host ("        NOTE '{0}' tops up {1}, which you already hold - the overwrite shape" -f $r.title, $r.apply.ticker) -ForegroundColor DarkGray
            }
        }
    }
}

# --------------------------------------------------------------------------- #
Sec "M4. The mix engine and the book quote the same NAV"
# --------------------------------------------------------------------------- #
# `_rebalance_to` had its own NAV, computed without FX. Two NAVs is how one of
# them gets to be wrong without anything looking broken.
$mix = Api GET "/api/v1/mix"
if ($null -eq $mix -or $null -eq $pf) {
    Skip "M4.1 need both /mix and /portfolio"
} else {
    $gap = [Math]::Abs([double]$mix.nav - [double]$pf.nav_ils)
    if ($gap -lt 1.0) {
        Ok ("M4.1 /mix and /portfolio agree on NAV ({0:N2})" -f $mix.nav)
    } else {
        Bad ("M4.1 /mix says {0:N2}, /portfolio says {1:N2}" -f $mix.nav, $pf.nav_ils)
    }
}

# --------------------------------------------------------------------------- #
Sec "M5. Sleeve funding previews without spending money it does not have"
# --------------------------------------------------------------------------- #
# dry_run, so this is read-only. The preview must not plan to draw more cash
# than the book holds - the clamp PR 1 removed used to hide exactly that.
$prev = Api POST "/api/v1/plan/sleeves/fund" @{ dry_run = $true }
if ($null -eq $prev) {
    Skip "M5.1 funding preview unavailable"
} elseif ($null -eq $pf) {
    Skip "M5.1 need /portfolio to compare"
} else {
    $fromCash = 0.0
    if ($null -ne $prev.funding -and $null -ne $prev.funding.from_cash_ils) { $fromCash = [double]$prev.funding.from_cash_ils }
    if ($fromCash -le [double]$pf.cash_ils + 0.01) {
        Ok ("M5.1 preview draws {0:N2} of cash against a {1:N2} balance" -f $fromCash, $pf.cash_ils)
    } else {
        Bad ("M5.1 preview draws {0:N2} but the book holds {1:N2}" -f $fromCash, $pf.cash_ils)
    }
    if ($null -ne $prev.resulting_sleeve_pct) {
        Write-Host ("        preview would leave sleeves at {0}%" -f $prev.resulting_sleeve_pct) -ForegroundColor DarkGray
    }
}

# --------------------------------------------------------------------------- #
Sec "M6. Accepting a card conserves NAV (WRITES - opt in)"
# --------------------------------------------------------------------------- #
# This is the only check that proves the fix end to end, and the only one that
# changes your book. It reads NAV, accepts one actionable card, reads NAV again,
# and asserts the difference is the tax the card itself reported. Nothing else.
if (-not $AllowExecute) {
    Skip "M6.1 not run - pass -AllowExecute to accept a real card (the local suite covers this offline)"
} elseif ($null -eq $recs) {
    Skip "M6.1 no recommendations to accept"
} else {
    $target = @($recs.recommendations | Where-Object { $_.actionable -eq $true }) | Select-Object -First 1
    if ($null -eq $target) {
        Skip "M6.1 no actionable card on the board"
    } else {
        Write-Host "        about to accept: '$($target.title)' [$($target.apply.kind)]" -ForegroundColor Yellow
        $before = Api GET "/api/v1/portfolio"
        $res = Api POST "/api/v1/recommendations/$($target.id)/accept"
        Start-Sleep -Seconds 2
        $after = Api GET "/api/v1/portfolio"
        if ($null -eq $res -or $null -eq $before -or $null -eq $after) {
            Bad "M6.1 accept or one of the reads failed - inspect the book by hand before doing anything else"
        } else {
            $tax = 0.0
            if ($null -ne $res.tax_ils) { $tax = [double]$res.tax_ils }
            $delta = [double]$after.nav_ils - [double]$before.nav_ils
            $leak = $delta + $tax
            Write-Host ("        NAV {0:N2} -> {1:N2}  delta {2:+0.00;-0.00}  tax {3:N2}" -f $before.nav_ils, $after.nav_ils, $delta, $tax) -ForegroundColor DarkGray
            if ([Math]::Abs($leak) -lt 1.0) {
                Ok ("M6.1 accept conserved NAV (leak {0:+0.00;-0.00})" -f $leak)
            } else {
                Bad ("M6.1 accept moved {0:N2} ILS that no trade accounts for" -f $leak)
            }
        }
    }
}

# --------------------------------------------------------------------------- #
Write-Host ""
Write-Host "  PASS $pass   FAIL $fail   SKIP $skip" -ForegroundColor $(if ($fail -gt 0) { 'Red' } else { 'Green' })
Write-Host ""
if ($fail -gt 0) { exit 1 }
exit 0
