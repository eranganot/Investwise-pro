# SHIP - T5: the card ends in an instruction, and the instruction is measured.
#
#   .\scripts\ship-t5.ps1              # verify only
#   .\scripts\ship-t5.ps1 -Ship        # verify, then commit + push if green
#
# T5 fixes three things that together made the card read as a dead end:
#
#   1. The ceiling was seeded from a 252-day window and judged against a
#      ~1729-session one. That mismatch alone guarantees DRAWDOWN_BOUND on first
#      open. It was reporting a window difference as a fact about the book.
#   2. The target was seeded from the excess you already earn, so the first
#      solve asked "what would it take to stand still" -- answer, nothing.
#   3. There was no instruction. Five outcomes is the right model of the ANSWER;
#      it is not an answer to "so what do I do".
#
# The dangerous new surface is (3): an instruction carries more authority than a
# diagnosis. Two failures matter more than the rest, and both are asserted below
# by name rather than left to the suite:
#
#   - Telling the user to resize sleeves when the CORE is what breaches the
#     ceiling. That points at a control which provably cannot move the outcome.
#   - Implying that raising a risk ceiling produces return. It does not. It
#     removes a refusal, and the copy has to say so in those words.
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

# --- T5.1  the two named failures -------------------------------------------
Step "T5.1  it does not send you to the slider when the core is the problem" {
    & $Py -m pytest "tests/test_target_recommend.py::test_it_does_not_send_him_back_to_the_sleeve_slider" -q
}

Step "T5.1  a raised ceiling is described as a permission, not a return" {
    & $Py -m pytest "tests/test_target_recommend.py::test_a_raised_ceiling_is_described_as_a_permission_not_a_return" -q
}

Step "T5.1  the equal-risk finding is always said when it is negative" {
    & $Py -m pytest "tests/test_target_recommend.py::test_the_equal_risk_finding_outranks_the_solve_and_is_always_said" -q
}

Step "T5.1  no outcome renders a blank instruction" {
    & $Py -m pytest "tests/test_target_recommend.py::test_every_outcome_produces_a_headline_and_a_reason" -q
}

Step "T5.1  the recommendation invents no figure the solve did not measure" {
    # The card's founding rule: a card may not claim a figure it did not compute.
    # An instruction is a claim, so it is held to the same rule.
    & $Py -m pytest "tests/test_target_recommend.py::test_the_recommendation_invents_no_figure_the_solve_did_not_measure" -q
}

Step "T5.1  the whole recommendation suite" {
    & $Py -m pytest tests/test_target_recommend.py -q
}

# --- T5.2  it is pure, and it is wired --------------------------------------
Step "T5.2  recommend() is a pure function over the verdict" {
    # No session, no user, no price fetch. If this ever needs I/O it stops being
    # testable without a database, and the sentence the user acts on becomes the
    # least verifiable thing on the card.
    # Read from the FILE, not via inspect.getsource. getsource needs the module
    # to have been imported from disk with its source still resolvable; reading
    # the text is what actually answers "what does the shipped function say".
    & $Py -c "import inspect,re; from app.services.target_solver import recommend as r; assert list(inspect.signature(r).parameters)==['v','benchmark'], list(inspect.signature(r).parameters); s=open('app/services/target_solver.py',encoding='utf-8').read(); b=s[s.index('def recommend(v: dict'):]; b=b[:b.index('\ndef ') if '\ndef ' in b[1:] else len(b)]; assert 'await' not in b, 'recommend() awaits something'; assert 'session' not in b, 'recommend() touches a session'; print('   pure over the verdict; no await, no session')"
}

Step "T5.2  every solve attaches a recommendation" {
    # Select-String, NOT `python -c` with an embedded double-quoted string.
    # `\"` is not PowerShell escaping -- PowerShell uses backtick or doubled
    # quotes -- so `assert 'out[\"recommendation\"] = ...'` reached python as
    # `assert 'out[" recommendation\]` and died on an unterminated literal.
    # Same family as `git commit -m` shattering a message: stop passing text
    # through a parser that will interpret it. -SimpleMatch means the brackets
    # and quotes below are literal, not regex.
    $needle = 'out["recommendation"] = recommend('
    if (-not (Select-String -Path app\services\target_solver.py -Pattern $needle -SimpleMatch -Quiet)) {
        throw "solve_for does not attach a recommendation"
    }
    Write-Host "        attached in solve_for" -ForegroundColor DarkGray
    $global:LASTEXITCODE = 0
}

Step "T5.2  all five outcomes are covered by the branch table" {
    & $Py -c "from app.services import target_solver as t; outs=[t.REACHED,t.REACHED_ABOVE_CAP,t.DRAWDOWN_BOUND,t.UNREACHABLE,t.NOT_MEASURABLE]; [t.recommend({'outcome':o,'target':{},'measured':{}}) for o in outs]; print('   5/5 outcomes return without raising')"
}

Step "T5.2  nothing else broke" {
    & $Py -m pytest tests -q -x
}

# --- T5.3  the seed windows now agree ---------------------------------------
Step "T5.3  the seed asks for the solver's window, not 252 days" {
    $h = Get-Content app\static_app\index.html -Raw
    if ($h -notmatch [regex]::Escape('portfolio/performance?range=MAX')) {
        throw "the seed still calls performance with no range - it defaults to 252 days, and the solver measures ~1729 sessions"
    }
    # Scoped to _seedTarget's own body. The first version of this check forbade
    # an un-ranged performance call ANYWHERE in the file and failed on
    # index.html:2253 -- a different card entirely, for which 252 days may well
    # be the right window. A guard that polices code it was never reasoning
    # about produces a red line with no defect behind it.
    if ($h -notmatch '(?s)async function _seedTarget\(\)\{(.*?)\n\}') {
        throw "cannot find _seedTarget - the seed check has nothing to check"
    }
    $seed = $Matches[1]
    if ($seed -match 'portfolio/performance"') {
        throw "_seedTarget still calls performance with no range - it defaults to 252 days while the solver measures ~1729 sessions"
    }
    if ($seed -notmatch [regex]::Escape('range=MAX')) {
        throw "_seedTarget does not ask for range=MAX"
    }
    $global:LASTEXITCODE = 0
}

Step "T5.3  the target no longer seeds to a tautology" {
    $h = Get-Content app\static_app\index.html -Raw
    if ($h -notmatch [regex]::Escape('Math.max(0,Math.ceil(cur))')) {
        throw "the target is still seeded from the current excess - the first solve asks what it would take to stand still"
    }
    $global:LASTEXITCODE = 0
}

Step "T5.3  the card renders the instruction and the one-tap actions" {
    $h = Get-Content app\static_app\index.html -Raw
    foreach ($n in @('_tgtRecommendation', 'tgtApply', 'WHAT TO DO', 'equal_risk_warning')) {
        if ($h -notmatch [regex]::Escape($n)) { throw "index.html is missing $n" }
    }
    $global:LASTEXITCODE = 0
}

Step "T5.3  the one-tap stays read-only" {
    # Phase T is read-only WITHOUT EXCEPTION. tgtApply may only touch this card's
    # own two inputs. set_sleeves is the Phase A handoff and must remain inert:
    # a button that looks like it resizes a sleeve while doing nothing is worse
    # than no button at all.
    $h = Get-Content app\static_app\index.html -Raw
    if ($h -notmatch '(?s)function tgtApply\(kind,value\)\{(.*?)\n\}') { throw "cannot find tgtApply" }
    $body = $Matches[1]
    foreach ($verb in @('method:"POST"', 'method:"PUT"', 'method:"DELETE"', 'method:"PATCH"')) {
        if ($body -match [regex]::Escape($verb)) { throw "tgtApply issues a $verb - Phase T does not write" }
    }
    if ($body -match 'set_sleeves') { throw "tgtApply handles set_sleeves - that is Phase A, and it must not appear to act" }
    $global:LASTEXITCODE = 0
}

# --- T5.4  the write did not truncate ---------------------------------------
Step "T5.4  index.html is whole" {
    $lines = @(Get-Content app\static_app\index.html).Count
    Write-Host "        $lines lines" -ForegroundColor DarkGray
    if ($lines -lt 2410) { throw "only $lines lines - a truncated write looks exactly like this" }
    $tail = (Get-Content app\static_app\index.html -Tail 1).Trim()
    if ($tail -ne '</html>') { throw "file does not end in </html>, it ends in '$tail'" }
    $global:LASTEXITCODE = 0
}

Step "T5.4  the diff is an addition, not a rewrite" {
    $stat = git diff --numstat HEAD -- app/static_app/index.html
    if (-not $stat) {
        $stagedNow = @(git diff --cached --name-only -- app/static_app/index.html).Count
        if ($stagedNow -gt 0) { throw "index.html is already STAGED - commit or reset it, then re-run" }
        # "identical to HEAD" has TWO causes needing opposite responses, and the
        # first version of this step collapsed them into one red line. Ask the
        # file what it contains: if HEAD already carries the T5 markers, the
        # frontend simply shipped in an earlier commit (it did -- 74d2bc8 swept
        # index.html in under the Phase N message) and there is nothing to
        # verify here. If the markers are ABSENT, the edit genuinely never
        # landed, which is the failure this step exists for.
        $inHead = git show HEAD:app/static_app/index.html
        $missing = @('_tgtRecommendation', 'tgtApply', 'range=MAX') |
                   Where-Object { -not ($inHead -match [regex]::Escape($_)) }
        if ($missing.Count) {
            throw "index.html is identical to HEAD and HEAD is missing $($missing -join ', ') - the edit did not land"
        }
        Write-Host "        already committed, and HEAD carries the T5 markers - nothing pending" -ForegroundColor DarkGray
        $global:LASTEXITCODE = 0
        return
    }
    $parts = ($stat -split "`t")
    $added = [int]$parts[0]; $removed = [int]$parts[1]
    Write-Host "        +$added / -$removed vs HEAD" -ForegroundColor DarkGray
    if ($removed -gt 40) { throw "$removed lines removed - expected a near-pure addition. Suspect truncation." }
    if ($removed -gt 3 * [math]::Max($added, 1)) { throw "$removed removed against $added added - that is a rewrite" }
    $global:LASTEXITCODE = 0
}

Step "T5.4  the inline script parses" {
    $raw = Get-Content app\static_app\index.html -Raw
    $m = [regex]::Matches($raw, '(?s)<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>')
    if ($m.Count -eq 0) { throw "no inline <script> found - the extraction regex is wrong, not the file" }
    $js = ($m | ForEach-Object { $_.Groups[1].Value }) -join "`n;`n"
    $tmp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "iw-t5-check.js")
    [IO.File]::WriteAllText($tmp, $js, (New-Object Text.UTF8Encoding($false)))
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) { Write-Host "        node not found - SKIPPED" -ForegroundColor Yellow; $global:LASTEXITCODE = 0; return }
    & node --check $tmp
    Remove-Item $tmp -ErrorAction SilentlyContinue
}

Step "T5.4  the service worker version moved" {
    $sw = Get-Content app\static_app\sw.js -Raw
    if ($sw -notmatch "const VERSION = 'iw-v(\d+)'") { throw "cannot find VERSION in sw.js" }
    $now = [int]$Matches[1]
    $headSw = (git show HEAD:app/static_app/sw.js) -join "`n"
    $was = 0
    if ($headSw -match "const VERSION = 'iw-v(\d+)'") { $was = [int]$Matches[1] }
    Write-Host "        sw.js v$was -> v$now" -ForegroundColor DarkGray
    # The bump exists to invalidate a CACHED SHELL. If the shell is not changing
    # in this commit there is nothing to invalidate, and demanding a bump anyway
    # is a check enforcing its own ritual rather than the thing it protects --
    # which is how a green suite gets trained to accept a pointless edit.
    $shellChanged = [bool](git diff --numstat HEAD -- app/static_app/index.html)
    if (-not $shellChanged) {
        Write-Host "        index.html is unchanged vs HEAD - no cached shell to invalidate" -ForegroundColor DarkGray
        $global:LASTEXITCODE = 0
        return
    }
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

git add app/services/target_solver.py tests/test_target_recommend.py `
        app/static_app/index.html app/static_app/sw.js STATUS.md `
        scripts/ship-t5.ps1 scripts/smoke/smoke-t5.ps1

$staged = @(git diff --cached --name-only)
Write-Host "        staged $($staged.Count) file(s):" -ForegroundColor DarkGray
$staged | ForEach-Object { Write-Host "          $_" -ForegroundColor DarkGray }
if ($staged.Count -eq 0) { Write-Host "Nothing staged - already committed?" -ForegroundColor Yellow; exit 0 }

# Literal here-string plus `git commit -F <file>`. NEVER `git commit -m $msg`
# with an expandable here-string: PowerShell re-parses the embedded quotes.
$msg = @'
T5: the card ends in an instruction

Five outcomes was the right model of the ANSWER. It was not an answer to
"so what do I do", and the card stopped at the diagnosis.

Three fixes, in order of how much they were costing:

1. The ceiling was seeded from a 252-day window and then judged against a
   ~1729-session measurement. A ceiling seeded from a calm twelve months
   cannot survive a window containing 2020 and 2022, so the card opened on
   DRAWDOWN_BOUND every time -- reporting a window mismatch as though it
   were a fact about the book. The seed now asks for range=MAX, the same
   window the solver measures, and the note states the session count so any
   residual difference is visible rather than inferred.

2. The target was seeded from the excess you already earn, which makes the
   first solve ask "what would it take to stand still". The answer is always
   "nothing", and 0% sleeves is not advice. It now floors at 0 -- match the
   index -- with your actual figure still shown in the note.

3. target_solver.recommend() -- a pure function over the verdict, so the
   sentence the user acts on is testable without a database, a price feed or
   a browser. It invents no figure: every number traces to something the
   solve measured, and a test asserts exactly that.

Two failure modes are held by name because an instruction carries more
authority than a diagnosis:

  - When the CORE breaches the ceiling with no sleeve at all, the
    recommendation must NOT mention sleeve sizing. That would point at a
    control which provably cannot move the outcome.
  - Raising a risk ceiling must be described as a permission, not a return.
    It does not add a single percent; it stops the solver refusing to show
    you the options above your ceiling.

Excess at equal risk, when negative, is rendered ABOVE the instruction. A
book that is behind per unit of risk has a problem no sleeve size solves,
and answering the smaller question first would bury it.

Still read-only WITHOUT EXCEPTION. The one-tap actions refill this card's own
two inputs and re-solve; set_sleeves is left inert as the Phase A handoff.
'@
$tmp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "iw-t5-commit.txt")
[IO.File]::WriteAllText($tmp, $msg, (New-Object Text.UTF8Encoding($false)))
git commit -F $tmp
Remove-Item $tmp -ErrorAction SilentlyContinue

git push
Write-Host "`nPushed. Wait for the deploy, then:" -ForegroundColor Green
Write-Host "  .\scripts\smoke\smoke-t5.ps1" -ForegroundColor Gray
