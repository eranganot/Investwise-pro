# SMOKE - C29 (D130): "Your book vs the S&P 500" -- one card, the measured changes ranked by the amount at the bar.
#
#   .\scripts\smoke\smoke-c29-book-vs-spy-review.ps1 -Sha <short sha of the merge>
#
#   1  the stored optimiser result was made on this build (it has a review) -- else SKIP
#      (nightly 05:10 UTC, or Plan > the optimiser's refresh)
#   2  the review has the book's amount and yearly return at the bar, and SPY's
#   3  the options are best first, each adds 2% or more at the bar
#   4  Today has one review card holding the book against the index; with options it is a
#      book_review choice whose ranked lines match; the old "No sleeve change" note is gone
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "C29"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$o = Api GET "/api/v1/plan/optimizer"
$rv = $o.review

Sec "1  a review on this build"
if (-not $rv) { Skip ("the stored result ({0}) predates this build" -f $o.as_of); Stop-Smoke }
Ok ("reviewed {0}; {1} changes measured" -f $o.as_of, $rv.measured)

Sec "2  the book and the index at the bar"
$b = $rv.before
if ($b.amount_at_confidence -and $null -ne $b.roi_at_confidence_pct) { Ok ("book {0:N0} at the bar ({1}%/yr)" -f [double]$b.amount_at_confidence, $b.roi_at_confidence_pct) } else { Bad "the book's amount or return at the bar is missing" }
$bm = $rv.benchmark
if ($bm -and $bm.ticker -eq "SPY" -and $null -ne $bm.roi_at_confidence_pct) { Ok ("SPY {0:N0} ({1}%/yr)" -f [double]$bm.amount_at_confidence, $bm.roi_at_confidence_pct) }
elseif ($bm) { Bad ("benchmark is {0}, not SPY" -f $bm.ticker) } else { Skip "no benchmark in this solve" }

Sec "3  ranked by what each adds"
$opts = @($rv.options)
$step = [double]$rv.amount_step_pct
$prev = [double]::MaxValue; $okRank = $true; $okStep = $true
foreach ($x in $opts) {
    $g = [double]$x.gain_ils
    if ($g -gt $prev + 0.01) { $okRank = $false }
    if ($g / [double]$b.amount_at_confidence * 100 -lt $step - 0.01 -and $x.move -ne "set") { $okStep = $false }
    $prev = $g
}
if (-not $opts.Count) { Ok "no change adds enough (the card says so)" }
elseif ($okRank -and $okStep) { Ok ("{0} option(s), best adds {1:N0}" -f $opts.Count, [double]$opts[0].gain_ils) }
else { Bad ("ranked: {0}; each over {1}%: {2}" -f $okRank, $step, $okStep) }

Sec "4  the card"
$t = Api GET "/api/v1/recommendations"
$all = @(@($t.recommendations) + @($t.more_recommendations) + @(@($t.headsups) | Where-Object { $_.card } | ForEach-Object { $_.card }))
$cards = @($all | Where-Object { $_.meta -and $_.meta.review })
$old = @($all | Where-Object { $_.title -like "No sleeve change we can measure*" })
if ($old.Count) { Bad "the old ceiling note is still served" } else { Ok "no old ceiling note" }
if (-not $cards.Count) {
    if ([double]$b.roi_at_confidence_pct -lt [double]$bm.roi_at_confidence_pct) { Bad "below the index and no review card" } else { Skip "no review card (above the index, nothing to gain)" }
} else {
    $c = $cards[0]
    if ($c.title -like "Your book vs*") { Ok $c.title } else { Bad ("title: {0}" -f $c.title) }
    if ($opts.Count) {
        $ap = $c.apply
        if ($ap.kind -eq "book_review" -and @($ap.ranked).Count -ge 1 -and @($ap.ranked).Count -le $opts.Count) { Ok ("{0} ranked choice(s); first: {1}" -f @($ap.ranked).Count, $ap.ranked[0].label) } else { Bad "the card is not a ranked choice" }
    } else { Ok "a note, nothing to accept" }
}
Stop-Smoke
