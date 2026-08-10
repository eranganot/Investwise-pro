# SMOKE - P2: the card.
#
#   .\scripts\smoke\smoke-p2.ps1               # then chains smoke-p1 -> p0 -> e2e
#   .\scripts\smoke\smoke-p2.ps1 -SkipChain    # P2's own checks only
#
# Entirely read-only: P2 is presentation. Nothing here writes.
#
# What this CANNOT check: the two CSS fixes (the goal tabs scrolling instead of
# wrapping, and "VERY HIGH RISK" staying on one line) are rendering, and an HTTP
# check cannot see rendering. They are on the Pixel QA list instead. Asserting
# them here would be asserting a guess.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [switch]$SkipChain,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
$ApiHeaders = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
$pass = 0; $fail = 0; $skip = 0
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }

function Api($method, $path, $tmo = 90) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $($_.Exception.GetType().Name): $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

Write-Host "Smoke: P2 the card  ($BaseUrl)" -ForegroundColor White

# =========================================================================== #
Sec "P2  every Beat the Market card carries Style + Horizon"
# =========================================================================== #
$s = Api GET '/api/v1/strategies' 120
if ($null -eq $s) { Bad "/strategies unreachable - P2 cannot be assessed" }
else {
    $cards = @($s.by_goal.'Beat the Market')
    if ($cards.Count -eq 0) { Bad "no 'Beat the Market' cards in /strategies" }
    elseif ($null -eq $cards[0].PSObject.Properties['style']) {
        Bad "cards carry no 'style' field - P2 has not deployed to this environment"
    }
    else {
        $noStyle = @($cards | Where-Object { -not $_.style })
        $noHz    = @($cards | Where-Object { -not $_.horizon })
        if ($noStyle.Count -gt 0) { Bad "$($noStyle.Count) card(s) with no Style chip: $(($noStyle | ForEach-Object { $_.id }) -join ', ')" }
        else { Ok "all $($cards.Count) cards carry a Style chip" }
        if ($noHz.Count -gt 0) { Bad "$($noHz.Count) card(s) with no Horizon chip: $(($noHz | ForEach-Object { $_.id }) -join ', ')" }
        else { Ok "all $($cards.Count) cards carry a Horizon chip" }

        # Style must be derived from the basket, not a fixed label on every card.
        $styles = @($cards | ForEach-Object { $_.style } | Sort-Object -Unique)
        if ($styles.Count -le 1) { Bad "every card reports the same style ('$($styles -join '')') - that is a label, not a derivation" }
        else { Ok "styles differ across the family: $($styles -join ', ')" }

        $lev = @($cards | Where-Object { $_.uses_leverage })
        if ($lev.Count -eq 0) { Bad "no card flags leverage, but this family holds TQQQ and SOXL" }
        else { Ok "$($lev.Count) card(s) flag leverage: $(($lev | ForEach-Object { $_.id }) -join ', ')" }
    }

    # The claim that must not blur: MEASURED, never an estimate dressed as one.
    $withEst = @($cards | Where-Object { $null -ne $_.expected_return_pct -or $null -ne $_.profile })
    if ($withEst.Count -gt 0) {
        Bad "$($withEst.Count) measured card(s) also carry a DERIVED return - 'Backtested' and 'Est. return' are different claims"
    } else { Ok "no measured card carries a derived expected return" }

    $notMeasured = @($cards | Where-Object { $_.measured -ne $true })
    # No backticks in a double-quoted string: PowerShell reads them as escape
    # characters, so a markdown habit like `profile` becomes an escape sequence
    # and the parser unwinds through the whole rest of the file. That is what the
    # first live run of this script did -- 12 cascading "missing closing" errors,
    # none of them at the real fault.
    if ($notMeasured.Count -gt 0) { Bad "$($notMeasured.Count) card(s) not flagged measured, so the UI would read 'profile' instead of 'backtest'" }
    else { Ok "every card is flagged measured" }
}

Sec "P2  YOU must check these - an HTTP call cannot see rendering"
Write-Host @"
  On the phone, Plan tab (close and reopen the installed app first - the service
  worker moved to iw-v16, and the old shell shows none of this):

    [ ] The five goal tabs sit on ONE line and scroll sideways.
        Fail: "Beat the Market" sits on a second row, looking like a
        separate control rather than a fifth tab.

    [ ] "VERY HIGH RISK" on a card header is on ONE line.
        Fail: it wraps to two, so the header reads as two fragments.

    [ ] Every Beat the Market card shows a Style chip and a Horizon chip
        alongside Backtested / Volatility / Worst drawdown.
        Fail: chips missing, or every card says the same Style.

    [ ] The leveraged cards (TQQQ, SOXL) show the amber leverage warning.

    [ ] No card anywhere says "Est. return" - measured cards say "Backtested".
        This is the one that matters: an estimate wearing a measurement's
        label is the confusion this whole family exists to avoid.
"@ -ForegroundColor Gray

# =========================================================================== #
Write-Host "`n===== P2: $pass passed, $fail failed, $skip skipped =====" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "A SKIP is not a PASS - it means the check could not run." -ForegroundColor DarkYellow }

if ($SkipChain) {
    Write-Host "`n-SkipChain: smoke-p1 was NOT run, so this is a partial verdict.`n" -ForegroundColor DarkYellow
    exit $(if ($fail) { 1 } else { 0 })
}

Write-Host "`n`n===== CHAINING: smoke-p1.ps1 (which chains p0 -> e2e) =====" -ForegroundColor Magenta
$here = $PSScriptRoot
$psExe = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh' } else { 'powershell' }
$childOut = & $psExe -NoProfile -ExecutionPolicy Bypass -File "$here\smoke-p1.ps1" `
    -BaseUrl $BaseUrl 2>&1 | Out-String
Write-Host $childOut

$matches2 = [regex]::Matches($childOut, '(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped')
Write-Host "`n`n===== COMBINED VERDICT =====" -ForegroundColor Magenta
if ($matches2.Count -eq 0) {
    Write-Host "FAIL  could not read any tally from smoke-p1 - its result is unknown, which is not a pass." -ForegroundColor Red
    $fail++
} else {
    # Children print their own tally AND a combined one that already folds in
    # everything below them. Take the largest to avoid double-counting.
    $best = $matches2 | Sort-Object { [int]$_.Groups[1].Value + [int]$_.Groups[2].Value + [int]$_.Groups[3].Value } -Descending | Select-Object -First 1
    $cp = [int]$best.Groups[1].Value; $cf = [int]$best.Groups[2].Value; $cs = [int]$best.Groups[3].Value
    Write-Host "  P1 + earlier: $cp passed, $cf failed, $cs skipped" -ForegroundColor Gray
    $pass += $cp; $fail += $cf; $skip += $cs
}
Write-Host "  P2 + everything before it: $pass passed, $fail failed, $skip skipped" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "  A SKIP is not a PASS." -ForegroundColor DarkYellow }
Write-Host ""
exit $(if ($fail) { 1 } else { 0 })
