# ship-pr62-the-universe-reaches-tel-aviv.ps1
# PR 62 - the Tel Aviv section was a label with nothing behind it.
#
# RUN THIS AFTER PR 61.1. It refuses to start otherwise, and it applies its own
# patch, so nothing of PR 62 sits in the tree while you are shipping 61.1.
#
# Eran, 2026-09-06: "the app can (and should) recommend me assets from TASE",
# then "let's start with the Israeli companies for now, add the Israeli indexes
# to the backlog."
#
# IT COULD NOT, AND THE SOURCE SAID IT COULD. universe.py carried a section
# headed "Tel-Aviv (TASE) names (Yahoo .TA suffix)" and every one of its seven
# entries was market NASDAQ or NYSE with no .TA suffix - the US ADRs of Israeli
# companies. equity_universe() is the only list the screener and the signal
# service rank, so no shekel-denominated instrument could ever be proposed, on a
# book denominated in shekels and taxed under Israeli CGT.
#
# Everything else had been built for weeks: MARKET_CCY TASE -> ILS, the .TA
# symbol path, the ILA/agorot minor-unit detection and the 100x row repair. Only
# the candidate list was missing.
#
# VERIFIED LIVE 2026-09-06 through the app's own provider: all 17 entries
# returned price, currency ILA, sector, P/E and 60 sessions of history, across
# six sectors - a ranking over six banks is a sector bet wearing a country's
# name. TEVA, NICE and ESLT moved to Tel Aviv rather than being duplicated.
#
# AND ADDING THEM FIRES A GUN V6 SAW LOADED. V6's PR D called the unnormalised
# price writers "a loaded gun, not a firing one". Putting Tel Aviv in the
# universe is what schedules it:
#
#   _apply_buy_funded read float(q.price) and the raw currency code, then
#   qty = budget / fx_rate(ccy) / price. For LUMI.TA that is 7916 AGOROT used as
#   a shekel price - a hundredth of the shares - against an FX code, ILA, that no
#   provider knows, so fx_rate errors and falls back rather than converting.
#
#   broker_sync._to_intake is the same fault on the aggregator path and threw
#   away a currency field BrokerPosition has always had, INCLUDING on the cost
#   basis - a wrong basis is a wrong CGT estimate on every later card.
#
# Both are fixed here. That closes the second and third of the four writers V6's
# PR D named.
#
# NOT HERE: Israeli index funds. A TASE tracker trades under a numeric symbol
# (1159250.TA) and the provider returns Not Found; TA35.TA resolves as the INDEX,
# which cannot be bought. No shekel ETF is in this set and CLASS_ETF stays
# US-listed - a test asserts both. Parked in the backlog with its un-park
# condition.
#
# Gates on these exact bytes: 1732 SQLite, 1732 PostgreSQL on a dropped and
# recreated database, ruff clean. 5 of 9 new tests confirmed RED against 446ceaa.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo

Write-Host '=== 0. PR 61.1 must be in first ===' -ForegroundColor Cyan
$log = (git log --oneline -3 | Out-String)
if ($log -notmatch 'PR 61\.1') {
  throw 'PR 61.1 is not in the last three commits. Run ship-pr61-1-... first - PR 62 does not stack on an unshipped fix.'
}
git rev-parse --short HEAD
git status --short

Write-Host '=== 1. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr62.patch'
if (-not (Test-Path $patch)) { throw 'pr62.patch is not in the repo root' }
$already = (Select-String -Path 'app\services\universe.py' -Pattern 'LUMI.TA' -Quiet)
if ($already) {
  Write-Host '  already applied' -ForegroundColor Yellow
} else {
  git apply -p1 $patch
  if ($LASTEXITCODE -ne 0) { throw 'git apply failed - do not continue' }
  Write-Host '  applied' -ForegroundColor Green
}

Write-Host '=== 2. the STATUS entry ===' -ForegroundColor Cyan
$py = @'
import io
frag = io.open("PR62_STATUS.md", encoding="utf-8").read().rstrip("\n")
s = io.open("STATUS.md", encoding="utf-8").read()
if "PR 62 - the Tel Aviv section" in s or "PR 62 \u2014 the Tel Aviv section" in s:
    print("already present")
else:
    anchor = "## PR 61.1 "
    i = s.index(anchor)
    s = s[:i] + frag + "\n\n" + s[i:]
    s = s.replace("**HEAD is PR 61.1.**", "**HEAD is PR 62.**", 1)
    io.open("STATUS.md", "w", encoding="utf-8").write(s)
    print("inserted")
'@
$pyPath = Join-Path $repo '_status_insert.py'
[System.IO.File]::WriteAllText($pyPath, $py)
python $pyPath
if ($LASTEXITCODE -ne 0) { throw 'STATUS insert failed' }
Remove-Item $pyPath -Force

Write-Host '=== 3. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 4. the guards, then everything ===' -ForegroundColor Cyan
$guards = @(
  'tests/test_the_universe_reaches_tel_aviv.py',
  'tests/test_minor_unit_currencies.py',
  'tests/test_screener.py',
  'tests/test_recos_apply.py',
  'tests/test_card_claims.py'
)
$have = @()
foreach ($g in $guards) { if (Test-Path (Join-Path $repo $g)) { $have += $g } }
if ($have.Count -lt 2) { throw 'the guard list did not resolve - check the test filenames' }
python -m pytest -q @have
if ($LASTEXITCODE -ne 0) { throw 'the guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

Write-Host '=== 5. commit ===' -ForegroundColor Cyan
$msg = @'
PR 62: the Tel Aviv section was a label with nothing behind it

Eran: "the app can (and should) recommend me assets from TASE", then "let's
start with the Israeli companies for now, add the Israeli indexes to the
backlog."

IT COULD NOT, AND THE SOURCE SAID IT COULD. universe.py carried a section headed
"Tel-Aviv (TASE) names (Yahoo .TA suffix)" and a module docstring promising
"Tel-Aviv (TASE) names". Every one of its seven entries was market NASDAQ or
NYSE with no .TA suffix - the US ADRs of Israeli companies. equity_universe() is
the only list the screener (screener_agent.py:146) and the signal service
(signal_service.py:152) rank, so not one shekel-denominated instrument could ever
be proposed, on a book denominated in shekels and taxed under Israeli CGT. Every
suggestion the app has ever made carried an unhedged USD exposure it never named.

EVERYTHING ELSE HAD BEEN BUILT FOR WEEKS. MARKET_CCY["TASE"] = "ILS" (fx.py:33),
the .TA symbol path (live.py:50), the ILA/agorot minor-unit detection and the
100x row repair (fx.py:45-47, pricing_service.py:293). Only the candidate list
was missing, which is the part that decides what can ever be suggested.

VERIFIED LIVE 2026-09-06 through this app's own provider. All 17 entries returned
price, currency ILA, sector, P/E and 60 sessions of history: LUMI, POLI, MZTF,
DSCT, PHOE, HARL, ICL, TSEM, NVMI, CAMT, NICE.TA, ESLT.TA, TEVA.TA, BEZQ, AZRG,
ORA, ELAL - six sectors, because a ranking over six banks is a sector bet wearing
a country's name.

TEVA, NICE and ESLT moved to Tel Aviv rather than being duplicated: both listings
are real and genuinely different instruments, but one company holding two slots
in one ranking is a thumb on the scale. CHKP, WIX, MNDY and GLOB have no TASE
line here and stay where they trade.

AND ADDING THEM FIRES A GUN V6 SAW LOADED. V6's PR D called the unnormalised
price writers "a loaded gun, not a firing one, because no broker sync runs today
and no CSV import is scheduled". Putting Tel Aviv in the universe is what
schedules it, so the writer is fixed in the same PR rather than after the first
wrong purchase.

_apply_buy_funded read float(q.price) and the raw currency code, then computed
qty = budget / fx_rate(ccy) / price. For LUMI.TA that is 7916 AGOROT used as a
shekel price - a hundredth of the shares bought - against an FX code, ILA, that
no provider knows, so fx_rate errors and falls back rather than converting. Two
wrongs that do not cancel. to_major_units says "use at every boundary" in its own
docstring; PR 45 fixed add_holding and this boundary was left because nothing in
the universe could reach it.

broker_sync._to_intake is the same fault on the aggregator path, and it threw
away a currency field BrokerPosition has carried since it was written -
INCLUDING on the cost basis, which is worse, because a wrong basis is a wrong CGT
estimate on every card that quotes one afterwards. Both halves are fixed here,
which closes the second and third of the four writers V6's PR D named.

WHAT IS NOT HERE, AND WHY. Israeli index funds. A TASE tracker trades under a
numeric symbol (1159250.TA, 1146836.TA) and the provider returns Not Found for
every one tried; TA35.TA resolves as the INDEX, which cannot be bought. So there
is no shekel-denominated ETF in this set and recommendations.CLASS_ETF stays
US-listed - a test asserts both, because a row claiming otherwise would be this
same defect in the other direction. Parked in the product backlog with its
un-park condition.

tests/test_the_universe_reaches_tel_aviv.py - 9 tests, 5 confirmed RED against
446ceaa. One of them asserts the SHAPE of the original defect over every row
rather than the ones I touched: nothing structural prevented a NASDAQ ticker
being filed under TASE, so nothing structural prevented it coming back.

Also lands EXECUTION_PLAN_2026-09-06_v7.md - the V5 + V6 reconciliation, every
row re-checked against production or the tree.

Gates: 1732 passed on SQLite, 1732 on PostgreSQL against a dropped and recreated
database, ruff check app tests alembic clean.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/universe.py app/services/recommendations.py app/services/broker_sync.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- tests/test_the_universe_reaches_tel_aviv.py
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- STATUS.md EXECUTION_PLAN_2026-09-06_v7.md
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git add -- scripts/deploy/ship-pr62-the-universe-reaches-tel-aviv.ps1
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

# The transfer mechanism, not part of the PR.
foreach ($tmp in @('PR62_STATUS.md', 'pr62.patch')) {
  $p = Join-Path $repo $tmp
  if (Test-Path $p) { Remove-Item $p -Force; Write-Host ("removed {0}" -f $tmp) }
}

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 6. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

$keyPath = Join-Path $repo 'scripts\.agent-key'
if (Test-Path $keyPath) {
  $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }
  Write-Host '=== 7. can the screener see Tel Aviv now ===' -ForegroundColor Cyan
  try {
    $sc = Invoke-RestMethod -Uri "$base/api/v1/screener/equities?top_n=25" -Headers $apiHeaders -TimeoutSec 180
    $picks = @($sc.picks)
    if ($picks.Count -eq 0) { $picks = @($sc.results) }
    $tase = @($picks | Where-Object { $_.ticker -like '*.TA' })
    Write-Host ("  ranked {0} names, {1} of them Tel Aviv" -f $picks.Count, $tase.Count)
    foreach ($p in $tase) {
      Write-Host ("    {0,-10} {1}" -f $p.ticker, $p.name)
    }
    if ($tase.Count -eq 0) {
      Write-Host '  none ranked. Not necessarily wrong - the scan drops candidates the' -ForegroundColor Yellow
      Write-Host '  provider cannot reach, and it says so in the log. Check the deploy SHA' -ForegroundColor Yellow
      Write-Host '  first, then the screener log line for UNREACHABLE.' -ForegroundColor Yellow
    }
  } catch {
    Write-Host ("  screener endpoint did not answer: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
    Write-Host '  Check the Opportunities tab in the app instead.' -ForegroundColor Yellow
  }
}

Write-Host ''
Write-Host 'PR 62 SHIPPED.' -ForegroundColor Green
Write-Host '  Next: PR 63 - the sleeve target follows its signal (option a).'
