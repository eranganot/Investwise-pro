# repair-ledger-pr73.ps1
# PR 73, plan step B3: repair the trade history. PREVIEW FIRST; writes only on "y".
#
#   .\scripts\ops\repair-ledger-pr73.ps1
#
# What it writes (Eran's Q4 answer, 2026-09-28):
#   * one Adjust leg per holding per UNRECORDED REBALANCE, dated at the accept,
#     for every holding the reconstruction could solve (USO, UGA, VTI, QQQ on
#     2026-09-28) - "measured" when two holdings agree on the factor,
#     "exact_total" when the holding's total gap is exact and its steps were
#     solved from itself;
#   * the blind sells the 09-21 repair tool sizes as "estimate" (8 on 09-28),
#     plus any "measured" ones and duplicates it has always written.
# Nothing else. Rows the preview marks "unresolved" are left alone and listed.
#
# This is BOOKKEEPING, not a trade: nothing is bought or sold anywhere, NAV and
# cash do not change. It corrects the app's record of how the book got to the
# share counts it already holds. Adjust rows carry no shekel value, so they are
# never read as deposits or withdrawals.
#
# Pure ASCII, no backticks.

param([string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\agent-key.ps1"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }

Write-Host '=== preview (read-only) ===' -ForegroundColor Cyan
$p = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/v1/portfolio/ledger/repair" -Headers $ApiHeaders -TimeoutSec 300
$ur = $p.unrecorded_rebalances
if ($null -eq $ur) { throw 'the server has no unrecorded_rebalances section - is PR 73 deployed?' }

Write-Host ''
Write-Host 'Rebalance accepts that recorded no trade:' -ForegroundColor White
foreach ($a in @($ur.accepts)) { Write-Host ("  {0}  {1}" -f $a.at, $a.card) }

Write-Host ''
Write-Host 'Adjust legs it would write:' -ForegroundColor White
foreach ($a in @($ur.adjusts)) {
    Write-Host ("  {0,-5} {1,10:N4} sh  x{2}  {3,-11} at {4}  (factor from {5})" -f $a.ticker, [double]$a.shares, $a.factor, $a.confidence, $a.at, ((@($a.factor_from)) -join ', '))
}
if (@($ur.unresolved).Count -gt 0) {
    Write-Host ''
    Write-Host 'Left alone (unresolved):' -ForegroundColor Yellow
    foreach ($u in @($ur.unresolved)) { Write-Host ("  {0,-5} gap {1} - {2}" -f $u.ticker, $u.gap_shares, $u.why) -ForegroundColor Yellow }
}
if (@($ur.disagreements).Count -gt 0) {
    Write-Host ''
    Write-Host 'DISAGREEMENTS (nothing is written for these holdings):' -ForegroundColor Red
    foreach ($d in @($ur.disagreements)) { Write-Host ("  {0}: replays to {1}, holds {2}" -f $d.ticker, $d.expected_now, $d.now) -ForegroundColor Red }
}

$est = @($p.blind_sells | Where-Object { $_.confidence -eq 'estimate' })
$mea = @($p.blind_sells | Where-Object { $_.confidence -eq 'measured' })
Write-Host ''
Write-Host ("Blind sells it would size: {0} estimate, {1} measured; {2} left unresolved." -f $est.Count, $mea.Count, @($p.blind_sells | Where-Object { $_.confidence -eq 'unresolved' }).Count) -ForegroundColor White
foreach ($b in $est + $mea) { Write-Host ("  {0,-5} {1} {2,10:N6} sh  {3} - {4}" -f $b.ticker, $b.at, [double]$b.shares, $b.confidence, $b.method) }

Write-Host ''
if ((Read-Host 'Write all of the above? (y/N)') -ne 'y') { Write-Host 'Nothing written.' -ForegroundColor Yellow; return }

$body = @{ confirm = $true; include_estimates = $true; include_rebalances = $true } | ConvertTo-Json
$r = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/portfolio/ledger/repair" -Headers $ApiHeaders -ContentType 'application/json' -Body $body -TimeoutSec 300
Write-Host ''
Write-Host ("Written: {0} rebalance Adjust legs, {1} blind sells sized, {2} duplicates voided." -f @($r.rebalances_written).Count, @($r.written).Count, @($r.voided).Count) -ForegroundColor Green

Write-Host ''
Write-Host '=== check: does each repaired holding now reconcile? ===' -ForegroundColor Cyan
foreach ($tk in (@($ur.adjusts) | ForEach-Object { $_.ticker } | Sort-Object -Unique)) {
    $h = Invoke-RestMethod -Method GET -Uri "$BaseUrl/api/v1/portfolio/position-history?ticker=$tk&range=1M" -Headers $ApiHeaders -TimeoutSec 120
    if ($h.value.available) { Write-Host ("  PASS  {0}: your position line is drawn" -f $tk) -ForegroundColor Green }
    elseif ($h.value.reason -match 'cannot be right') { Write-Host ("  FAIL  {0}: {1}" -f $tk, $h.value.reason) -ForegroundColor Red }
    else { Write-Host ("  NOTE  {0}: {1}" -f $tk, $h.value.reason) -ForegroundColor Yellow }
}
