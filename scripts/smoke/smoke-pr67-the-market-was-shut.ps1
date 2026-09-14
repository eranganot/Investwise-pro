# SMOKE - PR 67: the market was shut, the ledger was blind, and nothing said
# "take some off the table".
#
#   .\scripts\smoke\smoke-pr67-the-market-was-shut.ps1
#   .\scripts\smoke\smoke-pr67-the-market-was-shut.ps1 -Sha abc1234
#   .\scripts\smoke\smoke-pr67-the-market-was-shut.ps1 -IncludeSnapshot
#
# READ-ONLY by default. -IncludeSnapshot adds ONE write: POST
# /portfolio/nav-history/snapshot, which is an upsert of today's row - the same
# thing the 22:10 job does, and the app's own "record now" button. It is the
# only way to observe the trading-day guard from outside.
#
# WHAT THIS CAN AND CANNOT PROVE TODAY. Four of the five fixes are observable
# immediately. The snapshot guard is not: today is a trading day, so the honest
# answer is "it recorded, which is correct". Run this again on a SATURDAY and
# check 6 flips to the proof - ok=false, "market closed". That asymmetry is
# stated in the output rather than hidden, because a check that cannot fail
# today is not a check that passed.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Sha,
    [switch]$SkipShaCheck,
    [switch]$IncludeSnapshot,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\..\agent-key.ps1"
$pass = 0; $fail = 0; $skip = 0
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host ""; Write-Host $m -ForegroundColor Cyan }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }

$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }

function Api($method, $path, $tmo = 120) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        $prefix = ''
        if ($code) { $prefix = "HTTP $code - " }
        Write-Host "        $method $path -> $prefix$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

Write-Host "Smoke: PR 67  ($BaseUrl)" -ForegroundColor White

# =========================================================================== #
Sec "0  is this even the build we are talking about"
# =========================================================================== #
$h = $null
try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
if ($null -eq $h) {
    Bad "/health unreachable - nothing below means anything"
    Write-Host ""
    Write-Host "ABORTING: cannot identify the running build." -ForegroundColor Red
    exit 1
}
Note "commit $($h.commit)   schema $($h.schema_revision)   builds_own_schema=$($h.builds_own_schema)"

if ($SkipShaCheck) {
    Skip "SHA check skipped by request"
} elseif ($Sha) {
    if ($h.commit -like "$($Sha.Trim())*") { Ok "serving $($h.commit)" }
    else { Bad "serving $($h.commit), expected $Sha - THE DEPLOY HAS NOT LANDED, stop reading here" }
} else {
    Skip "no -Sha given; pass one to prove the deploy landed"
}

# The migration. main.py's ADD COLUMN mirror adds trim_fraction at boot whether
# or not alembic runs, so this is about the MIGRATION, not the column - and the
# two have diverged before (0011 took out three endpoints, 0015 degraded for ten
# days). A revision behind head means the mirror is carrying production alone.
if ($h.schema_revision -eq '0022_rule_trim_fraction') {
    Ok "schema at 0022_rule_trim_fraction"
} elseif ($h.schema_revision -eq '0021_plan_cash_target') {
    Skip "schema still at 0021 - the column comes from main.py's mirror, not the migration"
    Note "not fatal (the mirror is what reaches production today) but worth knowing"
} else {
    Skip "schema at $($h.schema_revision)"
}

# =========================================================================== #
Sec "1+2  the chart: holes explained, and no invented deposits"
# =========================================================================== #
# 'range', not 'days'. The endpoint signature is (range: str = "1M") and an
# unknown query param is silently ignored - so ?days=35 "worked" by falling
# back to the default, which is the kind of thing that looks tested and is not.
$nav = Api GET '/api/v1/portfolio/nav-history?range=1M' 180
if ($null -eq $nav) { Bad "/portfolio/nav-history unreachable" }
elseif ($nav.ok -ne $true) { Bad "nav-history not ok: $($nav.reason)" }
else {
    Note "total $($nav.total_pct)%  over $($nav.measured_steps) of $($nav.steps) steps"

    # --- the captions that sent Eran here in the first place ---------------- #
    $um = @($nav.unmeasured_days)
    if ($um.Count -eq 0) {
        Ok "no unmeasured days in this window"
    } else {
        $blamesHistory = @($um | Where-Object { $_.reason -like '*not enough price history*' })
        $blamesMarket  = @($um | Where-Object { $_.reason -like '*market was shut*' })
        foreach ($u in $um) { Note "$($u.as_of): $($u.reason)" }
        if ($blamesMarket.Count -gt 0) {
            Ok "$($blamesMarket.Count) unmeasured day(s) name the market being shut"
        }
        if ($blamesHistory.Count -gt 0) {
            # Not automatically wrong - a genuinely short history still says
            # this. It IS wrong for a weekend or a holiday, which is every one
            # of the four days that produced the complaint.
            Bad "$($blamesHistory.Count) day(s) still blame the price history"
            Note "check whether those dates had a session. If they did not, this is the old caption."
        } else {
            Ok "nothing blames 'not enough price history'"
        }
    }

    # --- the ILS 208.56 that never moved ------------------------------------ #
    $flow = [math]::Abs([double]$nav.flow_total_ils)
    $unex = @($nav.unexplained_flows)
    Note "flow_total_ils $($nav.flow_total_ils)   unexplained_flows $($unex.Count)"
    if ($unex.Count -eq 0) {
        Ok "no estimated flows reported as money in or out"
    } else {
        # EXPECTED on the rows that already existed. The ledger fix only covers
        # trades recorded from now on; past swaps left no legs to find, so this
        # is a statement about history, not a regression.
        Skip "$($unex.Count) estimated flow(s) still on PRE-EXISTING rows"
        foreach ($u in $unex) { Note "$($u.as_of): $($u.amount_ils) ILS (estimated=$($u.estimated))" }
        Note "these are historical. The test is whether NEW swaps add to this list."
    }
    $res = @($nav.resolved_changes)
    if ($res.Count -gt 0) {
        $byLedger = @($res | Where-Object { $_.reason -like '*not in or out of the account*' })
        if ($byLedger.Count -gt 0) {
            Ok "$($byLedger.Count) shape change(s) explained by the TRADE LEDGER, not estimated"
        }
    }

    # =========================================================================== #
    Sec "6  the SPY line, and the trap next to it"
    # =========================================================================== #
    $bm = $nav.benchmark
    if ($null -eq $bm) {
        Bad "no 'benchmark' in the payload - either the deploy is old or the benchmark could not be priced"
    } else {
        Ok "benchmark present: $($bm.ticker) $($bm.total_pct)%"
        $gap = [math]::Round([double]$nav.total_pct - [double]$bm.total_pct, 2)
        Note "you $($nav.total_pct)%  vs  $($bm.ticker) $($bm.total_pct)%   ->  $gap points"

        # THE INVARIANT THAT MATTERS. Both lines must answer the same question
        # over the same market time. If the benchmark carries a number on a step
        # the account line refused to measure, the comparison is between two
        # different windows and every reading off it is wrong.
        $n = @($nav.dates).Count
        if (@($bm.pct).Count -ne $n) {
            Bad "benchmark has $(@($bm.pct).Count) points against $n dates - not aligned"
        } else {
            Ok "benchmark aligned to the same $n dates"
            $mismatch = 0
            for ($i = 0; $i -lt $n; $i++) {
                $accountNull = ($null -eq $nav.pct[$i])
                $benchNull   = ($null -eq $bm.pct[$i])
                if ($accountNull -ne $benchNull) {
                    $mismatch++
                    Note "step $($nav.dates[$i]): account null=$accountNull benchmark null=$benchNull"
                }
            }
            if ($mismatch -eq 0) {
                Ok "both lines break on exactly the same steps"
            } else {
                Bad "$mismatch step(s) where one line claims a measurement the other does not"
            }
        }
        if ($bm.measured_steps -eq $nav.measured_steps) {
            Ok "same measured_steps ($($bm.measured_steps)) - same market time"
        } else {
            Bad "benchmark measured $($bm.measured_steps) steps, account line $($nav.measured_steps)"
        }
    }
}

# =========================================================================== #
Sec "3  no ghost positions, and no rules armed on nothing"
# =========================================================================== #
$pf = Api GET '/api/v1/portfolio' 90
$rules = Api GET '/api/v1/rules' 90
if ($null -eq $pf) { Bad "/portfolio unreachable" }
else {
    $ghosts = @($pf.positions | Where-Object { [double]$_.quantity -le 0 -and $_.ticker -ne 'CASH' })
    if ($ghosts.Count -eq 0) {
        Ok "no zero-quantity positions"
    } else {
        # The code stops NEW ones. The existing USO row is Eran's to delete.
        Skip "$($ghosts.Count) zero-quantity position(s) still in the book"
        foreach ($g in $ghosts) { Note "$($g.ticker) at $($g.quantity) shares" }
        Note "PR 67 stops new ones; it does not clean an existing row. Deleting is irreversible."
    }

    if ($null -eq $rules) { Skip "/rules unreachable - cannot check armed rules" }
    else {
        $held = @($pf.positions | Where-Object { [double]$_.quantity -gt 0 } | ForEach-Object { $_.ticker.ToUpper() })
        $orphans = @($rules.rules | Where-Object { $_.active -and ($held -notcontains $_.ticker.ToUpper()) })
        if ($orphans.Count -eq 0) {
            Ok "every active rule is on a ticker you actually hold"
        } else {
            Bad "$($orphans.Count) active rule(s) armed on tickers not held"
            foreach ($o in $orphans) { Note "$($o.ticker) $($o.rule_type) level $($o.level)" }
            Note "if the ghost above is still there, this is its three rules and it clears with it"
        }
    }
}

# =========================================================================== #
Sec "5  take-profit trims, and the app says when"
# =========================================================================== #
$recs = Api GET '/api/v1/recommendations' 240
if ($null -eq $recs) { Bad "/recommendations unreachable" }
else {
    if (@($recs.degraded).Count -gt 0) {
        Bad "degraded agents: $($recs.degraded -join ', ') - a MISSING card says nothing about the state"
    } else {
        Ok "no degraded agents - an absent card is real information"
    }

    # A triggered take_profit must carry kind 'trim', never 'sell_position'.
    # Usually nothing is triggered, and that is a SKIP, not a pass.
    $tp = @($recs.recommendations | Where-Object { $_.apply -and $_.apply.rule_type -eq 'take_profit' })
    if ($tp.Count -eq 0) {
        Skip "no take-profit is currently triggered - cannot observe its apply kind"
        Note "SOXL's take-profit is at +80%, so this will stay unobservable for a long time"
    } else {
        foreach ($c in $tp) {
            if ($c.apply.kind -eq 'trim') { Ok "take-profit on $($c.apply.ticker) applies as a TRIM" }
            else { Bad "take-profit on $($c.apply.ticker) still applies as '$($c.apply.kind)'" }
        }
    }

    # The profit cards. Each must carry est_amount or it can never un-mute.
    $profit = @($recs.recommendations | Where-Object {
        $_.title -like '*cap*' -or $_.title -like '*take-profit*' -or
        $_.title -like '*trailing stop*' -or $_.title -like '*is up *' })
    if ($profit.Count -eq 0) {
        Skip "no profit cards on Today right now"
        Note "correct if nothing is near a level and nothing is up 25%+"
    } else {
        Ok "$($profit.Count) profit card(s) on Today"
        foreach ($c in $profit) {
            Note "$($c.title)"
            # 0.0 is as bad as null here: 'moved_on' un-mutes on the amount
            # MOVING, and zero never moves. PR 67 shipped the near-cap card at
            # 0.0 and the unit test asserting "is not None" passed on it.
            if ($null -eq $c.est_amount -or [double]$c.est_amount -le 0) {
                Bad "'$($c.title)' est_amount is '$($c.est_amount)' - it can never un-mute"
            }
        }
        if (@($profit | Where-Object { $null -eq $_.est_amount -or [double]$_.est_amount -le 0 }).Count -eq 0) {
            Ok "every profit card carries a non-zero est_amount"
        }
    }

    # A CARD THAT FIRES WHEN NOTHING HAS HAPPENED IS THE OTHER FAILURE.
    #
    # PR 67 shipped a "closing on your trailing stop" card measured as distance
    # from the level as a share of price. A trailing stop RATCHETS to
    # peak x (1 - trail), so a 10% trail sits ~10% below the price whenever the
    # position is at its peak: the card fired on VTI, BND and QQQ at once, with
    # QQQ having given back 7.8% of its trail. PR 67.1 measures the fraction of
    # the trail actually given back instead. This re-derives that from the
    # rules payload so the noise cannot come back unnoticed.
    if ($null -ne $rules) {
        foreach ($r in @($rules.rules | Where-Object { $_.rule_type -eq 'trailing_stop' -and $_.active })) {
            $trail = [double]$r.level / 100.0
            if ($trail -le 0 -or $trail -ge 1) { continue }
            $target = [double]$r.target
            $price = [double]$r.current_price
            if ($target -le 0 -or $price -le 0) { continue }
            $peak = $target / (1.0 - $trail)
            $span = $peak - $target
            if ($span -le 0) { continue }
            $given = ($peak - $price) / $span
            $card = @($profit | Where-Object { $_.title -like "*$($r.ticker)*" -and $_.title -like '*trailing stop*' })
            $pctGiven = [math]::Round($given * 100, 1)
            if ($given -lt 0.60 -and $card.Count -gt 0) {
                Bad "$($r.ticker) has given back only $pctGiven% of its trail and there IS a card"
                Note "this is the PR 67 noise defect - the card is measuring trail width, not danger"
            } elseif ($given -ge 0.60 -and $card.Count -eq 0) {
                Bad "$($r.ticker) has given back $pctGiven% of its trail and there is NO card"
            } elseif ($given -ge 0.60) {
                Ok "$($r.ticker) has given back $pctGiven% of its trail and the app says so"
            }
        }
        Ok "no trailing-stop card fires on a position sitting at its peak"
    }

    # A CHECK THE DATA CAN FAIL. If a holding is inside 2 points of a max_weight
    # cap it set, the near-cap card must exist. This is the SOXL case: 37.8%
    # against 38% on 2026-09-14, with the app silent.
    if ($null -ne $rules -and $null -ne $pf -and [double]$pf.nav_ils -gt 0) {
        foreach ($r in @($rules.rules | Where-Object { $_.rule_type -eq 'max_weight' -and $_.active })) {
            $pos = @($pf.positions | Where-Object { $_.ticker.ToUpper() -eq $r.ticker.ToUpper() })
            if ($pos.Count -eq 0) { continue }
            $w = 100.0 * [double]$pos[0].value_ils / [double]$pf.nav_ils
            $head = [double]$r.level - $w
            if ($head -gt 0 -and $head -le 2.0) {
                $card = @($profit | Where-Object { $_.title -like "*$($r.ticker)*" -and $_.title -like '*cap*' })
                if ($card.Count -gt 0) {
                    Ok "$($r.ticker) is $([math]::Round($head,2)) points under its cap and the app says so"
                } else {
                    Bad "$($r.ticker) is $([math]::Round($head,2)) points under its $($r.level)% cap and there is NO card"
                }
            }
        }
    }
}

# =========================================================================== #
Sec "3b  a ghost must not silence the buy idea it resembles"
# =========================================================================== #
# The least visible consequence of a zero-quantity row, and the one nobody
# would have gone looking for. MEASURED 2026-09-14: USO sat at 0.0 shares and
# ranked 85.4 on the app's own commodity screen -- ABOVE COMT at 79.2 -- and
# the live card recommended COMT. _buy_idea_recs skips a ticker the book
# already holds, which is right; 'held' was built from every row regardless of
# quantity, so the ghost made the app skip its own top-ranked candidate.
$scr = Api GET '/api/v1/screener?limit=200' 240
if ($null -eq $scr) { Skip "/screener unreachable - cannot check the ranking" }
elseif ($null -eq $pf) { Skip "no portfolio - cannot check what is held" }
else {
    $com = @($scr.commodities)
    if ($com.Count -eq 0) { Skip "the commodity screen returned nothing" }
    else {
        $realHeld = @($pf.positions | Where-Object { [double]$_.quantity -gt 0 } |
                      ForEach-Object { $_.ticker.ToUpper() })
        $top = $com[0]
        Note ("commodity ranking: " + (($com | Select-Object -First 4 |
              ForEach-Object { "$($_.ticker) $($_.score)" }) -join '  '))
        $named = @($recs.recommendations | Where-Object { $_.title -like "*$($top.ticker)*" })
        if ($realHeld -contains $top.ticker.ToUpper()) {
            Ok "$($top.ticker) ranks top and you already hold it - no buy card expected"
        } elseif ($named.Count -gt 0) {
            Ok "$($top.ticker) ranks top ($($top.score)) and the app names it"
        } else {
            # Only a FAIL if a LOWER-ranked commodity got a card instead. No
            # card at all is a funding decision, not a suppression.
            $others = @($recs.recommendations | Where-Object {
                $t = $_.title
                @($com | Where-Object { $t -like "*$($_.ticker)*" }).Count -gt 0 })
            if ($others.Count -gt 0) {
                Bad "$($top.ticker) ranks top ($($top.score)) but the card names $($others[0].title)"
                Note "check for a zero-quantity $($top.ticker) row - that is what suppressed it before"
            } else {
                Skip "$($top.ticker) ranks top and no commodity card is on Today (funding, not suppression)"
            }
        }
    }
}

# =========================================================================== #
Sec "1b  the trading-day guard (the one that needs a Saturday)"
# =========================================================================== #
$today = (Get-Date).ToUniversalTime()
$isWeekend = ($today.DayOfWeek -eq 'Saturday' -or $today.DayOfWeek -eq 'Sunday')
if (-not $IncludeSnapshot) {
    Skip "not run (read-only). Re-run with -IncludeSnapshot to observe it."
    Note "today is $($today.DayOfWeek) UTC. On a weekend this is the proof; on a trading day it only shows it still records."
} else {
    $snap = Api POST '/api/v1/portfolio/nav-history/snapshot' 120
    if ($null -eq $snap) { Bad "snapshot endpoint unreachable" }
    elseif ($isWeekend) {
        if ($snap.ok -eq $false -and $snap.reason -eq 'market closed') {
            Ok "weekend: refused, '$($snap.detail)'"
        } else {
            Bad "weekend: recorded anyway (ok=$($snap.ok)) - the guard is not live"
        }
    } else {
        if ($snap.ok -eq $true) {
            Ok "trading day: recorded, as it should"
            Note "this does NOT prove the guard. Run again on a Saturday for that."
        } elseif ($snap.reason -eq 'market closed') {
            # A weekday holiday, or the benchmark says there was no session.
            Ok "weekday with no session: refused, '$($snap.detail)'"
        } else {
            Bad "trading day: did not record - $($snap.reason) $($snap.detail)"
        }
    }
}

# =========================================================================== #
Write-Host ""
Write-Host "=================================================" -ForegroundColor White
Write-Host "  PASS $pass   FAIL $fail   SKIP $skip" -ForegroundColor White
Write-Host "=================================================" -ForegroundColor White
if ($fail -gt 0) {
    Write-Host "PR 67 smoke FAILED." -ForegroundColor Red
    exit 1
}
Write-Host "PR 67 smoke clean." -ForegroundColor Green
Write-Host "Still outstanding by design:" -ForegroundColor DarkGray
Write-Host "  * the snapshot guard needs a SATURDAY to prove (check 1b)" -ForegroundColor DarkGray
Write-Host "  * estimated flows on PRE-EXISTING rows do not clear - only new swaps are covered" -ForegroundColor DarkGray
Write-Host "  * a zero-quantity row, if any, is yours to delete" -ForegroundColor DarkGray
exit 0
