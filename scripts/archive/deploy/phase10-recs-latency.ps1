# PHASE 10 - /recommendations latency + AI errors that say why + smoke-test cap fix.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase10-recs-latency.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    # 1. latency
    "app/core/config.py",
    "app/providers/registry.py",
    "tests/test_provider_cache_ttl.py",
    # 2. AI failures that name their cause
    "app/services/llm.py",
    "app/services/ai_service.py",
    "app/services/ask_service.py",
    "tests/test_llm_error_surfacing.py",
    # ask_service moved to gemini_generate_ex, so the assistant test patches the
    # new name -- patching a symbol the module no longer imports proves nothing.
    "tests/test_assistant.py",
    # 3. the "cap holds" fix -- it lives in the smoke test, not the app
    "scripts",
    "STATUS.md"
)

Invoke-Suite -Focus @("tests/test_provider_cache_ttl.py", "tests/test_llm_error_surfacing.py",
                      "tests/test_assistant.py", "tests/test_recommendations.py",
                      "tests/test_recs_extras.py", "tests/test_market_data_live.py")

$msg = @(
    "perf+ux: cache slow-moving provider data; AI failures now say why",
    "",
    "1. /recommendations took 24.2s while every other endpoint answered in under",
    "   a second (plan 0.2s, portfolio 0.1s, adversary/diagnostics 0.8s).",
    "",
    "   Not the LLM - diagnostics makes a real Gemini call in 0.8s. Not rate",
    "   limiting - the token bucket raises rather than sleeps, and 20/sec covers",
    "   18 calls. Not retry backoff - ResilienceTier defaults base_delay to 0.",
    "   Each ruled out by reading the code, not assumed.",
    "",
    "   It is provider fan-out: for a 6-holding book, build_recommendations makes",
    "   ~18 sequential calls. _holding_verdict_recs fetches each ticker's",
    "   fundamentals, _hedge_recs fetches the SAME fundamentals again for sector",
    "   weights, and _momentum_recs pulls 200 days of bars per holding. All three",
    "   shared provider_cache_ttl_sec = 15s - shorter than the request itself, so",
    "   nothing was ever warm. 15s is right for a quote and wrong for a quarterly",
    "   filing. Split by how fast the data moves: quotes 15s (unchanged), history",
    "   1h, fundamentals 6h. Same breaker and rate limit, longer memory.",
    "",
    "2. AI failures collapsed into a bare None, so 'Summary unavailable.' and",
    "   'couldn't reach the model' were shown for HTTP 429 'Your prepayment",
    "   credits are depleted' - a two-minute billing fix that looked identical to",
    "   a permanent outage. The real cause was only reachable via the adversary",
    "   diagnostics endpoint. New gemini_generate_ex() returns (text, reason) and",
    "   _classify() maps the failure to something actionable: no key / credits",
    "   exhausted / key rejected / model gone / timeout / unreachable. Wired into",
    "   the portfolio, holding and macro summaries and into Ask InvestWise;",
    "   gemini_generate keeps its old signature for callers that only want text.",
    "",
    "3. The 'concentration cap' failure in the phase 9 smoke test was the test's",
    "   bug, not the app's. It hardcoded 0.25 while risk_tolerance High sets the",
    "   cap to 0.40 - diversification_score 100 proves AMZN at 29% is within",
    "   limits. The script now reads the cap from /api/v1/plan. Also fixed a",
    "   false PASS: check 4 reported 'no competing cash cards' when the API had",
    "   timed out and returned null. Checks now SKIP or FAIL on a null payload.",
    "   Commits scripts/ so the deploy and smoke scripts survive a fresh clone.",
    "",
    "+6 tests (test_provider_cache_ttl.py, test_llm_error_surfacing.py)."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nAfter deploy - first call is cold, second should be fast:" -ForegroundColor Cyan
Write-Host '  1..2 | ForEach-Object { $sw=[Diagnostics.Stopwatch]::StartNew(); Invoke-RestMethod "$B/api/v1/recommendations" -Headers $H -TimeoutSec 120 | Out-Null; $sw.Stop(); "call $_`: $([math]::Round($sw.Elapsed.TotalSeconds,1))s" }'
Write-Host "`nThen:  .\scripts\smoke\smoke-phase9.ps1   (3 and 5 should PASS, not SKIP)" -ForegroundColor Cyan
