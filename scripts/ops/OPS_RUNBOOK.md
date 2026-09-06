# Ops runbook — 2026-09

The ops list has been the oldest thing on the board for weeks. Part of the reason is that
none of it was **checkable**: you rotate a token and nothing tells you it is done, so the
item never leaves the list even when it has.

So this runbook has two halves. `scripts\ops\ops-check.ps1` answers *what state is each item
actually in* — read-only, changes nothing, prints no secret. This file says *in what order,
and why that order*.

```powershell
.\scripts\ops\ops-check.ps1
```

Run it before you start and again when you finish. Anything still `TODO` is genuinely
outstanding; `MANUAL` means only you can see it.

---

## The order, and the reasoning

### 1. Branch protection on `main` — do this first

**Two minutes, zero risk, and the only item on this list that prevents a class of incident
rather than reducing an exposure.** Everything else makes a bad day less likely; this one makes
a whole category of bad day impossible.

github.com/eranganot/Investwise-pro → **Settings → Branches → Add branch protection rule**

- Branch name pattern: `main`
- ☑ Require a pull request before merging *(you can allow yourself to bypass; the point is that
  an accidental direct push to a repo that deploys on push is no longer one keystroke)*
- ☑ Require status checks to pass — select the CI workflow
- ☑ Do not allow force pushes

Note what this repo's push does: **`git push origin main` deploys to production.** That is why
this is first.

`ops-check.ps1` verifies it if you set `$env:GITHUB_TOKEN` to a PAT with `repo` scope; without
one it reports `UNKNOWN` rather than guessing.

### 2. Rotate the GitHub token

github.com/settings/tokens → regenerate. Then push once so git re-authenticates.

Five minutes, low risk. The failure mode if you get it wrong is a push that asks for
credentials, which you will notice immediately.

### 3. Pin the VAPID keypair

```powershell
.\scripts\ops\verify-vapid-pin.ps1 -Before
#   ... do the pin ...
.\scripts\ops\verify-vapid-pin.ps1 -After -TestPush
```

That script carries the full procedure in its own header, and it **never touches the private
key** — it reads the compare the app already publishes at `/api/v1/push/status`.

The one thing to hold on to: `push_service.get_vapid` warns, in its own words, that
**"pinning the wrong pair is silent, and it kills every subscription."** If `-After` reports a
mismatch, delete both Railway variables immediately and you are back where you started.

Why it matters: the pair exists only as a `kv_settings` row in production. If that database is
ever rebuilt or restored, the app mints a new pair, your phone's subscription is bound to a key
nobody holds, and every push afterwards reports as sent and arrives nowhere.

### 4. Rotate the Railway token

Railway → Account → Tokens. Five minutes.

### 5. Check for `DATABASE_PUBLIC_URL` — two minutes, and possibly the highest-value item here

Railway → the **Postgres** service → **Variables**.

Nothing in this repo mentions it, and Railway's Postgres normally exposes one. If it is there,
then alembic can be run against production **from this laptop**, and the `kv_settings` rows can
be read directly — which is the constraint the entire migration saga (PRs 46, 47, 48, 52) was
built around. `health.schema_revision`'s docstring records two failed attempts to reach that
database from outside, both because `DATABASE_URL` names a private host that does not resolve.

If you find one, tell me and several open items get cheaper at once.

### 6. Postgres password — **recommended NOT now**

This is the one item I would leave alone, and the reasoning is worth stating rather than
leaving as a gap in the list:

- It has never been exposed.
- It is wired through Railway's own variable references, so rotating it means touching the
  plumbing that connects the app to its database.
- A wrong move takes the database offline, which is a worse day than the risk being removed.

**Rotate it on a reason** — suspected exposure, someone leaving, a support incident — not on a
schedule. If a reason appears, do it with the healthcheck from PR 49 in place, so a container
that cannot reach the database fails its deploy instead of taking traffic.

---

## What ops means here beyond credentials

`ops-check.ps1` also verifies the two deploy invariants, because "the pipeline cannot tell a
working deploy from an outage" was an ops problem that cost two hours:

- **`healthcheckPath`** (PR 49) — a container that cannot serve `/health/ready` never takes
  traffic. Railway keeps the working one and fails the deploy.
- **`preDeployCommand`** (PR 52) — the migrations run as their own step, and
  `/health.schema_revision` says which revision production is stamped at. That field moving
  `0020 → 0021` on the PR 54 deploy is how we know the step actually runs.

---

## Recording

When an item is done, re-run `ops-check.ps1` and let the output be the record. Do not tick
anything off in a document that nothing verifies — that is how this list came to contain items
that were already finished.


---

## Branch protection will not enforce on this repository (2026-09-06)

This was the first item on the list, described as two minutes. It is not available.

GitHub's Rulesets page says it plainly on this repo: *"Your rulesets won't be enforced on this
private repository until you move to GitHub Team organization account."* Protected branches on a
PRIVATE repository need a paid plan. The ruleset can be created and will look armed; it will not
fire. That is worse than nothing, because it reads as done.

**Two real options.**

1. **GitHub Pro**, about $4/month, which makes the ruleset actually enforce. Configure it as
   *Restrict deletions* + *Block force pushes* only — **not** "Require a pull request before
   merging", which would break every ship script in `scripts/deploy/`, since they all end in
   `git push origin main`.

2. **The local hook**, which is free and works today:

   ```
   cp scripts/ops/pre-push .git/hooks/pre-push
   chmod +x .git/hooks/pre-push        # Git Bash
   ```

   Verified against a throwaway origin: a normal push is allowed, `--force` is refused, a branch
   delete is refused. It is NOT equivalent to server-side protection — it runs only on machines
   where it is installed, and `git push --no-verify` steps straight past it. Worth having anyway:
   one person pushes to this repo, and the incident it prevents (a rewritten `main` replacing what
   is deployed) is one you would commit by accident, not on purpose.

`ops-check.ps1` reports this item as MANUAL rather than TODO for that reason. A check that stays
red for something you cannot do is a check you learn to ignore.
