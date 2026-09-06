# ship-pr59-1-the-rules-a-retired-sleeve-leaves.ps1
# PR 59.1 - a retired sleeve left its stops behind, and nothing could tell they
# were its. Run after PR 59 (7953423).
#
# FOUND ON THE LIVE BOOK, the day after PR 59 shipped. The B2 swap did what it
# claimed - 33.09% moved, nothing bought or sold, the TQQQ max_weight cap went
# inactive, the SOXL cap re-levelled 40.0 -> 33.9. Then /api/v1/rules showed:
#
#   TQQQ  max_weight     39.0   False   sleeve      <- retired correctly
#   TQQQ  take_profit    41.0   True    (no owner)
#   TQQQ  trailing_stop  20.0   True    (no owner)
#   TQQQ  stop_loss      65.7   True    (no owner)
#
# ROOT CAUSE. _arm_sleeve_caps retires a cap for a ticker no sleeve wants, and
# its docstring says why: a row removed without re-levelling leaves a ceiling on
# the book with nothing behind it. The stop and take-profit had no such path -
# not because anyone decided they should outlive the sleeve, but because they
# could not be IDENTIFIED. discipline_rules emitted them with no strategy_id,
# apply passes that field straight through, and they landed as NULL.
# signal_rules had stamped its own all along; only the discipline path did not.
#
# THE NOTES SETTLED WHICH WAS WHICH, which is why this does not guess:
#
#   41.0  "Swing: Dip-Buy in an Uptrend: bank a 41% gain..."   the sleeve's
#   20.0  "From uptrend alert (protect gains, 20% trail)"      not the sleeve's
#   65.7  "Caps downside ~15% below today's 77.30."            not the sleeve's
#
# Two of three had nothing to do with the sleeve, and the book still HOLDS
# TQQQ - so retiring "every active rule on a ticker no sleeve wants" would have
# disarmed two rules protecting a live position.
#
# WHAT SHIPS. discipline_rules stamps strategy_id. retire_strategy_rules
# deactivates rules carrying the departing id, wired into BOTH swap_sleeve and
# retire_sleeve, running BEFORE _arm_sleeve_caps because that function commits.
# Marked, not assumed: only a stamped rule is touched, and an unowned rule on an
# affected ticker is NAMED and left alone.
#
# IT CANNOT CLEAN UP RULES ARMED BEFORE THE STAMP, including the three above.
# You have already disarmed the 41.0 by hand; the other two should stay.
#
# Also in: scripts/ops/pre-push, plus the runbook and ops-check corrected to say
# GitHub will not enforce a ruleset on a private repo on this plan.
#
# Gates already run on these exact bytes: 1706 SQLite, 1706 PostgreSQL on a
# dropped and recreated database, ruff clean.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$base = 'https://investwise-pro-production.up.railway.app'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

$keyPath = Join-Path $repo 'scripts\.agent-key'
$haveKey = Test-Path $keyPath
if ($haveKey) { $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() } }

if ($haveKey) {
  Write-Host '=== 0b. what is armed on TQQQ right now ===' -ForegroundColor Cyan
  $rules = (Invoke-RestMethod -Uri "$base/api/v1/rules" -Headers $apiHeaders -TimeoutSec 60).rules
  foreach ($r in ($rules | Where-Object { $_.ticker -eq 'TQQQ' })) {
    $own = if ($r.strategy_id) { $r.strategy_id } else { '(no owner)' }
    Write-Host ("  {0,-14} {1,7} active={2,-5} {3}" -f $r.rule_type, $r.level, $r.active, $own)
  }
  Write-Host '  Rules already armed carry no owner and this deploy cannot adopt them.'
}

Write-Host '=== 1. the pre-push hook ===' -ForegroundColor Cyan
$hook = Join-Path $repo '.git\hooks\pre-push'
if (Test-Path $hook) {
  Write-Host '  installed' -ForegroundColor Green
} else {
  Write-Host '  NOT installed. GitHub will not enforce a ruleset on a private repo' -ForegroundColor Yellow
  Write-Host '  on this plan, so this hook is the protection that actually works:' -ForegroundColor Yellow
  Write-Host '    cp scripts\ops\pre-push .git\hooks\pre-push' -ForegroundColor Yellow
}

Write-Host '=== 2. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 3. the rule-retirement guards, then everything ===' -ForegroundColor Cyan
python -m pytest -q tests/test_a_rule_can_be_retired_without_dropping_the_position.py tests/test_p4_rules_and_drift.py tests/test_card_claims.py
if ($LASTEXITCODE -ne 0) { throw 'the rule guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 4. commit ===' -ForegroundColor Cyan
$msg = @'
PR 59.1: a retired sleeve left its stops behind, and nothing could tell they were its

Found on the live book the day after PR 59 shipped. The B2 swap did what it
claimed - 33.09% moved from btm_swing_dip to btm_qqq_hold, nothing bought or
sold, the TQQQ max_weight cap went inactive, the SOXL cap re-levelled 40.0 to
33.9, correcting a stale hand-set value on the way. Then /api/v1/rules showed:

  TQQQ  max_weight     39.0   False   sleeve      <- retired correctly
  TQQQ  take_profit    41.0   True    (no owner)
  TQQQ  trailing_stop  20.0   True    (no owner)
  TQQQ  stop_loss      65.7   True    (no owner)

ROOT CAUSE. _arm_sleeve_caps retires a cap for a ticker no sleeve wants any
more, and its own docstring says why: "a row removed without re-levelling the
caps leaves a ceiling on the book with nothing behind it". The stop and the
take-profit had no equivalent path - and not because anyone decided they should
outlive the sleeve. They could not be IDENTIFIED. discipline_rules emitted them
with no strategy_id, the apply path passes that field straight through, and
they landed in the database as NULL. Nothing could say which sleeve armed them,
so nothing could retire them. signal_rules had stamped its own all along; only
the discipline path did not.

THE NOTES SETTLED WHICH WAS WHICH, by evidence rather than by level arithmetic:

  41.0  "Swing: Dip-Buy in an Uptrend: bank a 41% gain..."   the sleeve's
  20.0  "From uptrend alert (protect gains, 20% trail)"      not the sleeve's
  65.7  "Caps downside ~15% below today's 77.30."            not the sleeve's

Two of the three had nothing to do with the sleeve, which is the case for
refusing to guess: retiring "every active rule on a ticker no sleeve wants
any more" would have disarmed both - and the book still HOLDS TQQQ, so those
two are protecting a live position.

WHAT SHIPS.

  * discipline_rules stamps strategy_id on the trailing stop and the
    take-profit, so a sleeve-armed rule can be traced back to its sleeve.
  * retire_strategy_rules deactivates rules carrying the departing strategy id,
    wired into BOTH swap_sleeve and retire_sleeve, and running BEFORE
    _arm_sleeve_caps because that function commits - a stop left armed by a
    commit that succeeded is the same ceiling-with-nothing-behind-it, one rule
    type over.
  * Ownership is MARKED, NOT ASSUMED, the lesson _arm_sleeve_caps records
    learning the hard way. Only a stamped rule is touched. A rule with no owner
    on an affected ticker is NAMED and left alone, returned as
    "left armed - no owner recorded", so the caller can report it rather than
    silently leaving it armed or silently killing a rule the user wrote.
  * Retired, not deleted: active = False, history kept.

IT CANNOT CLEAN UP RULES ARMED BEFORE THE STAMP, including the three above, and
does not pretend to. They carry NULL, and guessing at them is the failure this
PR is about.

ALSO IN: scripts/ops/pre-push, a hook that refuses a force-push or a deletion of
main - verified against a throwaway origin, where a normal push is allowed, a
--force push is refused and a branch delete is refused. It exists because GitHub
will not enforce a ruleset on a private repository on this plan; the Rulesets
page says so itself, so two correctly ticked boxes were configured against a
rule that would never fire. The hook is not equivalent - it runs only where
installed and --no-verify walks past it - but it stops the accident, which is
the whole class of incident here. OPS_RUNBOOK.md and ops-check.ps1 now say this
plainly, and ops-check reports the item as MANUAL rather than TODO: a check that
stays red for something you cannot do is a check you learn to ignore.

Gates: 1706 passed on SQLite, 1706 on PostgreSQL against a dropped and
recreated database, ruff check app tests alembic clean. All three new
assertions were confirmed RED against the pre-fix tree.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/services/strategy_catalog.py app/services/strategy_service.py
git add -- tests/test_a_rule_can_be_retired_without_dropping_the_position.py
git add -- scripts/ops/pre-push scripts/ops/ops-check.ps1 scripts/ops/OPS_RUNBOOK.md
git add -- STATUS.md scripts/deploy/ship-pr59-1-the-rules-a-retired-sleeve-leaves.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 5. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

if ($haveKey) {
  Write-Host '=== 6. what a sleeve-armed rule looks like now ===' -ForegroundColor Cyan
  Write-Host '  Nothing changed on your book: this deploy fixes the NEXT retirement.'
  Write-Host '  Accept a discipline card and its rules will carry a strategy_id;'
  Write-Host '  retire or swap that sleeve and they go inactive with it.'
  Write-Host ''
  $rules2 = (Invoke-RestMethod -Uri "$base/api/v1/rules" -Headers $apiHeaders -TimeoutSec 60).rules
  $orphans = @($rules2 | Where-Object { $_.active -and -not $_.strategy_id })
  Write-Host ("  active rules with no owner, carried over: {0}" -f $orphans.Count)
  foreach ($r in $orphans) {
    Write-Host ("    {0,-6} {1,-14} {2,7}" -f $r.ticker, $r.rule_type, $r.level)
  }
  Write-Host '  These predate the stamp. Judge them by their note, not by their ticker.'
}

Write-Host ''
Write-Host 'PR 59.1 SHIPPED.' -ForegroundColor Green
Write-Host '  Next: item 4 - the sleeve cards become actionable (option 2).'
