# SMOKE - P1: the sleeve has to mean something.
#
#   .\scripts\smoke\smoke-p1.ps1                # read-only, then chains smoke-p0
#   .\scripts\smoke\smoke-p1.ps1 -SkipChain     # P1's own checks only
#   .\scripts\smoke\smoke-p1.ps1 -Apply         # ALSO applies the strategy (WRITES your plan)
#
# READ-ONLY by default. The preview checks are GETs. -Apply writes: it sets your
# plan's objective, risk tolerance, strategy and sleeve, and arms a max_weight
# rule. It places no trades.
#
# ASCII ONLY. PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM, so a UTF-8
# em-dash in a comment kills the parse on a line that looks fine.
#
# Per the phase convention: own checks, then every earlier phase's smoke
# (smoke-p0, which itself chains smoke-e2e), then one combined verdict.

param(
    [switch]$Apply,
    [switch]$SkipChain,
    [string]$Strategy = "btm_trend_tqqq",
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

function MaxWeightRules($ticker) {
    $r = Api GET '/api/v1/rules'
    if ($null -eq $r) { return $null }      # $null means UNREACHABLE, only ever that
    # The leading comma is load-bearing. PowerShell unrolls a returned empty array
    # into $null, so `return @()` from a function is indistinguishable from a
    # failed call -- which is exactly how "no cap armed yet" got reported as
    # "/rules unreachable". Wrapping in a single-element outer array stops the
    # unroll, so an empty result stays an empty array.
    return ,@($r.rules | Where-Object { $_.rule_type -eq 'max_weight' -and $_.ticker -eq $ticker })
}

Write-Host "Smoke: P1 sleeve enforcement  ($BaseUrl)" -ForegroundColor White
if (-not $Apply) { Write-Host "read-only (pass -Apply to actually apply the strategy)" -ForegroundColor DarkGray }

# =========================================================================== #
Sec "P1.2  'What changes?' must answer 'what do I need to get rid of?'"
# =========================================================================== #
$rulesBefore = MaxWeightRules 'TQQQ'
$p20 = Api GET "/api/v1/strategies/$Strategy/preview?sleeve_pct=20"
$p90 = Api GET "/api/v1/strategies/$Strategy/preview?sleeve_pct=90"

if ($null -eq $p20 -or $null -eq $p90) { Bad "preview unreachable - P1.2 cannot be assessed" }
elseif ($null -eq $p20.PSObject.Properties['sleeve_cap']) {
    Bad "preview carries no 'sleeve_cap' field - P1 has not deployed to this environment"
}
else {
    if ($p20.sleeve_cap -and @($p20.sleeve_cap).Count -gt 0) {
        Ok "preview names the cap it would arm: $((@($p20.sleeve_cap) | ForEach-Object { "$($_.ticker) $($_.level_pct)%" }) -join ', ')"
        if ([double]@($p20.sleeve_cap)[0].level_pct -eq 20) { Ok "the cap equals the sleeve you asked for (20%)" }
        else { Bad "asked for a 20% sleeve, preview says the cap would be $(@($p20.sleeve_cap)[0].level_pct)%" }
    } else { Bad "no sleeve_cap in the preview of a rule-based strategy" }

    # THE bug this phase exists for: 20% and 90% used to be indistinguishable.
    $c20 = if ($p20.sleeve_cap) { [double]@($p20.sleeve_cap)[0].level_pct } else { -1 }
    $c90 = if ($p90.sleeve_cap) { [double]@($p90.sleeve_cap)[0].level_pct } else { -1 }
    if ($c20 -lt 0 -or $c90 -lt 0) { Skip "cannot compare sleeve sizes - one preview had no cap" }
    elseif ($c20 -eq $c90) { Bad "20% and 90% produce the SAME plan - the sleeve is still decorative" }
    else { Ok "20% and 90% produce different plans ($c20% vs $c90%)" }

    # The funding plan is the direct answer to "what do I need to get rid of?"
    $f = $p20.funding
    if ($null -eq $f) { Bad "no funding plan in the preview - it is back to near-empty rebalance actions" }
    elseif ($f.ok -eq $false) { Ok "funding abstained, with a reason: $($f.reason)" }
    elseif ($f.nothing_to_do) { Skip "already at the sleeve target, so there is nothing to fund" }
    else {
        if (@($f.buys).Count -gt 0) { Ok "preview names what it would buy: $((@($f.buys) | ForEach-Object { "$($_.ticker) ~$([math]::Round($_.buy_ils))" }) -join ', ')" }
        else { Bad "funding plan has no buy legs" }
        if ($f.dry_run -ne $true) { Bad "the preview's funding plan does not say dry_run:true" }
        else { Ok "the funding plan is a dry run" }
        foreach ($s in @($f.funding.sells)) {
            if (-not $s.ticker -or -not $s.shares -or $null -eq $s.tax_ils -or -not $s.reason) {
                Bad "a funding leg is missing ticker / shares / est. tax / reason"
            }
        }
        if (@($f.funding.sells).Count -gt 0) { Ok "every funding leg names ticker, shares, est. CGT and why" }
    }

    # A preview must not arm anything.
    $rulesAfter = MaxWeightRules 'TQQQ'
    if ($null -eq $rulesBefore -or $null -eq $rulesAfter) { Skip "/rules unreachable, cannot prove the preview wrote nothing" }
    elseif (@($rulesAfter).Count -ne @($rulesBefore).Count) { Bad "previewing changed your rules ($(@($rulesBefore).Count) -> $(@($rulesAfter).Count))" }
    else { Ok "previewing armed nothing ($(@($rulesAfter).Count) max_weight rule(s) on TQQQ, unchanged)" }
}

# =========================================================================== #
Sec "P1.1  one cap, at the size the slider says"
# =========================================================================== #
if ($Apply) {
    Write-Host "   applying '$Strategy' at 20% (this WRITES your plan)" -ForegroundColor Yellow
    $a1 = try { Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/strategies/$Strategy/apply?sleeve_pct=20" `
            -Headers $ApiHeaders -TimeoutSec 120 } catch { $null }
    if ($null -eq $a1) { Bad "apply unreachable" }
    elseif ($a1.ok -eq $false) { Bad "apply refused: $($a1.error)" }
    else {
        if (@($a1.sleeve_caps).Count -gt 0) { Ok "apply reports the cap it armed: $((@($a1.sleeve_caps) | ForEach-Object { "$($_.ticker) $($_.level)% ($($_.action))" }) -join ', ')" }
        else { Bad "apply armed no cap - the sleeve is still decorative" }
        if ($a1.sleeve_cap_note -and $a1.sleeve_cap_note -like '*does not make the rebalancer aim for it*') {
            Ok "states the honest limit: a cap is not a target"
        } else { Bad "no note saying a cap does not make the rebalancer aim for the sleeve" }

        $now = MaxWeightRules 'TQQQ'
        if ($null -eq $now) { Skip "/rules unreachable after apply" }
        elseif (@($now).Count -eq 0) { Bad "apply reported a cap but /rules has no max_weight on TQQQ" }
        elseif (@($now).Count -gt 1) {
            Bad "$($now.Count) max_weight rules on TQQQ at $((@($now) | ForEach-Object { "$($_.level)%" }) -join ', ') - the duplicate P1 was meant to remove"
        }
        else {
            Ok "exactly one max_weight on TQQQ, at $($now[0].level)%"
            if ([double]$now[0].level -eq 20) { Ok "armed at the sleeve size, not at 1.5x it" }
            else { Bad "armed at $($now[0].level)% for a 20% sleeve" }
        }

        # Idempotence: applying again must re-level, never stack.
        $null = try { Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/strategies/$Strategy/apply?sleeve_pct=20" `
                -Headers $ApiHeaders -TimeoutSec 120 } catch { $null }
        $twice = MaxWeightRules 'TQQQ'
        if ($null -eq $twice) { Skip "/rules unreachable after the second apply" }
        elseif (@($twice).Count -eq 1) { Ok "applying twice re-levels rather than stacking a second cap" }
        else { Bad "applying twice left $(@($twice).Count) caps on TQQQ" }
    }
} else {
    Skip "apply not run (read-only). Re-run with -Apply to verify the cap is really armed."
    $now = MaxWeightRules 'TQQQ'
    if ($null -eq $now) { Skip "/rules unreachable" }
    elseif (@($now).Count -gt 1) {
        Bad "$(@($now).Count) max_weight rules already on TQQQ - duplicates from the old sleeve*1.5 suggestion?"
    }
    elseif (@($now).Count -eq 1) { Ok "one existing max_weight on TQQQ, at $($now[0].level)%" }
    else { Skip "no max_weight on TQQQ yet - apply the strategy to arm one" }
}

# The old suggestion must be gone, or Today will offer a second, different cap.
$recs = Api GET '/api/v1/recommendations' 120
if ($null -eq $recs) { Skip "/recommendations unreachable, cannot check for a competing cap suggestion" }
else {
    if ($recs.degraded -and @($recs.degraded).Count -gt 0) {
        Bad "agents degraded: $(@($recs.degraded) -join ', ') - a missing card says nothing"
    } else { Ok "no agent degraded" }
    $disc = @($recs.recommendations | Where-Object { $_.id -like 'stratrules_*' })
    $offered = @()
    foreach ($c in $disc) { $offered += @($c.apply.rules | Where-Object { $_.rule_type -eq 'max_weight' }) }
    if ($offered.Count -gt 0) {
        Bad "the discipline card still offers a max_weight ($((@($offered) | ForEach-Object { "$($_.ticker) $($_.level)%" }) -join ', ')) - that is the duplicate cap P1 removed"
    } else { Ok "the discipline card no longer offers a competing cap" }
}

# =========================================================================== #
Write-Host "`n===== P1: $pass passed, $fail failed, $skip skipped =====" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "A SKIP is not a PASS - it means the check could not run." -ForegroundColor DarkYellow }

if ($SkipChain) {
    Write-Host "`n-SkipChain: smoke-p0 was NOT run, so this is a partial verdict.`n" -ForegroundColor DarkYellow
    exit $(if ($fail) { 1 } else { 0 })
}

Write-Host "`n`n===== CHAINING: smoke-p0.ps1 (which chains smoke-e2e) =====" -ForegroundColor Magenta
$here = $PSScriptRoot
# Sub-process, so the child's Write-Host output lands on real stdout and can be
# captured. In-process, Write-Host goes to the information stream and `2>&1`
# would not see it - that exact mistake made an earlier chain report
# "result unknown" while printing a perfectly good tally to the console.
$psExe = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh' } else { 'powershell' }
$childOut = & $psExe -NoProfile -ExecutionPolicy Bypass -File "$here\smoke-p0.ps1" `
    -BaseUrl $BaseUrl 2>&1 | Out-String
Write-Host $childOut

$cp = 0; $cf = 0; $cs = 0; $found = 0
foreach ($m in [regex]::Matches($childOut, '(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped')) {
    $cp += [int]$m.Groups[1].Value; $cf += [int]$m.Groups[2].Value; $cs += [int]$m.Groups[3].Value; $found++
}
# smoke-p0 prints its own tally AND a combined one that already folds in
# smoke-e2e. Taking the largest avoids double-counting the same checks.
if ($found -gt 0) {
    $best = [regex]::Matches($childOut, '(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped') |
        Sort-Object { [int]$_.Groups[1].Value + [int]$_.Groups[2].Value + [int]$_.Groups[3].Value } -Descending |
        Select-Object -First 1
    $cp = [int]$best.Groups[1].Value; $cf = [int]$best.Groups[2].Value; $cs = [int]$best.Groups[3].Value
}

Write-Host "`n`n===== COMBINED VERDICT =====" -ForegroundColor Magenta
if ($found -eq 0) {
    Write-Host "FAIL  could not read any tally from smoke-p0 - its result is unknown, which is not a pass." -ForegroundColor Red
    $fail++
} else {
    Write-Host "  P0 + earlier: $cp passed, $cf failed, $cs skipped" -ForegroundColor Gray
    $pass += $cp; $fail += $cf; $skip += $cs
}
Write-Host "  P1 + everything before it: $pass passed, $fail failed, $skip skipped" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "  A SKIP is not a PASS." -ForegroundColor DarkYellow }
Write-Host ""
exit $(if ($fail) { 1 } else { 0 })
