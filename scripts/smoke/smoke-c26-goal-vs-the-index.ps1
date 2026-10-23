# SMOKE - C26 (D126): the goal is held against a US stock index fund; no lever offers a target below it.
#
#   .\scripts\smoke\smoke-c26-goal-vs-the-index.ps1 -Sha <short sha of the merge>
#
#   1  the stored goal solve carries `benchmark` (VTI on the solve's own paths) with its return at the bar
#        (SKIP until a solve runs on this build: nightly, or Plan > Goal > Re-solve now)
#   2  Today: no "Set a target" card whose target is below the index's
#   3  the goal note: below the index it says so and offers no lower target
#   4  NOT CHANGED: the deposit / deadline levers are still offered when the solve has them
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "C26"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$f = Api GET "/api/v1/plan/frontier"
$bm = $f.benchmark
$lv = $f.levers

Sec "1  the solve measures the index"
if (-not $bm) { Skip ("no benchmark on the stored solve (solved {0}): wait for the nightly solve or Re-solve now" -f $f.computed_at); Stop-Smoke }
if ($null -ne $bm.roi_at_confidence_pct -and $bm.ticker) { Ok ("{0} ({1}) reaches {2}%/yr at the bar; this book {3}%/yr" -f $bm.label, $bm.ticker, $bm.roi_at_confidence_pct, $lv.roi_at_confidence_pct) }
else { Bad "benchmark without a return at the bar" }
$below = ($null -ne $lv.roi_at_confidence_pct) -and ([double]$lv.roi_at_confidence_pct -lt [double]$bm.roi_at_confidence_pct)
Note ("book {0} the index at the bar" -f $(if ($below) { "below" } else { "at or above" }))

$t = Api GET "/api/v1/recommendations"
$all = @(@($t.recommendations) + @($t.more_recommendations) + @(@($t.headsups) | ForEach-Object { $_.card }))

Sec "2  no target below the index"
$tc = @($all | Where-Object { $_.meta.lever -eq "target" })
$bad = @($tc | Where-Object { [double]$_.apply.fields.target_roi_pct -lt [double]$bm.roi_at_confidence_pct })
if ($bad.Count) { foreach ($x in $bad) { Bad ("offers {0}%/yr, below the index's {1}%/yr" -f $x.apply.fields.target_roi_pct, $bm.roi_at_confidence_pct) } }
elseif ($tc.Count) { Ok ("target card at {0}%/yr, at or above the index" -f $tc[0].apply.fields.target_roi_pct) }
else { Ok "no target card below the index" }

Sec "3  the goal note"
$note = @($all | Where-Object { $_.meta.optimizer -and $_.meta.ceiling })
if ($note.Count -eq 0) { Skip "no optimiser ceiling note today" }
else {
    $why = "$($note[0].why)"
    if ($below) {
        if ($why -match "not beating it" -and $why -notmatch "lower your target return") { Ok "says the book is not beating the index; no lower target" }
        else { Bad "below the index, and the note does not say so or still offers a lower target" }
    } else {
        if ($why -match "ahead of") { Ok "says the book is ahead of the index" } else { Note "above the index; no comparison sentence" }
    }
}

Sec "4  not changed: money and time levers"
$dep = @($all | Where-Object { $_.meta.lever -eq "deposit" -or $_.meta.lever -eq "deadline" })
if ($lv.extra_monthly_ils -or $lv.deadline_extra_years) {
    if ($dep.Count) { Ok ("{0} money/time lever card(s)" -f $dep.Count) } else { Note "levers measured but no card (muted, or the solve is about an earlier book)" }
} else { Skip "the solve found no deposit or deadline lever" }
Stop-Smoke
