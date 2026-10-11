# SMOKE - PR 111: logging out ends THIS session (D73, option 2).
#
#   .\scripts\smoke\smoke-pr111-logout.ps1 -Sha <short sha of the merge>
#
#   Uses a THROWAWAY token, never your own session:
#   1  the agent key mints a READ_ONLY m2m token (/auth/m2m)
#   2  the token reads /auth/me -> 200
#   3  /auth/logout with that token as the bearer -> redirect
#   4  the same token -> 401 (before PR 111: still 200)
#   5  non-change: the agent key still works; /auth/logout with no session is harmless
#
# Writes exactly one row: the throwaway token's jti in revoked_tokens. ASCII ONLY.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "PR 111"
Assert-Build -Sha $Sha -Schema '0028_plan_thresholds' -Why "(unchanged: PR 111 has no migration)"

function Code($method, $path, $headers) {
    try { $r = Invoke-WebRequest -Method $method -Uri "$BaseUrl$path" -Headers $headers -UseBasicParsing -TimeoutSec 60 -MaximumRedirection 0; return [int]$r.StatusCode }
    catch { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } else { return -1 } }
}

Sec "1-2  a throwaway token"
$m = $null
try { $m = Invoke-RestMethod -Method POST -Uri "$BaseUrl/api/v1/auth/m2m" -Headers $script:ApiHeaders -ContentType 'application/json' -Body '{"client":"smoke-pr111","role":"READ_ONLY"}' -TimeoutSec 60 } catch {}
if (-not $m -or -not $m.m2m_token) { Bad "could not mint a throwaway token"; Stop-Smoke }
$h = @{ Authorization = "Bearer $($m.m2m_token)" }
$c0 = Code GET "/api/v1/auth/me" $h
if ($c0 -eq 200) { Ok "throwaway token works before logout" } else { Bad "throwaway token -> $c0 before logout" }

Sec "3-4  logout ends it"
$c1 = Code GET "/auth/logout" $h
if ($c1 -in 302, 307) { Ok "logout -> $c1" } else { Bad "logout -> $c1" }
$c2 = Code GET "/api/v1/auth/me" $h
if ($c2 -eq 401) { Ok "the same token after logout -> 401" } else { Bad "the same token after logout -> $c2 (want 401)" }

Sec "5  non-change"
$me = Api GET "/api/v1/auth/me"
if ($me -and $me.role -eq 'SUPERADMIN') { Ok "agent key still SUPERADMIN" } else { Bad "agent key broken" }
$c3 = Code GET "/auth/logout" @{}
if ($c3 -in 302, 307) { Ok "logout with no session -> $c3" } else { Bad "logout with no session -> $c3" }
Stop-Smoke
