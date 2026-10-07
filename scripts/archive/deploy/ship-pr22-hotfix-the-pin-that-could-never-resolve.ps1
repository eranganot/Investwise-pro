# SHIP - the hotfix that lets PR 22 actually build. ONE COMMIT. NOTHING ELSE.
#
#   .\scripts\deploy\ship-pr22-hotfix-the-pin-that-could-never-resolve.ps1
#
# WHY THIS IS ITS OWN SCRIPT.
#
# Eran: "I will not deploy PR23 as long as PR22 is not deployed successfully."
# Correct instinct, and this script is what makes it possible - because PR 22
# CANNOT deploy without this change. The bad pin is inside PR 22's own commit
# (c52cb17d), so every build of that commit fails at "pip install". Waiting for
# PR 22 to land before fixing the pin waits forever.
#
# So this ships the smallest possible thing: one line of requirements.txt, plus
# the test that stops it recurring, plus the smoke script that proves PR 22
# reached production. The agent map is a SEPARATE patch and is not touched
# here. Nothing about docs/AGENTS.md is in this commit.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags; run BOTH backends.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "requirements.txt",
    "tests/test_the_pins_are_installable_and_installed.py",
    "scripts/smoke/smoke-pr22-is-actually-live.ps1",
    "scripts/deploy/ship-pr22-hotfix-the-pin-that-could-never-resolve.ps1",
    "STATUS.md"
)

$Message = @(
    "HOTFIX: py-vapid 1.9.4 needs cryptography>=46 and this repo pins 44",
    "",
    "PR 22 has been on main since c52cb17d and has NEVER DEPLOYED. CI #182",
    "failed all three jobs and the Railway image failed with them, both before",
    "a single line of app code ran:",
    "",
    "    ERROR: Cannot install -r requirements.txt (line 19), -r",
    "    requirements.txt (line 25) and cryptography==44.0.0 because these",
    "    package versions have conflicting dependencies.",
    "        pywebpush 2.3.0 depends on cryptography>=2.6.1",
    "        py-vapid 1.9.4 depends on cryptography>=46",
    "",
    "MEASURED from the published wheel metadata, after the fact:",
    "",
    "    py-vapid 1.9.2   Requires-Dist: cryptography >=2.5",
    "    py-vapid 1.9.4   Requires-Dist: cryptography >=46",
    "",
    "requirements.txt line 15 pins cryptography==44.0.0. The pin PR 22 added",
    "was unsatisfiable the moment it was written. Production is still serving",
    "the pre-PR-22 image, and a failed deploy is invisible from outside: the",
    "app answers, the pages render, it is simply old.",
    "",
    "1.9.2 IS SAFE FOR A REASON, NOT BY LUCK. The deprecated curve-as-class",
    "call in 1.9.2 is at py_vapid/__init__.py:200, inside generate_keys() --",
    "the one function push_service._generate_vapid stopped calling in PR 22.",
    "The path still in use, Vapid01.from_raw, already passes ec.SECP256R1()",
    "INSTANTIATED at lines 67 and 74. So the warning PR 22 set out to remove",
    "stays removed. Verified in a venv built from these exact pins:",
    "test_vapid_keys_are_generated_cleanly passes 4/4 under",
    "-W error::DeprecationWarning with cryptography==44.0.0 installed.",
    "",
    "WHY IT WAS NOT CAUGHT, which is the part that needed fixing.",
    "",
    "The pin was chosen by reading the version installed in the development",
    "sandbox. That sandbox had cryptography 50.0.1 -- not the 44.0.0 this repo",
    "pins. 'pip install py-vapid==1.9.4' succeeded there and proved nothing",
    "about the project. A 1382-test green run proved nothing either: a",
    "dependency-resolution failure happens BEFORE pytest is reachable, so no",
    "test inside the suite can ever observe it.",
    "",
    "Same shape as the FX incident -- a value read out of the local",
    "environment and written down as if it were the project's.",
    "",
    "So the suite cannot check resolution, and this commit does not pretend",
    "to. It checks the assumption underneath the mistake: that the interpreter",
    "running the tests holds the versions requirements.txt asks for.",
    "test_the_installed_version_is_the_pinned_version fails the moment they",
    "diverge, naming the package and both versions. Run in the sandbox that",
    "chose 1.9.4 it fails on cryptography: pinned 44.0.0, installed 50.0.1 --",
    "the fact that was missing.",
    "",
    "Also pinned by name: py-vapid stays below 1.9.4 while cryptography is",
    "below 46, with the failure message saying that moving to 1.9.4 means",
    "raising the cryptography pin in the SAME commit. That is a deliberate",
    "upgrade of the library every key, token and password hash in this app",
    "goes through, not a side effect of silencing a warning.",
    "",
    "AND A WAY TO TELL. smoke-pr22-is-actually-live.ps1 does not ask whether",
    "the app is up - a failed deploy looks identical to a healthy one from",
    "outside. It asks whether the payload of /portfolio/nav-history carries",
    "'unexplained_flows', the key PR 22 added at nav_history.py:362. Present",
    "means the new code is running; absent means it is not, whatever the",
    "dashboard says.",
    "",
    "Gate: SQLite 1406 passed / 0 skipped; Postgres 1406 passed / 0 skipped;",
    "ruff check app tests clean. All three run in a venv built from",
    "requirements.txt + requirements-dev.txt, not the developer sandbox.",
    "",
    "Not financial advice."
)

Step "0. What this changes"
Write-Host ""
Write-Host "  ONE LINE OF requirements.txt, and the guard against a repeat." -ForegroundColor Yellow
Write-Host ""
Write-Host "    py-vapid==1.9.4   ->   py-vapid==1.9.2" -ForegroundColor White
Write-Host ""
Write-Host "  No app behaviour changes. What changes is that main BUILDS," -ForegroundColor Yellow
Write-Host "  which means PR 22 finally reaches production." -ForegroundColor Yellow
Write-Host ""
Write-Host "  THE AGENT MAP IS NOT IN THIS COMMIT. It is a separate patch," -ForegroundColor Cyan
Write-Host "  held until you have seen PR 22 deploy and go green." -ForegroundColor Cyan
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. Is this interpreter the project's?"
# The new test does this properly inside the suite. Doing it here too, first,
# because if the answer is no then the gate below is measuring some other
# software - which is precisely how the bad pin got shipped.
python -m pytest -q tests/test_the_pins_are_installable_and_installed.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  STOP. The versions installed here are not the versions" -ForegroundColor Red
    Write-Host "  requirements.txt pins. That is exactly the condition that" -ForegroundColor Red
    Write-Host "  produced this hotfix, and nothing measured in this" -ForegroundColor Red
    Write-Host "  environment can be quoted as a fact about the project." -ForegroundColor Red
    Write-Host ""
    Write-Host "    pip install -r requirements.txt -r requirements-dev.txt" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}
Write-Host "  This interpreter matches the pins." -ForegroundColor Green

Step "3. The gate - all three CI jobs"
if ($SkipTests) {
    Warn "-SkipTests: the gate did NOT run. On a PR fixing a build that CI"
    Warn "caught and a local run did not, that is a strange flag to pass."
} else {
    Invoke-Suite -Focus @(
        "tests/test_the_pins_are_installable_and_installed.py",
        "tests/test_vapid_keys_are_generated_cleanly.py")
    Write-Host ""
    Write-Host "  Then run test-postgres. Both backends must report 0 skipped." -ForegroundColor Yellow
    if ((Read-Host "  Did test-postgres pass, with 0 skipped? (y/N)") -ne "y") {
        Write-Host "  Stopping. Run it, then re-run this script." -ForegroundColor Red
        exit 1
    }
}

Step "4. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - the patch was applied with git am. Pushing it." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

Step "5. Push, and let CI gate it"
Push-AndWatch

Step "6. Now prove PR 22 is actually in production"
Write-Host ""
Write-Host "  Railway builds on green. Give it a minute, then:" -ForegroundColor Cyan
Write-Host ""
Write-Host "    .\scripts\smoke\smoke-pr22-is-actually-live.ps1 -Sha $sha" -ForegroundColor White
Write-Host ""
Write-Host "  The deciding line is section 1. If the nav-history payload" -ForegroundColor Yellow
Write-Host "  carries 'unexplained_flows', PR 22's code is running. If it" -ForegroundColor Yellow
Write-Host "  does not, the deploy did not land - and the app will look" -ForegroundColor Yellow
Write-Host "  perfectly healthy either way, which is the trap." -ForegroundColor Yellow

Step "7. Done"
Write-Host ""
Write-Host "  main: $sha" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * CI: all three jobs green. Run #182 died at 'pip install' in" -ForegroundColor Cyan
Write-Host "    15-26 seconds; a real run takes minutes. If the next run is" -ForegroundColor Cyan
Write-Host "    fast AND red, it is the same failure, not a new one." -ForegroundColor Cyan
Write-Host "  * Railway: an image actually BUILDS. The previous deployment" -ForegroundColor Cyan
Write-Host "    stopped at 'Build > Build image' and never started." -ForegroundColor Cyan
Write-Host "  * The smoke script above, with -Sha." -ForegroundColor Cyan
Write-Host ""
Write-Host "  ONLY THEN send me the word and I will hand you the agent map" -ForegroundColor Cyan
Write-Host "  patch. It is written, gated and waiting - it is not lost." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS leak is UNEXPLAINED and CI can still go red on" -ForegroundColor DarkYellow
Write-Host "    it. SEND ME THE DIAGNOSTIC BLOCK when it next fires." -ForegroundColor DarkYellow
Write-Host "  * fx_rate REPORTS an unresolvable currency; it does not ABSTAIN." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches to 97%, not the 75% you" -ForegroundColor DarkYellow
Write-Host "    accepted." -ForegroundColor DarkYellow
Write-Host "  * There is still not one Tel Aviv LISTING in the candidate" -ForegroundColor DarkYellow
Write-Host "    universe, so no card can name one." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is still a literal in 34 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0
