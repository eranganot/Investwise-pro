# ONE place that answers "what is the agent API key", and PROVES the answer.
#
#   . "$PSScriptRoot\..\agent-key.ps1"      # from scripts/smoke/*
#   $ApiHeaders = @{ 'x-agent-key' = Get-AgentKey -BaseUrl $BaseUrl }
#
# There is no "create a key" endpoint. app/core/config.py:140 reads a single
# setting, agent_api_key, from the AGENT_API_KEY environment variable on
# Railway, and app/core/auth.py:115 grants SUPERADMIN write access to any
# request whose x-agent-key header equals it. One value, server-side. So a NEW
# key means changing that Railway variable - which instantly breaks every script
# that has the old one baked in, and 34 of them do.
#
# That is the open backlog item: the key is a literal in 34 committed scripts,
# 160+ commits of history, and pasted into chat transcripts repeatedly. It has
# been deferred four times and has not become less true.
#
# This file is the step that makes rotating it a one-line change instead of a
# 34-file change. New scripts call Get-AgentKey; old ones can be migrated to it
# whenever anyone is in them anyway.
#
# Resolution order, most private first:
#   1. $env:IW_AGENT_KEY          - set per session, never on disk
#   2. scripts\.agent-key         - one line, git-ignored (see .gitignore)
#   3. the literal already committed in scripts\smoke\smoke-p0.ps1
#      Using it adds NO new exposure - it is already in the repo and in the
#      history - but it says so out loud, because a key read out of a tracked
#      file is not a secret and should not feel like one.
#
# EACH CANDIDATE IS TESTED BEFORE IT IS RETURNED, and a rejected one falls
# through to the next. The first version of this file returned $env:IW_AGENT_KEY
# unconditionally, so an env var holding a WRONG value silently beat a working
# fallback -- and every check downstream then reported "the endpoint did not
# answer" when the server had answered perfectly clearly with a 401. Preferring
# a source is not the same as trusting it.

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

    $keyFile = Join-Path $PSScriptRoot ".agent-key"
    if (Test-Path $keyFile) {
        $fromFile = (Get-Content $keyFile -TotalCount 1).Trim()
        if ($fromFile) {
            $candidates += ,@{ key = $fromFile; from = "scripts\.agent-key (git-ignored)" }
        }
    }

    # Last resort: the value that is already committed. Not a fallback to be
    # comfortable about - it is the backlog item, restated at the moment it
    # matters.
    $p0 = Join-Path $PSScriptRoot "smoke\smoke-p0.ps1"
    if (Test-Path $p0) {
        $hit = Select-String -Path $p0 -Pattern 'iwk_[A-Za-z0-9_\-]+' | Select-Object -First 1
        if ($hit) {
            $candidates += ,@{ key = $hit.Matches[0].Value
                               from = "scripts\smoke\smoke-p0.ps1, where it is COMMITTED" }
        }
    }

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
                if ($c.from -like "*COMMITTED*") {
                    Write-Host "       That key is in git history and in chat transcripts. Rotating" -ForegroundColor Yellow
                    Write-Host "       it is an open backlog item. To stop using it here:" -ForegroundColor Yellow
                    Write-Host '         $env:IW_AGENT_KEY = "<key>"' -ForegroundColor DarkGray
                    Write-Host "       or put it on one line in scripts\.agent-key (git-ignored)." -ForegroundColor DarkGray
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

# ROTATING IT, when that day comes:
#   1. Generate:  python -c "import secrets; print('iwk_' + secrets.token_urlsafe(36))"
#   2. Set AGENT_API_KEY to the new value in Railway, and redeploy.
#   3. Put the same value in scripts\.agent-key (git-ignored) on each machine.
#   4. Strip the literal out of the 34 scripts that carry it and have them call
#      Get-AgentKey. Until step 4 is done, those scripts break at step 2 - which
#      is exactly why this helper exists first.
