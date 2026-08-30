# SHIP PR 19 - the contradiction check stops reading card titles.
#
#   .\scripts\deploy\ship-pr19-contradictions-read-structure.ps1
#   .\scripts\deploy\ship-pr19-contradictions-read-structure.ps1 -SkipTests
#
# No migration. No engine version bump. No card is suppressed by this: it makes
# a warning appear that should have been there, and changes nothing else.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags. AND run the
# Postgres job. See CLAUDE.md.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/services/contradictions.py",
    "app/services/recommendations.py",
    "tests/test_contradictions_read_structure.py",
    "scripts/agent-key.ps1",
    "scripts/smoke/smoke-rebalance-cash.ps1",
    "scripts/smoke/smoke-minor-units.ps1",
    "scripts/deploy/ship-pr19-contradictions-read-structure.ps1",
    "scripts/smoke/smoke-contradictions.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 19: two cards contradicted each other and rule 2 saw neither leg",
    "",
    "Eran, 2026-08-29, on the deployed app: one card sold ~1,071 ILS of Equities",
    "('Rebalance toward your target mix', BOOK scope) while another bought ~3,139",
    "of SOXL (SLEEVE scope). SOXL IS Equities, so accepting both moves the mix",
    "the wrong way. contradictions.py rule 2 exists for exactly this and reported",
    "nothing. Measured on the real pair: 0 contradictions found, 0 notes on",
    "either card.",
    "",
    "TWO HOLES, BOTH LIVE AT ONCE, AND THE PAIR WENT THROUGH BOTH:",
    "",
    "  * :111 - the sell side required",
    "    str(r.get('title','')).lower().startswith('sell'). _reconcile merges the",
    "    rebalance legs and renames the survivor 'Rebalance toward your target",
    "    mix', which does not start with 'sell'.",
    "  * :109 - 'if not cls: continue'. The SOXL sleeve top-up is ticker-scoped",
    "    and carries no asset_class at all, so the card was skipped before its",
    "    direction was ever considered.",
    "",
    "THE DEEPER DEFECT, WHICH THE PLAN DID NOT ANTICIPATE. The merge does not",
    "just rename the card, it destroys the structure the check needs. After",
    "_reconcile, apply.asset_class names whichever leg sorted FIRST and every",
    "other leg survives only as a TITLE STRING in meta.merged_legs. So a card",
    "that sells Equities and buys Commodities presented itself to every consumer",
    "as one card about Equities. There was no data to read even if rule 2 had",
    "wanted to.",
    "",
    "FIXED STRUCTURALLY ON BOTH SIDES, per the card-claim rule already in force",
    "(verify by applying the card's spec, never by asserting on card text):",
    "",
    "  1. the generator records apply.direction instead of leaving it to be read",
    "     back off the title;",
    "  2. _reconcile writes apply.legs[] - one entry per leg with its own class,",
    "     direction and amount - so the merged card describes itself as data;",
    "  3. contradictions._class_moves derives {(asset_class, direction)} from",
    "     apply.legs[] -> apply.asset_class + kind -> allocation_mix.classify on",
    "     every ticker the card names. NO BRANCH OF IT READS A TITLE. A card with",
    "     no asset_class is not a card with no asset class.",
    "",
    "A card is never flagged against itself: a merged rebalance sells one class",
    "and buys another in ONE decision, and flagging that would turn this fix into",
    "a permanent false alarm on every rebalance - a fast way to get a real",
    "warning ignored.",
    "",
    "STILL REPORTED, NOT SUPPRESSED. Both cards are defensible on their own and",
    "the choice is Eran's; what was unacceptable was being told nothing. Each",
    "card now carries a note naming the other.",
    "",
    "MEASURED, OLD CODE VS NEW, ON THE LIVE PAIR:",
    "  before   0 contradictions, 0 buys_and_sells_one_class, no notes",
    "  after    1 contradiction,  1 buys_and_sells_one_class (Equities), both",
    "           cards noted",
    "",
    "ALSO IN HERE: the agent-key helper shipped in PR 18 had the same defect it",
    "was written to describe, and Eran hit it within the hour. Get-AgentKey",
    "preferred $env:IW_AGENT_KEY UNCONDITIONALLY, so an env var holding a WRONG",
    "value silently beat a working fallback - and the smoke scripts then printed",
    "'/portfolio did not answer' when the server had answered perfectly clearly",
    "with a 401. Two very different failures rendered identically, which is the",
    "class of defect these scripts exist to catch.",
    "",
    "  * Get-AgentKey now TESTS each candidate against the live API and falls",
    "    through on a 401/403, naming the source it skipped. Only 401/403 is a",
    "    rejection: a timeout or a 5xx says nothing about the key, so the key is",
    "    kept rather than discarded for the wrong reason.",
    "  * The smoke Api helper prints the status code, and says in words that a",
    "    401 is a rejected key and not an outage.",
    "  * The request splat is $reqArgs, never $args - $args is a PowerShell",
    "    AUTOMATIC variable holding the function's own unbound arguments. Same",
    "    family as the $h that once clobbered the auth headers.",
    "",
    "Verified against the live deploy from the sandbox: with IW_AGENT_KEY set to",
    "the placeholder string, Get-AgentKey reports the 401, skips it, resolves the",
    "committed key, and Test-AgentKey confirms that key authenticates while a",
    "deliberately wrong one does not.",
    "",
    "Every .ps1 in this PR is parse-checked with the PowerShell language parser",
    "rather than eyeballed - 0 parse errors across all seven ship and smoke",
    "scripts written this session.",
    "",
    "Gate: SQLite 1351 passed / 4 skipped; Postgres 1347 passed / 12 skipped;",
    "ruff check app tests clean. The new tests assert on the reported",
    "contradiction and on _class_moves, never on card text - including one that",
    "renames the card to five different titles and requires the answer not to",
    "move.",
    "",
    "Not financial advice."
)

Step "0. What this changes on your screen"
Write-Host "  Nothing is suppressed and no card disappears." -ForegroundColor Yellow
Write-Host ""
Write-Host "  When two cards move the same asset class in opposite directions," -ForegroundColor Yellow
Write-Host "  BOTH now carry a note naming the other, and the pair appears in" -ForegroundColor Yellow
Write-Host "  contradictions[] on /api/v1/recommendations." -ForegroundColor Yellow
Write-Host ""
Write-Host "  You may see a warning on a pair you have been living with. That" -ForegroundColor Yellow
Write-Host "  pair was always there; the check could not see it." -ForegroundColor Yellow
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. The gate - all three CI jobs"
if ($SkipTests) {
    Warn "-SkipTests: the gate did NOT run. CI is then the only thing between"
    Warn "this and Railway, and Railway deploys on red."
} else {
    Invoke-Suite -Focus @(
        "tests/test_contradictions_read_structure.py",
        "tests/test_live_contradictions.py",
        "tests/test_card_claims.py",
        "tests/test_recos_apply.py")
    Write-Host ""
    Write-Host "  The SQLite run above is NOT the Postgres job." -ForegroundColor Yellow
    Write-Host "  Run test-postgres before answering yes. Recipe: CLAUDE.md." -ForegroundColor Yellow
    if ((Read-Host "  Did test-postgres pass? (y/N)") -ne "y") {
        Write-Host "  Stopping. Run it, then re-run this script." -ForegroundColor Red
        exit 1
    }
}

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

Step "6. Smoke"
Write-Host "    .\scripts\smoke\smoke-contradictions.ps1 -Sha $sha" -ForegroundColor White
Write-Host "  Read-only throughout - contradictions are computed on the GET." -ForegroundColor DarkGray

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Today: if a rebalance card and a sleeve top-up both move the" -ForegroundColor Cyan
Write-Host "    same class, each now says so under the card. Before this deploy" -ForegroundColor Cyan
Write-Host "    the exact pair you found said nothing at all." -ForegroundColor Cyan
Write-Host "  * /api/v1/recommendations: a merged rebalance card now carries" -ForegroundColor Cyan
Write-Host "    apply.legs[] with a class and a direction per leg, instead of" -ForegroundColor Cyan
Write-Host "    only meta.merged_legs, which was prose." -ForegroundColor Cyan
Write-Host "  * Nothing was suppressed. If a card vanished, that is a different" -ForegroundColor Cyan
Write-Host "    rule and worth telling me about." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The account-history chart still reads a holding added through" -ForegroundColor DarkYellow
Write-Host "    intake as performance, and still claims there were no flows." -ForegroundColor DarkYellow
Write-Host "    PR 20." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches up to 97% rather than the" -ForegroundColor DarkYellow
Write-Host "    75% you accepted. PR 21. After that the bucket is empty." -ForegroundColor DarkYellow
Write-Host "  * The intermittent +739.21 ILS leak is still unexplained. A" -ForegroundColor DarkYellow
Write-Host "    recurrence now logs a NAV CONSERVATION BREACH line (PR 17)." -ForegroundColor DarkYellow
Write-Host "  * fx_rate REPORTS an unresolvable currency; it does not ABSTAIN." -ForegroundColor DarkYellow
Write-Host "  * There is still not one Tel Aviv LISTING in the candidate" -ForegroundColor DarkYellow
Write-Host "    universe, so no card can name one. PR 24." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is still a literal in 34 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
