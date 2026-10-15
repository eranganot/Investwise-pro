# MEASURE - PR 133: the wave-3 candidates (D1-D13) against the owner-approved gate, on production prices.
#
#   powershell -ExecutionPolicy Bypass -File scripts\measure-diversifiers.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\measure-diversifiers.ps1 -Only D3,D12
#
# READ-ONLY: each call measures and stores nothing. Slow (a few minutes per candidate).
# Writes every result to -Out (JSON) and prints one line per candidate. ASCII ONLY.

param([string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
      [string[]]$Only = @(),
      [string]$Out = "C:\Users\erang\.claude\jobs\0cf8d3e2\tmp\diversifiers.json")
. "$PSScriptRoot\smoke\_smoke-common.ps1"
Start-Smoke "wave-3 diversifiers"

$codes = if ($Only.Count) { $Only } else { 1..13 | ForEach-Object { "D$_" } }
$rows = @()
foreach ($c in $codes) {
    Write-Host ("measuring {0} ..." -f $c) -ForegroundColor DarkGray
    $r = Api GET "/api/v1/strategies/diversifiers/$c/measure" 900
    if ($null -eq $r) { $rows += @{ code = $c; ok = $false; detail = "no answer" }; continue }
    $rows += $r
    if (-not $r.ok) { Write-Host ("{0,-4} not measured: {1} {2}" -f $c, $r.reason, $r.detail) -ForegroundColor Yellow; continue }
    $t = $r.tests
    $flag = { param($x) if ($x -eq $true) { "pass" } elseif ($x -eq $false) { "FAIL" } else { "n/a" } }
    Write-Host ("{0,-4} {1,-38} {2,6}%/yr dd -{3,5}%  QQQ {4,6}%  risk {5,-12} B1 {6} G2 {7} G3 {8} G4 {9}{10} G5 {11}  corrQQQ {12}  => {13}" -f `
        $r.code, $r.name, $r.window.cagr_pct, $r.window.max_drawdown_pct, $r.qqq.cagr_pct, $r.measured_risk,
        (& $flag $t.B1.pass), (& $flag $t.G2.pass), (& $flag $t.G3.pass), (& $flag $t.G4.pass),
        $(if ($t.G4.badge) { "($($t.G4.badge))" } else { "" }), (& $flag $t.G5.pass),
        $t.G1.correlation_qqq, $(if ($r.passes_before_g6) { "PASSES" } else { "fails" }))
}
Write-Host "near-duplicates (G6) ..." -ForegroundColor DarkGray
$nd = Api GET "/api/v1/strategies/diversifiers/near-duplicates" 900
$all = @{ measured = $rows; near_duplicates = $nd; at = (Get-Date).ToString("s") }
$all | ConvertTo-Json -Depth 12 | Out-File -Encoding utf8 $Out
Write-Host ("near-duplicates: {0}" -f (($nd.near_duplicates | ForEach-Object { "$($_.a)+$($_.b) $($_.correlation)" }) -join ', '))
Write-Host "saved $Out"
