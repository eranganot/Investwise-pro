# Production Hardening Checklist

InvestWise Pro runs in dev with open access and auto-created tables. Before
treating the Railway deployment as production, work through this list.

## 1. Rotate the exposed secrets (do this first)
These appeared in plaintext during setup and should be considered compromised:
- **GitHub token** - GitHub → Settings → Developer settings → Personal access tokens → revoke + reissue.
- **Railway token** - Railway → Account → Tokens → revoke + reissue.
- **Postgres password** - Railway → Postgres service → rotate credentials (the
  app reads `${{Postgres.DATABASE_URL}}`, so it picks up the new value automatically).

## 2. Set production environment variables (Railway → app service → Variables)
| Variable | Value | Why |
|---|---|---|
| `ENVIRONMENT` | `production` | enables startup warnings, signals prod |
| `DEBUG` | `false` | turns off SQL echo / verbose logging |
| `API_KEY` | a long random string | requires `X-API-Key` on write endpoints |
| `AUTO_CREATE_TABLES` | `false` | use Alembic migrations instead (see below) |

With `API_KEY` set, callers must send `X-API-Key: <key>` on POST `/intake/*`,
`/decision-feed/generate`, and `/actions`. `GET /api/v1/auth/status` reports whether auth is on.

## 3. Use migrations instead of auto-create
A baseline migration (`alembic/versions/0001_initial.py`) creates the full schema.
For an existing database that already has the tables, baseline it once:
```bash
alembic stamp head
```
For a fresh database:
```bash
alembic upgrade head
```
To run migrations automatically on deploy, set the Railway start command to:
```
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 4. Remove unused services
The project only needs the **Postgres** plugin. Delete the **Redis** and
**storage Bucket** services Railway added during setup to avoid extra cost.

## 5. Operational endpoints
- `GET /health` - liveness
- `GET /health/ready` - readiness (verifies DB connectivity; returns 503 if down)
- Security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`)
  are applied to every response.

## 6. Still advisable
- Confirm the tax parameters (CGT %, surtax %, threshold) with your accountant;
  they are config-driven (`CGT_RATE`, `SURTAX_RATE`, `SURTAX_THRESHOLD_ILS`).
- Move the project out of OneDrive locally (git/Docker behave better elsewhere).
