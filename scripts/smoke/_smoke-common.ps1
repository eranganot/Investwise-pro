# Shared plumbing for the per-PR smoke scripts (scripts/smoke/smoke-prNN-*.ps1).
#
#   param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
#   . "$PSScriptRoot\_smoke-common.ps1"
#   Start-Smoke "PR 88"
#   Assert-Build -Sha $Sha -Schema '0025_rule_params' -Why "PR 88 has no migration"
#   ... Ok / Bad / Skip / Api ...
#   Stop-Smoke                      # prints the tally, exits 1 on any FAIL
#
# EVERY PR THAT SHIPS GETS ONE (CLAUDE.md, ship rules, 2026-10-05). Section 0 is
# always the build: a smoke result from a stale container is the failure two
# debugging rounds were lost to, so nothing after it counts until /health says
# the pushed SHA is the one serving.
#
# READ-ONLY by convention. A smoke script that changes the book is a ship step,
# and belongs in the ship script where it is visible.
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\..\agent-key.ps1"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
$script:pass = 0; $script:fail = 0; $script:skip = 0

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host ""; Write-Host $m -ForegroundColor Cyan }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }

function Start-Smoke($name) {
    $script:ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
    Write-Host "Smoke: $name  ($BaseUrl)" -ForegroundColor White
}

function Api($method, $path, $tmo = 300) {
    try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $script:ApiHeaders -TimeoutSec $tmo }
    catch { Write-Host "        $method $path -> $($_.Exception.Message)" -ForegroundColor DarkRed; return $null }
}

# Waits up to $WaitSec for /health to serve $Sha (Railway takes a few minutes),
# then checks the schema revision. Exits on an unreachable or stale build:
# every later section would be measuring the wrong code.
function Assert-Build([string]$Sha, [string]$Schema, [string]$Why = "", [int]$WaitSec = 600) {
    Sec "0  build"
    $deadline = (Get-Date).AddSeconds($WaitSec)
    $h = $null
    while ($true) {
        $h = $null; try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
        if (-not $Sha) { break }
        if ($h -and $h.commit -like "$($Sha.Trim())*") { break }
        if ((Get-Date) -gt $deadline) { break }
        Note ("waiting for {0} (serving {1})" -f $Sha, $(if ($h) { $h.commit } else { 'nothing' }))
        Start-Sleep -Seconds 20
    }
    if ($null -eq $h) { Bad "/health unreachable"; Stop-Smoke }
    if ($Sha) {
        if ($h.commit -like "$($Sha.Trim())*") { Ok "serving $($h.commit)" }
        else { Bad "serving $($h.commit), expected $Sha - nothing below would be about this PR"; Stop-Smoke }
    } else { Skip "no -Sha: cannot prove which build answered" }
    if ($Schema) {
        if ($h.schema_revision -eq $Schema) { Ok "schema $Schema $Why" } else { Bad "schema $($h.schema_revision), expected $Schema" }
    }
}

function Stop-Smoke {
    Write-Host ""
    Write-Host ("PASS {0}   FAIL {1}   SKIP {2}" -f $script:pass, $script:fail, $script:skip) -ForegroundColor White
    if ($script:fail -gt 0) { exit 1 }
    exit 0
}
