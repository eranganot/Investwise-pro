# SMOKE - PR 117: keyboard and focus (L12, D84).
#
#   .\scripts\smoke\smoke-pr117-keyboard-focus.ps1 -Sha <short sha of the merge>
#
#   1  the served shell is this build's: sw.js iw-v57; one .seg with the blue
#      selection (D84); a :focus-visible ring; the one keydown handler
#   2  nothing else changed: /portfolio, Today builds
#
# READ-ONLY. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 117"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this PR)"

Sec "1  the shell"
try {
    $sw = (Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 30).Content
    if ($sw -match "iw-v57") { Ok "sw.js iw-v57" } else { Bad "sw.js is not iw-v57" }
    $html = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 30).Content
    $segs = ([regex]::Matches($html, "(?m)^\s*\.seg\{")).Count
    if ($segs -eq 1 -and $html -match "\.seg button\.on\{background:var\(--blue\)") { Ok "one .seg, blue selection (D84)" } else { Bad ("{0} .seg blocks" -f $segs) }
    if ($html -match ":focus-visible\{" -and $html -match 'document\.addEventListener\("keydown"') { Ok "focus ring and the key handler are served" } else { Bad "focus ring or key handler missing" }
} catch { Bad ("shell fetch failed: {0}" -f $_.Exception.Message) }

Sec "2  nothing else changed"
$pf = Api GET "/api/v1/portfolio"
if ($pf -and @($pf.positions).Count -gt 0) {
    $sum = (@($pf.positions) | Measure-Object -Property value_ils -Sum).Sum
    if ([math]::Abs($sum - [double]$pf.nav_ils) -le 1) { Ok ("{0} positions, nav_ils {1:N2} = sum" -f @($pf.positions).Count, [double]$pf.nav_ils) }
    else { Bad "nav_ils != sum of rows" }
} else { Bad "/portfolio returned no positions" }
$r = Api GET "/api/v1/recommendations"
if ($r -and @($r.degraded).Count -eq 0) { Ok "Today built, degraded = []" } else { Bad "Today degraded or failed" }
Stop-Smoke
