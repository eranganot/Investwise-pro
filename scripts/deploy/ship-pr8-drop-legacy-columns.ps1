# SHIP PR 7b + PR 8 - the core picker says what each family is FOR, and the
# legacy strategy columns are deleted.
#
#   .\scripts\deploy\ship-pr8-drop-legacy-columns.ps1
#   .\scripts\deploy\ship-pr8-drop-legacy-columns.ps1 -SkipTests
#
# THIS ONE DROPS TWO PRODUCTION COLUMNS. Read section 0 before running it.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml. AND run the Postgres job - see CLAUDE.md.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "alembic/versions/0019_drop_plan_strategy_columns.py",
    "app/main.py",
    "app/models/tables.py",
    "app/api/routes/plan.py",
    "app/services/plan_service.py",
    "app/services/recommendations.py",
    "app/services/sleeve_service.py",
    "app/services/strategy_service.py",
    "app/services/strategy_signal_service.py",
    "app/static_app/index.html",
    "tests/test_c2_sleeves.py",
    "tests/test_c3_funding.py",
    "tests/test_c4_per_sleeve.py",
    "tests/test_c6_core.py",
    "tests/test_core_choice.py",
    "tests/test_plan_sleeves.py",
    "tests/test_sleeve.py",
    "tests/test_startup_ddl_covers_migrations.py",
    "tests/test_strategy_signals.py",
    "scripts/smoke/smoke-c1.ps1",
    "scripts/smoke/smoke-c2.ps1",
    "scripts/smoke/smoke-p4.ps1",
    "scripts/smoke/smoke-beat-market.ps1",
    "scripts/deploy/ship-pr8-drop-legacy-columns.ps1",
    "STATUS.md"
)

$Message = @(
    "Delete plans.strategy / plans.strategy_sleeve_pct - the spare copy was lying",
    "",
    "Those two columns can express exactly ONE sleeve. plan_sleeves (0014)",
    "replaced them with N rows and C1-C4 moved every reader across. What was left",
    "behind was not a spare copy but a SECOND ANSWER, and nothing kept the two in",
    "step.",
    "",
    "Measured on the live book today:",
    "",
    "    plans.strategy = btm_swing_dip @ 15.0%      <- stale",
    "    plan_sleeves   = btm_swing_dip @ 38.4%",
    "                     btm_trend_soxl @ 25.6%",
    "",
    "23 points out of date, AND STILL CONSULTED - by the tax harvester's",
    "sleeve-exclusion set, the strategy-signal service and the sleeve-drift card.",
    "All three fallbacks existed to make the protection 'never weaker than it was",
    "in C1'. By the end they made it weaker: they would have computed from 15% a",
    "book that actually ran 38.4%, leaving a third of the sleeve outside harvest",
    "protection and measuring drift against a target nobody set.",
    "",
    "GONE: readers in recommendations.py (x2), strategy_signal_service and",
    "strategy_service.load_basket; writes in the plan_service whitelist,",
    "apply_strategy and retire_sleeve; the legacy block and both fields on /plan;",
    "the _appliedIds() fallback in index.html; both one-shot backfills and the",
    "startup call; the model columns. The startup DDL now DROPs rather than ADDs,",
    "because alembic does not run against production - that list is the mechanism.",
    "",
    "The backfills' KV keys are KEPT ON PURPOSE. They are the record that the",
    "migration happened, and the only thing that would stop a resurrected backfill",
    "re-running against a book that has since had sleeves removed by hand. Both",
    "had already been spent on every live database; production logged '0 sleeve(s)",
    "created' on every boot.",
    "",
    "THE GUARD TEST HAD TO CHANGE SHAPE.",
    "test_startup_ddl_covers_migrations.py asserts every migration-added column is",
    "mirrored in main.py's startup DDL. It had no concept of a DROP, so it would",
    "have demanded these two columns forever. It now walks migrations in order,",
    "lets a later drop_column cancel an earlier add_column, ignores downgrade()",
    "bodies, and gained the mirror-image assertion: a column a migration drops must",
    "be dropped by the startup DDL too. Otherwise the DDL merely stops ADDING it",
    "and every existing database keeps it - which is the exact failure 0019 exists",
    "to end.",
    "",
    "~26 assertions were RE-PREMISED, not deleted. Each read plans.strategy while",
    "asserting something real; the property moved to /plan/sleeves and they moved",
    "with it. Clearest case: test_removing_the_named_sleeve_moves_the_legacy_pointer",
    "guarded a REPAIR (re-point the column at an arbitrary survivor). It is now",
    "test_removing_a_sleeve_leaves_no_second_answer_anywhere, which asserts the",
    "thing that repair was standing in for.",
    "",
    "Four smoke scripts follow: smoke-c1 C1.3/C1.5, smoke-c2 C2.3, smoke-p4 drift",
    "sizing, smoke-beat-market's active-strategy pick. Each now asks the live",
    "deploy the one question only it can answer - are the columns actually gone",
    "from THIS database.",
    "",
    "ALSO IN THIS COMMIT - PR 7b, the core picker.",
    "",
    "Eran: 'I want indication on the core strategy about which strategy group is",
    "it (i.e. grow/balance/income/preserve) - it will help me decide.'",
    "",
    "Fourteen names arrived with no way to tell Leveraged Momentum from",
    "Low-Volatility Defensive except by squinting at the mix. Every family is",
    "keyed by a goal group in strategies.py and /plan/core/options has always",
    "returned it; the picker just never drew it. Each row now leads with the",
    "group, and the four words are defined once on the panel - a chip whose word",
    "you have to guess is decoration.",
    "",
    "And the picker is reachable a SECOND time. 'Clear' was the only way out of a",
    "core you had already chosen, and between pressing Clear and pressing Use the",
    "book has no core at all - every rebalance card falls back to measuring drift",
    "against the objective. The first core is chosen before you have seen what it",
    "does, so changing it is the normal case. It gets a Change button that does",
    "not pass through 'unmanaged'; the family in use shows as 'In use'.",
    "",
    "The panel states the size the choice actually governs: the sleeves claim 64%",
    "and this cannot touch them. Both halves matter - a Grow core does not make",
    "the book much more aggressive, and a Preserve one does not rescue it.",
    "",
    "STILL DELIBERATELY ABSENT: the app does not RECOMMEND a core. Every 'worst",
    "fall' on that panel is an estimate from the catalogue profile, not a",
    "measurement, and a modelled number wearing the authority of a measured one is",
    "the failure this app keeps having. The ranked answer is T6b."
)

Step "0. What this deploy does to your database"
Write-Host "  Migration 0019 DROPS two columns from the plans table:" -ForegroundColor Yellow
Write-Host "      plans.strategy" -ForegroundColor Yellow
Write-Host "      plans.strategy_sleeve_pct" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Not reversible in any useful sense. downgrade() recreates the shape" -ForegroundColor DarkGray
Write-Host "  but not the contents, and the contents were WRONG - 15.0% against a" -ForegroundColor DarkGray
Write-Host "  book running 38.4%. Everything worth keeping is in plan_sleeves," -ForegroundColor DarkGray
Write-Host "  which is the point. Section 3 below prints what they hold right now," -ForegroundColor DarkGray
Write-Host "  BEFORE anything is pushed, so there is a record." -ForegroundColor DarkGray

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite }

Step "3. Record what the live book says BEFORE the drop"
$ApiHeaders = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
try {
    $preSl = Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan/sleeves" -Headers $ApiHeaders -TimeoutSec 60
    $prePlan = Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan" -Headers $ApiHeaders -TimeoutSec 60
    Write-Host "  plan_sleeves:" -ForegroundColor DarkGray
    foreach ($r in @($preSl.sleeves)) {
        Write-Host "      $($r.strategy_id)  $($r.sleeve_pct)% target" -ForegroundColor DarkGray
    }
    Write-Host "      core: $(if ($preSl.core) { $preSl.core.strategy_id } else { '(none)' })" -ForegroundColor DarkGray
    Write-Host "  the columns about to be dropped:" -ForegroundColor DarkGray
    Write-Host "      plans.strategy            = $($prePlan.strategy)" -ForegroundColor DarkGray
    Write-Host "      plans.strategy_sleeve_pct = $($prePlan.strategy_sleeve_pct)" -ForegroundColor DarkGray
} catch {
    Warn "could not read the live plan first: $($_.Exception.GetType().Name)"
}

Step "4. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - verifying what is already on main." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

Step "5. Push, and let CI gate it"
Push-AndWatch

Step "6. Wait for Railway to actually serve it"
$live = ""
$deadline = (Get-Date).AddMinutes(8)
while ((Get-Date) -lt $deadline) {
    try {
        $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 20
        $live = "$($h.commit)"
        if ($live -eq $sha) { break }
    } catch { $live = "(no answer)" }
    Write-Host "  serving $live, waiting for $sha ..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 20
}
if ($live -eq $sha) { Write-Host "  Railway is serving $sha" -ForegroundColor Green }
else { Warn "Still serving '$live' after 8 minutes. Check the Railway build log." }

Step "7. Verify the columns are gone and nothing lost its sleeves"
$fail = 0
try {
    $p = Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan" -Headers $ApiHeaders -TimeoutSec 60
    $stale = @($p.PSObject.Properties.Name | Where-Object { $_ -in @('strategy', 'strategy_sleeve_pct') })
    if ($stale.Count -eq 0) {
        Write-Host "  PASS  /plan carries no strategy pointer" -ForegroundColor Green
    } else {
        Write-Host "  FAIL  /plan still returns: $($stale -join ', ')" -ForegroundColor Red
        $fail = 1
    }

    $sl = Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan/sleeves" -Headers $ApiHeaders -TimeoutSec 60
    if ($sl.PSObject.Properties.Name -contains 'legacy') {
        Write-Host "  FAIL  /plan/sleeves still carries a legacy block" -ForegroundColor Red
        $fail = 1
    } else {
        Write-Host "  PASS  no legacy block on /plan/sleeves" -ForegroundColor Green
    }

    # THE ONE THAT MATTERS. A dropped column must not take the sleeves with it.
    $now = @(@($sl.sleeves) | ForEach-Object { "$($_.strategy_id) $($_.sleeve_pct)%" })
    if ($now.Count -eq 0) {
        Write-Host "  FAIL  the book now runs NO sleeves - the drop took them" -ForegroundColor Red
        $fail = 1
    } else {
        Write-Host "  PASS  sleeves intact: $($now -join ', ')" -ForegroundColor Green
    }
    if ($preSl -and @($preSl.sleeves).Count -ne @($sl.sleeves).Count) {
        Write-Host "  FAIL  sleeve count moved: $(@($preSl.sleeves).Count) -> $(@($sl.sleeves).Count)" -ForegroundColor Red
        $fail = 1
    }

    # The core survived too, and the picker can still list families.
    $opts = Invoke-RestMethod -Uri "$BaseUrl/api/v1/plan/core/options" -Headers $ApiHeaders -TimeoutSec 60
    $groups = @($opts.options | ForEach-Object { $_.goal } | Sort-Object -Unique)
    $want = @('Balanced', 'Grow', 'Income', 'Preserve')
    if (@(Compare-Object $groups $want -SyncWindow 0).Count -eq 0) {
        Write-Host "  PASS  every family carries a goal group ($($groups -join ', '))" -ForegroundColor Green
    } else {
        Write-Host "  FAIL  goal groups are $($groups -join ', '), expected $($want -join ', ')" -ForegroundColor Red
        $fail = 1
    }
    $noDd = @($opts.options | Where-Object { $null -eq $_.est_max_drawdown_pct -and $null -eq $_.measured_max_drawdown_pct })
    if ($noDd.Count -gt 0) {
        Write-Host "  FAIL  $($noDd.Count) family/families offer no drawdown figure at all" -ForegroundColor Red
        $fail = 1
    }
    $meas = @($opts.options | Where-Object { $_.drawdown_source -eq 'measured' })
    Write-Host "        $($meas.Count)/$(@($opts.options).Count) families now have a MEASURED drawdown" -ForegroundColor DarkGray
    if ($meas.Count -eq 0) {
        Write-Host "        (still all estimates - the backtest refresh runs 03:30 UTC)" -ForegroundColor DarkGray
    }
} catch {
    Write-Host "  FAIL  $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor Red
    $fail = 1
}

Step "8. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES: on the Plan tab, the core row now has a" -ForegroundColor Cyan
Write-Host "  Change button beside Clear. Press it. Every family should carry a" -ForegroundColor Cyan
Write-Host "  coloured group chip - PRESERVE / INCOME / BALANCED / GROW - with the" -ForegroundColor Cyan
Write-Host "  four words explained on the panel, and the one you already run" -ForegroundColor Cyan
Write-Host "  should read 'In use' rather than offering itself again." -ForegroundColor Cyan
Write-Host ""
Write-Host "  Your sleeves must still read the same targets they did before this" -ForegroundColor Cyan
Write-Host "  deploy. Section 3 printed them; compare." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE: the panel does not RECOMMEND a core, and" -ForegroundColor DarkYellow
Write-Host "  most 'worst fall' figures will still say 'est.'. A modelled number" -ForegroundColor DarkYellow
Write-Host "  is not a measured one and this app does not let them wear each" -ForegroundColor DarkYellow
Write-Host "  other's clothes. T6b is where a recommendation gets a measurement" -ForegroundColor DarkYellow
Write-Host "  behind it." -ForegroundColor DarkYellow
Write-Host ""
if ($fail -ne 0) { exit 1 }
exit 0
