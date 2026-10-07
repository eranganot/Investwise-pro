# InvestWise Pro — Remediation Plan

Tracks every improvement from the code review (commit `e643835`) through to done.
Each item is mapped to a remediation phase (R#) with approach, effort, and status.

Legend: ✅ done · 🚧 in progress · ⬜ planned · 🔒 needs your decision

---

## Wave A — Critical (correctness & safety)

### R1 · C1 — Decimal money across engines  ⬜
Replace `float` currency math in tax / allocation / scenario / analytics with a `Money`
(`Decimal`, ROUND_HALF_UP, 2dp) type. **Effort:** S. **Risk:** low (internal).

### R2 · C3 — Recalibrate the Impact-Score gate  ⬜
Unknown sub-scores currently default to a neutral 50, which floors impact above the `≥20`
gate. Lower the unknown penalty, recalibrate `min_impact_score`, add a property test that
weak signals are rejected. **Effort:** S.

### R3 · C2 — Honest financial models  ⬜ (🔒 tax-law specifics)
- Add a machine-readable `model_assumptions` block to risk / tax / scenario / decision output.
- Monte Carlo: add a fat-tailed (Student-t) option alongside GBM; document i.i.d. / per-signal limits.
- Scenario: asset-class-aware shocks instead of one blended delta.
- The actual CGT/surtax/inflation-basis rules still need an accountant's sign-off (🔒).
**Effort:** M.

### R4 · C4 — Auth hardening  ⬜ (🔒 credential policy)
- `credentials` table with bcrypt hashes + per-user role (new table — safe under create_all).
- DB-backed refresh-token revocation (`revoked_tokens` table) — survives restart, shared across instances.
- Login verifies against the table; bootstrap a SuperAdmin from `AUTH_PASSWORD` on first run.
- Persistent JWT keys remain env-driven; document that blank = ephemeral (dev only).
**Effort:** M.

---

## Wave B — High (integrity & consistency)

### R5 · H1 — Weekly feed from the real portfolio  ⬜
Drive `/decision-feed/weekly` from `load_positions` (demo only as fallback); drop the
`workflows → decision_feed.DEFAULT_OBSERVATIONS` coupling. **Effort:** S.

### R6 · H2 — Acting user from the JWT principal  ⬜
When auth is on, resolve the acting user from the authenticated principal instead of the
hard-coded SuperAdmin; keep the default in open mode. **Effort:** S.

### R7 · H3 — Recommendation auto-expiry  ⬜
Persist `expires_at` in the `decision_items.payload` (avoids an existing-table column change),
compute staleness on read, and add an APScheduler job that closes stale feeds. **Effort:** S.

### R8 · H4 — Migrations for new tables + prod switch  ⬜
Alembic migration(s) for the new auth tables; document switching prod to `alembic upgrade head`
with `AUTO_CREATE_TABLES=false`. **Effort:** S.

---

## Wave C — Medium (quality, ops) — planned, not in this pass

- **M1** API/integration tests via `TestClient` (auth on/off, 403/401, allocation veto) + a
  **Postgres** test job (not just SQLite).
- **M2** GitHub Actions CI running pytest (+ ruff/mypy), gating deploys.
- **M3** Observability: structured JSON logging, request-id middleware, `/metrics` (Prometheus),
  Sentry, and a persisted append-only audit table (not just stdout).
- **M4** Move scoring/analytics magic numbers into config; calibrate against the learning loop.
- **M5** Extract a `PipelineOrchestrator` service so route handlers stay thin.

## Wave D — Low (polish)

- **L1** Resolve the two frontends (delete the unused Vite scaffold or build the real one); add a
  login flow to the served dashboard so it works when `REQUIRE_AUTH=true`.
- **L2** Dockerfile: non-root user, `HEALTHCHECK`, `.dockerignore`, multi-stage.
- **L3** Per-IP rate limiting on the public API and `/auth/token`.
- **L4** Split web vs worker requirements to slim the web image.
- **L5** Rotate the GitHub/Railway tokens + DB password exposed during setup.

---

## Execution order (this pass = Wave A + Wave B)
R1 → R2 → R3 → R4 (Critical), then R5 → R6 → R7 → R8 (High). Each phase: implemented clean,
unit-tested, pushed, and verified live. Waves C and D follow on confirmation.
