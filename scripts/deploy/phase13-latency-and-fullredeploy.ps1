# PHASE 13 - kill the /recommendations latency + stop under-deploying cash.
# Last deployment of the session.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase13-latency-and-fullredeploy.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/services/war_room.py",
    "app/api/routes/war_room.py",
    "app/services/recommendations.py",
    "tests/test_war_room_narrate.py",
    "tests/test_redeploy_cash.py",
    # its fake_payload stub had a frozen signature and rejected narrate=
    "tests/test_signal_service.py",
    "scripts",
    "STATUS.md"
)

Invoke-Suite -Focus @("tests/test_war_room_narrate.py", "tests/test_redeploy_cash.py",
                      "tests/test_recommendations.py", "tests/test_signal_service.py",
                      "tests/test_recs_extras.py")

$msg = @(
    "perf+fix: skip war-room LLM prose on Today; redeploy all spendable cash",
    "",
    "1. /recommendations LATENCY - MEASURED, NOT GUESSED.",
    "   Phase 12's timings_ms named the culprit outright:",
    "     holding_verdicts 4ms | hedge 0 | momentum 0 | income_cost 0",
    "     war_room 6726ms | reconcile_and_filter 2 | backtest 0 | buy_ideas 0",
    "   war_room owned effectively the whole endpoint, and was SLOWER warm than",
    "   cold - the tell that it is not cacheable provider data. It is",
    "   adversary.narrate(): one live Gemini call PER SIGNAL, synchronous, on the",
    "   event loop of a single-worker uvicorn. It got slower after the Gemini",
    "   billing top-up, because the calls had previously failed fast with 429.",
    "",
    "   That prose is never rendered on a Today card (cards use outcome_label,",
    "   impact and confidence), so build_war_room takes narrate: bool = True and",
    "   the recommendations path passes narrate=False. The war-room view keeps",
    "   its narrative. Expect ~6.7s -> well under 1s.",
    "",
    "   Worth noting the earlier 24.2s -> 5s provider-cache fix was real but was",
    "   never the main cost; without timings_ms the next step would have been",
    "   another guess.",
    "",
    "2. THE REDEPLOY CARD UNDER-DEPLOYED.",
    "   After executing, cash sat at 12% against a 3% floor - ~1,940 left idle",
    "   while the card read as a complete answer. A Grow plan targets 10% Fixed",
    "   Income; nothing is held and the screener had no candidate, so that leg",
    "   was dropped and its share of the surplus silently evaporated.",
    "   Now a second pass reallocates an unfillable class's budget into the",
    "   classes that CAN absorb it, still clipped by the single-name cap; and",
    "   anything genuinely unplaceable is stated on the card:",
    "     'X could not be placed - you hold no Fixed Income and no candidate",
    "      was available'",
    "   Silence was the defect - the money vanished from the plan without a word.",
    "",
    "+6 tests (test_war_room_narrate.py, test_redeploy_cash.py)."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nAfter deploy - confirm the latency is gone:" -ForegroundColor Cyan
Write-Host '  $A = @{ "x-agent-key" = $env:IW_AGENT_KEY }'
Write-Host '  $B = "https://investwise-pro-production.up.railway.app"'
Write-Host '  Invoke-RestMethod "$B/api/v1/recommendations" -Headers $A | Select-Object -Expand timings_ms'
Write-Host "`nThen the full suite:  .\scripts\smoke\smoke-all.ps1" -ForegroundColor Cyan
