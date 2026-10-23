# SMOKE - P1 (D122): the goal solve prices a grid of goals, shown as "Compare goals" on the Plan tab.
#
#   .\scripts\smoke\smoke-p1-goal-comparison.ps1 -Sha <short sha of the merge>
#
#   1  the stored goal verdict carries rules.goal_grid (written by the 04:10 UTC solve, or a re-solve, AFTER the deploy)
#   2  the owner's own goal's cell agrees with the headline (same replay; within 6 points on 500 vs 2000 paths)
#   3  an easier goal is never less likely than a harder one at the same date and deposit
#   4  NOT CHANGED: the headline still reads with and without rules; the shell is iw-v73 or later
#
# READ-ONLY (never re-solves). ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "P1 goal comparison"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$f = Api GET "/api/v1/plan/frontier"
if (-not $f -or -not $f.ok) { Bad "no goal verdict"; Stop-Smoke }
Note ("verdict age {0} h, source {1}" -f $f.age_hours, $f.source)
$g = $f.rules.goal_grid
$cells = @($g.cells)

Sec "1  the grid is stored"
if (-not $g) { Skip "no goal_grid yet: the 04:10 UTC solve (or a re-solve) after the deploy writes it" }
elseif ($cells.Count -eq 0) { Bad ("grid with no cells ({0} not computed)" -f $g.not_computed) }
else { Ok ("{0} cell(s) on {1} paths in {2}s; {3} not computed" -f $cells.Count, $g.paths, $g.seconds, $g.not_computed) }

Sec "2  the owner's own goal agrees with the headline"
$roi = [double]$f.goal.target / [double]$g.put_in_ils
$own = @($cells | Where-Object { [double]$_.extra_years -eq 0 -and [double]$_.extra_monthly_ils -eq 0 -and
    [math]::Abs([double]$_.target_ils - [double]$f.goal.target) -lt 2 })[0]
if (-not $g) { Skip "no grid yet" }
elseif (-not $own) { Note "the plan's %/yr is not one of the grid's rows"; Ok "nothing to compare" }
elseif ([math]::Abs([double]$own.p_goal_pct - [double]$f.headline.p_goal_pct) -le 6) { Ok ("own goal {0}% vs headline {1}%" -f $own.p_goal_pct, $f.headline.p_goal_pct) }
else { Bad ("own goal {0}% vs headline {1}%" -f $own.p_goal_pct, $f.headline.p_goal_pct) }

Sec "3  an easier goal is never less likely"
if (-not $g) { Skip "no grid yet" }
else {
    $bad = 0
    foreach ($grp in ($cells | Group-Object { "$($_.extra_years)|$($_.extra_monthly_ils)" })) {
        $sorted = @($grp.Group | Sort-Object { [double]$_.roi_pct } -Descending)
        for ($i = 1; $i -lt $sorted.Count; $i++) {
            if ([double]$sorted[$i].p_goal_pct + 3 -lt [double]$sorted[$i - 1].p_goal_pct) {
                Bad ("{0}%/yr ({1}%) less likely than {2}%/yr ({3}%) at {4}" -f $sorted[$i].roi_pct, $sorted[$i].p_goal_pct, $sorted[$i - 1].roi_pct, $sorted[$i - 1].p_goal_pct, $grp.Name); $bad++ }
        }
    }
    if ($bad -eq 0) { Ok "odds rise as the goal eases, in every column (3-point sampling tolerance)" }
    foreach ($c in @($cells | Where-Object { [double]$_.extra_years -eq 0 -and [double]$_.extra_monthly_ils -eq 0 })) {
        Note ("{0}%/yr by the deadline: {1}% with rules ({2}% without; halving {3}%)" -f $c.roi_pct, $c.p_goal_pct, $c.p_goal_without_rules_pct, $c.p_halve_without_rules_pct)
    }
}

Sec "4  not changed"
if ($null -ne $f.headline.p_goal_pct -and $null -ne $f.headline.without_rules_pct) { Ok ("headline {0}% with rules, {1}% without" -f $f.headline.p_goal_pct, $f.headline.without_rules_pct) }
else { Bad "headline incomplete" }
try { $sw = Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 30
      if ($sw.Content -match "iw-v(\d+)" -and [int]$Matches[1] -ge 73) { Ok ("shell iw-v{0}" -f $Matches[1]) } else { Bad "shell older than iw-v73" } } catch { Bad "/app/sw.js unreachable" }
Stop-Smoke
