# PHASE 1 - Cash is never quoted as a stock.
#
# The dashboard read "Liquid cash 521,904 - 96.4% of portfolio" while the edit
# modal showed the true 1,934.52. The price-refresh job quoted the synthetic
# CASH row against the real NASDAQ ticker CASH (Pathward Financial, ~$73) and
# stamped it USD; FX then multiplied it again. This is the highest-impact fix:
# NAV, the allocation donut, funding sizing and rule weights were all derived
# from the inflated number.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase1-cash.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/services/intake_service.py",
    "app/services/pricing_service.py",
    "tests/test_cash_pricing_guard.py"
)

Invoke-Suite -Focus @("tests/test_cash_pricing_guard.py", "tests/test_cash.py", "tests/test_portfolio_totals.py")

$msg = @(
    "fix(cash): never quote the synthetic CASH row as a listed stock",
    "",
    "refresh_all_positions iterated every Position including ticker CASH, which",
    "is also a real NASDAQ listing (Pathward Financial, ~`$73). The balance was",
    "repriced as a US bank stock and meta.price_currency overwritten to USD, so",
    "FX multiplied it a second time: 1,934.52 x ~73 x ~3.70 ~= 521,904.",
    "",
    "Reported symptom: the Holdings cash card and the total portfolio value",
    "disagreed with the cash edit modal, and 'you put in 24,027' produced a",
    "fictional +2153% gain. The same inflated NAV fed the allocation mix, the",
    "funding engine's spendable-cash floor and every weight-based trading rule.",
    "",
    "- is_cash_position(): ticker OR asset_class, so the guard survives renames",
    "- repair_cash_row(): idempotent self-heal back to the ILS-native invariant",
    "  (1 unit = 1 shekel), clearing the stale quoting breadcrumbs; already-",
    "  corrupted rows fix themselves on the next refresh, which primes at boot",
    "- refresh_all_positions now reports skipped_cash / repaired_cash",
    "",
    "+6 tests (test_cash_pricing_guard.py)."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nAfter deploy, verify on the Pixel 9:" -ForegroundColor Cyan
Write-Host "  - Holdings cash card == the value in the cash edit modal"
Write-Host "  - Total portfolio value and 'you put in' are sane again (no +2153%)"
Write-Host "  - Cash slice in the allocation donut matches the real balance"
