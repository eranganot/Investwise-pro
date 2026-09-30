# REPAIR - PR 86: write the three Buy rows the 2026-09-30 held-cash accept left
# out of the trade ledger (VTI 620, USO 270, BND 270 from the SOXL reserve).
#
#   .\scripts\repair\repair-pr86-held-cash-ledger.ps1            (preview, then asks)
#   .\scripts\repair\repair-pr86-held-cash-ledger.ps1 -Yes       (no prompt)
#
# Run AFTER ship-pr86 has deployed. It needs the PR 86 endpoints.
#
# WHAT IT WRITES: three ledger rows only (txn Buy, source held_cash_redeploy),
# dated at the accept. It does NOT touch positions or cash - those are already
# right. Shares = amount / (price at the accept x FX), the accept's own maths.
#
# SAFETY: it refuses to write unless BND's reconstructed "before" quantity is
# 13.8723 - the ledger's own 07:42 basket buy, an independent anchor. Running it
# twice writes nothing the second time.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [switch]$Yes,
    [string]$Card = 'rec_561e88',
    [string]$Legs = 'VTI:620,USO:270,BND:270',
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\agent-key.ps1"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }

function Call($method, $path, $body = $null) {
    try {
        if ($null -ne $body) {
            $json = $body | ConvertTo-Json -Depth 5 -Compress
            $bytes = [Text.Encoding]::UTF8.GetBytes($json)
            return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -ContentType 'application/json' -Body $bytes -TimeoutSec 180
        }
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec 180
    } catch {
        $code = $null
        try { $code = [int]$_.Exception.Response.StatusCode } catch {}
        if ($code -eq 401 -or $code -eq 403) { throw "HTTP $code - the agent key was rejected (not an outage)" }
        if ($code -eq 404) { throw "HTTP 404 on $path - PR 86 is not deployed yet; run ship-pr86 first" }
        throw "$method $path failed: HTTP $code $($_.Exception.Message)"
    }
}

$h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30
Write-Host "Repair: PR 86 held-cash ledger rows   (serving $($h.commit))" -ForegroundColor White

$q = "/api/v1/portfolio/ledger/repair-held-cash?card=$Card&legs=$([uri]::EscapeDataString($Legs))"
$p = Call GET $q
if (-not $p.ok) { throw "preview refused: $($p.reason)" }
Write-Host ""
Write-Host "Accept time: $($p.at)" -ForegroundColor Cyan
foreach ($l in @($p.legs)) {
    if ($l.status -eq 'to write') {
        Write-Host ("  {0,-5} {1,8} ILS -> {2,8} shares  @ {3} x FX {4} ({5})   held {6} -> before {7}" -f $l.ticker, $l.amount_ils, $l.shares, $l.price, $l.fx, $l.price_source, $l.held_now, $l.held_before)
    } else {
        Write-Host ("  {0,-5} {1,8} ILS -> {2}" -f $l.ticker, $l.amount_ils, $l.status) -ForegroundColor Yellow
    }
}

$toWrite = [int]$p.to_write
if ($toWrite -eq 0) {
    Write-Host ""
    Write-Host "Nothing to write (already recorded, or nothing provable)." -ForegroundColor Green
    return
}

# The independent anchor: BND before tonight's buy = the 07:42 basket buy.
$bnd = @($p.legs | Where-Object { $_.ticker -eq 'BND' -and $_.status -eq 'to write' })
if ($bnd.Count -eq 1) {
    $diff = [math]::Abs([double]$bnd[0].held_before - 13.8723)
    if ($diff -gt 0.001) {
        throw "ANCHOR FAILED: BND before = $($bnd[0].held_before), expected 13.8723 - the price or FX differs from the accept's; NOT writing. Send this output to Claude."
    }
    Write-Host ""
    Write-Host "  anchor OK: BND before = $($bnd[0].held_before) = the 07:42 basket buy 13.8723" -ForegroundColor Green
}

if (-not $Yes) {
    Write-Host ""
    $ans = Read-Host "Write these $toWrite ledger row(s)? Positions and cash are not touched. (y/N)"
    if ($ans -notmatch '^(y|yes)$') { Write-Host "Nothing written." -ForegroundColor Yellow; return }
}

$out = Call POST '/api/v1/portfolio/ledger/repair-held-cash' @{ card = $Card; legs = $Legs; confirm = $true }
Write-Host ""
foreach ($w in @($out.written)) { Write-Host "  written: Buy $($w.shares) $($w.ticker) (ILS $($w.amount_ils)) at $($w.at)" -ForegroundColor Green }

# Verify from the ledger itself.
$led = Call GET '/api/v1/portfolio/ledger?limit=50'
$mine = @($led.entries | Where-Object { $_.source -eq 'held_cash_redeploy' -and "$($_.note)" -like "*$Card*" })
$want = @($Legs.Split(',') | ForEach-Object { $_.Split(':')[0].Trim().ToUpper() })
$missing = @($want | Where-Object { $t = $_; -not (@($mine | Where-Object { $_.ticker -eq $t }).Count) })
Write-Host ""
if ($missing.Count -eq 0) { Write-Host "Ledger now holds all $($want.Count) buys for $Card. Tell Claude 'repaired'." -ForegroundColor Green }
else { Write-Host "Ledger is still missing: $($missing -join ', ') - send this output to Claude." -ForegroundColor Red; exit 1 }
