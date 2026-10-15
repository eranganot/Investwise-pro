# SMOKE - PR 131: one holding split between the sleeves that share it (D95); one goal chance (D96).
#
#   .\scripts\smoke\smoke-pr131-one-number-per-sleeve.ps1 -Sha <short sha of the merge>
#
#   1  the sleeves' rows add up to what the sleeves hold together (no ticker counted twice)
#   2  Today's On track, the projection's goal chance and the Plan tab quote the SAME number;
#      an out-of-date one is labelled with its reason, never swapped for the rough no-rules run
#   3  the automatic re-solve skips a current goal (only asked when the goal is current)
#   4  nothing else changed: the shell is iw-v64, the sleeves stay behind the login
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 131"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  one holding, one sleeve's share"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY" }
else {
    $sl = Api GET "/api/v1/plan/sleeves"
    if ($sl) {
        $sum = 0.0
        foreach ($s in @($sl.sleeves)) {
            $sum += [double]$s.held_pct
            $sh = ($s.shared_with.PSObject.Properties | ForEach-Object { "$($_.Name) with $($_.Value -join '+')" }) -join '; '
            Note ("{0,-28} held {1,6:N1}% / target {2,5:N1}%  {3}" -f $s.strategy_id, (100 * [double]$s.held_pct), [double]$s.sleeve_pct, $sh)
        }
        $tot = [double]$sl.allocated_held_pct
        if ([math]::Abs($sum - $tot) -le 0.002) { Ok ("rows add to {0:N1}% = the sleeves' union {1:N1}%" -f (100 * $sum), (100 * $tot)) }
        else { Bad ("rows add to {0:N1}% but the sleeves hold {1:N1}% together - a ticker is counted twice" -f (100 * $sum), (100 * $tot)) }
        if ($null -ne ($sl.sleeves | Select-Object -First 1).shared_with) { Ok "rows carry shared_with" } else { Bad "shared_with missing" }
    } else { Bad "/plan/sleeves did not answer" }
}

Sec "2  one goal chance"
if (-not $haveKey) { Skip "no agent key" }
else {
    $pj = Api GET "/api/v1/plan/projection"
    $hc = Api GET "/api/v1/health-check"
    $pl = Api GET "/api/v1/plan"
    $gh = $pj.goal_headline; $gc = $pl.goal_chance
    if ($null -eq $gh -and $null -eq $gc) {
        Skip ("no quotable goal verdict; Today shows: {0}" -f $hc.on_track_detail)
    } elseif ($null -eq $gh -or $null -eq $gc) { Bad "projection and plan disagree on whether there is a goal chance" }
    else {
        Note ("goal model: {0}% with rules, {1}% without; current={2} {3}" -f $gh.p_goal_pct, $gh.without_rules_pct, $gh.current, $gh.stale_text)
        if ([double]$gh.p_goal_pct -eq [double]$gc.p_goal_pct) { Ok "projection and Plan quote $($gh.p_goal_pct)%" } else { Bad "projection $($gh.p_goal_pct)% vs Plan $($gc.p_goal_pct)%" }
        if ([math]::Round([double]$gh.p_goal_pct) -eq [double]$hc.on_track) { Ok "Today's On track is $($hc.on_track)" } else { Bad "Today's On track $($hc.on_track) vs goal $($gh.p_goal_pct)%" }
        if ($hc.on_track_detail -like "*rough estimate*") { Bad "Today still quotes the rough estimate: $($hc.on_track_detail)" } else { Ok "Today names the goal model" }
        if ($gh.current -eq $false) {
            if ($hc.on_track_detail -like "*As of*") { Ok "the old chance is labelled: $($gh.stale_text)" } else { Bad "an old chance shown without its age" }
        } else { Ok "the chance is current" }
    }
}

Sec "3  automatic re-solve"
# Called only when the goal is current, so the server must skip it: a solve
# would store a verdict, and this script writes nothing.
if (-not $haveKey) { Skip "no agent key" }
elseif (-not ($gh -and $gh.current)) { Skip "goal not current: calling it would run a solve (the screens do that)" }
else {
    $r = Api POST "/api/v1/plan/frontier/refresh?only_if_stale=true" 120
    if ($r -and $r.skipped -eq "CURRENT") { Ok "skips a current goal" } else { Bad ("did not skip: {0}" -f ($r | ConvertTo-Json -Compress -Depth 3)) }
}

Sec "4  nothing else changed"
try { $sw = Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 30
      if ($sw.Content -match "iw-v64") { Ok "shell iw-v64" } else { Bad "sw.js is not iw-v64" } } catch { Bad "/app/sw.js unreachable" }
try { Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan/sleeves" -TimeoutSec 60 | Out-Null; Bad "/plan/sleeves answered without a key" }
catch { $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}; if ($code -eq 401) { Ok "/plan/sleeves needs a key (401)" } else { Bad "/plan/sleeves answered $code" } }

Stop-Smoke
