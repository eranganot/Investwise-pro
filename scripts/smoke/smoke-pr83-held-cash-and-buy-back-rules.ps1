# SMOKE - PR 83 (+ PR 84): cash a buy-back hold keeps back gets a choice card;
# every hold is a visible, editable buy_back rule.
#
#   .\scripts\smoke\smoke-pr83-held-cash-and-buy-back-rules.ps1
#   .\scripts\smoke\smoke-pr83-held-cash-and-buy-back-rules.ps1 -Sha d099406
#   .\scripts\smoke\smoke-pr83-held-cash-and-buy-back-rules.ps1 -EditRoundTrip
#
# READ-ONLY by default. It never accepts a card and never places a trade.
# -EditRoundTrip adds ONE write: PATCH each buy_back rule with the level and
# note it ALREADY has (a no-op save) to prove the edit path works on a hold.
#
# GENERAL, NOT SOXL: every check loops over whatever tickers are on hold
# (book_target.held_back + the buy_back rules). SOXL is only today's case.
#
# PR 84 checks (volatility-based split, honest labels) run only when the
# served card carries PR 84's fields; before that they SKIP and say why.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Sha,
    [switch]$SkipShaCheck,
    [switch]$EditRoundTrip,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

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

function Api($method, $path, $body = $null, $tmo = 300) {
    try {
        if ($null -ne $body) {
            $json = $body | ConvertTo-Json -Depth 6 -Compress
            $bytes = [Text.Encoding]::UTF8.GetBytes($json)
            return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -ContentType 'application/json' -Body $bytes -TimeoutSec $tmo
        }
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> HTTP $code $($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

function Num($x) { if ($null -eq $x) { return $null } return [double]$x }

Write-Host "Smoke: PR 83 held cash + buy-back rules  ($BaseUrl)" -ForegroundColor White

# =========================================================================== #
Sec "0  the build"
# =========================================================================== #
$h = $null
try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
if ($null -eq $h) { Bad "/health unreachable"; exit 1 }
Note "commit $($h.commit)   schema $($h.schema_revision)"
if ($SkipShaCheck) { Skip "SHA check skipped by request" }
elseif ($Sha) {
    if ($h.commit -like "$($Sha.Trim())*") { Ok "serving $($h.commit)" }
    else { Bad "serving $($h.commit), expected $Sha - the deploy has not landed" }
} else { Skip "no -Sha given" }
if ($h.schema_revision -eq '0025_rule_params') { Ok "schema 0025_rule_params (no migration in PR 83)" }
else { Bad "schema is $($h.schema_revision), expected 0025_rule_params" }

# =========================================================================== #
Sec "1  holds are RULES you can see"
# =========================================================================== #
$rulesResp = Api GET '/api/v1/rules'
if ($null -eq $rulesResp) { Bad "GET /rules failed"; exit 1 }
$allRules = @($rulesResp.rules)
$typeNames = @()
foreach ($t in @($rulesResp.types)) { if ($t -is [string]) { $typeNames += $t } elseif ($t.type) { $typeNames += $t.type } elseif ($t.value) { $typeNames += $t.value } }
if (($typeNames -contains 'buy_back') -or ($typeNames.Count -eq 0)) {
    if ($typeNames.Count -eq 0) { Skip "rule types list has an unknown shape - not checked" } else { Ok "buy_back is a rule type" }
} else { Bad "buy_back missing from rule types: $($typeNames -join ', ')" }

$bb = @($allRules | Where-Object { $_.rule_type -eq 'buy_back' -and $_.active })
Note "$($bb.Count) active buy_back rule(s)"
foreach ($r in $bb) {
    $lvl = Num $r.level; $px = Num $r.current_price
    Note "$($r.ticker): level $lvl, now $px, triggered=$($r.triggered)  status: $($r.status)"
    if ($lvl -gt 0 -and $r.mode -eq 'price') { Ok "$($r.ticker) buy-back rule has a price level ($lvl)" }
    elseif ($lvl -eq 0) { Ok "$($r.ticker) buy-back rule waits on the drawdown guard (level 0)" }
    else { Bad "$($r.ticker) buy-back rule has level $lvl mode $($r.mode)" }
    if ($r.status) { Ok "$($r.ticker) row has a status line" } else { Bad "$($r.ticker) row has no status line" }
    if ("$($r.status)" -match 'of cash waiting') { Note "(PR 83 wording: the row calls the plan gap 'cash'; PR 84 says 'plan wants ... more')" }
    if ($lvl -gt 0 -and $null -ne $px) {
        $below = ($px -lt $lvl)
        if ([bool]$r.breached_now -eq $below) { Ok "$($r.ticker) breached_now=$($r.breached_now) agrees with price vs level" }
        else { Bad "$($r.ticker) breached_now=$($r.breached_now) but price $px vs level $lvl" }
    }
}

# =========================================================================== #
Sec "2  Today: the held-cash card"
# =========================================================================== #
$recs = Api GET '/api/v1/recommendations'
if ($null -eq $recs) { Bad "GET /recommendations failed"; exit 1 }
if (@($recs.degraded).Count -eq 0) { Ok "degraded: [] (no agent failed)" } else { Bad "degraded: $(@($recs.degraded) -join ', ')" }
$cards = @($recs.recommendations) + @($recs.more_recommendations)
$heldBack = @{}
if ($recs.book_target -and $recs.book_target.held_back) {
    foreach ($p in $recs.book_target.held_back.PSObject.Properties) { $heldBack[$p.Name.ToUpper()] = [double]$p.Value }
}
Note ("held_back: " + (($heldBack.Keys | ForEach-Object { "$_=$($heldBack[$_])" }) -join ', '))

# Every ticker the book holds back must have a rule (the "I don't see any rule" bug).
foreach ($tk in $heldBack.Keys) {
    if (@($bb | Where-Object { $_.ticker.ToUpper() -eq $tk }).Count -ge 1) { Ok "$tk is held back AND has a buy_back rule" }
    else { Bad "$tk is held back but has NO buy_back rule in Rules" }
}

$hc = @($cards | Where-Object { $_.apply -and $_.apply.kind -eq 'held_cash' })
$pr84 = $false
if ($hc.Count -eq 0) {
    if ($heldBack.Count -eq 0) { Skip "nothing is held back right now - no held-cash card expected" }
    else { Note "held_back is set but there is no held-cash card: fine ONLY if cash is under the trigger or the reserve does not eat the spendable cash" ; Skip "no held-cash card to check" }
} else {
    $c = $hc[0]; $ap = $c.apply
    Ok "held-cash card: '$($c.title)'"
    $held = @($ap.tickers | ForEach-Object { $_.ToUpper() })
    $opts = @($ap.options.PSObject.Properties.Name)
    if (($opts -contains 'keep') -and ($opts -contains 'buy') -and ($opts -contains 'redeploy')) { Ok "three options: keep / buy / redeploy" }
    else { Bad "options are: $($opts -join ', ')" }
    if ($c.decision) { Ok "it is a decision card (asks you to choose)" } else { Bad "decision flag missing" }
    $rd = $ap.options.redeploy
    $dests = @($rd.destinations)
    $ideas = @($dests | Where-Object { $_.kind -eq 'idea' })
    Note "$($dests.Count) destination(s), $($ideas.Count) idea(s); reserved $($ap.reserved_ils), keep $($rd.suggested_keep_ils)"
    foreach ($d in $dests) {
        $why = @($d.why) -join ' | '
        Note ("  {0,-6} {1,-6} {2,-14} now {3}% / target {4}%  suggest {5}  - {6}" -f $d.ticker, $d.kind, $d.asset_class, $d.class_now_pct, $d.class_target_pct, $d.suggested_ils, $why)
    }
    if ($dests.Count -ge 1) { Ok "invest-elsewhere lists places" } else { Bad "invest-elsewhere lists nowhere" }
    if ($ideas.Count -le 5) { Ok "at most 5 ideas ($($ideas.Count))" } else { Bad "$($ideas.Count) ideas, expected at most 5" }
    $dup = @($dests | Where-Object { $held -contains $_.ticker.ToUpper() })
    if ($dup.Count -eq 0) { Ok "no held ticker is offered as 'elsewhere'" } else { Bad "held ticker offered elsewhere: $(@($dup.ticker) -join ', ')" }
    $noWhy = @($dests | Where-Object { @($_.why).Count -eq 0 })
    if ($noWhy.Count -eq 0) { Ok "every place says why" } else { Bad "no reason on: $(@($noWhy.ticker) -join ', ')" }
    $noMix = @($dests | Where-Object { $null -eq $_.class_now_pct -or $null -eq $_.class_target_pct })
    if ($noMix.Count -eq 0) { Ok "every place shows its class now vs target" } else { Bad "no class mix on: $(@($noMix.ticker) -join ', ')" }

    $min = Num $rd.min_leg_ils; if ($null -eq $min) { $min = 250 }
    $badLeg = @($dests | Where-Object { (Num $_.suggested_ils) -gt 0 -and (Num $_.suggested_ils) -lt $min })
    if ($badLeg.Count -eq 0) { Ok "every suggested amount is 0 or at least the $min minimum" } else { Bad "below-minimum suggestion on $(@($badLeg.ticker) -join ', ')" }
    $sug = 0.0; foreach ($d in $dests) { $sug += (Num $d.suggested_ils) }
    $sumDiff = [math]::Abs(($sug + (Num $rd.suggested_keep_ils)) - (Num $ap.reserved_ils))
    if ($sumDiff -lt 1.0) { Ok "invest $sug + keep $($rd.suggested_keep_ils) = reserved $($ap.reserved_ils)" }
    else { Bad "invest $sug + keep $($rd.suggested_keep_ils) != reserved $($ap.reserved_ils)" }
    if ($rd.split_why) { Ok "the split is argued" } else { Bad "no split_why" }
    # PR 85: the elsewhere tile is ALWAYS pre-filled when a place can take the minimum
    # (Eran: "I asked you to suggest the amounts"); the touch chance only picks the option.
    $canTake = @($dests | Where-Object { ($_.kind -eq 'idea') -or ((Num $_.max_ils) -ge $min) })
    if ($canTake.Count -gt 0 -and (Num $ap.reserved_ils) -ge (2 * $min)) {
        if ($sug -ge $min) { Ok "the elsewhere tile is pre-filled (invest $sug)" }
        else { Bad "the elsewhere tile suggests nothing (every box 0) - the PR 84 regression" }
    }
    if ($null -ne $rd.touch_probability) {
        $wantRec = 'redeploy'; if ([double]$rd.touch_probability -ge 0.5) { $wantRec = 'keep' }
        if ($ap.recommended -eq $wantRec) { Ok ("recommended '{0}' fits a {1:P0} chance of reaching the level" -f $ap.recommended, [double]$rd.touch_probability) }
        else { Bad "recommended '$($ap.recommended)' but the chance is $($rd.touch_probability)" }
    }
    Note "split: $($rd.split_why)"
    Note "impact: $($c.impact)"
    $buyLegs = @($ap.options.buy.legs)
    $buySum = 0.0; foreach ($l in $buyLegs) { $buySum += (Num $l.amount_ils) }
    if ([math]::Abs($buySum - (Num $ap.reserved_ils)) -lt 1.0) { Ok "buy-back option spends exactly the reserve ($buySum)" }
    else { Bad "buy-back option spends $buySum, reserve is $($ap.reserved_ils)" }

    # ---- PR 84: the split uses the hold's volatility; honest labels --------
    $pr84 = (@($dests | Where-Object { $_.kind -eq 'plan' -or $null -ne $_.plan_label }).Count -gt 0)
    if (-not $pr84) {
        Skip "PR 84 not served yet: split says '$($rd.split_why)' (known: volatility not read - fixed in PR 84)"
    } else {
        if ($null -ne $rd.touch_probability) { Ok ("split uses the measured chance of reaching the level: {0:P0}" -f [double]$rd.touch_probability) }
        else { Bad "touch_probability is null - the hold's volatility still is not read" }
        foreach ($d in @($dests | Where-Object { $_.kind -eq 'plan' })) {
            $under = ((Num $d.class_now_pct) -lt (Num $d.class_target_pct))
            $want = 'keeps the plan''s mix'; if ($under) { $want = 'under target' }
            if ($d.plan_label -eq $want) { Ok "$($d.ticker) labelled '$($d.plan_label)' (class $($d.class_now_pct)% vs $($d.class_target_pct)%)" }
            else { Bad "$($d.ticker) labelled '$($d.plan_label)', expected '$want'" }
            if ((Num $d.suggested_ils) -le (Num $d.max_ils) + 0.01) { Ok "$($d.ticker) suggestion $($d.suggested_ils) within its limit $($d.max_ils)" }
            else { Bad "$($d.ticker) suggestion $($d.suggested_ils) above its limit $($d.max_ils)" }
        }
    }
}

# =========================================================================== #
Sec "3  one card per buy-back (no duplicate buys of a held ticker)"
# =========================================================================== #
$bbTk = @($bb | ForEach-Object { $_.ticker.ToUpper() })
$firedTk = @($bb | Where-Object { $_.triggered } | ForEach-Object { $_.ticker.ToUpper() })
$sellKinds = @('trim', 'sell', 'sell_losers', 'rule_vs_sleeve', 'none', 'held_cash', 'set_standing_order')
foreach ($tk in $bbTk) {
    $buyers = @()
    foreach ($c in $cards) {
        if (-not $c.apply) { continue }
        $k = $c.apply.kind
        if ($sellKinds -contains $k) { continue }
        $mt = ''; if ($c.meta -and $c.meta.ticker) { $mt = "$($c.meta.ticker)".ToUpper() }
        $at = ''; if ($c.apply.ticker) { $at = "$($c.apply.ticker)".ToUpper() }
        $legT = @(); foreach ($l in @($c.apply.legs)) { if ($l.ticker) { $legT += "$($l.ticker)".ToUpper() } }
        if ($mt -eq $tk -or $at -eq $tk -or $legT -contains $tk) { $buyers += "$($c.id) [$k] $($c.title)" }
    }
    if ($firedTk -contains $tk) {
        if ($buyers.Count -eq 1 -and $buyers[0] -like 'rule_*') { Ok "$tk fired: exactly one card buys it back, the rule's" }
        else { Bad "$tk fired: $($buyers.Count) card(s) buy it: $($buyers -join ' ; ')" }
    } else {
        if ($buyers.Count -eq 0) { Ok "$tk on hold above its level: nothing buys it" }
        else { Bad "$tk is on hold but a card buys it: $($buyers -join ' ; ')" }
    }
}
if ($bbTk.Count -eq 0) { Skip "no buy_back rules - nothing to check" }

# =========================================================================== #
Sec "4  the served page"
# =========================================================================== #
$page = $null; try { $page = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($null -eq $page) { Bad "could not fetch /app/" }
else {
    if ($page -match 'buy_back:\{label') { Ok "Rules tab knows the buy_back rule" } else { Bad "page has no buy_back rule label (old page?)" }
    if ($page -match 'function hcUpdate') { Ok "held-cash card has editable amounts" } else { Bad "no hcUpdate (old page?)" }
}
$sw = $null; try { $sw = (Invoke-WebRequest -Uri "$BaseUrl/app/sw.js" -UseBasicParsing -TimeoutSec 30).Content } catch {}
if ($sw -match "VERSION = '(iw-v\d+)'") {
    $v = [int]($Matches[1] -replace 'iw-v', '')
    if ($v -ge 34) { Ok "service worker $($Matches[1]) (phones pick up the new page)" } else { Bad "service worker $($Matches[1]), expected iw-v34+" }
} else { Bad "no VERSION in sw.js" }

# =========================================================================== #
Sec "5  optional: edit a buy-back rule (no-op save)"
# =========================================================================== #
if (-not $EditRoundTrip) { Skip "pass -EditRoundTrip to PATCH each buy_back rule with its current level" }
else {
    foreach ($r in $bb) {
        $out = Api PATCH "/api/v1/rules/$($r.id)" @{ level = [double]$r.level; note = $r.note }
        if ($out -and $out.ok) { Ok "$($r.ticker) buy-back rule saved via PATCH (level unchanged $($r.level))" }
        else { Bad "$($r.ticker) PATCH failed: $($out | ConvertTo-Json -Compress -Depth 4)" }
    }
    $again = Api GET '/api/v1/rules'
    foreach ($r in $bb) {
        $n = @($again.rules | Where-Object { $_.id -eq $r.id })
        if ($n.Count -eq 1 -and [math]::Abs((Num $n[0].level) - (Num $r.level)) -lt 1e-6 -and $n[0].active) { Ok "$($r.ticker) still active at $($n[0].level)" }
        else { Bad "$($r.ticker) changed after a no-op save" }
    }
}

Write-Host ""
$col = 'Green'; if ($fail -gt 0) { $col = 'Red' }
Write-Host "PASS $pass   FAIL $fail   SKIP $skip" -ForegroundColor $col
if ($fail -gt 0) { exit 1 }
