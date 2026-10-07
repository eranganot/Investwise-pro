# SHIP - Phase T4: the card, and the repo hygiene that makes a frontend write verifiable.
#
#   .\scripts\ship-t4.ps1              # verify only
#   .\scripts\ship-t4.ps1 -Ship        # verify, then commit + push if green
#
# T4 writes to a 145KB index.html on a Windows mount. safe-windows-edits Rule 3
# says the way to prove such a write did not truncate is that git diff --stat
# matches the expected magnitude - so that check is encoded here rather than
# left to good intentions. T4.0 (untracking frontend/node_modules) is what makes
# that diff legible in the first place.
#
# Python DOES change here: POST /portfolio/performance gains range/history_days
# so the Today chart's window is measured server-side rather than sliced.

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

# --- T4.0 -------------------------------------------------------------------
Step "T4.0  frontend/node_modules is out of the index" {
    $n = @(git ls-files frontend/node_modules).Count
    if ($n -ne 0) { throw "$n files still tracked - run: git rm -r --cached frontend/node_modules" }
    $global:LASTEXITCODE = 0
}

Step "T4.0  .gitignore keeps it out" {
    if (-not (Select-String -Path .gitignore -Pattern '^node_modules/' -Quiet)) {
        throw "no node_modules rule in .gitignore - it will sit as an untracked ?? forever"
    }
    $global:LASTEXITCODE = 0
}

Step "T4.0  git status returns promptly again" {
    # It used to time out at 40s walking 2,463 extra files. The point of the fix
    # is a legible diff, so the check is that the command is usable.
    $sw = [Diagnostics.Stopwatch]::StartNew()
    git status -sb | Out-Null
    $sw.Stop()
    Write-Host "        git status took $([math]::Round($sw.Elapsed.TotalSeconds,1))s" -ForegroundColor DarkGray
    if ($sw.Elapsed.TotalSeconds -gt 20) { throw "git status took $($sw.Elapsed.TotalSeconds)s - still walking something large" }
    $global:LASTEXITCODE = 0
}

# --- Rule 3: the write did not truncate -------------------------------------
Step "index.html is whole (line count + tail sentinel)" {
    # (Get-Content).Count, NOT `Measure-Object -Line`. Measure-Object counts the
    # lines INSIDE each string it is given, and an empty string counts as zero --
    # so it silently under-reports by the number of blank lines in the file (62
    # here: 2308 real, 2246 reported) and fails a healthy file as truncated.
    $lines = @(Get-Content app\static_app\index.html).Count
    Write-Host "        $lines lines" -ForegroundColor DarkGray
    if ($lines -lt 2280) { throw "only $lines lines - a truncated write looks exactly like this" }
    $tail = (Get-Content app\static_app\index.html -Tail 1).Trim()
    if ($tail -ne '</html>') { throw "file does not end in </html>, it ends in '$tail'" }
    $global:LASTEXITCODE = 0
}

Step "the diff is an addition, not a rewrite" {
    # Compared against HEAD, not against the INDEX. `git diff --numstat -- <f>`
    # is worktree-vs-index, and the index is mutable staging: once anything has
    # been `git add`-ed (this script stages .gitignore, and a previous failed run
    # may have left it staged), an index-relative diff stops answering "how much
    # did this write change the last known-good file". HEAD is the fixed point
    # that Rule 3 actually cares about.
    $stat = git diff --numstat HEAD -- app/static_app/index.html
    if (-not $stat) {
        # Say WHY there is no diff instead of asserting one cause. These need
        # opposite responses: already committed is fine, not-written is not.
        $inHead    = @(git diff --numstat HEAD -- app/static_app/index.html).Count
        $stagedNow = @(git diff --cached --name-only -- app/static_app/index.html).Count
        Write-Host "        vs HEAD rows: $inHead ; staged: $stagedNow" -ForegroundColor DarkGray
        if ($stagedNow -gt 0) { throw "index.html is already STAGED - commit or reset it, then re-run" }
        throw "index.html is identical to HEAD - the edit did not land, or it is already committed"
    }
    $parts = ($stat -split "`t")
    $added = [int]$parts[0]; $removed = [int]$parts[1]
    Write-Host "        +$added / -$removed vs HEAD" -ForegroundColor DarkGray
    # A ~240-line insertion showing -1900 is the truncation signature. Restore
    # immediately with git checkout -- <file> if this fires.
    # Truncation is a REMOVAL signature. The old floor here ("at least 100 lines
    # added") was a proxy for "did the edit land", and it went stale the moment
    # the bulk of T4 was committed: a legitimate follow-up refinement is +18/-13,
    # which the floor failed as if the file had been destroyed. Wholeness is
    # already proven above by the line count, the </html> sentinel and the wiring
    # checks -- so this step should only assert what only it can see.
    if ($removed -gt 40) { throw "$removed lines removed - expected a near-pure addition. Suspect truncation." }
    if ($removed -gt 3 * [math]::Max($added, 1)) {
        throw "$removed removed against $added added - that shape is a rewrite, not an edit"
    }
    $global:LASTEXITCODE = 0
}

Step "the inline script parses" {
    # node --check on the extracted <script>, because a syntax error in a 116KB
    # inline block takes out the whole app and nothing else here would catch it.
    #
    # Extracted in PowerShell, NOT by piping a here-string into python's stdin:
    # `$text | & $Py -` hands the text to PowerShell's command parser, which
    # tries to resolve the first line as a command name. Same family as
    # `git commit -m` shattering a message -- stop passing text through a parser
    # that will interpret it.
    $raw = Get-Content app\static_app\index.html -Raw
    $m = [regex]::Matches($raw, '(?s)<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>')
    if ($m.Count -ne 1) { throw "expected 1 inline script block, found $($m.Count)" }
    $tmp = Join-Path $root "_t4_check.js"
    [IO.File]::WriteAllText($tmp, $m[0].Groups[1].Value, (New-Object Text.UTF8Encoding($false)))
    try {
        if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
            throw "node is not on PATH - cannot syntax-check the inline script"
        }
        node --check $tmp
        if ($LASTEXITCODE -ne 0) { throw "node --check failed on the inline script" }
    } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }
    $global:LASTEXITCODE = 0
}

Step "the Today chart is wired in, and its range is a SERVER parameter" {
    # A client-side slice would re-base the percentage against the wrong day,
    # and slicing a downsampled long series would draw sixteen sessions as a
    # week. The range has to reach the fetch.
    if (-not (Select-String -Path app\static_app\index.html -Pattern 'loadTodayPerf\(\)' -Quiet)) {
        throw "loadTodayPerf() is not called from loadToday()"
    }
    if (-not (Select-String -Path app\static_app\index.html -Pattern 'performance\?range=' -Quiet)) {
        throw "the chart does not pass range= to the server - it is slicing client-side"
    }
    if (-not (Select-String -Path app\api\routes\intake.py -Pattern 'PERF_RANGES' -Quiet)) {
        throw "the route does not accept a range"
    }
    $global:LASTEXITCODE = 0
}

Step "the card is wired into the render, not just defined" {
    if (-not (Select-String -Path app\static_app\index.html -Pattern 'sleevePanel\(\)\+targetCard\(\)' -Quiet)) {
        throw "targetCard() is not in the stratBox render - it would never appear"
    }
    $global:LASTEXITCODE = 0
}

Step "the service worker cache is bumped" {
    $v = (Select-String -Path app\static_app\sw.js -Pattern "const VERSION = 'iw-v(\d+)'").Matches[0].Groups[1].Value
    Write-Host "        iw-v$v" -ForegroundColor DarkGray
    if ([int]$v -lt 21) { throw "sw.js is still iw-v$v - installed apps would keep the old shell" }
    $global:LASTEXITCODE = 0
}

Step "no slider on the target card" {
    # A slider implies every position on it is attainable. This card exists
    # because most of them are not.
    $blk = Get-Content app\static_app\index.html -Raw
    $i = $blk.IndexOf('// ---- T4: the target card')
    $j = $blk.IndexOf('function sleevePanel(){', $i)
    if ($i -lt 0 -or $j -lt 0) { throw "could not locate the T4 block" }
    if ($blk.Substring($i, $j - $i) -match 'type="range"') { throw "the card contains a range input" }
    $global:LASTEXITCODE = 0
}

# --- regression -------------------------------------------------------------
Step "the range parameter is honoured, and an unknown one abstains" {
    & $Py -m pytest tests/test_performance_range.py -q --no-header -p no:randomly
}

Step "full suite" { & $Py -m pytest -q --no-header }
Step "ruff app + tests" { & $Py -m ruff check app tests }

# --- verdict ----------------------------------------------------------------
Write-Host "`n================ PHASE T4 ================" -ForegroundColor White
foreach ($k in $results.Keys) {
    $v = $results[$k]
    $c = if ($v -eq "PASS") { "Green" } elseif ($v -eq "FAIL") { "Red" } else { "Yellow" }
    Write-Host ("  {0,-52} {1}" -f $k, $v) -ForegroundColor $c
}
$failed = ($results.Values | Where-Object { $_ -eq "FAIL" }).Count
Write-Host ("  ---- {0} pass / {1} fail" -f `
    (($results.Values | Where-Object { $_ -eq "PASS" }).Count), $failed)

if ($failed -gt 0) { Write-Host "`nNOT GREEN -- nothing shipped." -ForegroundColor Red; exit 1 }
if (-not $Ship) { Write-Host "`nGREEN. Re-run with -Ship to commit and push." -ForegroundColor Green; exit 0 }

# --- ship -------------------------------------------------------------------
# TWO commits on purpose: 2,463 index deletions and a UI feature have nothing to
# do with each other, and a reviewer scrolling past the deletions would never
# find the card.
Write-Host "`n--- commit 1/2: T4.0 untrack node_modules" -ForegroundColor Cyan
# NOT `git add -u frontend/node_modules`: -u only matches TRACKED files, and the
# whole point is that `git rm -r --cached` already untracked them. The pathspec
# then matches nothing, git errors, and ErrorActionPreference=Stop makes it
# terminal. The deletions are ALREADY staged - they just need committing.
git add .gitignore

$stagedList = @(git diff --cached --name-only)
$dels  = @($stagedList | Where-Object { $_ -like 'frontend/node_modules/*' })
$other = @($stagedList | Where-Object { $_ -notlike 'frontend/node_modules/*' -and $_ -ne '.gitignore' })
Write-Host "        $($stagedList.Count) staged: $($dels.Count) node_modules deletions + .gitignore" -ForegroundColor DarkGray

if ($other.Count -gt 0) {
    # Committing something nobody looked at, under a message about node_modules,
    # is the shape of a commit claiming a change it does not describe.
    Write-Host "Refusing: unexpected paths staged for commit 1:" -ForegroundColor Red
    $other | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    exit 1
}
if ($stagedList.Count -eq 0) {
    Write-Host "        nothing staged - commit 1 already done, skipping" -ForegroundColor Yellow
    $skipCommit1 = $true
} elseif ($dels.Count -eq 0) {
    Write-Host "Refusing: .gitignore is staged but no node_modules deletions are." -ForegroundColor Red
    Write-Host "Run first:  git rm -r --cached frontend/node_modules" -ForegroundColor Red
    exit 1
}

$msg0 = @'
chore: untrack frontend/node_modules -- it was making git status lie

2,463 installed-package files were tracked, with no node_modules rule in
.gitignore. .gitattributes then declared *.ps1 checkout-CRLF, so every
node_modules/.bin/*.ps1 was stored LF and re-flagged on every operation
that consults the filter.

Cost, all observed in one session: git status and git diff timing out at
40s walking the extra tree, and -- the expensive one -- git status --short
returning an EMPTY view while five files sat staged. A clean tree was very
nearly reported on that basis.

.gitattributes already says EOL noise is "part of why git add -A is banned
in this repo". That ban treats the symptom; this is the cause.

git rm -r --cached only: nothing left the working tree. Verified after by
git ls-files frontend/node_modules returning zero and git status -sb
returning in ~6s.

Separate open question, not settled here: frontend/ contains only
node_modules and package-lock.json, no source -- the UI that ships is
app/static_app/index.html. Whether the directory is vestigial needs its own
evidence; untracking is correct either way.
'@
if (-not $skipCommit1) {
    $mf0 = Join-Path $env:TEMP ("iw_t40_msg_{0}.txt" -f (Get-Random))
    [System.IO.File]::WriteAllText($mf0, $msg0, (New-Object System.Text.UTF8Encoding($false)))
    try {
        git commit -F $mf0
        if ($LASTEXITCODE -ne 0) { Write-Host "commit 1 failed" -ForegroundColor Red; exit 1 }
    } finally { Remove-Item $mf0 -ErrorAction SilentlyContinue }
}

Write-Host "`n--- commit 2/2: T4.1 the card" -ForegroundColor Cyan
$files = @("app/static_app/index.html", "app/static_app/sw.js",
           "app/api/routes/intake.py", "tests/test_performance_range.py",
           "scripts/smoke/smoke-t4.ps1",
           "BEAT_MARKET_TARGET_SOLVER_PLAN.md")
git add $files
git diff --cached --name-only

$msg = @'
feat(t4): the card -- ask what it would take, and read an answer that can be no

One card on the Plan screen, under the sleeve panel. It renders the solver
T2 and T3 already return; nothing new is computed here.

TWO INPUTS, AND NEVER A SLIDER. "Beat the benchmark by ___ %/yr" and
"without exceeding ___ % drawdown", side by side, neither optional and
neither hidden behind an advanced toggle. A slider implies every position
on it is attainable, which is exactly the impression this card exists to
remove. The ship script asserts there is no range input in the block.

BOTH DEFAULTS ARE MEASURED, NOT CHOSEN. The target seeds from the book's
own excess over the benchmark and the ceiling from the drawdown it already
carries, read from /portfolio/performance. If that read fails the fields
stay EMPTY rather than filling with a plausible number -- a default nobody
measured is the card guessing on your behalf.

OUTPUT ORDER: verdict, then the size (or the reason there is none), then
the binding constraint, then what was measured, then equal-risk excess,
then what it costs, then provenance. UNREACHABLE and DRAWDOWN_BOUND render
warn, never bad -- they are correct answers and must not look like a
failure. DRAWDOWN_BOUND additionally shows the best size inside the ceiling
and, when the floor note fires, that the book breaches the ceiling holding
no sleeve at all.

The equal-risk line is the one that decides things: when raw excess is
positive and equal-risk excess is negative the card says so in a sentence
-- it beats the market on return and loses to it per unit of risk.

Median beside mean on the projection, with the gap stated as a percentage,
and the block labelled a projection rather than a measurement. Everything
else is labelled measured, with the window, session count, benchmark,
solver and blend engine versions underneath.

Says what it is: this changes nothing, and acting on it means the existing
Resize sleeve and Fund all sleeves controls -- no brokerage order either
way, in the same words the funding cards already use.

ALSO IN T4: a portfolio-over-time chart on Today. X is time, Y is PERCENT
CHANGE -- not value, so the chart reads the same whether the book is worth
8k or 800k, and so the benchmark can share the axis. Ranges 1W / 1M / 1Q /
1Y / Max.

The range is a SERVER parameter (POST /portfolio/performance?range=),
not a client-side slice, and that is the load-bearing decision.
index_series normalises to the first value IN THE SERIES IT FETCHED, so a
re-based percentage only means "change over this window" if the series was
fetched for that window. Slicing a long series client-side would re-base
against the wrong day; slicing a DOWNSAMPLED one would be worse -- at 160
points a ten-year series is one point per ~16 sessions, so "last week"
would be two points sixteen sessions apart, drawn as a week.

Cached per range client-side, because each call is one provider fetch PER
HOLDING and flicking between ranges uncached re-fans-out every time.

A window with too few sessions (1W over a holiday week) says which window
failed rather than blanking the card.

sw.js iw-v20 -> iw-v21.

Verified per safe-windows-edits Rule 3, which is now encoded in
ship-t4.ps1 rather than left to intention: line count, </html> sentinel,
diff magnitude (a near-pure addition; >40 removed lines fails as suspected
truncation), and node --check on the extracted inline script.
'@

$mf = Join-Path $env:TEMP ("iw_t4_msg_{0}.txt" -f (Get-Random))
[System.IO.File]::WriteAllText($mf, $msg, (New-Object System.Text.UTF8Encoding($false)))
try {
    git commit -F $mf
    if ($LASTEXITCODE -ne 0) { Write-Host "commit 2 failed" -ForegroundColor Red; exit 1 }
} finally { Remove-Item $mf -ErrorAction SilentlyContinue }

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
Write-Host "`n--- pushing $branch" -ForegroundColor Cyan
git push origin $branch
if ($LASTEXITCODE -ne 0) { Write-Host "push failed" -ForegroundColor Red; exit 1 }

Write-Host "`nPUSHED. After the deploy:" -ForegroundColor Green
Write-Host "  .\scripts\smoke\smoke-t4.ps1" -ForegroundColor DarkGray
Write-Host "  Then open Plan on the phone. If the card is missing, the installed PWA is" -ForegroundColor DarkGray
Write-Host "  holding the old shell: close and reopen it once (sw is iw-v21)." -ForegroundColor DarkGray
