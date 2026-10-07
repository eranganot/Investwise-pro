# Rotating the two credentials that were published

Both of these were readable by anyone with access to this repository, which was
public until 2026-08-30. Making it private does not un-publish them: they are in
160+ commits of history, and both were pasted into chat transcripts. **They must
be replaced, not merely hidden.**

PR 26 made rotation possible. It did not perform it — the new values only exist
once you set them on Railway.

## What was wrong

Measured against production on 2026-08-30, before PR 26:

| probe | result |
| --- | --- |
| `GET /api/v1/portfolio` with no key | `401` |
| `GET /api/v1/portfolio` with a wrong key | `401` |
| `GET /api/v1/portfolio` with the key committed in 33 scripts | **`200`** |
| `POST /api/v1/auth/token` with the default password from `config.py` | **`200`, SUPERADMIN token** |

Authentication was working correctly. The credentials were simply public.

The password half was worse than it looked, because **the documented remedy did
nothing.** `PRODUCTION.md` section 8 says to set `AUTH_PASSWORD`.
`ensure_superadmin_credential` only ever ran an `INSERT` — `if ... is None` — so
the hash was written once, at first boot, and every later change to the
environment variable was ignored. There is no password-change endpoint in the
app. The published password could not be retired by any configuration change at
all.

## What PR 26 changed

* `auth_password` has no default. Blank means password login is **unavailable**,
  not open. Google sign-in is a separate path and is unaffected.
* `AUTH_PASSWORD` is now authoritative: if the stored hash does not verify
  against it, the stored hash is replaced. This is the app's only means of
  changing the SuperAdmin password, and it did not exist before.
* A password in `auth_service.PUBLISHED_PASSWORDS` is refused even if someone
  sets it deliberately, and an existing row still holding one is scrambled to a
  random value on the next call. `/api/v1/auth/token` awaits that function
  **before** it checks credentials, so the first request to arrive after the
  deploy retires the old hash — including a request from someone arriving to
  use it.
* The agent key literal is gone from all 33 scripts. They call `Get-AgentKey`
  (`scripts/agent-key.ps1`), which reads `$env:IW_AGENT_KEY` or the git-ignored
  `scripts/.agent-key`. There is no third source, deliberately.
* `tests/test_no_secret_is_committed.py` turns CI red if either value — or
  anything key-shaped — reappears in a tracked *or* newly added file.

## Rotating, in order

The ship script for PR 26 generates both values and prints them. If you would
rather do it by hand:

```powershell
python -c "import secrets; print('iwk_' + secrets.token_urlsafe(36))"
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

1. **Deploy PR 26 first.** Until it is live, setting `AUTH_PASSWORD` changes
   nothing, because the old code ignores it.

2. **On Railway**, set both variables on the service and redeploy:
   * `AGENT_API_KEY` = the new `iwk_...` value
   * `AUTH_PASSWORD` = the new password

3. **On each machine you run scripts from**, put the new agent key on one line
   in `scripts\.agent-key` (git-ignored), or set it per session:

   ```powershell
   $env:IW_AGENT_KEY = "<the new key>"
   ```

4. **Verify** with `scripts\smoke\smoke-the-published-credentials-are-dead.ps1`.
   It checks the four things that matter: the old key is refused, the new key
   works, the published password is refused, and prices still load.

Between steps 1 and 2 the old key keeps working — it is still what
`AGENT_API_KEY` is set to. That window is the only reason to do step 2 promptly.

## If a script suddenly 401s

`Get-AgentKey` tests each candidate against the live API and tells you which one
the server rejected. A rejection after rotation means the machine still has the
old value: fix `scripts\.agent-key`, or clear a stale environment variable with
`Remove-Item Env:\IW_AGENT_KEY`.

## What is still true after all of this

* The old values remain in git history forever. Rotation makes them useless; it
  does not remove them. Rewriting history is not worth it for two credentials
  that will be dead.
* The agent key still grants **SUPERADMIN** to any request carrying it
  (`app/core/auth.py:115`). That is by design, and it means the new key deserves
  the handling the old one never got: never in a file git can see, never in
  chat, never in a screenshot.
