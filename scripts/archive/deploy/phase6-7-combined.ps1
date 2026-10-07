# PHASES 6 + 7 combined, with the test-isolation fix.
#
# Self-contained (does not dot-source _common.ps1) so it can also be pasted
# straight into the console. Idempotent: files already committed by an earlier
# phase-6 run simply show no diff and stage nothing.
#
#   .\scripts\deploy\phase6-7-combined.ps1

$ErrorActionPreference = 'Stop'
Set-Location 'C:\dev\Investwise-pro'

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne 'main') { Write-Host "On '$branch', not main." -ForegroundColor Yellow; if ((Read-Host 'Continue? (y/N)') -ne 'y') { exit 1 } }
git fetch origin
if ((git status -sb | Select-Object -First 1) -match 'behind') { Write-Host 'Local main is behind origin - pull first.' -ForegroundColor Red; exit 1 }

# --- gate: the isolation fix must hold under the REAL ordering ---------------
Write-Host "`nFocused check - the two tests that collided:" -ForegroundColor Cyan
python -m pytest tests/test_rule_resolution.py tests/test_trading_rule_suggestions.py tests/test_cash_pricing_guard.py -q
if ($LASTEXITCODE -ne 0) { Write-Host 'Focused tests FAILED - isolation fix did not hold.' -ForegroundColor Red; exit 1 }

Write-Host "`nFull suite (~7 min) + ruff..." -ForegroundColor Cyan
python -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host 'Suite FAILED - not committing.' -ForegroundColor Red; exit 1 }
python -m ruff check app
if ($LASTEXITCODE -ne 0) { Write-Host 'ruff FAILED (CI gates on this).' -ForegroundColor Red; exit 1 }
Write-Host 'Green.' -ForegroundColor Green

$files = @(
    "app/services/rules_service.py",
    "app/services/recommendations.py",
    "tests/test_rule_resolution.py",
    "tests/test_redeploy_cash.py",
    "tests/test_cash_pricing_guard.py",
    "STATUS.md"
)
git add -- $files
if (-not (git diff --cached --name-only)) { Write-Host 'Nothing staged - already committed.' -ForegroundColor Yellow; exit 0 }
git diff --cached --stat

$msg = @(
    "fix(rules)+feat(cash): clear triggered rules; redeploy idle cash",
    "",
    "Three reported issues, one commit - they share recommendations.py.",
    "",
    "1. TRIGGERED RULES NEVER CLEARED.",
    "   '4 trading rules triggered: CASH, MSFT, META, META - I already took",
    "   actions'. `triggered` latches True and only price alerts ever reset it,",
    "   so Accept / Mark-as-done / Ignore hid the CARD while the banner - which",
    "   reads the rules table directly - kept counting finished work.",
    "   resolve_rule() now stamps the audit event AND clears the flag. A fired",
    "   stop-loss / take-profit / trailing stop / buy-dip is a ONE-SHOT order and",
    "   is consumed; leaving it armed would re-fire on the identical condition a",
    "   second later. Max-weight and price alerts are standing conditions and",
    "   re-arm instead. Wired into apply, dismiss and complete.",
    "",
    "2. CASH WAS TREATED AS A TRADEABLE HOLDING.",
    "   It sat in the rule position index, so the suggester offered stops on a",
    "   cash balance. Harmless while the row was mispriced at ~72.9 - but the",
    "   phase 1 pricing fix reset it to its true 1.0, which those stops read as a",
    "   98% crash and fired. Cash is excluded, and a rule whose ticker is no",
    "   longer tradeable is retired rather than skipped (skipping left a latched",
    "   flag counting toward the banner with no card able to clear it).",
    "",
    "3. SURPLUS CASH HAD NO EXECUTABLE HOME.",
    "   After accepting a stop-loss: 'now I have more cash - where should I put",
    "   it?'. The portfolio sat at 30% cash against a 3% floor and the only card",
    "   about it was apply:none. New _redeploy_cash_recs emits ONE sized card,",
    "   derived not invented: spendable = cash above the objective's floor;",
    "   candidates = each asset class under target, filled first from holdings",
    "   already owned that sit below their share of it, then a screener pick for",
    "   any class with no representation; every leg clipped by the single-name",
    "   cap; sub-minimum legs dropped rather than rounded into dust.",
    "   New redeploy_cash apply-kind executes it, pricing each leg live and",
    "   debiting cash leg by leg, so a price move between building and accepting",
    "   can only shorten the list - never overspend. Topping up an existing",
    "   position blends the cost basis so gain/loss stays honest. The old",
    "   advisory 'Put idle cash to work' card is dropped when this one fires.",
    "",
    "TEST ISOLATION FIX (regression introduced by this batch):",
    "   test_rule_resolution and test_cash_pricing_guard patched module globals",
    "   with bare assignments that were never undone, so every later test in the",
    "   session ran against a two-position fake portfolio. That starved",
    "   test_trading_rule_suggestions of its holdings (StopIteration on 'AAA')",
    "   while passing in isolation. Both now use monkeypatch.",
    "",
    "+8 tests (test_rule_resolution.py, test_redeploy_cash.py)."
)
Set-Content -Path '.\COMMIT_MSG.txt' -Value $msg -Encoding utf8
git commit -F .\COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { Write-Host 'Commit failed.' -ForegroundColor Red; exit 1 }
Remove-Item .\COMMIT_MSG.txt -ErrorAction SilentlyContinue

git push origin main
if ($LASTEXITCODE -ne 0) { Write-Host 'Push failed.' -ForegroundColor Red; exit 1 }
Write-Host 'Pushed - CI is the gate, Railway deploys on green.' -ForegroundColor Green
if (Get-Command gh -ErrorAction SilentlyContinue) { gh run watch --exit-status }

Write-Host "`nAfter deploy:" -ForegroundColor Cyan
Write-Host '  $H = @{ "x-agent-key" = $env:IW_AGENT_KEY }'
Write-Host '  $B = "https://investwise-pro-production.up.railway.app"'
Write-Host '  # retire the stale CASH/acted-on rules'
Write-Host '  Invoke-RestMethod -Method POST "$B/api/v1/rules/evaluate" -Headers $H'
Write-Host '  (Invoke-RestMethod "$B/api/v1/rules" -Headers $H).rules | Where-Object { $_.triggered } | Format-Table ticker, rule_type, active'
Write-Host '  # preview the redeploy plan WITHOUT executing'
Write-Host '  $c = (Invoke-RestMethod "$B/api/v1/recommendations" -Headers $H).recommendations | Where-Object { $_.apply.kind -eq "redeploy_cash" }'
Write-Host '  $c.title; $c.action; $c.apply.legs | Format-Table ticker, amount_ils, reason'
