# PHASE 15 - live daily strategy signals (Beat the Market, Phase D).
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase15-strategy-signals.ps1
#
# DB: adds `strategy_signal_state` via alembic 0010. Run `alembic upgrade head`
#     after the deploy if AUTO_CREATE_TABLES is off in production.
#
# USER-VISIBLE, but only if you have applied one of the Beat the Market
# strategies: when its rule changes what it wants to hold, a card appears on
# Today. Nothing appears while the rule is repeating itself, which is most days.
#
# New daily job at 06:15.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/models/tables.py",
    "app/services/strategy_signal_service.py",
    "app/services/strategy_catalog.py",
    "app/services/recommendations.py",
    "app/api/routes/strategy.py",
    "app/worker/scheduler.py",
    "alembic/versions/0010_strategy_signal_state.py",
    "tests/test_strategy_signals.py",
    "scripts/smoke/smoke-phase10-15.ps1",
    "scripts/deploy/phase15-strategy-signals.ps1"
)

Invoke-Suite -Focus @("tests/test_strategy_signals.py", "tests/test_backtest_service.py",
                      "tests/test_strategy_backtest.py", "tests/test_accept_honesty.py",
                      "tests/test_done_vs_ignored.py", "tests/test_reconcile.py",
                      "tests/test_recommendations.py")

$msg = @(
    "feat(strategy): the active rule tells you when it changes its mind",
    "",
    "Phase D. A backtest says what a rule WOULD have done; this says what it",
    "wants today. Acting late is the main reason a rule underperforms its own",
    "backtest, and until now nothing told the user the rule had moved.",
    "",
    "ONLY A CHANGE IS NEWS. A trend or swing rule emits a target every",
    "session and almost always repeats yesterday's, so notifying on the",
    "target rather than on the change would produce a daily message saying",
    "nothing -- which is how people learn to ignore an app. New",
    "strategy_signal_state (alembic 0010) stores the last target as a plain",
    "ticker->weight map, so a flip is detected by comparing what would be",
    "HELD rather than by diffing prose that may be reworded later.",
    "",
    "* Daily job at 06:15, after the US close has settled into the daily",
    "  feed. Hourly would not help: these rules read daily closes, so an",
    "  intraday re-evaluation can only repeat itself or react to a bar that",
    "  is not final.",
    "* A flip becomes one Today card, alongside triggered trading rules --",
    "  the same class of event, a discipline the user chose, speaking.",
    "* GET /strategies/signal is READ-ONLY and deliberately does not record a",
    "  flip. A GET that consumed the signal would make `"were you notified?`"",
    "  depend on whether you happened to open the page, and would leave the",
    "  daily job with nothing to report. POST /strategies/signal/ack clears",
    "  one once acted on.",
    "",
    "THREE REFUSALS, each of which would otherwise produce a confident wrong",
    "instruction:",
    "",
    "  1. A stale feed abstains. `"The rule says move to cash`", derived from",
    "     week-old closes, reads as today's instruction while describing last",
    "     week's market. Newest close older than 3 days -> STALE_FEED.",
    "  2. The first evaluation is a baseline, never a flip. Announcing it",
    "     would tell the user their strategy `"changed`" the moment they",
    "     applied it.",
    "  3. A dismissed signal is resolved rather than re-derived, so one you",
    "     already declined cannot reappear every morning -- the exact nagging",
    "     the flip-only design exists to prevent.",
    "",
    "The execution firewall holds: the card states on its face that no",
    "brokerage order is placed and that the trade must be mirrored at the",
    "broker. The app can tell you the rule fired; placing it is yours.",
    "",
    "+9 tests. ruff clean."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'After Railway reports Active:' -ForegroundColor Cyan
Write-Host '  1. alembic upgrade head' -ForegroundColor Gray
Write-Host '  2. Apply a Beat the Market strategy from the Plan tab' -ForegroundColor Gray
Write-Host '  3. GET /api/v1/strategies/signal  -> what the rule wants today' -ForegroundColor Gray
Write-Host '  4. .\scripts\smoke\smoke-phase10-15.ps1' -ForegroundColor Gray
Write-Host '  Expect NO Today card until the rule actually changes its mind.' -ForegroundColor DarkGray
