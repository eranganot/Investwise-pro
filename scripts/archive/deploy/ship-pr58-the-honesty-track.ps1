# ship-pr58-the-honesty-track.ps1
# PR 58 - the screen stops reporting things that did not happen.
# Run after PR 57 (1658946).
#
# THREE CLAIMS WENT IN. TWO HELD. One was mine and was wrong.
#
# 1. "What's moving" reported four events that never happened. HELD.
#    BuiltinEconomicDataProvider returned four hardcoded sentences - "US CPI
#    release above consensus", "USD/ILS broke a multi-week range" - unchanged
#    since the day they were typed, with severity scores of 95 and 80.
#
#    What made them harmful is everything TRUE around them. market_impact
#    named the reader's actual holdings and computed the actual shekel
#    exposure ("touches 36% of your portfolio, 7,634"), _playbook turned each
#    into advice ("Consider hedging the currency or rebalancing toward ILS
#    assets"), and the card printed last_refreshed above it - which comes from
#    market_status(), the PRICE refresh clock. A frozen list wore a moving
#    timestamp and read as intelligence that updated four minutes ago.
#
#    get_events() now returns []. The clock is gone. The payload carries
#    source / live / source_note, and the card says "No market-intelligence
#    source is configured". annotate, _playbook and the exposure maths are
#    UNTOUCHED - wire a real provider and the panel comes straight back.
#
# 2. Settings.objective was written by plan_settings, commented "the plan
#    objective the agents optimize toward", and read by no agent. Every reader
#    takes plan.objective off the plan. Field, write and the test that
#    asserted the round-trip are gone.
#
# 3. war_room.AGENTS names seven agents that do not exist. DID NOT HOLD.
#    All seven speak in real transcripts, five unconditionally. I had read a
#    true line in docs/AGENTS.md about app/agents/ holding five modules and
#    drawn a false conclusion: those are two different taxonomies. A test now
#    pins it in both directions.
#
# THE BUG THIS PR NEARLY INTRODUCED BY FIXING IT: with events gone, the war
# room's Research line was inside "if research:" and stopped appending, while
# AGENTS still advertised Research to /war-room. The same defect one layer
# down, created by removing it. Research now always speaks, and says why it
# has nothing. Caught by an existing test that asserts the SEQUENCE of
# speakers rather than their membership.
#
# WHAT TO EXPECT AFTER: /api/v1/research/events returns count 0, live false,
# and a source_note. The Explore card reads "No market-intelligence source is
# configured" instead of four invented rows. Step 5 checks it.
#
# Gates already run on these exact bytes: 1686 SQLite, 1686 PostgreSQL on a
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
  Write-Host '=== 0b. what the screen is claiming right now ===' -ForegroundColor Cyan
  $ev = Invoke-RestMethod -Uri "$base/api/v1/research/events" -Headers $apiHeaders -TimeoutSec 60
  Write-Host ("  events reported: {0}" -f $ev.count)
  if ($ev.last_refreshed) {
    Write-Host ("  captioned with the PRICE clock: {0}" -f $ev.last_refreshed) -ForegroundColor Yellow
  }
  foreach ($e in $ev.events) {
    Write-Host ("    - {0} (relevance {1})" -f $e.event_type, $e.relevance_score) -ForegroundColor Yellow
  }
  Write-Host '  none of those happened. That is what this deploy removes.' -ForegroundColor Yellow
}

Write-Host '=== 1. ruff ===' -ForegroundColor Cyan
python -m ruff check app tests alembic
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }

Write-Host '=== 2. the honesty guards, then everything ===' -ForegroundColor Cyan
python -m pytest -q tests/test_the_screen_does_not_report_things_that_did_not_happen.py tests/test_the_agent_roster_is_what_actually_runs.py tests/test_war_room.py tests/test_market_research.py
if ($LASTEXITCODE -ne 0) { throw 'the honesty guards failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 3. commit ===' -ForegroundColor Cyan
$msg = @'
PR 58: the screen stops reporting things that did not happen

Three claims went in. Two held. One was mine and was wrong, and the correction
is recorded rather than quietly dropped.

1. "WHAT'S MOVING" REPORTED FOUR EVENTS THAT NEVER HAPPENED. Held.

BuiltinEconomicDataProvider returned four hardcoded sentences, unchanged since
the day they were typed: "US CPI release above consensus; rate-path
implications." (severity 80), "USD/ILS broke a multi-week range.", "Israeli
surtax threshold guidance revised." (severity 95), "TA-35 constituent earnings
cluster this week."

What made them harmful is everything TRUE that was wrapped around them.
market_impact.annotate named the reader's actual holdings in each one and
computed the actual shekel exposure - "touches 36% of your portfolio (7,634)".
_playbook turned each into advice - "Consider hedging the currency or
rebalancing toward ILS assets". And the card printed last_refreshed above it,
which comes from market_status(): the PRICE refresh clock. So a frozen list
wore a moving timestamp and read as market intelligence that had updated four
minutes ago.

Every layer is honest by itself. The exposure arithmetic is right, the FX
normalisation is right, the playbook is a defensible mapping from event type to
action. They were composed on top of four sentences that were not true, and the
composition is what reached the user.

docs/AGENTS.md had already found all of this, including the borrowed clock, and
test_the_agent_roster_is_what_actually_runs.py pinned it as a defect with the
comment "Passing means the bug is still there." This is the fix for a defect
that was already written down, which is the argument for writing them down.

THE FIX IS TO RETURN NOTHING, not to label the fiction. The frozen FX table
survives being labelled because it has a job: some number must value a dollar
holding, and a stale one with a loud warning beats a crash. Nothing needs an
economic event to exist. get_events() returns [], the borrowed clock is gone,
and the payload carries source / live / source_note instead. The card says "No
market-intelligence source is configured, so there is nothing to report here."
economic_provider() warns once per process the way fx_provider() does.

annotate, _playbook and the exposure maths are UNTOUCHED. They are the part
worth keeping; wire a real provider and the whole panel comes back.

THE BUG THIS PR NEARLY INTRODUCED BY FIXING IT. With events gone, research is
empty on every deploy, and the war room's Research line - inside "if research:"
- simply stopped appending. AGENTS advertises "Research" to /war-room as the
cast list, so the transcript would have named a speaker who never speaks: the
same defect, one layer down, created by removing it. Research now always
speaks, and says it has nothing and why. Caught by an existing test that
asserts the SEQUENCE of the first three speakers rather than their membership,
which is the whole argument for asserting on sequence.

2. Settings.objective WAS A WIRING DIAGRAM FOR A WIRE NOBODY RAN. Held.

Written by plan_settings, carrying the comment "plan objective
(Grow/Balanced/Preserve/Income) the agents optimize toward", and read by no
agent. Every reader goes to plan.objective directly:
recommendations.py:991/1178/3730, strategy_service.py:515/630. The only thing
that ever touched the settings copy was a test asserting it round-tripped - and
that assertion was the only evidence the wire existed, which is what made a
dead field look load-bearing. Field, write and assertion all removed.

3. war_room.AGENTS NAMES SEVEN AGENTS THAT DO NOT EXIST. DID NOT HOLD.

This claim was mine and it was wrong. All seven names are emitted in
transcripts built by the real pipeline, five of them unconditionally. I had
read a true observation in docs/AGENTS.md - "war_room.py:18 declares SEVEN
agents, five classes exist under app/agents/, and the overlap is TWO" - and
drawn a false conclusion from it. Those are two different taxonomies:
app/agents/ holds five decision-making modules, while the war room's names are
pipeline ROLES, most of which are engines. Whether calling an engine an "agent"
in a "chat transcript" is itself overclaiming is a fair argument and a separate
one; the list is not fabricated.
test_every_war_room_agent_is_one_that_actually_speaks pins it in both
directions - no name advertised that never speaks, no speaker missing from the
cast.

Five existing tests asserted the old behaviour and were REWRITTEN rather than
deleted, each keeping the property that mattered: the research agent's evidence
contract now runs against a stub instead of the fixture, and the "is the agent
thinning the list?" check still works with nothing to thin.

Gates: 1686 passed on SQLite, 1686 on PostgreSQL against a dropped and
recreated database, ruff check app tests alembic clean. Five of the seven new
assertions were confirmed RED against the pre-PR-58 tree.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- app/providers/builtin.py app/providers/registry.py app/api/routes/market.py
git add -- app/services/war_room.py app/services/plan_service.py app/core/config.py
git add -- app/static_app/index.html docs/AGENTS.md
git add -- tests/test_the_screen_does_not_report_things_that_did_not_happen.py
git add -- tests/test_the_agent_roster_is_what_actually_runs.py tests/test_api_integration.py
git add -- tests/test_market_research.py tests/test_recos_apply.py
git add -- STATUS.md scripts/deploy/ship-pr58-the-honesty-track.ps1
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 4. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

# ------------------------------------------ does the screen still claim it?
if ($haveKey) {
  Write-Host '=== 5. what the screen claims now ===' -ForegroundColor Cyan
  $ev2 = Invoke-RestMethod -Uri "$base/api/v1/research/events" -Headers $apiHeaders -TimeoutSec 60

  Write-Host ("  events reported: {0}" -f $ev2.count)
  Write-Host ("  live:            {0}" -f $ev2.live)
  Write-Host ("  source:          {0}" -f $ev2.source)
  Write-Host ("  note:            {0}" -f $ev2.source_note)

  $clock = $ev2.PSObject.Properties['last_refreshed']
  if ($null -eq $clock) {
    Write-Host '  the borrowed price clock is gone' -ForegroundColor Green
  } else {
    Write-Host '  STILL PRESENT: last_refreshed on the events payload' -ForegroundColor Red
  }

  if ($ev2.count -eq 0 -and $ev2.source_note) {
    Write-Host ''
    Write-Host '  THE PANEL NO LONGER REPORTS EVENTS THAT DID NOT HAPPEN,' -ForegroundColor Green
    Write-Host '  and it says why rather than going blank.' -ForegroundColor Green
  } elseif ($ev2.count -gt 0) {
    Write-Host ''
    Write-Host ('  {0} events still returned. Check the deploy SHA.' -f $ev2.count) -ForegroundColor Red
  }

  Write-Host ''
  Write-Host '  Open Explore in the app and confirm the card reads the note above,' -ForegroundColor Cyan
  Write-Host '  and that the Glossary below it still renders.' -ForegroundColor Cyan
}

Write-Host ''
Write-Host 'PR 58 SHIPPED.' -ForegroundColor Green
Write-Host '  Still open: .\scripts\ops\ops-check.ps1 - branch protection and VAPID.'
