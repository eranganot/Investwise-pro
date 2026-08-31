# SMOKE - PR 26, against the LIVE deploy. READ-ONLY throughout.
#
#   .\scripts\smoke\smoke-the-published-credentials-are-dead.ps1
#   .\scripts\smoke\smoke-the-published-credentials-are-dead.ps1 -Sha 1234abc
#
# ONE QUESTION: can a stranger still get in?
#
# On 2026-08-30, against production, they could - two different ways:
#
#   GET  /api/v1/portfolio   with the key committed in 33 scripts  -> 200
#   POST /api/v1/auth/token  with the default from config.py       -> 200
#                                                          SUPERADMIN token
#
# This checks that both are now refused, that the replacement works, and that
# nothing else moved.
#
# SECTIONS 1 AND 2 ARE THE POINT. Both must be green. If section 1 passes and
# section 2 does not, the code deployed but Railway was never updated - the
# rotation is half done and the old key is still live.
#
# House rules (smoke-p0.ps1): a SKIP is never a PASS; the exception TYPE is
# printed on failure; $ApiHeaders never $h; $reqArgs never $args.

param(
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
    [string]$Sha = "")

. "$PSScriptRoot\..\agent-key.ps1"

$script:Pass = 0; $script:Fail = 0; $script:Skipped = 0

function Sec($m)  { Write-Host ""; Write-Host "-- $m" -ForegroundColor Cyan }
function Ok($m)   { $script:Pass++;    Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m)  { $script:Fail++;    Write-Host "  FAIL  $m" -ForegroundColor Red }
function Skip($m) { $script:Skipped++; Write-Host "  SKIP  $m" -ForegroundColor Yellow }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }

# Returns the STATUS CODE, not the body. This whole script is about which code
# comes back, and a 401 is an answer -- reporting it as "no response" would
# render a locked door and an outage identically.
function Get-Status($method, $path, $headers, $body, $timeout = 60) {
    $reqArgs = @{
        Method     = $method
        Uri        = "$BaseUrl$path"
        TimeoutSec = $timeout
    }
    if ($headers) { $reqArgs.Headers = $headers }
    if ($body) {
        $reqArgs.Body = $body
        $reqArgs.ContentType = 'application/json'
    }
    try {
        Invoke-RestMethod @reqArgs | Out-Null
        return 200
    } catch {
        if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $_.Exception.Response) {
            try { return [int]$_.Exception.Response.StatusCode } catch { }
        }
        Note "$($_.Exception.GetType().Name): $($_.Exception.Message)"
        return 0
    }
}

Write-Host ""
Write-Host "InvestWise Pro - are the published credentials dead?" -ForegroundColor White
Write-Host "$BaseUrl" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
Sec "0  which build is serving"
try {
    $health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 60
    Note "serving commit $($health.commit), version $($health.version)"
    if ($Sha) {
        if ("$($health.commit)" -eq $Sha) { Ok "serving $Sha" }
        else { Bad "serving '$($health.commit)', expected '$Sha' - the rest describes the OLD build" }
    } else {
        Skip "no -Sha given, so the build was not verified"
    }
} catch {
    Bad "/health unreachable - nothing below can be trusted"
}

# ---------------------------------------------------------------------------
Sec "1  THE ONE THAT MATTERS - the published SuperAdmin password"
# It was the DEFAULT VALUE of a field in config.py, in a public repo, and it
# worked. Setting AUTH_PASSWORD could not retire it: the credential row was
# INSERTed once and the variable was never read again.
$loginBody = '{"email":"eran.ganot@gmail.com","password":"changeme-dev"}'
$code = Get-Status POST '/api/v1/auth/token' $null $loginBody
if ($code -eq 200) {
    Bad "the published password STILL WORKS - a SUPERADMIN token was issued"
    Note "The code did not deploy, or this build predates PR 26. Check section 0."
} elseif ($code -eq 401) {
    Ok "the published password is refused (401)"
} elseif ($code -eq 0) {
    Bad "no answer at all - see the line above. NOT the same as 'refused'."
} else {
    Ok "the published password is refused (HTTP $code)"
}

# ---------------------------------------------------------------------------
Sec "2  the retired agent key"
# Not stored anywhere any more, so it is reconstructed from nothing: this
# section asks the resolver whether the key it has is the burned one, and asks
# the SERVER whether whatever key it has is still accepted.
$AgentKey = Get-AgentKey -BaseUrl $BaseUrl -Quiet
if (-not $AgentKey) {
    Bad "no agent key resolved, so nothing below could be checked"
    Note 'Set it: $env:IW_AGENT_KEY = "<the new key>"'
    Note "or one line in scripts\.agent-key (git-ignored). See ROTATION.md."
} else {
    if (Test-IsBurnedKey -Key $AgentKey) {
        Bad "the key on THIS MACHINE is the retired one"
        Note "AGENT_API_KEY on Railway has not been rotated, or this machine"
        Note "still holds the old value in scripts\.agent-key or IW_AGENT_KEY."
    } else {
        Ok "the key on this machine is not the retired one"
    }

    $code = Get-Status GET '/api/v1/portfolio' @{ 'x-agent-key' = $AgentKey } $null 120
    if ($code -eq 200) { Ok "the current key is accepted by the server" }
    elseif ($code -eq 401 -or $code -eq 403) {
        Bad "the current key is REJECTED (HTTP $code)"
        Note "Railway and this machine disagree. Copy AGENT_API_KEY from"
        Note "Railway into scripts\.agent-key."
    } else { Bad "unexpected HTTP $code" }
}

# ---------------------------------------------------------------------------
Sec "3  a wrong key is still refused"
# The control. If this passes with a 200, auth is off entirely and every other
# result in this file is meaningless.
$code = Get-Status GET '/api/v1/portfolio' @{ 'x-agent-key' = 'iwk_not_a_real_key_at_all_0000' } $null
if ($code -eq 401 -or $code -eq 403) { Ok "a made-up key gets HTTP $code" }
elseif ($code -eq 200) { Bad "A MADE-UP KEY WAS ACCEPTED. Auth is not enforced." }
else { Bad "unexpected HTTP $code" }

Sec "4  no key at all is still refused"
$code = Get-Status GET '/api/v1/portfolio' $null $null
if ($code -eq 401 -or $code -eq 403) { Ok "no key gets HTTP $code" }
elseif ($code -eq 200) { Bad "AN UNAUTHENTICATED REQUEST WAS ACCEPTED." }
else { Bad "unexpected HTTP $code" }

# ---------------------------------------------------------------------------
Sec "5  nothing else moved"
# This PR touches authentication and shell scripts. It must not move a shekel.
if ($AgentKey) {
    # Splatted, not continued with a backtick. There are no backticks in this
    # repo's scripts: one has broken a paste three separate times.
    $navArgs = @{
        Method     = 'GET'
        Uri        = "$BaseUrl/api/v1/portfolio"
        Headers    = @{ 'x-agent-key' = $AgentKey }
        TimeoutSec = 120
    }
    try {
        $portfolio = Invoke-RestMethod @navArgs
        $nav = [math]::Round([double]$portfolio.nav_ils, 2)
        Note "NAV $nav ILS across $(@($portfolio.positions).Count) positions"
        Ok "the portfolio still loads"
        Note "COMPARE THIS NAV TO BEFORE THE DEPLOY. It must not have moved."
    } catch {
        Bad "/portfolio failed: $($_.Exception.GetType().Name)"
    }
} else {
    Skip "no key, so the portfolio could not be read"
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
    Write-Host "  RED. See ROTATION.md." -ForegroundColor Red
    exit 1
}
Write-Host "  Green. Both published credentials are refused." -ForegroundColor Green
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The old values remain in git history forever. Rotation makes" -ForegroundColor DarkYellow
Write-Host "    them useless; it does not remove them." -ForegroundColor DarkYellow
Write-Host "  * The agent key still grants SUPERADMIN to anyone holding it" -ForegroundColor DarkYellow
Write-Host "    (app/core/auth.py:115). By design - and the new one deserves" -ForegroundColor DarkYellow
Write-Host "    the handling the old one never got." -ForegroundColor DarkYellow
Write-Host "  * Not one Tel Aviv LISTING is in the candidate universe, and P/B" -ForegroundColor DarkYellow
Write-Host "    for a .TA listing is off by FX x 100. Item 3's other half." -ForegroundColor DarkYellow
Write-Host "  * The coverage pin rides on equity ideas, so it vanishes when" -ForegroundColor DarkYellow
Write-Host "    there are none. My defect, next PR." -ForegroundColor DarkYellow
Write-Host "  * The performance chart still has no date axis." -ForegroundColor DarkYellow
Write-Host "  * Item 2 has no factor tilt: nothing derives one." -ForegroundColor DarkYellow
Write-Host "  * 'What is moving' has no live provider - four fixture rows." -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS leak is UNEXPLAINED." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
exit 0
