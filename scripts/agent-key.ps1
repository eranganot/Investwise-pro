# ONE place that answers "what is the agent API key", and PROVES the answer.
#
#   . "$PSScriptRoot\..\agent-key.ps1"      # from scripts/smoke/*
#   $ApiHeaders = @{ 'x-agent-key' = Get-AgentKey -BaseUrl $BaseUrl }
#
# There is no "create a key" endpoint. app/core/config.py reads a single
# setting, agent_api_key, from the AGENT_API_KEY environment variable on
# Railway, and app/core/auth.py:115 grants SUPERADMIN write access to any
# request whose x-agent-key header equals it. One value, server-side.
#
# PR 26 finished the migration this file was written for. The literal that
# used to be baked into 33 committed scripts is gone from all of them, and
# the third resolution candidate -- "read it back out of smoke-p0.ps1" --
# went with it. There is no longer anywhere in the repo to read a key from,
# which is the entire point: rotating is now one Railway variable plus one
# git-ignored line on each machine.
#
# The old value must still be treated as burned. It was committed to a repo
# that was public until 2026-08-30, it is in 160+ commits of history, and it
# was pasted into chat transcripts repeatedly. Making the repo private does
# not un-publish it.
#
# Resolution order, most private first:
#   1. $env:IW_AGENT_KEY          - set per session, never on disk
#   2. scripts\.agent-key         - one line, git-ignored (see .gitignore)
#
# There is no third candidate any more. If neither is set, this returns $null
# and says how to fix it, which is a far better failure than a working script
# authenticating with a key everyone has.
#
# EACH CANDIDATE IS TESTED BEFORE IT IS RETURNED, and a rejected one falls
# through to the next. The first version of this file returned $env:IW_AGENT_KEY
# unconditionally, so an env var holding a WRONG value silently beat a working
# fallback -- and every check downstream then reported "the endpoint did not
# answer" when the server had answered perfectly clearly with a 401. Preferring
# a source is not the same as trusting it.

function Test-IsBurnedKey {
    <#
      .SYNOPSIS
      $true if this is a key that was published and must never be used again.

      Compared by SHA-256, not by value. A fingerprint is not a credential:
      storing the hash lets this file RECOGNISE the retired key without
      carrying it, which is the whole reason the literal was removed. Adding
      the value back "just to compare against it" would undo PR 26.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Key)

    if (-not $Key) { return $false }
    $burned = @(
        # The key committed in 33 scripts from 2026-06 to 2026-08-30.
        'BCB13D47A22BEF955A858CCBC3ABED3D36FF443F29B580EB71145CA400B66B1E'
    )
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Key))
    } finally {
        $sha.Dispose()
    }
    $hex = ($bytes | ForEach-Object { $_.ToString('X2') }) -join ''
    return ($burned -contains $hex)
}

function Test-AgentKey {
    <#
      .SYNOPSIS
      $true if the live API accepts this key, $false if it REJECTS it.

      Only 401/403 is a rejection. A timeout, a 5xx or a DNS failure says
      nothing about the key, so the key is kept -- discarding a good key
      because the network hiccuped would send the caller down the fallback
      chain for the wrong reason.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Key,
        [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
        [int]$TimeoutSec = 25)

    # Splatted, not continued with a backtick. A backtick is an escape character
    # inside a double-quoted PowerShell string and a line written with one has
    # broken a script in this repo before; the house rule is that there are none.
    $probe = @{
        Method     = 'GET'
        Uri        = "$BaseUrl/api/v1/portfolio"
        Headers    = @{ 'x-agent-key' = $Key }
        TimeoutSec = $TimeoutSec
    }
    try {
        Invoke-RestMethod @probe | Out-Null
        return $true
    } catch {
        $code = 0
        if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $_.Exception.Response) {
            try { $code = [int]$_.Exception.Response.StatusCode } catch { $code = 0 }
        }
        if ($code -eq 401 -or $code -eq 403) { return $false }
        # Anything else is not evidence about the key.
        return $true
    }
}

function Get-AgentKey {
    [CmdletBinding()]
    param(
        [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
        [switch]$NoValidate,
        [switch]$Quiet)

    $candidates = @()

    if ($env:IW_AGENT_KEY) {
        $candidates += ,@{ key = $env:IW_AGENT_KEY; from = "IW_AGENT_KEY" }
    }

    # PR 154: the read-only smoke key (D131), set on the cloud environment so a
    # scheduled run needs no one's computer. GET/HEAD only: a smoke's POST is
    # refused with 403, and the smokes report that as SKIP.
    if ($env:IW_SMOKE_KEY) {
        $candidates += ,@{ key = $env:IW_SMOKE_KEY; from = "IW_SMOKE_KEY (read-only)" }
    }

    $keyFile = Join-Path $PSScriptRoot ".agent-key"
    if (Test-Path $keyFile) {
        $fromFile = (Get-Content $keyFile -TotalCount 1).Trim()
        if ($fromFile) {
            $candidates += ,@{ key = $fromFile; from = "scripts\.agent-key (git-ignored)" }
        }
    }

    # There is deliberately no third candidate. PR 26 removed the literal from
    # every tracked script, and a resolver that reads a key back out of the
    # repository would quietly reinstate the thing that was fixed.

    if ($candidates.Count -eq 0) {
        Write-Host ""
        Write-Host "  No agent key found anywhere. Set one for this session:" -ForegroundColor Red
        Write-Host '    $env:IW_AGENT_KEY = "<paste the real key>"' -ForegroundColor White
        Write-Host "  or write it, one line, to scripts\.agent-key (git-ignored)." -ForegroundColor White
        Write-Host "  The live value is whatever AGENT_API_KEY is set to on Railway." -ForegroundColor DarkGray
        Write-Host ""
        return $null
    }

    if ($NoValidate) {
        if (-not $Quiet) { Write-Host "  key: from $($candidates[0].from) (not validated)" -ForegroundColor DarkGray }
        return $candidates[0].key
    }

    $rejected = @()
    foreach ($c in $candidates) {
        if (Test-AgentKey -Key $c.key -BaseUrl $BaseUrl) {
            if (-not $Quiet) {
                if ($rejected.Count -gt 0) {
                    foreach ($r in $rejected) {
                        Write-Host "  key: $r was REJECTED by the server (401) - skipping it" -ForegroundColor Yellow
                    }
                }
                Write-Host "  key: from $($c.from)" -ForegroundColor DarkGray
                if (Test-IsBurnedKey -Key $c.key) {
                    Write-Host ""
                    Write-Host "  WARNING: this is the RETIRED key." -ForegroundColor Red
                    Write-Host "  It was committed to this repo while the repo was public, it is in" -ForegroundColor Yellow
                    Write-Host "  160+ commits of history, and it was pasted into chat. Making the" -ForegroundColor Yellow
                    Write-Host "  repo private did not un-publish it. The server still accepts it," -ForegroundColor Yellow
                    Write-Host "  which means AGENT_API_KEY on Railway has not been rotated yet." -ForegroundColor Yellow
                    Write-Host "  See docs/ROTATION.md." -ForegroundColor Yellow
                    Write-Host ""
                }
            }
            return $c.key
        }
        $rejected += $c.from
    }

    Write-Host ""
    Write-Host "  EVERY candidate key was rejected by $BaseUrl with a 401:" -ForegroundColor Red
    foreach ($r in $rejected) { Write-Host "    - $r" -ForegroundColor Red }
    Write-Host ""
    Write-Host "  That means AGENT_API_KEY on Railway is not any of these - most" -ForegroundColor Yellow
    Write-Host "  likely it was rotated. Read the current value from the Railway" -ForegroundColor Yellow
    Write-Host "  variables for the service, then:" -ForegroundColor Yellow
    Write-Host '    $env:IW_AGENT_KEY = "<the value from Railway>"' -ForegroundColor White
    Write-Host ""
    return $null
}

# ROTATING IT. Step 4 is done as of PR 26, so this is now a two-machine chore
# rather than a 33-file edit. The full runbook, including the SuperAdmin
# password, is in docs/ROTATION.md; the short version:
#   1. Generate:  python -c "import secrets; print('iwk_' + secrets.token_urlsafe(36))"
#   2. Set AGENT_API_KEY to the new value in Railway, and redeploy.
#   3. Put the same value in scripts\.agent-key (git-ignored) on each machine.
# Nothing in the repository needs to change, which is the difference PR 26 made.
