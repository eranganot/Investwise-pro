# SMOKE - is PR 22 ACTUALLY DEPLOYED? Against the LIVE app. READ-ONLY.
#
#   .\scripts\smoke\smoke-pr22-is-actually-live.ps1
#   .\scripts\smoke\smoke-pr22-is-actually-live.ps1 -Sha 1234abc
#
# THIS SCRIPT ANSWERS EXACTLY ONE QUESTION: did PR 22 reach production.
#
# PR 22 was pushed and its build never ran. All three CI jobs and the Railway
# image died at "pip install" -- py-vapid==1.9.4 requires cryptography>=46 and
# requirements.txt pins cryptography==44.0.0. Unsatisfiable. So main HAS the
# PR 22 code and production DOES NOT: the URL below is still serving the
# pre-PR-22 image, and has been since.
#
# A failed deploy is invisible from the outside. The app answers, the pages
# render, nothing looks wrong -- it is simply old. That is why this script
# does not ask "is the app up". It asks whether the SPECIFIC THING PR 22 ADDED
# is present in the payload:
#
#     /api/v1/portfolio/nav-history now returns "unexplained_flows"
#
# That key did not exist before PR 22 (nav_history.py:362). Its presence is
# proof the new code is running; its absence is proof it is not, whatever the
# sha says and whatever the dashboard says.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; the exception TYPE
# printed on first failure; $ApiHeaders never $h.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "")

. "$PSScriptRoot\..\agent-key.ps1"
$AgentKey = Get-AgentKey -BaseUrl $BaseUrl
if (-not $AgentKey) { exit 1 }
$ApiHeaders = @{ 'x-agent-key' = $AgentKey }
$script:Pass = 0; $script:Fail = 0; $script:Skipped = 0

function Sec($m) { Write-Host ""; Write-Host "-- $m" -ForegroundColor Cyan }
function Ok($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Skip($m) { $script:Skipped++; Write-Host "  SKIP  $m" -ForegroundColor Yellow }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }

# A 401 IS AN ANSWER. Reporting it as "did not answer" renders a rejected key
# and an outage identically, and the reader cannot tell them apart.
function Api($method, $path, $timeout = 180) {
    # NOT $args - a PowerShell AUTOMATIC variable, same family as the $h that
    # once silently clobbered the auth headers.
    $reqArgs = @{
        Method     = $method
        Uri        = "$BaseUrl$path"
        Headers    = $ApiHeaders
        TimeoutSec = $timeout
    }
    try {
        return Invoke-RestMethod @reqArgs
    } catch {
        $code = 0
        if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $_.Exception.Response) {
            try { $code = [int]$_.Exception.Response.StatusCode } catch { $code = 0 }
        }
        if ($code -eq 401 -or $code -eq 403) {
            Write-Host "        HTTP $code - the server REJECTED the agent key. Not an outage." -ForegroundColor Yellow
            Write-Host "        Clear a stale one and let agent-key.ps1 resolve it:" -ForegroundColor DarkGray
            Write-Host "          Remove-Item Env:\IW_AGENT_KEY" -ForegroundColor DarkGray
        } elseif ($code -gt 0) {
            Write-Host "        HTTP $code from $path - $($_.Exception.GetType().Name)" -ForegroundColor DarkGray
        } else {
            Write-Host "        $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor DarkGray
        }
        return $null
    }
}

Write-Host ""
Write-Host "InvestWise Pro - did PR 22 actually reach production?" -ForegroundColor White
Write-Host "$BaseUrl" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
Sec "0  which build is serving"
$health = Api GET '/health' 60
if ($null -eq $health) {
    Bad "/health unreachable - nothing below can be trusted"
} else {
    Note "serving commit $($health.commit)"
    if ($Sha) {
        if ("$($health.commit)" -eq $Sha) { Ok "serving $Sha" }
        else { Bad "serving '$($health.commit)', expected '$Sha' - this is NOT the build you just pushed" }
    } else {
        Note "pass -Sha <sha> to gate on the exact commit"
        Skip "no -Sha given, so the sha was not verified"
    }
}

# ---------------------------------------------------------------------------
Sec "1  THE DECIDING CHECK - is PR 22's code actually running?"
# nav_history.py:362 added "unexplained_flows" to this payload. The key did not
# exist before PR 22. This does not depend on the sha, the dashboard, or the
# deploy log: either the running process has the new code or it does not.
$hist = Api GET '/api/v1/portfolio/nav-history?range=1M' 120
if ($null -eq $hist) {
    Bad "/portfolio/nav-history returned nothing - see the line above for why"
} else {
    $keys = @($hist.PSObject.Properties.Name)
    Note "payload keys: $($keys -join ', ')"
    if ($keys -contains 'unexplained_flows') {
        Ok "'unexplained_flows' is present - PR 22 IS LIVE"
    } else {
        Bad "'unexplained_flows' is MISSING - the running build predates PR 22"
        Note "This is the whole point of the run. Whatever the deploy dashboard"
        Note "says, the process answering this URL does not have PR 22's code."
    }

    if ("$($hist.kind)" -eq 'nav_snapshots') {
        Note "kind=nav_snapshots, range=$($hist.range), sessions=$($hist.window.sessions)"
        Note "total_pct=$($hist.total_pct)  simple_change_pct=$($hist.simple_change_pct)  flow_total_ils=$($hist.flow_total_ils)"
    } else {
        Note "kind=$($hist.kind) - not enough snapshots yet to chart a window"
        Note "$($hist.detail)"
    }
}

# ---------------------------------------------------------------------------
Sec "2  PR 22's actual fix, on your real book"
# The incident: a holding entered through the Holdings screen writes no ledger
# row, so the whole jump in NAV compounded as return and the chart read +35%.
# PR 22 reads NavSnapshot.positions and names the step instead.
#
# THIS SECTION CANNOT MANUFACTURE THE CONDITION. It reports what your book
# shows. An empty unexplained_flows here is not a failure - it means no
# unexplained step happened in this window, which is the normal case.
if ($null -ne $hist -and @($hist.PSObject.Properties.Name) -contains 'unexplained_flows') {
    $flags = @($hist.unexplained_flows)
    if ($flags.Count -eq 0) {
        Ok "no unexplained steps in this window (the normal case)"
        Note "Nothing to attribute. If you HAVE added a holding through the"
        Note "Holdings screen recently, widen the range (-> ?range=3M or 1Y)"
        Note "and re-run; the step should be named rather than compounded."
    } else {
        Ok "$($flags.Count) step(s) NAMED rather than counted as performance"
        foreach ($f in $flags) {
            Note "  $($f.as_of): $([math]::Round([double]$f.amount_ils,2)) ILS, positions $($f.positions_before) -> $($f.positions_after)"
            Note "    $($f.reason)"
        }
        Note "Before PR 22 each of these was silently compounded INTO total_pct"
        Note "under a caption reading 'no deposits or withdrawals in this window'."
    }
} else {
    Skip "PR 22 is not live, so its fix could not be observed"
}

# ---------------------------------------------------------------------------
Sec "3  web push survived the dependency change"
# The hotfix swaps py-vapid 1.9.4 -> 1.9.2, the library that generates and
# signs VAPID keys. If that broke anything at runtime it breaks here: the
# public key must be raw, unpadded base64url - 65 bytes starting 0x04.
$pk = Api GET '/api/v1/push/public-key' 60
if ($null -eq $pk) {
    Bad "/push/public-key returned nothing - see the line above for why"
} else {
    $key = "$($pk.public_key)"
    if (-not $key) {
        Bad "the endpoint answered but carried no public_key"
    } else {
        if ($key.Contains('=')) { Bad "the key is padded - that breaks the VAPID header" }
        else { Ok "unpadded base64url" }
        $b64 = $key.Replace('-', '+').Replace('_', '/')
        switch ($key.Length % 4) { 2 { $b64 += '==' } 3 { $b64 += '=' } }
        try {
            $bytes = [Convert]::FromBase64String($b64)
            if ($bytes.Length -eq 65 -and $bytes[0] -eq 4) {
                Ok "a real uncompressed P-256 point (65 bytes, leading 0x04)"
            } else {
                Bad "decoded to $($bytes.Length) bytes leading 0x$('{0:x2}' -f $bytes[0]) - expected 65 and 0x04"
            }
        } catch {
            Bad "the key is not decodable base64url: $($_.Exception.GetType().Name)"
        }
    }
}

# ---------------------------------------------------------------------------
Sec "4  nothing else moved"
# A dependency hotfix should change no portfolio number at all. Reading NAV
# here so that "the pin change was inert" is an observation rather than a hope.
$portfolio = Api GET '/api/v1/portfolio' 120
if ($null -eq $portfolio) {
    Bad "/portfolio returned nothing - see the line above for why"
} else {
    $nav = [math]::Round([double]$portfolio.nav_ils, 2)
    Note "NAV $nav ILS across $(@($portfolio.positions).Count) positions"
    Ok "portfolio reads cleanly after the dependency change"
    Note "COMPARE THIS NAV TO WHAT YOU SAW BEFORE THE DEPLOY. A pin change"
    Note "must not move it by a single agora. If it did, stop and tell me."
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "-- result" -ForegroundColor Cyan
Write-Host "  PASS $($script:Pass)   FAIL $($script:Fail)   SKIP $($script:Skipped)" -ForegroundColor White
if ($script:Skipped -gt 0) {
    Write-Host "  A SKIP IS NEVER A PASS. $($script:Skipped) check(s) did not run." -ForegroundColor Yellow
}
Write-Host ""
if ($script:Fail -gt 0) {
    Write-Host "  RED. PR 22 is not confirmed live." -ForegroundColor Red
    exit 1
}
Write-Host "  Green. PR 22 is deployed and running." -ForegroundColor Green
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS leak is UNEXPLAINED and CI can still go red on" -ForegroundColor DarkYellow
Write-Host "    it. When it fires it now prints the API NAV, the in-session" -ForegroundColor DarkYellow
Write-Host "    drift, the apply spec, every position that moved and the cards" -ForegroundColor DarkYellow
Write-Host "    emitted. SEND ME THAT BLOCK." -ForegroundColor DarkYellow
Write-Host "  * fx_rate REPORTS an unresolvable currency; it does not ABSTAIN." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches to 97%, not the 75% you" -ForegroundColor DarkYellow
Write-Host "    accepted." -ForegroundColor DarkYellow
Write-Host "  * There is still not one Tel Aviv LISTING in the candidate" -ForegroundColor DarkYellow
Write-Host "    universe, so no card can name one." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is still a literal in 34 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
exit 0
