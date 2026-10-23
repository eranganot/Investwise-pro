# InvestWise Pro documentation

Start here. Each page states the date and commit it was written against, and when code and docs disagree, **the code
wins**: fix the page in the same PR.

## Reading order for someone new

1. **[PRODUCT.md](PRODUCT.md)**: what the app is, who it's for, the product principles. *(10 min)*
2. **[ARCHITECTURE.md](ARCHITECTURE.md)**: the system, the layers, boot, auth, the Today request flow, jobs, caches. *(15 min)*
3. **[DOMAIN.md](DOMAIN.md)**: how holdings + a plan become cards; funding; the goal; the invariants. *(30 min, the heart of it)*
4. **[DEVELOPMENT.md](DEVELOPMENT.md)**: run it, test it, change it, ship it (including the per-PR smoke). *(15 min)*
5. Then dip in as needed:

| Topic | Page |
|---|---|
| Tables, columns, migrations | [DATA_MODEL.md](DATA_MODEL.md) |
| Every endpoint, who calls it | [API.md](API.md) |
| The agents vs engines, cited line by line | [AGENTS.md](AGENTS.md) |
| UI structure, card anatomy, design tokens, service worker, UX principles | [FRONTEND.md](FRONTEND.md) |
| Production, deploy, health, secrets, runbook, incidents | [OPERATIONS.md](OPERATIONS.md) · [ROTATION.md](ROTATION.md) |
| Why things are the way they are | [DECISIONS.md](DECISIONS.md) |
| How we got here; incidents that became rules | [HISTORY.md](HISTORY.md) |
| **What's open / next** | [BACKLOG.md](BACKLOG.md) · active plan: [plans/EXECUTION_PLAN_2026-10-23_after-review.md](plans/EXECUTION_PLAN_2026-10-23_after-review.md) (previous: [2026-10-08 hardening](plans/EXECUTION_PLAN_2026-10-08_hardening.md), complete) |
| Words | [GLOSSARY.md](GLOSSARY.md) |
| Design explorations | [design/](design/) |
| Old plans, reviews, status (verbatim) | [archive/](archive/README.md) |

## Other places that hold truth

| File | Role |
|---|---|
| [`/STATUS.md`](../STATUS.md) | **The changelog:** one entry per shipped PR (newest first, PR 88 on): what, why, how it was verified, smoke result. Update it every time you ship. |
| [`/CLAUDE.md`](../CLAUDE.md) | Standing brief for Claude sessions: domain rules, sharp edges, ship rules. |
| [`/qa/`](../qa/) | Pixel 9 manual QA lists, one per user-visible PR. |
| [`/scripts/README.md`](../scripts/README.md) | Operator tooling and smokes. |
| `/docs` on a running instance | Live OpenAPI. |

## Keeping the docs true

- A PR that changes behaviour updates the page that describes it. A new decision gets a `D` entry in DECISIONS.md.
- Opening or closing work edits BACKLOG.md. Shipping adds a STATUS.md entry.
- `tests/test_the_agent_roster_is_what_actually_runs.py` pins AGENTS.md's claims to the code. If you change the roster, both change.
- Don't start a new top-level `PLAN_*.md`. Put a new plan in `docs/plans/`, keep its evidence beside it
  (`docs/plans/evidence-<date>/`), and move both to `docs/archive/plans/` when it's done.
- The probes in `docs/plans/evidence-*/probes/` are scratch reproductions, outside `testpaths`, so CI never runs them. A PR that fixes a
  finding promotes its probe into `tests/` as the regression test.
