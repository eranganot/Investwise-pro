# SMOKE - PR 9, against the LIVE deploy. READ-ONLY.
#
#   .\scripts\smoke\smoke-card-scope.ps1
#   .\scripts\smoke\smoke-card-scope.ps1 -Sha 1234abc
#
# Three claims, all of which were FALSE on this book on 2026-08-25:
#
#   1. There is ONE target mix, it covers the whole book, and every card that
#      quotes a target quotes that one. (Was: the core's 36% target measured
#      against 100% of the book, every leg 2.78x too big, and a second card
#      quoting the objective's 10% for a class the first called 25%.)
#   2. Every card names which strategy it serves.
#   3. No card sells a sleeve that is sitting on its target. (Was: the loudest
#      card on Today, HIGH, wanted 24.231 TQQQ shares out of a sleeve targeting
#      38.4% and holding 38.59%.)
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

function Api($method, $path, $timeout = 240) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $timeout
    } catch {
        Write-Host "        $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor DarkGray
        return $null
    }
}

Write-Host ""
Write-Host "InvestWise Pro - one target, scoped cards, no self-contradiction" -ForegroundColor White
Write-Host "$BaseUrl" -ForegroundColor DarkGray

# =========================================================================== #
Sec "0  the deploy landed"
# =========================================================================== #
$health = Api GET '/health' 60
if ($null -eq $health) { Bad "/health unreachable - nothing below can be trusted" }
elseif ($Sha) {
    if ("$($health.commit)" -eq $Sha) { Ok "serving $Sha" }
    else { Bad "serving '$($health.commit)', expected '$Sha' - the rest describes the OLD build" }
} else {
    Write-Host "        serving $($health.commit) (pass -Sha to gate on it)" -ForegroundColor DarkGray
}

# =========================================================================== #
Sec "1  the book target is the SLEEVES PLUS THE CORE, and adds to 100%"
# =========================================================================== #
$recs = Api GET '/api/v1/recommendations'
$sl = Api GET '/api/v1/plan/sleeves'
if ($null -eq $recs -or $null -eq $sl) {
    Bad "cannot read /recommendations + /plan/sleeves - nothing below can run"
} else {
    $bt = $recs.book_target
    if ($null -eq $bt) {
        Bad "/recommendations carries no book_target - this deploy predates PR 9"
    } else {
        $sum = 0.0
        foreach ($p in $bt.target.PSObject.Properties) { $sum += [double]$p.Value }
        if ([Math]::Abs($sum - 1.0) -lt 0.005) {
            Ok ("target sums to 100%: " + (($bt.target.PSObject.Properties | ForEach-Object { "$($_.Name) $([Math]::Round([double]$_.Value*100,1))%" }) -join ", "))
        } else {
            Bad "target sums to $([Math]::Round($sum*100,1))%, not 100% - AllocationEngine will size sells that fund nothing"
        }

        # The two halves must add to the whole. This is the arithmetic that was
        # wrong: the core's share was used as if it were the book's.
        $mismatch = @()
        foreach ($p in $bt.target.PSObject.Properties) {
            $s = [double]($bt.sleeves.$($p.Name)); $c = [double]($bt.core.$($p.Name))
            if ([Math]::Abs(($s + $c) - [double]$p.Value) -gt 0.005) {
                $mismatch += "$($p.Name): sleeves $s + core $c <> target $($p.Value)"
            }
        }
        if ($mismatch.Count -eq 0) { Ok "every class: sleeves + core = target" }
        else { foreach ($x in $mismatch) { Bad $x } }

        # And the split must match what the Plan tab says.
        $alloc = [double]$sl.allocated_pct / 100.0
        $coreP = [double]$bt.core_pct
        if ([Math]::Abs((1.0 - $alloc) - $coreP) -lt 0.005) {
            Ok "core is $([Math]::Round($coreP*100,1))% of the book, matching /plan/sleeves"
        } else {
            Bad "book_target says the core is $([Math]::Round($coreP*100,1))% but /plan/sleeves leaves $([Math]::Round((1-$alloc)*100,1))%"
        }
    }
}

# =========================================================================== #
Sec "2  /mix and Today quote the SAME target"
# =========================================================================== #
$mix = Api GET '/api/v1/mix'
if ($null -eq $mix -or $null -eq $recs.book_target) {
    Skip "cannot compare - one of the two endpoints is unavailable"
} else {
    $bad = @()
    foreach ($p in $recs.book_target.target.PSObject.Properties) {
        $there = [double]($mix.target_allocation.$($p.Name))
        if ([Math]::Abs($there - [double]$p.Value) -gt 0.005) {
            $bad += "$($p.Name): Today $($p.Value) vs /mix $there"
        }
    }
    if ($bad.Count -eq 0) { Ok "/mix and Today agree on every class" }
    else { foreach ($x in $bad) { Bad $x } }
    if ($null -eq $mix.target_from_sleeves) { Bad "/mix does not say where its target came from" }
    else { Ok "/mix carries its provenance (target_from_sleeves / target_from_core)" }
}

# =========================================================================== #
Sec "3  every card says which strategy it serves"
# =========================================================================== #
if ($null -eq $recs) {
    Skip "no recommendations payload"
} else {
    $cards = @($recs.recommendations)
    if ($cards.Count -eq 0) { Skip "no cards on Today right now - nothing to scope" }
    else {
        $unscoped = @($cards | Where-Object { -not $_.scope -or -not $_.scope.kind })
        if ($unscoped.Count -eq 0) { Ok "all $($cards.Count) cards carry a scope" }
        else { Bad "$($unscoped.Count) card(s) carry no scope: $((@($unscoped | ForEach-Object { $_.id })) -join ', ')" }

        $kinds = @($cards | ForEach-Object { $_.scope.kind } | Sort-Object -Unique)
        $bogus = @($kinds | Where-Object { $_ -notin @('sleeve', 'core', 'book') })
        if ($bogus.Count -eq 0) { Ok "scopes seen: $($kinds -join ', ')" }
        else { Bad "unknown scope kind(s): $($bogus -join ', ')" }

        # A sleeve-scoped card must name a strategy that is actually a sleeve.
        $sleeveIds = @(@($sl.sleeves) | ForEach-Object { $_.strategy_id })
        $orphan = @($cards | Where-Object {
            $_.scope.kind -eq 'sleeve' -and $_.scope.strategy_id -notin $sleeveIds })
        if ($orphan.Count -eq 0) { Ok "every sleeve-scoped card names a sleeve you run" }
        else {
            foreach ($o in $orphan) {
                Write-Host "        '$($o.title)' -> $($o.scope.strategy_id)" -ForegroundColor DarkGray
            }
            Bad "$($orphan.Count) card(s) claim a sleeve this book does not run"
        }

        # And a core-scoped card must name the core, when one is chosen.
        $coreId = "$($sl.core.strategy_id)"
        if ($coreId) {
            $wrong = @($cards | Where-Object {
                $_.scope.kind -eq 'core' -and "$($_.scope.strategy_id)" -ne $coreId })
            if ($wrong.Count -eq 0) { Ok "core-scoped cards all name '$coreId'" }
            else { Bad "$($wrong.Count) core-scoped card(s) name something other than '$coreId'" }
        } else {
            Skip "no core chosen, so core-scoped cards have nothing to name"
        }
    }
}

# =========================================================================== #
Sec "4  no card is fighting a sleeve, and no two cards are fighting each other"
# =========================================================================== #
if ($null -eq $recs) {
    Skip "no recommendations payload"
} else {
    $con = @($recs.contradictions)
    if ($con.Count -eq 0) {
        Ok "no contradictions - the expected state"
    } else {
        foreach ($x in $con) {
            $line = "$($x.kind): $($x.detail)"
            if ($x.kind -eq 'two_targets_for_one_class') {
                # This one is a BUG in a generator, not a choice for the user.
                Bad $line
            } elseif ($x.suppressed) {
                Write-Host "  NOTE  suppressed a card: $line" -ForegroundColor Yellow
                $script:Skipped++
            } else {
                Write-Host "  NOTE  flagged for you to arbitrate: $line" -ForegroundColor Yellow
                $script:Skipped++
            }
        }
    }

    # THE LIVE ONE, asserted directly rather than trusted to the checker: no
    # visible card may propose selling a ticker that is at or under its sleeve
    # target. If the checker regresses, this still catches it.
    $targets = @{}
    foreach ($s in @($sl.sleeves)) {
        foreach ($t in @($s.tickers)) { $targets[$t.ToUpper()] = [double]$s.sleeve_pct }
    }
    $pf = Api GET '/api/v1/portfolio'
    $held = @{}
    if ($pf -and $pf.nav_ils) {
        foreach ($p in @($pf.positions)) {
            $held["$($p.ticker)".ToUpper()] = ([double]$p.value_ils / [double]$pf.nav_ils) * 100.0
        }
    }
    $fighting = @()
    foreach ($cd in @($recs.recommendations)) {
        $k = "$($cd.apply.kind)"
        if ($k -notin @('trim', 'sell_position', 'sell_losers', 'rebalance_to_objective')) { continue }
        $tk = "$($cd.apply.ticker)".ToUpper()
        if (-not $tk -or -not $targets.ContainsKey($tk)) { continue }
        if ($held[$tk] -le $targets[$tk] + 0.5) {
            $fighting += "'$($cd.title)' sells $tk at $([Math]::Round($held[$tk],1))% against a $($targets[$tk])% sleeve target"
        }
    }
    if ($fighting.Count -eq 0) { Ok "no visible card sells a sleeve that is on target" }
    else { foreach ($f in $fighting) { Bad $f } }
}

# =========================================================================== #
Sec "5  the rules screen reads weight caps in POINTS, not dollars"
# =========================================================================== #
$rules = Api GET '/api/v1/rules' 120
if ($null -eq $rules) {
    Skip "/api/v1/rules unreachable"
} else {
    $caps = @(@($rules.rules) | Where-Object { $_.rule_type -eq 'max_weight' })
    if ($caps.Count -eq 0) { Skip "no weight caps on this book" }
    else {
        $noUnit = @($caps | Where-Object { $_.unit -ne 'pct_of_portfolio' -or $null -eq $_.current })
        if ($noUnit.Count -eq 0) { Ok "$($caps.Count) weight cap(s), each reporting its own weight in points of book" }
        else { Bad "$($noUnit.Count) weight cap(s) do not report their own unit - the UI will fall back to price" }

        # A cap BELOW its sleeve target fires forever. PR 9 raises a HIGH card
        # offering to re-level; this is the underlying condition.
        $stale = @()
        foreach ($cp in $caps) {
            $tk = "$($cp.ticker)".ToUpper()
            if ($targets -and $targets.ContainsKey($tk) -and [double]$cp.level -lt $targets[$tk] - 0.05) {
                $stale += "$tk capped at $($cp.level)% against a $($targets[$tk])% sleeve"
            }
        }
        if ($stale.Count -eq 0) {
            Ok "no cap sits below the sleeve it governs"
        } else {
            foreach ($x in $stale) {
                Write-Host "  NOTE  $x" -ForegroundColor Yellow
            }
            $script:Skipped++
            $carded = @($recs.recommendations | Where-Object { "$($_.apply.kind)" -eq 'relevel_sleeve_caps' })
            if ($carded.Count -gt 0) { Ok "...and Today is offering to re-level them in one tap" }
            else { Bad "stale caps exist but no re-level card is being offered" }
        }
    }
}

Write-Host ""
Write-Host "  $($script:Pass) passed / $($script:Fail) failed / $($script:Skipped) skipped-or-noted" -ForegroundColor White
Write-Host ""
if ($script:Fail -ne 0) { exit 1 }
exit 0
