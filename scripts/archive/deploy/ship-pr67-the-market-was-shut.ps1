# ship-pr67-the-market-was-shut.ps1
# PR 67 - the market was shut, the ledger was blind, and nothing ever said
# "take some off the table". Run AFTER PR 66. It applies its own patch.
#
# Eran, 2026-09-14, five numbered complaints from one screenshot. Four were the
# same two causes wearing different clothes, one was a feature that did not
# exist, and one was not a defect at all.
#
# 1 + 2 - THE HOLES IN THE CHART, AND THE 208.56 ILS THAT NEVER MOVED.
#
# Every snapshot existed. What was missing was the MEASUREMENT. The index that
# POST /portfolio/performance returns runs
#     ... 09-02, 09-03, 09-04, 09-08, 09-09, 09-10, 09-11
# 2026-09-07 HAS NO BAR AT ALL, because it was US Labor Day. Run
# window_return_from against the unmeasured days and all four fall out of two
# lines of that function:
#
#   09-07 (Mon, holiday)  prev 09-06 (Sun)  09-04 -> 09-04   ka == kb
#   09-08 (Tue)           prev 09-07        09-04 -> 09-08   span 4 vs 1 asked
#   09-12 (Sat)           prev 09-11 (Fri)  09-11 -> 09-11   ka == kb
#   08-29 (Sat)           prev 08-28 (Fri)  08-28 -> 08-28   ka == kb
#
# nav_snapshot is a CALENDAR cron - 22:10 every night, weekends and holidays
# included - while the price history it is measured against has only TRADING
# days. And the +/- ILS figures were not deposits at all: every book change in
# that window came from /recommendations/*/accept, /plan/target/apply,
# /plan/sleeves/fund, /strategies/*/apply and /plan/sleeves/*/swap - internal
# reallocations, all of them. nav_history read only the CONTRIBUTIONS ledger,
# which correctly says nothing about trades, and NEVER ASKED THE TRADE LEDGER,
# which exists for exactly this. /plan/sleeves/fund compounded it by never
# writing to that ledger at all - the hole ledger_service's own docstring warns
# about, in its own words: "if a write path can move a position and does not
# call record_leg, that path is a hole in the ledger."
#
# So the app estimated, and reported its own reconstruction's error term as
# money: +132.11 on 08-31, -340.67 on 09-01, captioned as "-208.56 ILS you
# moved in or out" on a book where nothing moved in or out.
#
# 3 - USO AT 0 SHARES, HOLDING THREE ARMED RULES.
#
# /api/v1/portfolio returned USO with quantity 0.0, cost basis intact, counted
# in count: 6, rendered as "0 shares - +9.1% since you bought", holding
# Commodities at 0% of the book - which is what the live "Buy COMT" card was
# reacting to - and /api/v1/rules showed it carrying a take-profit at 168.17, a
# 16% trailing stop and a stop-loss at 124.92, ALL active, ON NOTHING.
#
# The trim branch was the ONLY sell path in the app with no delete-at-zero
# test. purge_orphan_rules was NOT broken: held comes from load_positions,
# which still returned the zero row, so the rules were not orphans. It did its
# job perfectly on a lie. Fix the cause, do not add a second mechanism beside a
# correct one.
#
# 4 - PRICES: NO DEFECT FOUND, AND NOTHING WAS CHANGED FOR IT.
#
# price_refresh: 327 runs, 0 failures, reading Yahoo regularMarketPrice - the
# live intraday quote, not a daily bar. A probe against production AND against
# Yahoo directly, every 10 minutes, found them identical at every sample. The
# complaint was made at 06:18 UTC on a Monday with the US market shut since
# Friday. What IS wrong is that the screen says price_freshness "fresh" over a
# Friday close, which reads as broken on a Monday. Left alone deliberately.
#
# 5 - NO CARD EVER SAID "TAKE SOME OFF THE TABLE".
#
# Three things, none of them a bug, which is why it survived so long: the only
# ACTIONABLE trim was a concentration-cap breach; "Trim: X" was a FUNDAMENTALS
# verdict (score < 40) shipping apply kind "none" and est_amount null; and
# SOXL's take-profit sits at +80%, so nothing was going to fire. Meanwhile SOXL
# sat at 37.8% against a 38% cap HE set - two tenths of a point from breaching,
# with the app silent.
#
# And one real defect found on the way: execution_plan returned sell_position -
# a FULL EXIT - for a fired take_profit, while the note the app writes on its
# own take-profit rules reads "Takes some off the table if it climbs ~10% more."
# A card may not claim a change it does not produce, and this one claimed the
# opposite of what it did.
#
# WHY THE SLICE IS FIXED AND NOT TREND-CONDITIONED. Eran asked exactly that.
# This repo already ran the experiment - STATUS.md B2, 2,514 sessions: the
# trend overlay on TQQQ returned 17.19% CAGR against 40.25% for simply holding
# it and 20.73% for holding QQQ, with a DEEPER drawdown (40.55% vs 35.12%) and
# ~7 points a year to 45 round-trips. The trend-dependent exit already exists
# and is correct: trailing_stop ratchets off peak_price, so it sells late in a
# strong trend and early in a weak one without forecasting anything.
#
# 6 - THE SPY LINE, AND THE TRAP IN IT.
#
# performance() already returns a benchmark_index, and drawing THAT here would
# have been wrong in the most convincing possible way: it is aligned to TODAY's
# book priced backwards, which over the same window reads -8.28% against the
# account line's +4.11%. Both correct, comparison meaningless - the
# two-targets-one-book shape this repo has paid for before. benchmark_series
# builds SPY from its own closes on the SNAPSHOT dates and breaks on the SAME
# unmeasured steps. Measured: you +4.11%, SPY +0.18%, +3.93 points.
#
# MIGRATION 0022 adds trading_rules.trim_fraction. The mirror in main.py's
# ADD COLUMN IF NOT EXISTS list is what actually reaches production today, so
# the column arrives at startup whether or not alembic runs. Null is not zero:
# null means the house default (25%), zero would be a rule that sells nothing.
#
# WHAT TO EXPECT AFTER: tonight's 22:10 job is the first that can skip. Today is
# a Monday with a session, so it should still record. The first visible proof is
# next Saturday, when no snapshot appears. Step 7 asks production directly.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 67: the market was shut, the ledger was blind, and nothing said take some off the table'

# IDEMPOTENT. These scripts get re-run from scrollback hours later; four
# duplicate commits across this repo and WealthOS trace to exactly that.
if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 67 already landed - nothing to do.' -ForegroundColor Yellow
    return
}

Write-Host '=== 1. sync check ===' -ForegroundColor Cyan
# A stale .git/index.lock blocks every git command here. The sandbox cannot
# delete one it created on the mount, so clearing it is this block's job.
Remove-Item (Join-Path $repo '.git\index.lock') -Force -ErrorAction SilentlyContinue
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
$sb = (git status -sb | Select-Object -First 1)
Write-Host "  $sb"
if ($sb -match 'behind') { throw 'local main is behind origin - pull/rebase first' }

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr67-the-market-was-shut.patch'
if (-not (Test-Path $patch)) { throw "patch not found: $patch" }

# ALREADY-APPLIED IS THE NORMAL CASE, NOT AN ERROR.
#
# This threw on its first real run, and the fault was the script's. The patch
# was delivered with a "run git apply" instruction beside it AND applied again
# here, so applying it by hand first - the obvious thing to do - made step 2
# fail with 20 lines of "already exists in working directory" and "patch does
# not apply". The commit-subject guard at the top cannot catch that: it only
# detects a re-run AFTER a successful commit.
#
# --reverse --check is the definitive test and the honest one. If the patch
# reverses cleanly, every hunk is already present and none is partial; a
# HALF-applied tree fails this and still reaches the throw below, which is
# exactly the case that must not be waved through.
#
# --check and --reverse never touch the index, so neither can leave the
# .git/index.lock that plain --3way does.
# REDIRECTING STDERR TO $null IS NOT SAFE HERE. Under
# $ErrorActionPreference = 'Stop', Windows PowerShell 5.1 turns a native
# command's STDERR into a TERMINATING error -- so the moment this check does
# its job and reports "does not apply", git's own error text kills the script
# before the exit code is ever read.
#
# It survived the PR 67 run only because the reverse-check SUCCEEDED there and
# printed nothing. The first time it correctly said no, it threw:
#   NativeCommandError ... at ship-pr67-1...ps1:106
#
# PowerShell 7 does not behave this way, which is exactly why a sandbox test on
# pwsh 7 could not reproduce it -- evidence from the wrong instance. Merging
# stderr into the success stream and discarding it yields no ErrorRecord on
# either version.
$eap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
git apply --check --reverse $patch 2>&1 | Out-Null
$reverseOk = ($LASTEXITCODE -eq 0)
git apply --check $patch 2>&1 | Out-Null
$forwardOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $eap

if ($reverseOk) {
    Write-Host '  already applied in full (reverse-check clean) - skipping.' -ForegroundColor Yellow
} else {
    if (-not $forwardOk) { throw 'patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}

Write-Host '=== 3. STATUS.md ===' -ForegroundColor Cyan
# Already spliced into the working tree by the session that wrote this patch -
# the patch deliberately does NOT carry STATUS.md, because the working copy had
# a newer weekly-review edit than HEAD and including it would have conflicted.
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 67') {
    throw 'STATUS.md has no PR 67 section - re-splice it before committing'
}
Write-Host '  PR 67 section present.' -ForegroundColor Green

Write-Host '=== 4. the gate (this is what CI runs) ===' -ForegroundColor Cyan
python -m pytest -q
$suite = $LASTEXITCODE
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
if ($suite -ne 0) {
    # ONE known red on main at fab6e46, before and after this PR:
    #   test_a_quote_of_zero_is_not_a_quote::test_a_real_quote_afterwards_heals_the_row
    # Anything else is this PR's problem.
    Write-Host ''
    Write-Host '  Suite non-zero. Expected: exactly 1 failure, the pre-existing' -ForegroundColor Yellow
    Write-Host '  test_a_real_quote_afterwards_heals_the_row on main.' -ForegroundColor Yellow
    Write-Host '  Sandbox measured 1815 passed / 1 failed with this patch applied.' -ForegroundColor Yellow
    if ((Read-Host '  Is that the only failure? (y/N)') -ne 'y') { throw 'stop and read the failures' }
}
Write-Host '  Green (modulo the known red).' -ForegroundColor Green

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$files = @(
    'app/services/nav_history.py',
    'app/services/ledger_service.py',
    'app/services/strategy_service.py',
    'app/services/recommendations.py',
    'app/services/rules_service.py',
    'app/services/intake_service.py',
    'app/api/routes/intake.py',
    'app/models/tables.py',
    'app/main.py',
    'app/static_app/index.html',
    'app/static_app/sw.js',
    'alembic/versions/0022_rule_trim_fraction.py',
    'STATUS.md',
    'tests/test_the_market_was_shut_not_the_history_missing.py',
    'tests/test_a_swap_is_not_a_deposit.py',
    'tests/test_a_position_at_zero_is_not_a_holding.py',
    'tests/test_the_take_profit_ladder.py',
    'tests/test_the_app_says_when_to_take_profits.py',
    'tests/test_the_benchmark_line_answers_the_same_question.py',
    'tests/test_rule_actions.py'
)

$msg = @(
    $subject,
    '',
    'Five complaints from one screenshot. Four were two causes.',
    '',
    '1+2 nav_snapshot is a CALENDAR cron while the price index has only TRADING',
    '    days. 2026-09-07 was US Labor Day and has no bar, so window_return_from',
    '    resolved both ends of four steps to the same close (or across a 4-day',
    '    span for a 1-day step) and dropped them. is_trading_day() now gates the',
    '    job: weekends need no feed call, holidays read the benchmark last close.',
    '    It FAILS OPEN - this job silently lost 7 nights once, and a snapshot not',
    '    taken cannot be reconstructed, so every uncertain case records.',
    '    window_reason_from() names the market being shut instead of blaming the',
    '    price history, which was true of nothing.',
    '',
    '    nav_history read only the CONTRIBUTIONS ledger and never asked the TRADE',
    '    ledger, so an internal reallocation looked like possible money movement,',
    '    got estimated against a reconstruction, and the residual was reported as',
    '    a deposit: +132.11 (08-31), -340.67 (09-01), captioned -208.56 ILS.',
    '    ledger_service.traded_dates() is now consulted. /plan/sleeves/fund wrote',
    '    no ledger rows at all - the hole ledger_service own docstring warns of.',
    '',
    '3   The trim branch was the ONLY sell path with no delete-at-zero test, so a',
    '    full trim left USO at quantity 0.0: counted in positions, rendering',
    '    "+9.1% since you bought", holding Commodities at 0%, and keeping three',
    '    rules armed on nothing. purge_orphan_rules was not broken - held comes',
    '    from load_positions, which returned the zero row. Fix the cause.',
    '',
    '4   Prices: NO DEFECT. 327 refresh runs, 0 failures, live intraday quotes,',
    '    identical to Yahoo at every sample. Nothing changed.',
    '',
    '5   take_profit fired as sell_position - a FULL EXIT - while its own note',
    '    said "takes some off the table". Now a 25% trim that re-arms one rung',
    '    higher instead of latching off; trim_fraction (0022) sizes it per rule.',
    '    A DECLINED firing still latches. Fixed slice, not trend-conditioned:',
    '    STATUS B2 measured that idea at 17.19% CAGR vs 40.25% for holding, with',
    '    a deeper drawdown. trailing_stop is already the trend-aware exit.',
    '    _profit_recs adds three cards, each carrying est_amount - without one,',
    '    moved_on can never un-mute, which is why Trim: SOXL stayed hidden.',
    '',
    '6   SPY on the account chart, from SPY closes on the SNAPSHOT dates, broken',
    '    on the SAME unmeasured steps. NOT performance() benchmark_index, which',
    '    is aligned to today book priced backwards: -8.28% over a window the',
    '    account did +4.11%. Measured: you +4.11%, SPY +0.18%, +3.93 points.',
    '',
    'Caught by this repo own guard on the way in: the first benchmark fetch ran',
    'a synchronous guarded_history on the event loop. Both it and is_trading_day',
    'go through offload now - note the guard only catches a DIRECT call.',
    '',
    'Gate: 1815 passed, 1 failed (pre-existing on main), ruff clean.',
    '',
    'Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>',
    'Claude-Session: https://claude.ai/code/session_01L3Ar15DWSZgSVtcgCFu9ZN'
)

if (-not (New-Commit -Files $files -Message $msg)) { throw 'nothing committed' }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  committed $sha" -ForegroundColor Green

Write-Host '=== 6. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# ---------------------------------------------------------------------------
# Post-deploy: ask production, do not assume.
# The key is NEVER inlined - a key pasted into a script lives forever in the
# transcript, and this one can execute trades. setx IWP_AGENT_KEY "..." once.
# ---------------------------------------------------------------------------
$haveKey = [bool]$env:IWP_AGENT_KEY
if (-not $haveKey) {
    Write-Host ''
    Write-Host 'IWP_AGENT_KEY not set - skipping the production checks.' -ForegroundColor Yellow
    Write-Host 'Set it once with:  setx IWP_AGENT_KEY "<the agent key>"' -ForegroundColor DarkGray
} else {
    $apiHeaders = @{ 'x-agent-key' = $env:IWP_AGENT_KEY }

    Write-Host ''
    Write-Host '=== 7. the chart, after ===' -ForegroundColor Cyan
    $h = Invoke-RestMethod -Uri "$base/api/v1/portfolio/nav-history?days=35" -Headers $apiHeaders -TimeoutSec 120
    Write-Host ("  total {0}%  over {1} of {2} steps" -f $h.total_pct, $h.measured_steps, $h.steps)

    if ($h.benchmark) {
        $gap = [math]::Round($h.total_pct - $h.benchmark.total_pct, 2)
        $line = "  {0}: {1}%   you are {2} points against it" -f $h.benchmark.ticker, $h.benchmark.total_pct, $gap
        Write-Host $line -ForegroundColor Green
    } else {
        Write-Host '  NO BENCHMARK in the payload. Check the deploy SHA.' -ForegroundColor Red
    }

    $flow = [math]::Abs($h.flow_total_ils)
    if ($flow -gt 0.5) {
        Write-Host ("  flow_total_ils still {0}" -f $h.flow_total_ils) -ForegroundColor Yellow
        Write-Host '  EXPECTED on the existing rows: the ledger fix only covers trades' -ForegroundColor DarkGray
        Write-Host '  recorded from now on. Past swaps left no legs to find.' -ForegroundColor DarkGray
    } else {
        Write-Host '  flow_total_ils ~0 - no invented deposits.' -ForegroundColor Green
    }

    foreach ($u in @($h.unmeasured_days)) {
        Write-Host ("  unmeasured {0}: {1}" -f $u.as_of, $u.reason) -ForegroundColor DarkGray
    }

    Write-Host ''
    Write-Host '=== 8. the ghost, and its rules ===' -ForegroundColor Cyan
    $p = Invoke-RestMethod -Uri "$base/api/v1/portfolio" -Headers $apiHeaders -TimeoutSec 60
    $ghosts = @($p.positions | Where-Object { $_.quantity -eq 0 -and $_.ticker -ne 'CASH' })
    if ($ghosts.Count -eq 0) {
        Write-Host '  no zero-quantity positions.' -ForegroundColor Green
    } else {
        foreach ($g in $ghosts) {
            Write-Host ("  {0} still at 0 shares" -f $g.ticker) -ForegroundColor Yellow
        }
        Write-Host ''
        Write-Host '  THE CODE STOPS NEW ONES. IT DOES NOT CLEAN THIS ROW.' -ForegroundColor Yellow
        Write-Host '  Deleting is irreversible, so it is yours to run:' -ForegroundColor Yellow
        # SINGLE-QUOTED so the key is NOT expanded into the console. This text
        # ends up in scrollback and in pasted transcripts, and this key can
        # execute trades - the reason "rotate the agent key" was an open STATUS
        # item for four weeks.
        foreach ($g in $ghosts) {
            $cmd = '    Invoke-RestMethod -Method Delete -Headers @{''x-agent-key''=$env:IWP_AGENT_KEY} -Uri ''' + $base + '/api/v1/portfolio/position?ticker=' + $g.ticker + ''''
            Write-Host $cmd -ForegroundColor DarkGray
        }
        Write-Host '  purge_orphan_rules takes its rules with it on the next read.' -ForegroundColor DarkGray
    }

    Write-Host ''
    Write-Host '=== 9. the profit cards ===' -ForegroundColor Cyan
    $r = Invoke-RestMethod -Uri "$base/api/v1/recommendations" -Headers $apiHeaders -TimeoutSec 180
    $profit = @($r.recommendations | Where-Object {
        $_.title -like '*cap*' -or $_.title -like '*take-profit*' -or
        $_.title -like '*trailing stop*' -or $_.title -like '*is up*' })
    if ($profit.Count -eq 0) {
        Write-Host '  none yet. SOXL was 37.8% vs its 38% cap - if it has since' -ForegroundColor DarkGray
        Write-Host '  fallen back, there is genuinely nothing to say.' -ForegroundColor DarkGray
    }
    foreach ($c in $profit) {
        Write-Host ("  {0}" -f $c.title) -ForegroundColor Green
        Write-Host ("    {0}" -f $c.action)
        if ($null -eq $c.est_amount) {
            Write-Host '    NO est_amount - this card can never un-mute.' -ForegroundColor Red
        }
    }
}

Write-Host ''
Write-Host 'PR 67 SHIPPED.' -ForegroundColor Green
Write-Host 'First visible proof of the snapshot fix is SATURDAY: no new point.' -ForegroundColor DarkGray
