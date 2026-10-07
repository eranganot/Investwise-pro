# SHIP - Phase N: real account history.
#
#   .\scripts\ship-n.ps1              # verify only
#   .\scripts\ship-n.ps1 -Ship        # verify, then commit + push if green
#
# Phase N is the first phase that WRITES a new table, so it carries two risks
# the T phases did not:
#
#   1. A migration that cannot run. 0001_initial calls Base.metadata.create_all,
#      so on a fresh database nav_snapshots already exists when 0016 runs. 0013,
#      0014 and 0015 are guarded for exactly this; 0016 must be too, and this
#      script asserts the guard rather than trusting that I remembered it.
#   2. A return line that counts deposits as performance. That is the failure
#      this phase exists to prevent, and it is the one that would never look
#      wrong on screen -- it errs in the flattering direction. The pytest step
#      below fails the whole script if that single test is missing or skipped.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param([switch]$Ship)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$Py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

$results = [ordered]@{}
function Step([string]$name, [scriptblock]$body) {
    Write-Host "`n--- $name" -ForegroundColor Cyan
    try {
        & $body
        if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
        $script:results[$name] = "PASS"
        Write-Host "    PASS" -ForegroundColor Green
    } catch {
        $script:results[$name] = "FAIL"
        Write-Host "    FAIL: $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor Red
    }
}

# --- N.1  the files are present ---------------------------------------------
Step "N.1  every Phase N file is on disk" {
    $need = @(
        "app\services\nav_history.py",
        "tests\test_nav_history.py",
        "alembic\versions\0016_nav_snapshots.py",
        "app\models\tables.py",
        "app\api\routes\intake.py",
        "app\worker\scheduler.py",
        "app\static_app\index.html",
        "app\static_app\sw.js"
    )
    $missing = @($need | Where-Object { -not (Test-Path $_) })
    if ($missing.Count) { throw "missing: $($missing -join ', ')" }
    $global:LASTEXITCODE = 0
}

# --- N.2  it imports --------------------------------------------------------
Step "N.2  nav_history imports without the app running" {
    # The pure arithmetic must be importable with no database and no FastAPI, or
    # the tests below are testing something other than what ships.
    & $Py -c "import app.services.nav_history as n; assert n.SNAPSHOT_VERSION; assert set(n.RANGE_DAYS)=={'1W','1M','1Q','1Y','MAX'}; print('   engine', n.SNAPSHOT_VERSION, '| ranges', ','.join(n.RANGE_DAYS))"
}

Step "N.2  every touched Python file compiles" {
    & $Py -m py_compile app\services\nav_history.py app\models\tables.py app\api\routes\intake.py app\worker\scheduler.py alembic\versions\0016_nav_snapshots.py
}

# --- N.3  THE test ----------------------------------------------------------
Step "N.3  a deposit does not move the return (the load-bearing test)" {
    # Named explicitly, not folded into the file run below. If this test is ever
    # renamed away or deleted, `pytest <file>` would still go green with 14 of 15
    # passing and the one that matters gone. -q plus an exact node id makes its
    # absence an error instead of a silent pass.
    & $Py -m pytest "tests/test_nav_history.py::test_a_deposit_does_not_move_the_return" -q
}

Step "N.3  the whole nav_history suite" {
    & $Py -m pytest tests/test_nav_history.py -q
}

Step "N.3  nothing else broke" {
    & $Py -m pytest tests -q -x --ignore=tests/test_nav_history.py
}

# --- N.4  the migration can actually run ------------------------------------
Step "N.4  0016 is guarded against create_all" {
    $m = Get-Content alembic\versions\0016_nav_snapshots.py -Raw
    if ($m -notmatch 'get_table_names\(\)') {
        throw "0016 does not check get_table_names() - it will explode with 'already exists' on any database 0001_initial built"
    }
    if ($m -notmatch 'down_revision\s*=\s*"0015_backtest_benchmark_ticker"') {
        throw "0016 does not revise 0015 - the chain is broken"
    }
    if ($m -notmatch 'uq_nav_snapshots_subject_day') {
        throw "no unique constraint on (subject, as_of) - a job that runs twice would double-count a period in the chained return"
    }
    $global:LASTEXITCODE = 0
}

Step "N.4  alembic has exactly one head" {
    # Two heads is the failure mode where the deploy runs `upgrade head` and
    # picks the wrong one, so the table quietly never appears in production.
    $heads = @(& $Py -m alembic heads 2>&1 | Where-Object { $_ -match '\(head\)' })
    Write-Host "        $($heads -join ' | ')" -ForegroundColor DarkGray
    if ($heads.Count -ne 1) { throw "$($heads.Count) heads - expected 1" }
    if ("$heads" -notmatch '0016') { throw "head is not 0016: $heads" }
    $global:LASTEXITCODE = 0
}

Step "N.4  the model and the migration agree" {
    & $Py -c "from app.models.tables import NavSnapshot as N; cols=set(N.__table__.columns.keys()); need={'subject','as_of','nav_ils','cash_ils','invested_ils','positions','source','engine_version'}; miss=need-cols; assert not miss, 'model missing '+str(miss); print('   columns', len(cols))"
}

# --- N.5  the endpoints exist and are wired ---------------------------------
Step "N.5  both nav-history routes are registered on the app" {
    # Reads the real FastAPI route table, not the source text. A route defined
    # under a router that was never included greps fine and 404s in production.
    & $Py -c "from app.main import app; p={r.path for r in app.routes}; need=['/api/v1/portfolio/nav-history','/api/v1/portfolio/nav-history/snapshot']; miss=[x for x in need if x not in p]; assert not miss, 'not routed: '+str(miss); print('   routed:', ', '.join(need))"
}

Step "N.5  the read route is read-only, the write route is role-gated" {
    $src = Get-Content app\api\routes\intake.py -Raw
    if ($src -notmatch 'portfolio/nav-history/snapshot"[\s\S]{0,200}?require_role') {
        throw "the snapshot endpoint is not behind require_role - a write endpoint must be"
    }
    $global:LASTEXITCODE = 0
}

# --- N.6  the job is scheduled ----------------------------------------------
Step "N.6  the nightly snapshot job is registered" {
    $s = Get-Content app\worker\scheduler.py -Raw
    if ($s -notmatch 'id="nav_snapshot"') { throw "no nav_snapshot job in the scheduler" }
    if ($s -notmatch 'misfire_grace_time') {
        throw "no misfire_grace_time - a container restart across 22:10 would silently skip a day, and a skipped day is permanent"
    }
    $global:LASTEXITCODE = 0
}

# --- N.7  the frontend write did not truncate -------------------------------
Step "N.7  index.html is whole (line count + tail sentinel)" {
    # @(Get-Content).Count, NOT Measure-Object -Line: Measure-Object counts an
    # empty string as zero lines, so it under-reports by the number of blank
    # lines and fails a healthy file as truncated.
    $lines = @(Get-Content app\static_app\index.html).Count
    Write-Host "        $lines lines" -ForegroundColor DarkGray
    if ($lines -lt 2350) { throw "only $lines lines - a truncated write looks exactly like this" }
    $tail = (Get-Content app\static_app\index.html -Tail 1).Trim()
    if ($tail -ne '</html>') { throw "file does not end in </html>, it ends in '$tail'" }
    $global:LASTEXITCODE = 0
}

Step "N.7  the chart prefers recorded history and says which it drew" {
    $h = Get-Content app\static_app\index.html -Raw
    $need = @('drawTodayReal', 'portfolio/nav-history?range=', 'Your account history')
    $missing = @($need | Where-Object { $h -notmatch [regex]::Escape($_) })
    if ($missing.Count) { throw "index.html is missing: $($missing -join ', ')" }
    # The title swap is the honesty check. If the card can draw a backfill under
    # the heading "Your account history", the whole phase is undone by a label.
    if ($h -notmatch [regex]::Escape("How today's holdings have moved")) {
        throw "the fallback heading is gone - a backfilled curve would be labelled as real history"
    }
    $global:LASTEXITCODE = 0
}

Step "N.7  the inline script parses" {
    # node --check on the extracted <script>. Extracted in PowerShell and written
    # to a temp file - NOT piped into a process's stdin, because PowerShell
    # resolves the first line of piped text as a command name.
    $raw = Get-Content app\static_app\index.html -Raw
    $m = [regex]::Matches($raw, '(?s)<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>')
    if ($m.Count -eq 0) { throw "no inline <script> found - the extraction regex is wrong, not the file" }
    $js = ($m | ForEach-Object { $_.Groups[1].Value }) -join "`n;`n"
    $tmp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "iw-n-check.js")
    [IO.File]::WriteAllText($tmp, $js, (New-Object Text.UTF8Encoding($false)))
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) { Write-Host "        node not found - SKIPPED" -ForegroundColor Yellow; $global:LASTEXITCODE = 0; return }
    & node --check $tmp
    Remove-Item $tmp -ErrorAction SilentlyContinue
}

Step "N.7  the service worker version moved" {
    # If it did not, the browser serves the previous shell from cache and the
    # deploy looks like it did nothing. This has happened in this repo before.
    $sw = Get-Content app\static_app\sw.js -Raw
    if ($sw -notmatch "const VERSION = 'iw-v(\d+)'") { throw "cannot find VERSION in sw.js" }
    $now = [int]$Matches[1]
    $headSw = (git show HEAD:app/static_app/sw.js) -join "`n"
    $was = 0
    if ($headSw -match "const VERSION = 'iw-v(\d+)'") { $was = [int]$Matches[1] }
    Write-Host "        sw.js v$was -> v$now" -ForegroundColor DarkGray
    if ($now -le $was) { throw "sw.js is still v$now - bump it or the shell will be served from cache" }
    $global:LASTEXITCODE = 0
}

# --- summary ----------------------------------------------------------------
Write-Host "`n=============================================================" -ForegroundColor White
$fails = @($results.GetEnumerator() | Where-Object { $_.Value -eq "FAIL" })
foreach ($r in $results.GetEnumerator()) {
    $c = if ($r.Value -eq "PASS") { "Green" } else { "Red" }
    Write-Host ("{0,-6} {1}" -f $r.Value, $r.Key) -ForegroundColor $c
}
Write-Host "=============================================================" -ForegroundColor White
Write-Host "$(($results.Count - $fails.Count)) pass / $($fails.Count) fail" -ForegroundColor $(if ($fails.Count) { "Red" } else { "Green" })

if ($fails.Count) { Write-Host "`nNot shipping." -ForegroundColor Red; exit 1 }
if (-not $Ship) { Write-Host "`nGreen. Re-run with -Ship to commit and push." -ForegroundColor Yellow; exit 0 }

# --- ship -------------------------------------------------------------------
Write-Host "`n--- committing" -ForegroundColor Cyan

git add app/services/nav_history.py tests/test_nav_history.py `
        alembic/versions/0016_nav_snapshots.py app/models/tables.py `
        app/api/routes/intake.py app/worker/scheduler.py `
        app/static_app/index.html app/static_app/sw.js STATUS.md `
        scripts/ship-n.ps1 scripts/smoke/smoke-n.ps1

$staged = @(git diff --cached --name-only)
Write-Host "        staged $($staged.Count) file(s):" -ForegroundColor DarkGray
$staged | ForEach-Object { Write-Host "          $_" -ForegroundColor DarkGray }
if ($staged.Count -eq 0) { Write-Host "Nothing staged - already committed?" -ForegroundColor Yellow; exit 0 }

# Literal here-string plus `git commit -F <file>`. NEVER `git commit -m $msg`
# with an expandable here-string: PowerShell re-parses the embedded quotes and
# shatters the message into pathspecs.
$msg = @'
Phase N: record what the money actually did

Every historical number this app showed was a backfill - today's holdings
priced back through their own past. That answers "what would this book have
done", not "what did my money do". This starts the second record.

  nav_snapshots  one row per user per day: value, cash, contributions-to-date.
                 Unique on (subject, as_of) so a job that runs twice updates
                 instead of double-counting a period.
  22:10 job      records the day. misfire_grace_time is 12h because a skipped
                 day is permanent - past NAV cannot be reconstructed. The
                 transactions table has never been written (Transaction( has
                 zero writers outside the models), so there is nothing to
                 recover from. History can only start.
  time_weighted  deposits are not performance. 20,000 + a 5,000 deposit reads
                 +25% endpoint-to-endpoint and 0% time-weighted. Both figures
                 are returned; the gap between them IS the deposits.
  GET  /portfolio/nav-history?range=1W|1M|1Q|1Y|MAX
  POST /portfolio/nav-history/snapshot   (ANALYST) - starts history today
  Today card     prefers recorded history, falls back to the backfill, and
                 renames itself so the two can never be mistaken.

Without the time-weighting this feature would be worse than what it replaces:
same wrongness, more authority, erring in the flattering direction. That is
what tests/test_nav_history.py::test_a_deposit_does_not_move_the_return exists
to hold, and ship-n.ps1 runs it by name so it cannot be quietly deleted.
'@
$tmp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "iw-n-commit.txt")
[IO.File]::WriteAllText($tmp, $msg, (New-Object Text.UTF8Encoding($false)))
git commit -F $tmp
Remove-Item $tmp -ErrorAction SilentlyContinue

git push
Write-Host "`nPushed. Wait for the deploy, then:" -ForegroundColor Green
Write-Host "  .\scripts\smoke\smoke-n.ps1" -ForegroundColor Gray
