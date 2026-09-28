# SMOKE - PR 74: the goal is the money you put in; one goal everywhere; no card moves it.
#
#   .\scripts\smoke\smoke-pr74-goals-on-money-put-in.ps1 -Sha abc1234
#
# READ-ONLY. Accepts nothing, saves nothing.
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\..\agent-key.ps1"
$pass = 0; $fail = 0; $skip = 0
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host ""; Write-Host $m -ForegroundColor Cyan }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
function Api($method, $path, $tmo = 300) {
    try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo }
    catch { Write-Host "        $method $path -> $($_.Exception.Message)" -ForegroundColor DarkRed; return $null }
}
function Near($a, $b, $tol) { return ($null -ne $a) -and ($null -ne $b) -and ([math]::Abs([double]$a - [double]$b) -le $tol) }

Write-Host "Smoke: PR 74  ($BaseUrl)" -ForegroundColor White

Sec "0  build"
$h = $null; try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
if ($null -eq $h) { Bad "/health unreachable"; exit 1 }
if ($Sha) { if ($h.commit -like "$($Sha.Trim())*") { Ok "serving $($h.commit)" } else { Bad "serving $($h.commit), expected $Sha" } } else { Skip "no -Sha" }
if ($h.schema_revision -eq '0024_plan_cash_target_ils') { Ok "schema unchanged (0024) - PR 74 has no migration" } else { Bad "schema $($h.schema_revision)" }

Sec "1  the goal is built from the money you put in"
$p = Api GET "/api/v1/plan"
if ($null -eq $p) { Bad "/plan did not answer"; exit 1 }
$g = $p.goal
if ($null -eq $g) { Bad "/plan has no goal block (the old live-NAV target)" }
else {
    Note ("put in {0}  target {1}  deadline {2}  years left {3}" -f $g.put_in, $g.target, $g.deadline, $g.years_left)
    Note ("on-track line today {0}  you are {1} ahead(+)/behind(-)" -f $g.on_track_value, $g.ahead_by)
    if ($g.breakdown) { Note $g.breakdown }
    if ($g.basis -eq 'contributions') { Ok "target derived from your contributions" } else { Bad "goal basis is '$($g.basis)', expected contributions" }
    $ct = Api GET "/api/v1/portfolio/contributions"
    if ($ct -and (Near $g.put_in $ct.total_ils 0.01)) { Ok "built from the ledger's $($ct.total_ils) put in" } else { Bad "goal put_in $($g.put_in) vs ledger $($ct.total_ils)" }
}
# The old target was NAV x (1+roi)^n, so progress was the constant 1/(1+roi)^n.
if ($null -ne $p.target_roi_pct -and $p.horizon_years) {
    $per = @{ monthly = 12; quarterly = 4; yearly = 1 }[[string]$p.target_roi_period]; if (-not $per) { $per = 1 }
    $const = 1.0 / [math]::Pow(1.0 + [double]$p.target_roi_pct / 100.0, [double]$p.horizon_years * $per)
    Note ("progress {0}; the old constant would be {1:N4}" -f $p.goal_progress, $const)
    if ($null -eq $p.goal_progress) { Bad "no progress at all - no goal could be derived (see the goal block above)" }
    elseif (-not (Near $p.goal_progress $const 0.001)) { Ok "progress is NAV / target, not the old constant" }
    else { Bad "progress still equals 1/(1+roi)^n - the target still follows NAV" }
} else { Skip "no ROI on the plan - nothing to compare with the old formula" }

Sec "2  /plan/projection quotes the same goal"
$pj = Api GET "/api/v1/plan/projection"
if ($null -eq $pj -or $pj.message) { Skip "no projection" }
else {
    if (Near $pj.target_amount $p.target_amount 1) { Ok "same target ($($pj.target_amount))" } else { Bad "projection target $($pj.target_amount) vs /plan $($p.target_amount)" }
    if ($g -and (Near $pj.years ([math]::Round([double]$g.years_left, 1)) 0.01)) { Ok "years to the deadline: $($pj.years)" } else { Bad "projection years $($pj.years), deadline says $($g.years_left)" }
}

Sec "3  no card moves the goal; the behind card quotes the same one"
$recs = Api GET "/api/v1/recommendations"
$all = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
$movers = @($all | Where-Object { $_.apply.kind -eq 'set_plan' -and $_.apply.fields -and (
    $null -ne $_.apply.fields.target_amount -or $null -ne $_.apply.fields.horizon_years -or $null -ne $_.apply.fields.target_date) })
$named = @($all | Where-Object { $_.title -match 'actually hit' -or $_.title -match 'Give it more time' })
if ($movers.Count -eq 0 -and $named.Count -eq 0) { Ok "no card rewrites the target, horizon or deadline" }
else { foreach ($c in $movers + $named) { Bad ("goal-moving card: {0}" -f $c.title) } }
$behind = @($all | Where-Object { $_.dimension -eq 'goal' -and $_.apply.kind -eq 'set_standing_order' })
if ($behind.Count -eq 0) { Skip "no behind-goal card right now (on track, or ignored)" }
else {
    $c = $behind[0]
    Note $c.why
    if (Near $c.goal_target_ils $p.target_amount 1) { Ok "card target = /plan target" } else { Bad "card target $($c.goal_target_ils) vs /plan $($p.target_amount)" }
    if ($pj -and (Near $c.goal_years_left $pj.years 0.01)) { Ok "card years = projection years ($($c.goal_years_left))" } else { Bad "card years $($c.goal_years_left) vs projection $($pj.years)" }
    if (Near $c.return_pct $p.portfolio_expected_roi_pct 0.05) { Ok ("card return {0}%/yr = /plan's ({1})" -f $c.return_pct, $c.return_basis) } else { Bad "card return $($c.return_pct) vs /plan $($p.portfolio_expected_roi_pct)" }
    if ($c.return_basis -eq 'static') { Skip "return not measured - one line only, no conservative second line" }
    elseif ($c.why -match 'conservative|objective table') { Ok "the table figure is shown as a second line" }
    else { Bad "measured headline but no conservative line on the card" }
}

Sec "4  the plan screen"
$page = $null; try { $page = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($null -eq $page) { Skip "could not fetch the page" }
elseif ($page -match 'On-track line today' -and $page -notmatch 'PLAN_NAV') { Ok "served page derives the target from deposits and shows the on-track line" }
else { Bad "served page still derives the target from NAV" }

Write-Host ""
Write-Host ("PASS {0}   FAIL {1}   SKIP {2}" -f $pass, $fail, $skip) -ForegroundColor White
if ($fail -gt 0) { exit 1 }
