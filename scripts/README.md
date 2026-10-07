# scripts/

PowerShell operator tooling, written for Windows PowerShell 5.1: **ASCII only, no backtick continuations**.
Every script that talks to production authenticates with the agent key via `agent-key.ps1` (`Get-AgentKey`: `$env:IW_AGENT_KEY`,
or the git-ignored `scripts\.agent-key`). Default target: `https://investwise-pro-production.up.railway.app`.

## Live

| Path | What | Writes? |
|---|---|---|
| `smoke/_smoke-common.ps1` | The smoke framework: `Start-Smoke`, `Assert-Build` (pushed SHA serving + schema revision), `Api`, `Ok/Bad/Skip/Note`, `Stop-Smoke` | — |
| `smoke/smoke-prNN-*.ps1` | One per PR from PR 88 on. Run after the push: `.\scripts\smoke\smoke-prNN-<slug>.ps1 -Sha <short-sha>` | Read-only |
| `smoke/smoke-all.ps1`, `smoke-e2e.ps1`, `smoke-beat-market.ps1` | Broad regression smokes (`smoke-e2e -Full` **writes**: it recomputes backtests and applies a strategy) | Mostly read-only |
| `smoke/smoke-the-published-credentials-are-dead.ps1` | Proves the burned credentials are refused (see `docs/ROTATION.md`) | Read-only |
| `agent-key.ps1` | Resolve and verify the agent key; warns on the burned key | — |
| `get-error-log.ps1` | Pull Railway logs for a 500 (Railway CLI) → `scripts/railway-error.log` (git-ignored) | — |
| `ops/ops-check.ps1` | Read-only checklist of the owner-only ops items | — |
| `ops/OPS_RUNBOOK.md` | Order and procedure for those items | — |
| `ops/pre-push` | Git hook refusing force-push or deletion of main: `cp scripts/ops/pre-push .git/hooks/pre-push` | — |
| `ops/verify-vapid-pin.ps1`, `pin-vapid-from-db.sql` | VAPID pin procedure | SQL is read-only |
| `set-sleeves.ps1`, `set-contributions.ps1`, `retire-holding.ps1` | Operator book-editing CLIs | **Write to production** |

How to write a new smoke: [`docs/DEVELOPMENT.md §8`](../docs/DEVELOPMENT.md#8-shipping-a-pr).

## archive/

Historical one-shot scripts, kept for the record. **Not maintained; may not run from their new location.** Moved here on 2026-10-07.

| Path | What |
|---|---|
| `archive/deploy/` (128) | Per-phase and per-PR ship scripts (`ship-pr1` … `ship-pr87`, `phase*`, hotfixes). They come from when the sandbox could not commit. Since PR 88 the sandbox pushes directly and per-PR smokes replace them. `_common.ps1` is still read by `tests/test_sleeves_mean_what_they_say.py`. |
| `archive/smoke/` (64) | Pre-PR 88 smokes (phase, topic and PR 22–83 smokes), self-contained with inline settings |
| `archive/root/` | August T0–T6 / A / N phase shippers and one-shot fixes; `portfolio-restructure.ps1` |
| `archive/repair/` | One-shot ledger repairs (PR 73, PR 86), preview-first. Check STATUS before re-running one |
