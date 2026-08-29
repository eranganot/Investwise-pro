# ONE place that answers "what is the agent API key".
#
#   . "$PSScriptRoot\..\agent-key.ps1"      # from scripts/smoke/*
#   $ApiHeaders = @{ 'x-agent-key' = Get-AgentKey }
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

function Get-AgentKey {
    [CmdletBinding()]
    param([switch]$Quiet)

    if ($env:IW_AGENT_KEY) {
        if (-not $Quiet) { Write-Host "  key: from IW_AGENT_KEY" -ForegroundColor DarkGray }
        return $env:IW_AGENT_KEY
    }

    $keyFile = Join-Path $PSScriptRoot ".agent-key"
    if (Test-Path $keyFile) {
        $fromFile = (Get-Content $keyFile -TotalCount 1).Trim()
        if ($fromFile) {
            if (-not $Quiet) { Write-Host "  key: from scripts\.agent-key (git-ignored)" -ForegroundColor DarkGray }
            return $fromFile
        }
    }

    # Last resort: the value that is already committed. Not a fallback to be
    # comfortable about - it is the backlog item, restated at the moment it
    # matters.
    $p0 = Join-Path $PSScriptRoot "smoke\smoke-p0.ps1"
    if (Test-Path $p0) {
        $hit = Select-String -Path $p0 -Pattern 'iwk_[A-Za-z0-9_\-]+' | Select-Object -First 1
        if ($hit) {
            if (-not $Quiet) {
                Write-Host "  key: read out of scripts\smoke\smoke-p0.ps1, where it is COMMITTED." -ForegroundColor Yellow
                Write-Host "       That key is in git history and in chat transcripts. Rotating it" -ForegroundColor Yellow
                Write-Host "       is an open backlog item. To stop using it here:" -ForegroundColor Yellow
                Write-Host '         $env:IW_AGENT_KEY = "<key>"' -ForegroundColor DarkGray
                Write-Host "       or put it on one line in scripts\.agent-key (git-ignored)." -ForegroundColor DarkGray
            }
            return $hit.Matches[0].Value
        }
    }

    Write-Host ""
    Write-Host "  No agent key found. Set one for this session:" -ForegroundColor Red
    Write-Host '    $env:IW_AGENT_KEY = "<your agent key>"' -ForegroundColor White
    Write-Host "  or write it, one line, to scripts\.agent-key (git-ignored)." -ForegroundColor White
    Write-Host "  The live value is whatever AGENT_API_KEY is set to on Railway." -ForegroundColor DarkGray
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
