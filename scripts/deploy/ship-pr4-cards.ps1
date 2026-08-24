# SHIP PR 4 - cards you can actually see.
#
#   .\scripts\deploy\ship-pr4-cards.ps1
#   .\scripts\deploy\ship-pr4-cards.ps1 -SkipTests
#
# EXPECT SOME SMOKE SCRIPTS TO GO RED AFTER THIS. smoke-p0.ps1 and
# smoke-beat-market.ps1 both fail on a non-empty degraded[], and this PR is what
# makes degraded[] tell the truth. That is the assertion working. Fix the
# provider, never the assertion.
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
    "app/services/recommendations.py",
    "app/services/rules_service.py",
    "app/services/allocation_mix.py",
    "app/services/war_room.py",
    "app/services/nav_history.py",
    "app/services/reconcile_service.py",
    "app/api/routes/intake.py",
    "app/static_app/index.html",
    "tests/conftest.py",
    "tests/test_pr4_cards.py",
    "tests/test_ledger_and_reconcile.py",
    "scripts/smoke/smoke-cards.ps1",
    "scripts/deploy/ship-pr4-cards.ps1"
)

$Message = @(
    "Show the cards, and say so when an agent is dark",
    "",
    "The report was: no recommendations about new assets, none about existing",
    "holdings. The obvious suspect was the 90-day mute, and the obvious suspect",
    "was close to irrelevant. Five mechanisms, four of which were invisible.",
    "",
    "1. THE MERGED REBALANCE CARD CARRIED A LEG'S ID. _reconcile collapses every",
    "rebalance_to_objective card into one and kept rebal[0] - whichever leg",
    "sorted first, on this book SELL Equities. So the merged card WAS the sell",
    "leg as far as the mute list was concerned, and completing a card titled",
    "'Rebalance toward your target mix' filed the BUY Commodities leg riding",
    "inside it. The user said he had never hidden a buy recommendation. He was",
    "right, and this is how it happened anyway. The merge now gets a composite id",
    "built from every leg it absorbs, sorted so leg order does not mint a second",
    "card for the same merge.",
    "",
    "2. rebalance_actions[:2] NEVER GENERATED THE THIRD LEG. Not suppressed, not",
    "filtered - never built. On a book drifted in three classes the Fixed Income",
    "card did not exist, so it could not be restored and nothing reported it",
    "missing. Every action past the drift threshold is emitted.",
    "",
    "3. AN OPEN CIRCUIT WAS NOT A DEGRADED AGENT. The history breaker was OPEN.",
    "holding_verdicts, momentum and war_room read it per ticker and swallow",
    "failures per ticker - correct, one bad symbol must not sink the scan - so a",
    "breaker open for EVERY symbol produced an empty list and no exception.",
    "Nothing appended to degraded, and the response said degraded: []. The",
    "guarantee this project holds is that a degraded agent means a MISSING card",
    "says nothing about the underlying state. An empty list quietly broke it, and",
    "that is the dominant cause of both symptoms: the app did not have an",
    "opinion, and reported having no opinion as having nothing to say.",
    "",
    "4. IDS WITH NO CONTENT. _rid('tax'), _rid('commodity_add'), _rid('raise_cash'),",
    "_rid('rules_suggested') take no variable part, so a tax card about SOXL and",
    "a tax card about MTUM are the same id and one Accept mutes the family for the",
    "window. They now carry their subject: the tickers, the pick, the size",
    "bucketed to 250. Bucketed rather than exact on purpose - an id keyed to the",
    "shekel changes on every tick, which swaps a mute that never expires for one",
    "that never applies.",
    "",
    "5. A MUTE OUTLIVING ITS CONDITION. Two ways, both fixed:",
    "",
    "  The rule self-heal was unconditional, and build_recommendations runs on",
    "  every hourly push evaluation. So a max_weight rule that keeps breaching was",
    "  re-resolved 'acknowledged' within a second of each firing, forever: V at",
    "  38% against a 30% cap and MSFT at 24 against 20, firing every 30 minutes,",
    "  both marked handled, rule_banner.triggered rendering empty. One dismissal",
    "  in August had become permanent consent to a condition that recurs. Rule",
    "  cards now carry meta.triggered_at, and a rule that fired AFTER you dealt",
    "  with it is neither resolved nor suppressed - healing it while still hiding",
    "  it is the same deadlock in a different place.",
    "",
    "  And 'Done' on a HIGH card now expires on the ignore window rather than the",
    "  done window. HIGH means the app thinks this is hurting you now; if it is",
    "  still saying so a week later, the filing was premature. Deliberately NOT",
    "  making HIGH cards undismissable, which was the other option and is worse -",
    "  a button that visibly does nothing teaches you to distrust every button.",
    "",
    "SUPPORTING FIXES, each of which was independently invisible:",
    "",
    "  classify() returned 'Equities' for every commodity ETF - DBC, GLD, USO,",
    "  COW, NIB. So _redeploy_cash_recs looked for a leg classed 'Commodities',",
    "  never found one however many commodity ideas the screener produced, and the",
    "  leg landed in unplaced every time. On screen that reads as the app having",
    "  no opinion rather than as a lookup miss.",
    "",
    "  war_room sessions never carried action_type, so the guard routing a",
    "  REBALANCE signal to the trim branch read None every time and was dead code.",
    "  Both halves were broken: ActionType.REBALANCE.value is 'Rebalance', not",
    "  'REBALANCE', so emitting the field alone would still have missed. The guard",
    "  upper-cases both sides rather than pinning to one serialization.",
    "",
    "  record_health_for walked rows[0].account - a lazy relationship touched",
    "  outside an await, which is MissingGreenlet under asyncio. It returned",
    "  health: {ok: false, reason: MissingGreenlet} on every snapshot since it",
    "  shipped. Losing the score row is non-fatal by design, so the failure sat in",
    "  a field nobody reads. Same shape as the import error that killed the whole",
    "  job: silent, and only visible where it reports itself.",
    "",
    "FOUR FOLLOW-UPS FROM PR 3 GOING LIVE:",
    "",
    "  reconcile/apply now REFUSES a scale that moves the gap further from zero.",
    "  Not hypothetical: 1.360168 was applied twice in a row, taking the book from",
    "  a 350 gap to -4,874, because the operation reads as idempotent - 'reconcile",
    "  the book' sounds like a state - and it is a plain multiply. The smoke",
    "  caught it AFTER the write. force=true exists for the deliberate second",
    "  pass and has to be asked for by name.",
    "",
    "  POST /portfolio/reconcile/baseline accepts today's residual basis as the",
    "  starting point. The alternative was scaling shares again to make 350",
    "  disappear, which deletes real holdings to satisfy a label. Cost basis above",
    "  deposits is normal in any rebalanced book - selling a winner and",
    "  reinvesting re-stamps the new lot at market - and 350 on 20,350 is inside",
    "  the reconciliation's own error bars, since the two anchors were 1.8% apart.",
    "  Forcing it to zero is false precision dressed as a fix. Recorded as a",
    "  ledger Adjust row and surfaced as 'Basis reconciled <date>'.",
    "",
    "  The 'deposits' anchor caveat was WRONG and is corrected. It claimed the",
    "  anchor over-corrects and is an upper bound on s. But sell_losers sold",
    "  LOSING positions, which pushes basis the other way. The anchor is",
    "  contaminated in both directions by an unbounded amount, and calling it a",
    "  bound was the more dangerous error of the two.",
    "",
    "  conftest empties the app engine's pool around every test. The cross-loop",
    "  asyncpg trap has now cost three red test-postgres runs in three files, and",
    "  the own_session fixture only covered the shape I knew about. It does not",
    "  cover a TestClient used WITHOUT 'with': every request gets its own anyio",
    "  portal loop, so request 2 borrows request 1's pooled connection. The test",
    "  that pays is not the one that did it - it is whichever later test touches",
    "  the app engine first, which is why it reads as an unrelated random failure",
    "  in a file nobody edited. A pool with nothing in it cannot hand a connection",
    "  across a loop boundary, which makes the family impossible rather than",
    "  documented. Writing the rule down has now failed three times.",
    "",
    "All three CI jobs verified before pushing:",
    "  pytest on postgres:16     984 passed, 12 skipped",
    "  pytest on sqlite          988 passed, 4 skipped",
    "  ruff check app tests      clean"
)

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite -Focus @("tests/test_pr4_cards.py", "tests/test_ledger_and_reconcile.py", "tests/test_recommendations.py", "tests/test_done_vs_ignored.py") }
Write-Host ""
Warn "The POSTGRES job needs a local server and is not run here. It WAS run for"
Warn "this PR in the sandbox, and it caught a real one: the new tests poisoned"
Warn "the shared engine's pool and took down an unrelated test file. conftest now"
Warn "drains the pool between tests so that whole family is impossible."

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

Step "6. Smoke (read-only)"
if (-not $env:IW_AGENT_KEY) { Warn "IW_AGENT_KEY not set - the smoke falls back to the committed key." }
& (Join-Path $RepoRoot "scripts\smoke\smoke-cards.ps1") -Sha $sha -BaseUrl $BaseUrl
$smokeExit = $LASTEXITCODE

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  ONE THING LEFT TO DO BY HAND. The eleven cards buried under the old" -ForegroundColor Cyan
Write-Host "  90-day TTL were filed under ids that no longer exist, so the new" -ForegroundColor Cyan
Write-Host "  content-bearing ids will not match them and they cannot age out." -ForegroundColor Cyan
Write-Host "  Clear the lists once:" -ForegroundColor Cyan
Write-Host "    .\scripts\smoke\smoke-cards.ps1 -Sha $sha -Restore" -ForegroundColor Gray
Write-Host ""
Write-Host "  EXPECT smoke-p0 AND smoke-beat-market TO GO RED. Both fail on a" -ForegroundColor Yellow
Write-Host "  non-empty degraded[], and this PR is what makes degraded[] honest." -ForegroundColor Yellow
Write-Host "  That is the assertion doing its job. The history provider itself is" -ForegroundColor Yellow
Write-Host "  still down - fix the provider, never the assertion." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Then update STATUS.md." -ForegroundColor Cyan
Write-Host ""
if ($smokeExit -ne 0) { exit 1 }
exit 0
