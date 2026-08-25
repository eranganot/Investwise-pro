# PORTFOLIO RESTRUCTURE - 64% sleeves / 33% core / 3% cash
#
#   .\scripts\portfolio-restructure.ps1                 # DRY RUN. Reads only.
#   .\scripts\portfolio-restructure.ps1 -ApplySleeves   # stage 2: tracked book
#   .\scripts\portfolio-restructure.ps1 -RecordSells    # stage 3: AFTER you trade
#
# ============================================================================
# READ THIS BEFORE RUNNING ANYTHING
#
# THIS SCRIPT NEVER PLACES AN ORDER. It cannot. Nothing here talks to a broker,
# and no endpoint it calls can buy or sell a security. investing-discipline
# section 5, and it is not a stylistic preference -- it is the boundary the
# whole app is built around.
#
# What each stage actually is:
#
#   STAGE 1  PLAN        read-only. Prints the deltas. Default. Writes nothing.
#   STAGE 2  SLEEVES     writes plan_sleeves -- your INTENDED percentages. This
#                        is the tracked book. It buys nothing.
#   STAGE 3  RECORD      BOOKKEEPING, AFTER you have traded at your broker. It
#                        corrects the app's record to match what already
#                        happened. Nothing is bought or sold anywhere by this.
#
# YOU DO THE TRADING, between stage 2 and stage 3, at your broker. If you have
# not traded, do not run stage 3 -- you would be telling the app a story about a
# portfolio that does not exist.
#
# WHY THE BUYS ARE NOT IN THIS SCRIPT. Your sleeves are RULES, not holdings.
# btm_swing_dip and btm_trend_soxl choose what to hold from their own signals --
# as of 2026-08-14 the live signal wanted SOXL 100%, and on a flip the rule wants
# QQQ or SMH instead. Buying TQQQ by hand while the rule is risk-off takes a
# position your strategy never asked for. Use the app's "Fund all sleeves", which
# prices the legs the rule actually wants, at a price carrying its own as_of.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.
# ============================================================================

param(
    [switch]$ApplySleeves,
    [switch]$RecordSells,
    [double]$SleeveTotalPct = 64.0,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
if (-not $env:IW_AGENT_KEY) { Write-Host "IW_AGENT_KEY is not set." -ForegroundColor Red; exit 1 }
$H  = @{ 'x-agent-key' = $env:IW_AGENT_KEY }
$HJ = @{ 'x-agent-key' = $env:IW_AGENT_KEY; 'Content-Type' = 'application/json' }

function Api($m, $p, $t = 120) {
    try { return Invoke-RestMethod -Method $m -Uri "$BaseUrl$p" -Headers $H -TimeoutSec $t }
    catch { Write-Host "   $m $p -> $($_.Exception.Message)" -ForegroundColor DarkRed; return $null }
}
function Post($p, $b, $t = 120) {
    try { return Invoke-RestMethod -Method POST -Uri "$BaseUrl$p" -Headers $HJ `
              -Body ($b | ConvertTo-Json -Depth 6 -Compress) -TimeoutSec $t }
    catch { Write-Host "   POST $p -> $($_.Exception.Message)" -ForegroundColor DarkRed; return $null }
}
function Sec($m) { Write-Host "`n$m" -ForegroundColor Cyan }

# --- the target, named once -------------------------------------------------
# These are the ONLY holdings this script will ever sell. Anything not on this
# list is untouched, whatever the book contains -- a restructure that acts on
# names it was not given is one nobody can predict.
$SELL_ALL = @('V', 'MSFT', 'VXUS', 'AVUV')
# Core survivors and their target ILS. Not percentages: a percentage silently
# re-targets when NAV moves, and NAV moves while you are trading.
$CORE_TARGET = @{ 'SCHD' = 3500.0; 'QUAL' = 1800.0; 'MTUM' = 1682.0 }

# ============================================================================
Sec "STAGE 1  the plan (read-only, always runs)"
# ============================================================================
$pf = Api GET '/api/v1/portfolio'
if ($null -eq $pf) { Write-Host "cannot read the portfolio - stopping" -ForegroundColor Red; exit 1 }
$cash0 = [double]$pf.cash_ils
$nav0  = [double]$pf.nav_ils
Write-Host ("   NAV {0:N0} ILS   cash {1:N0}   invested {2:N0}   gain {3}%" -f `
            $nav0, $cash0, [double]$pf.invested_ils, $pf.gain_pct) -ForegroundColor Gray

# Freshness FIRST. A delisted instrument returns its last traded price forever:
# the call succeeds, nothing errors, and a position worth nothing looks healthy.
# Sizing a restructure against a frozen price is how a real book gets wrecked
# quietly. (COW: 14 months stale, fed NAV and every recommendation.)
# Uses the APP'S OWN price_stale flag, not date arithmetic written here. The
# app already computes staleness (price_stale / price_stale_days / freshness);
# a second implementation is a second answer to "is this price current", and
# those two eventually disagree -- the same reason the core basket excludes
# CASH by the app's predicate rather than by a ticker string.
$stale = @()
foreach ($p in $pf.positions) {
    if ($p.ticker -eq 'CASH') { continue }
    if ($p.price_stale) { $stale += ("{0} ({1} days, {2})" -f $p.ticker, $p.price_stale_days, $p.price_freshness) }
    elseif (-not $p.price_as_of) { $stale += "$($p.ticker) (no price_as_of at all)" }
}
if ($stale.Count) {
    Write-Host "   STALE PRICES: $($stale -join ', ')" -ForegroundColor Red
    Write-Host "   Refresh prices before restructuring. Stopping." -ForegroundColor Red
    exit 1
}
Write-Host "   every price is within 7 days" -ForegroundColor DarkGray

$byTicker = @{}
foreach ($p in $pf.positions) { $byTicker[$p.ticker] = $p }

$sells = @(); $proceeds = 0.0
foreach ($t in $SELL_ALL) {
    $p = $byTicker[$t]
    if ($null -eq $p) { Write-Host "   $t is not in the book - skipping" -ForegroundColor Yellow; continue }
    # value_ils comes from the API already FX-normalised. Recomputing it here
    # from quantity x price would need an FX rate this script does not have,
    # and `??` is PowerShell 7 syntax that dies on 5.1 anyway.
    $v = [double]$p.value_ils
    $sells += [pscustomobject]@{ ticker = $t; market = $p.market; qty = $p.quantity
                                 price = $p.current_price; ils = [math]::Round($v, 2) }
    $proceeds += $v
}

Write-Host "`n   SELL (you, at your broker):" -ForegroundColor White
$sells | ForEach-Object { Write-Host ("     {0,-6} {1,10:N4} sh @ {2,8}  = {3,9:N0} ILS" -f `
                                       $_.ticker, $_.qty, $_.price, $_.ils) }
Write-Host ("     {0,-6} {1,32} {2,9:N0} ILS" -f 'TOTAL', '', $proceeds) -ForegroundColor White

Write-Host "`n   THEN BUY:" -ForegroundColor White
$sleeveIls = $nav0 * $SleeveTotalPct / 100.0
Write-Host ("     sleeves -> {0:N0} ILS total ({1}% of NAV)." -f $sleeveIls, $SleeveTotalPct)
Write-Host "                Use 'Fund all sleeves' in the app, NOT a manual TQQQ/SOXL order." -ForegroundColor Yellow
Write-Host "                The rules pick the ticker; you only supply the capital." -ForegroundColor Yellow
foreach ($t in ($CORE_TARGET.Keys | Sort-Object)) {
    $held = if ($byTicker[$t]) { [double]$byTicker[$t].value_ils } else { 0.0 }
    $d = $CORE_TARGET[$t] - $held
    if ([math]::Abs($d) -lt 50) { Write-Host ("     {0,-6} leave it ({1:N0} ILS, target {2:N0})" -f $t, $held, $CORE_TARGET[$t]) }
    else { Write-Host ("     {0,-6} {1} {2:N0} ILS  ({3:N0} -> {4:N0})" -f `
                        $t, $(if ($d -gt 0) { 'BUY ' } else { 'SELL' }), [math]::Abs($d), $held, $CORE_TARGET[$t]) }
}

Write-Host "`n   INVARIANTS this script preserves, deliberately:" -ForegroundColor DarkGray
Write-Host "     NAV          unchanged by stage 3 - value moves between holdings and cash" -ForegroundColor DarkGray
Write-Host "     invested_ils UNTOUCHED - a restructure is neither a deposit nor a withdrawal," -ForegroundColor DarkGray
Write-Host "                  so your gain % must read the same before and after" -ForegroundColor DarkGray

if (-not $ApplySleeves -and -not $RecordSells) {
    Write-Host "`nDRY RUN. Nothing was written." -ForegroundColor Yellow
    Write-Host "  next:  -ApplySleeves   (tracked book only, no order)" -ForegroundColor Gray
    Write-Host "  then:  trade at your broker" -ForegroundColor Gray
    Write-Host "  then:  -RecordSells    (bookkeeping only)" -ForegroundColor Gray
    exit 0
}

# ============================================================================
if ($ApplySleeves) {
Sec "STAGE 2  set the sleeves to $SleeveTotalPct% (tracked book -- NO ORDER)"
# ============================================================================
    $sl = Api GET '/api/v1/plan/sleeves'
    if ($null -eq $sl) { Write-Host "cannot read sleeves" -ForegroundColor Red; exit 1 }
    $rows = @($sl.sleeves | Where-Object { -not $_.is_core })
    $cur = ($rows | Measure-Object -Property sleeve_pct -Sum).Sum
    if ($cur -le 0) { Write-Host "no sleeves to resize" -ForegroundColor Red; exit 1 }

    # Split at the CURRENT ratio -- the same axis T2 solves on. Changing the
    # ratio here would be a second answer to a question T6a is built to ask.
    $resizes = @($rows | ForEach-Object {
        @{ strategy_id = $_.strategy_id
           from_pct    = [math]::Round([double]$_.sleeve_pct, 2)
           to_pct      = [math]::Round([double]$_.sleeve_pct / $cur * $SleeveTotalPct, 2) } })
    Write-Host "   plan:" -ForegroundColor Gray
    $resizes | ForEach-Object { Write-Host ("     {0,-20} {1,6}% -> {2,6}%" -f $_.strategy_id, $_.from_pct, $_.to_pct) }

    $ans = Read-Host "`n   Write these to your tracked book? No order is placed. (yes/no)"
    if ($ans -ne 'yes') { Write-Host "   skipped." -ForegroundColor Yellow }
    else {
        $r = Post '/api/v1/plan/target/apply?confirm=true' @{
            resizes = $resizes
            context = @{ source = 'portfolio-restructure.ps1'; sleeve_total_pct = $SleeveTotalPct } }
        if ($null -eq $r -or -not $r.ok) { Write-Host "   REFUSED: $($r.reason) - $($r.detail)" -ForegroundColor Red }
        else {
            Write-Host "   applied. sleeves $($r.allocated_pct)%, core $($r.core_pct)%" -ForegroundColor Green
            Write-Host "   $($r.note)" -ForegroundColor DarkGray
            Write-Host "   undo:  Invoke-RestMethod -Method POST -Uri `"$BaseUrl/api/v1/plan/target/undo?confirm=true`" -Headers `$H" -ForegroundColor Gray
        }
    }
}

# ============================================================================
if ($RecordSells) {
Sec "STAGE 3  BOOKKEEPING -- record sells you have ALREADY made"
# ============================================================================
    Write-Host "   This is NOT a trade. Nothing is bought or sold anywhere by this." -ForegroundColor Yellow
    Write-Host "   It corrects the app's record to match what already happened at" -ForegroundColor Yellow
    Write-Host "   your broker. If you have not actually sold, STOP." -ForegroundColor Yellow
    Write-Host ""
    $sells | ForEach-Object { Write-Host ("     {0,-6} credit {1,9:N0} ILS cash, then remove the holding" -f $_.ticker, $_.ils) }
    Write-Host ("`n   cash {0:N0} -> {1:N0}   NAV should stay {2:N0}" -f $cash0, ($cash0 + $proceeds), $nav0)

    $ans = Read-Host "`n   Have you ALREADY sold all of these at your broker? (type: yes I sold)"
    if ($ans -ne 'yes I sold') { Write-Host "   Nothing written." -ForegroundColor Yellow; exit 0 }

    foreach ($s in $sells) {
        # CASH FIRST, THEN DELETE. investing-discipline 1c: order operations so
        # failure is visible and reversible. Credit-then-delete can over-count
        # (visible, fixable). Delete-then-credit loses the position AND its value
        # if the credit fails -- money vanishes from the book with no trace.
        Write-Host ("   {0}: crediting {1:N0} ILS..." -f $s.ticker, $s.ils) -ForegroundColor Gray
        $c = Post '/api/v1/portfolio/cash' @{ amount_ils = $s.ils; mode = 'adjust' }
        if ($null -eq $c -or -not $c.ok) {
            Write-Host "     cash credit FAILED - the holding is NOT removed. Book is intact." -ForegroundColor Red
            continue
        }
        $q = "ticker=$($s.ticker)" + $(if ($s.market) { "&market=$($s.market)" } else { '' })
        $d = Api DELETE "/api/v1/portfolio/position?$q"
        if ($null -eq $d) {
            Write-Host ("     DELETE failed AFTER crediting {0:N0}. Cash is now overstated by that" -f $s.ils) -ForegroundColor Red
            Write-Host  "     amount - visible and fixable. Reverse with mode='adjust' and a negative." -ForegroundColor Red
        } else { Write-Host "     removed. cash now $($c.cash_ils)" -ForegroundColor Green }
    }

    $after = Api GET '/api/v1/portfolio'
    if ($after) {
        Write-Host ("`n   NAV {0:N0} -> {1:N0}   cash {2:N0} -> {3:N0}   invested {4:N0} -> {5:N0}" -f `
                     $nav0, [double]$after.nav_ils, $cash0, [double]$after.cash_ils, `
                     [double]$pf.invested_ils, [double]$after.invested_ils)
        $navDrift = [math]::Abs([double]$after.nav_ils - $nav0)
        if ($navDrift -gt 50) { Write-Host ("   NAV MOVED BY {0:N0} - it should not have. Check before funding anything." -f $navDrift) -ForegroundColor Red }
        else { Write-Host "   NAV held. Bookkeeping only, as intended." -ForegroundColor Green }
        if ([math]::Abs([double]$after.invested_ils - [double]$pf.invested_ils) -gt 1) {
            Write-Host "   invested_ils CHANGED - a restructure is not a contribution. Investigate." -ForegroundColor Red
        }
    }
    Write-Host "`n   Next: 'Fund all sleeves' in the app, then top up MTUM and QUAL." -ForegroundColor Gray
}
