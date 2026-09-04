# SMOKE - PR 6: sleeves that mean 64%.
#
#   .\scripts\smoke\smoke-sleeves.ps1 -Sha <sha>          # READ-ONLY (default)
#   .\scripts\smoke\smoke-sleeves.ps1 -SkipShaCheck
#   .\scripts\smoke\smoke-sleeves.ps1 -Sha <sha> -Fund    # WRITES: funds the sleeves
#
# READ-ONLY by default. -Fund is the only writing switch and it is the whole
# point of the PR: everything else here just proves the app can now SEE the gap.
#
# WHAT THIS EXISTS TO CATCH. The Sleeves card read "Your sleeves - 64% of the
# book" while the sleeves held 15% of it. Two defects:
#
#   * sleeve_pct is a TARGET and was printed in the present tense. Nothing in
#     the codebase returned what a sleeve HOLDS - the only place occupancy was
#     computed was inside a funding result string - so the 49-point gap was
#     invisible from every screen.
#   * Funding was gated by the class-overweight budget, which is right when the
#     proceeds LEAVE the class and wrong when they come straight back. Selling V
#     to buy MTUM is Equities to Equities; the class weight does not move. The
#     budget authorised ~5,077 against the ~13,968 the sleeves needed, so both
#     sleeves abstained, every time, silently.
#
# S3 IS THE ONE THAT MATTERS: a dry-run fund that reaches the target instead of
# abstaining. S5 is the only check that proves the book actually moved, and it
# needs -Fund.
#
# House rules: a SKIP is never a PASS. Read paired state in one call. Print the
# exception TYPE on the first failure. Headers are $ApiHeaders, never $h.

param(
    [string]$Sha = "",
    [switch]$SkipShaCheck,
    [switch]$Fund,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\..\agent-key.ps1"
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
$pass = 0; $fail = 0; $skip = 0
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }

function Api($method, $path, $body = $null, $tmo = 180) {
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

function Pct($x) { if ($null -eq $x) { return "n/a" } ; return ("{0:N1}%" -f ([double]$x * 100)) }

Write-Host ""
Write-Host "SMOKE - sleeves that mean 64% (PR 6)" -ForegroundColor White
Write-Host "  $BaseUrl" -ForegroundColor DarkGray

# --------------------------------------------------------------------------- #
Sec "S0. The build under test is the one serving"
# --------------------------------------------------------------------------- #
$health = Api GET "/health"
if ($null -eq $health) {
    Bad "S0.1 /health did not answer - nothing below is meaningful"
} else {
    Ok "S0.1 /health answered, serving $($health.commit)"
    if ($SkipShaCheck) { Skip "S0.2 -SkipShaCheck set" }
    elseif (-not $Sha) { Skip "S0.2 no -Sha given" }
    elseif ($health.commit -eq $Sha.Substring(0, [Math]::Min(7, $Sha.Length))) { Ok "S0.2 serving the commit under test" }
    else { Bad "S0.2 serving $($health.commit), expected $Sha - STOP, everything below is about the old build" }
}

# --------------------------------------------------------------------------- #
Sec "S1. The payload can tell TARGET from ACTUAL"
# --------------------------------------------------------------------------- #
$sl = Api GET "/api/v1/plan/sleeves"
if ($null -eq $sl) {
    Bad "S1.0 /api/v1/plan/sleeves did not answer"
} else {
    $missing = @(@('allocated_held_pct','core_held_pct','nav_ils') | Where-Object { -not $sl.PSObject.Properties[$_] })
    if ($missing.Count -gt 0) {
        Bad ("S1.1 missing {0} - this deploy predates PR 6" -f ($missing -join ', '))
    } else {
        Ok "S1.1 the payload carries held as well as target"
    }

    Write-Host ("        sleeves: target {0}  held {1}   |   core: target {2}  held {3}" -f `
        (Pct ([double]$sl.allocated_pct / 100)), (Pct $sl.allocated_held_pct), `
        (Pct ([double]$sl.core_pct / 100)), (Pct $sl.core_held_pct)) -ForegroundColor DarkGray

    foreach ($s in $sl.sleeves) {
        $h = if ($null -ne $s.held_pct) { Pct $s.held_pct } else { "n/a" }
        Write-Host ("        {0,-24} target {1,7}   held {2,7}" -f $s.strategy_id, ("{0:N1}%" -f $s.sleeve_pct), $h) -ForegroundColor DarkGray
    }

    if ($sl.sleeves.Count -eq 0) {
        Skip "S1.2 no sleeves on this book, so there is no occupancy to report"
    } else {
        $rowsMissing = @($sl.sleeves | Where-Object { $null -eq $_.held_pct })
        if ($rowsMissing.Count -gt 0) {
            Bad ("S1.2 {0} sleeve row(s) have no held_pct" -f $rowsMissing.Count)
        } else {
            Ok "S1.2 every sleeve row reports what it holds"
        }
    }
}

# --------------------------------------------------------------------------- #
Sec "S2. The gap is NAMED rather than printed as a fact"
# --------------------------------------------------------------------------- #
if ($null -eq $sl -or $sl.sleeves.Count -eq 0) {
    Skip "S2.1 no sleeves to check"
} else {
    $t = [double]$sl.allocated_pct / 100.0
    $h = [double]$sl.allocated_held_pct
    $gapPts = [Math]::Round(($t - $h) * 100.0, 1)
    if ([Math]::Abs($gapPts) -lt 0.5) {
        Ok ("S2.1 sleeves are at their target ({0})" -f (Pct $h))
    } else {
        Skip ("S2.1 {0} points of your book are set to a strategy that is not holding it yet ({1} held against a {2} target). That is the TRUE state - the card now says so instead of printing the target. S3 says whether it can be closed." -f $gapPts, (Pct $h), (Pct $t))
    }
}

# --------------------------------------------------------------------------- #
Sec "S3. Can the target actually be reached? (THE ONE THAT MATTERS)"
# --------------------------------------------------------------------------- #
$dry = Api POST "/api/v1/plan/sleeves/fund?dry_run=true"
if ($null -eq $dry) {
    Bad "S3.0 the dry-run fund did not answer"
} elseif ($dry.sleeves.Count -eq 0) {
    Skip "S3.1 no sleeves to fund"
} else {
    foreach ($s in $dry.sleeves) {
        Write-Host ("        {0,-24} {1}{2}" -f $s.strategy_id, $s.status,
            $(if ($s.reason) { " - $($s.reason)" } else { "" })) -ForegroundColor DarkGray
    }
    $skipped = @($dry.sleeves | Where-Object { $_.status -eq 'skipped' })
    if ($skipped.Count -eq 0) {
        Ok ("S3.1 every sleeve can be funded - resulting {0:N1}% against an intended {1:N1}%" -f $dry.resulting_sleeve_pct, $dry.intended_sleeve_pct)
    } else {
        Bad ("S3.1 {0} sleeve(s) still abstain. If the reason mentions raising or a shortfall, the same-class swap did not engage." -f $skipped.Count)
    }

    $f = $dry.funding
    if ($null -eq $f) {
        Skip "S3.2 nothing to fund"
    } elseif ($null -eq $f.PSObject.Properties['same_class_swap_ils']) {
        Bad "S3.2 the funding plan does not report same_class_swap_ils - this deploy predates PR 6"
    } else {
        Write-Host ("        swap {0:N2} of NAV ({1}) | buying class {2} | tax {3:N2} | shortfall {4:N2}" -f `
            $f.same_class_swap_ils, (Pct $f.swap_pct_of_nav), $f.buying_class, $f.tax_ils, $f.shortfall_ils) -ForegroundColor DarkGray
        if ([double]$f.swap_pct_of_nav -gt 0.05) {
            Ok ("S3.2 the plan names its turnover: {0} of the book is a same-class swap, not a rebalance" -f (Pct $f.swap_pct_of_nav))
        } else {
            Skip "S3.2 little or no same-class swapping in this plan (cash may be covering it)"
        }
        if ($dry.funding_summary) {
            Write-Host ("        {0}" -f $dry.funding_summary) -ForegroundColor DarkGray
        }
    }
}

# --------------------------------------------------------------------------- #
Sec "S4. An empty sleeve keeps saying so"
# --------------------------------------------------------------------------- #
$recs = Api GET "/api/v1/recommendations" $null 240
if ($null -eq $recs) {
    Bad "S4.0 /api/v1/recommendations did not answer"
} else {
    $cold = @($recs.recommendations | Where-Object { $_.meta -and $_.meta.cold_start })
    $empty = @()
    if ($null -ne $sl) { $empty = @($sl.sleeves | Where-Object { $null -ne $_.held_pct -and [double]$_.held_pct -lt 0.005 }) }

    if ($empty.Count -eq 0) {
        Skip "S4.1 no empty sleeve, so there is no cold-start card to expect"
    } elseif ($cold.Count -eq 0) {
        Bad ("S4.1 {0} sleeve(s) hold nothing but no cold-start card is shown - it may be buried. POST /api/v1/recommendations/restore-completed once." -f $empty.Count)
    } else {
        $notPersistent = @($cold | Where-Object { -not $_.persistent })
        if ($notPersistent.Count -gt 0) {
            Bad "S4.1 the cold-start card is not marked persistent - Accept can still bury it"
        } else {
            Ok ("S4.1 {0} cold-start card(s), all persistent - Accept cannot file them as done" -f $cold.Count)
        }
    }
}

# --------------------------------------------------------------------------- #
Sec "S5. Actually move the book (WRITES - opt in)"
# --------------------------------------------------------------------------- #
if (-not $Fund) {
    Skip "S5.1 not run. Everything above proves the app can SEE the gap and says it can close it; only this proves it did. Re-run with -Fund when you have read S3's plan and its tax number."
} else {
    $before = if ($null -ne $sl) { [double]$sl.allocated_held_pct } else { $null }
    $res = Api POST "/api/v1/plan/sleeves/fund?dry_run=false"
    if ($null -eq $res) {
        Bad "S5.1 the fund call did not answer"
    } else {
        Ok ("S5.1 fund executed - resulting {0:N1}%" -f $res.resulting_sleeve_pct)
        $after = Api GET "/api/v1/plan/sleeves"
        if ($null -ne $after) {
            Write-Host ("        held before {0} -> after {1}   (target {2})" -f `
                (Pct $before), (Pct $after.allocated_held_pct), (Pct ([double]$after.allocated_pct / 100))) -ForegroundColor DarkGray
            $gap = [Math]::Abs([double]$after.allocated_held_pct - ([double]$after.allocated_pct / 100.0))
            if ($gap -lt 0.02) {
                Ok "S5.2 the sleeves now HOLD what they are set to"
            } else {
                Bad ("S5.2 still {0:N1} points short after funding" -f ($gap * 100))
            }
        }
    }
}

Write-Host ""
Write-Host "  PASS $pass   FAIL $fail   SKIP $skip" -ForegroundColor $(if ($fail -gt 0) { 'Red' } else { 'Green' })
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "    * The Sleeves card should read '<held>% held against a <target>%" -ForegroundColor Cyan
Write-Host "      target' - NOT '64% of the book'. A sleeve holding nothing should" -ForegroundColor Cyan
Write-Host "      say 'holds nothing' on its own row." -ForegroundColor Cyan
Write-Host "    * Holdings should show gain on what you PUT IN, with no" -ForegroundColor Cyan
Write-Host "      cost-basis breakdown line under it." -ForegroundColor Cyan
Write-Host ""
Write-Host "  THE SLEEVES DO NOT MOVE ON THEIR OWN. This PR makes 64% reachable" -ForegroundColor Yellow
Write-Host "  and makes the gap visible. Closing it is still a decision you take:" -ForegroundColor Yellow
Write-Host "  read S3's plan, its turnover and its tax, then run with -Fund or" -ForegroundColor Yellow
Write-Host "  press 'Fund all sleeves' on the Plan tab." -ForegroundColor Yellow
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE: no strategy manages the core (core is null)." -ForegroundColor DarkYellow
Write-Host "  That is the next PR." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -gt 0) { exit 1 }
exit 0
