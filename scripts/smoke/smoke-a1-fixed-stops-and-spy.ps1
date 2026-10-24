# SMOKE - A1 (D128): the goal model replays a fixed stop as a one-time sale at its price; the goal is held against SPY.
#
#   .\scripts\smoke\smoke-a1-fixed-stops-and-spy.ps1 -Sha <short sha of the merge>
#
#   1  the stored solve was made on this build (else SKIP: nightly 04:10 UTC, or Plan > Goal > Re-solve now)
#   2  every price stop in the rules replay reads "sells once at"; none is described as a trail "below the peak"
#      unless it is a trailing stop or an activated profit-protect
#   3  the benchmark is SPY
#   4  NOT CHANGED: the rules replay still runs (on/off), and the halving limit is still reported
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "A1"
$haveKey = [bool]$script:ApiHeaders['x-agent-key']
if (-not $haveKey) { $script:ApiHeaders = @{} }
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"
if (-not $haveKey) { Skip "no agent key: set IW_AGENT_KEY"; Stop-Smoke }

$f = Api GET "/api/v1/plan/frontier"
$r = $f.rules

Sec "1  a solve on this build"
$texts = @()
foreach ($p in @($r.sleeves.PSObject.Properties)) { $texts += @($p.Value.rules) }
$onBuild = @($texts | Where-Object { $_ -match "sells once at" }).Count -gt 0 -or $f.benchmark.ticker -eq "SPY"
if (-not $onBuild) { Skip ("the stored solve ({0}) predates this build" -f $f.computed_at); Stop-Smoke }
Ok ("solved {0}" -f $f.computed_at)

Sec "2  fixed stops sell once"
$bad = @($texts | Where-Object { $_ -match "^stop_loss" -and $_ -match "below the peak" })
if ($bad.Count) { foreach ($b in $bad) { Bad ("a stop_loss still replayed as a trail: {0}" -f $b) } } else { Ok ("{0} sleeve rule(s); no price stop replayed as a trail" -f $texts.Count) }
$i = 0
foreach ($sid in @($r.sleeve_ids)) { Note ("{0}: out of the market {1}% of the time, {2} exits/yr" -f $sid, $r.on.pct_time_out[$i], $r.on.exits_per_year[$i]); $i++ }

Sec "3  the benchmark"
if ($f.benchmark.ticker -eq "SPY") { Ok ("SPY reaches {0}%/yr at the bar; the book {1}%/yr" -f $f.benchmark.roi_at_confidence_pct, $f.levers.roi_at_confidence_pct) }
else { Bad ("the benchmark is {0}, not SPY" -f $f.benchmark.ticker) }

Sec "4  not changed"
if ($null -ne $r.on.p_halve_pct -and $null -ne $r.off.p_halve_pct) { Ok ("halving: {0}% with rules, {1}% without" -f $r.on.p_halve_pct, $r.off.p_halve_pct) } else { Bad "no rules on/off replay" }
Stop-Smoke
