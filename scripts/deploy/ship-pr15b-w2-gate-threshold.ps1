# SHIP PR 15b / W2 - the regime gate's threshold was never a measurement.
#
#   .\scripts\deploy\ship-pr15b-w2-gate-threshold.ps1
#   .\scripts\deploy\ship-pr15b-w2-gate-threshold.ps1 -SkipTests
#
# No migration, but ENGINE_VERSION moves a4 -> a5. Read section 0.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags. AND run the
# Postgres job - see CLAUDE.md.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/engines/strategy_backtest.py",
    "app/services/backtest_service.py",
    "app/core/config.py",
    "app/static_app/index.html",
    "tests/test_the_gate_threshold_is_yours.py",
    "scripts/deploy/ship-pr15b-w2-gate-threshold.ps1",
    "STATUS.md"
)

$Message = @(
    "W2: the regime gate's threshold was never a measurement, so it is yours now",
    "",
    "strategy_backtest.MAX_CAGR_GIVEUP_PCT = 1.0 has always carried the comment",
    "that it is 'decided, not discovered' - 'a filter that costs more than a",
    "point a year is buying that shelter too dearly' is a preference about money,",
    "and 1.0 is where somebody put it.",
    "",
    "On this catalog it is also decisive. btm_vol_target_tqqq, reproduced live",
    "2026-08-25 and matching STATUS.md:2911 exactly:",
    "",
    "  ungated   29.80%/yr at -63.51%",
    "  gated     28.09%/yr at -55.19%",
    "  deltas    -1.71%/yr for -8.32 points of drawdown",
    "",
    "It fails ONLY because 1.71 > 1.0. STATUS.md:2931 already put the question:",
    "'on a 3x leveraged sleeve, 8 points of drawdown for under 2%/yr is arguably",
    "a trade worth taking... whether vol_target should be switched on is Eran's",
    "call.'",
    "",
    "So this is not a measurement, it is a threshold handed back to its owner.",
    "",
    "1. THE LIMIT IS A SETTING. gate_verdict takes max_giveup_pct and reports",
    "which limit produced the verdict and whether it is still the one this module",
    "shipped with. That second field is not decoration: measure() always passes",
    "the setting, so an 'is None' check would have read False even when nobody",
    "had changed anything, and 'the user chose this limit' is exactly the claim a",
    "reader needs to be able to check.",
    "",
    "2. A GATE THAT LOST ON COST ALONE NOW REPORTS WHERE IT PASSES.",
    "would_improve_at_limit_pct is 1.71 for vol_target, with a note naming what",
    "that limit buys: 8.3 points of drawdown. 'It failed' and 'it would pass at",
    "1.71' are the same measurement, and only the second is a decision the reader",
    "can make.",
    "",
    "Offered ONLY when the gate reduced drawdown and lost on cost. A gate that",
    "failed on DRAWDOWN cannot be rescued by any limit, and offering one would",
    "invite raising the limit until something passes.",
    "",
    "3. THE OPT-IN IS PER STRATEGY AND STILL OFF BY DEFAULT. The rule in",
    "backtest_service is unchanged - 'the gate ships off everywhere until a human",
    "has compared the two numbers' - and what changed is that there is now a way",
    "for a human to say they have that is not a code edit.",
    "",
    "ENGINE_VERSION MOVED a4 -> a5 BECAUSE THE MEANING OF A ROW CHANGED. An",
    "opted-in strategy's HEADLINE numbers are the gated run. A gated CAGR sitting",
    "in the same field as an ungated one with nothing to tell them apart is worse",
    "than not having the gate at all, so every row now records",
    "regime_gate_applied, and the bump invalidates the existing rows so the",
    "nightly job recomputes rather than silently mixing the two.",
    "",
    "An opted-in strategy whose gated run ABSTAINS keeps its ungated numbers and",
    "says so, loudly, in opt_in_not_honoured and in the log. Falling back",
    "silently would put an ungated CAGR under a row that claims to be gated.",
    "",
    "AND THE COMPARISON IS FINALLY VISIBLE. backtest_service has run the strategy",
    "gated and ungated on every nightly refresh since the gate was written and",
    "stored both. Grepping index.html before this change, the only reader of",
    "robustness was out_of_sample - the regime block was computed nightly and",
    "rendered NOWHERE. A measurement nobody can see cannot be the human",
    "comparison the ship-off rule is waiting for.",
    "",
    "17 new tests. Suite green on SQLite (1270) and on Postgres 16 (1266), plus",
    "ruff on app and tests."
)

Step "0. What this deploy does to your database"
Write-Host "  No migration. But ENGINE_VERSION moves a4 -> a5." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Every stored backtest row becomes STALE by definition and is" -ForegroundColor DarkGray
Write-Host "  recomputed by the 03:30 job. Until then the Strategies page shows" -ForegroundColor DarkGray
Write-Host "  its last good numbers labelled stale, which is the designed" -ForegroundColor DarkGray
Write-Host "  behaviour: an old measurement beats no measurement." -ForegroundColor DarkGray

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite }

Step "3. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - verifying what is already on main." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

Step "4. Push, and let CI gate it"
Push-AndWatch

Step "5. Wait for Railway to actually serve it"
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

Step "6. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Strategies page, btm_vol_target_tqqq: a new amber chip reading" -ForegroundColor Cyan
Write-Host "    'Gate: 8.32 pts shallower for 1.71%/yr - passes at a 1.71%/yr" -ForegroundColor Cyan
Write-Host "    limit'. That comparison has existed nightly for months and has" -ForegroundColor Cyan
Write-Host "    never been on screen." -ForegroundColor Cyan
Write-Host "  * Rows will read STALE until the 03:30 job runs. Expected - the" -ForegroundColor Cyan
Write-Host "    engine version moved." -ForegroundColor Cyan
Write-Host "  * Nothing is gated yet. No strategy's numbers changed." -ForegroundColor Cyan
Write-Host ""
Write-Host "  TO ACTUALLY TURN IT ON, on Railway, then redeploy:" -ForegroundColor DarkYellow
Write-Host "    REGIME_GATE_MAX_CAGR_GIVEUP_PCT=2.0" -ForegroundColor DarkYellow
Write-Host "    REGIME_GATE_ENABLED_FOR=btm_vol_target_tqqq" -ForegroundColor DarkYellow
Write-Host "  The first alone only changes the VERDICT. The second is what" -ForegroundColor DarkYellow
Write-Host "  makes the gated run the headline. Both are your call, and the" -ForegroundColor DarkYellow
Write-Host "  app will not make it for you." -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The setting is an env var, not a UI control. A toggle that" -ForegroundColor DarkYellow
Write-Host "    changes what a stored measurement MEANS deserves more care than" -ForegroundColor DarkYellow
Write-Host "    a checkbox, and I would rather you set it deliberately." -ForegroundColor DarkYellow
Write-Host "  * W3 (contribution modelling) is unstarted. Its one-line caveat" -ForegroundColor DarkYellow
Write-Host "    fix can ship any time." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
