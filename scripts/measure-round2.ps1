# MEASURE - PR 134b: round 2 on production prices.
#   Option 2: S1-S3 (115 US stocks) against the Beat-the-Market gate.
#   Option 3: D1-D13 and S1-S3 funded from Volatility-Targeted Leverage, at 1x and 2x their suggested size.
#
#   powershell -ExecutionPolicy Bypass -File scripts\measure-round2.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\measure-round2.ps1 -Skip2      (option 3 only)
#
# READ-ONLY: measures and stores nothing. Slow (allow 1-2 hours). Saves every result to -Out. ASCII ONLY.

param([string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
      [switch]$Skip2, [switch]$Skip3,
      [string]$Out = "C:\Users\erang\.claude\jobs\0cf8d3e2\tmp\round2.json")
. "$PSScriptRoot\smoke\_smoke-common.ps1"
Start-Smoke "round 2"
$flag = { param($x) if ($x -eq $true) { "pass" } elseif ($x -eq $false) { "FAIL" } else { "n/a" } }
$stocks = @(); $funding = @(); $nd = $null

if (-not $Skip2) {
    foreach ($c in @("S1", "S2", "S3")) {
        Write-Host ("measuring {0} (115 stocks) ..." -f $c) -ForegroundColor DarkGray
        $r = Api GET "/api/v1/strategies/round2/stocks/$c/measure" 1800
        if ($null -eq $r) { $stocks += @{ code = $c; ok = $false; detail = "no answer" }; continue }
        $stocks += $r
        if (-not $r.ok) { Write-Host ("{0} not measured: {1} {2}" -f $c, $r.reason, $r.detail) -ForegroundColor Yellow; continue }
        $t = $r.tests
        $sb = ($r.same_bet | ForEach-Object { "{0} corr {1}{2}" -f $_.sleeve, $_.correlation, $(if ($_.same_bet) { " SAME" } else { "" }) }) -join '; '
        Write-Host ("{0} {1,-24} {2,6}%/yr dd -{3,5}%  QQQ {4,6}%  risk {5,-10} S1 {6} S2 {7} ({8}) S3 {9} ({10}) S4 {11}  => {12}  | {13}" -f `
            $r.code, $r.name, $r.window.cagr_pct, $r.window.max_drawdown_pct, $t.S1.qqq_cagr_pct, $r.measured_risk,
            (& $flag $t.S1.pass), (& $flag $t.S2.pass), $t.S2.selection_edge_pct, (& $flag $t.S3.pass), $t.S3.verdict,
            (& $flag $t.S4.pass), $(if ($r.passes_before_s5) { "PASSES" } else { "fails" }), $sb)
    }
    $nd = Api GET "/api/v1/strategies/round2/stocks/near-duplicates" 1800
    Write-Host ("S near-duplicates: {0}" -f (($nd.near_duplicates | ForEach-Object { "$($_.a)+$($_.b) $($_.correlation)" }) -join ', '))
}

if (-not $Skip3) {
    $codes = (1..13 | ForEach-Object { "D$_" }) + @("S1", "S2", "S3")
    foreach ($c in $codes) {
        foreach ($s in @(1, 2)) {
            Write-Host ("funding {0} x{1} from the leveraged sleeve ..." -f $c, $s) -ForegroundColor DarkGray
            $r = Api GET "/api/v1/strategies/round2/funding/$c/measure?scale=$s" 1800
            if ($null -eq $r) { $funding += @{ code = $c; scale = $s; ok = $false }; continue }
            $funding += $r
            if (-not $r.ok) { Write-Host ("{0} x{1} not measured: {2} {3}" -f $c, $s, $r.reason, $r.detail) -ForegroundColor Yellow; continue }
            Write-Host ("{0,-3} x{1} move {2,4}%  book {3,6}% -> {4,6}%/yr  dd -{5,5}% -> -{6,5}%  r/risk {7} -> {8}  QQQ {9}%  F1 {10} F2 {11}  => {12}" -f `
                $r.code, $s, $r.moved_pct, $r.book_without.cagr_pct, $r.book_with.cagr_pct,
                $r.book_without.max_drawdown_pct, $r.book_with.max_drawdown_pct,
                $r.book_without.return_per_risk, $r.book_with.return_per_risk, $r.qqq_cagr_pct,
                (& $flag $r.tests.F1.pass), (& $flag $r.tests.F2.pass), $(if ($r.passes) { "PASSES" } else { "fails" }))
        }
    }
}

@{ stocks = $stocks; stock_near_duplicates = $nd; funding = $funding; at = (Get-Date).ToString("s") } |
    ConvertTo-Json -Depth 12 | Out-File -Encoding utf8 $Out
Write-Host "saved $Out"
