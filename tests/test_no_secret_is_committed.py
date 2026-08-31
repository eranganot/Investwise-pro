"""A live SUPERADMIN credential sat in 33 committed scripts, and nothing said so.

WHAT HAPPENED, measured 2026-08-30 against production:

    POST /api/v1/auth/token {"password": "changeme-dev"}          -> 200 SUPERADMIN
    GET  /api/v1/portfolio  x-agent-key: <the committed literal>  -> 200
    GET  /api/v1/portfolio  x-agent-key: <a wrong key>            -> 401
    GET  /api/v1/portfolio  (no key)                              -> 401

Auth was working exactly as designed. Both credentials were simply public: the
repository was public until 2026-08-30, the agent key was a literal in 33
scripts, and the SuperAdmin password was the DEFAULT VALUE of a field in
config.py. Neither was ever a typo -- each was written deliberately, by someone
who needed a script to run, and then nothing ever looked again.

THIS FILE IS THE THING THAT LOOKS AGAIN.

It is a check the DATA can fail rather than one the code can pass: it reads the
actual tracked files, so a new script pasted in next month with the key in it
turns CI red on the first push. Reviews do not catch this -- four reviews did
not, across four PRs, while the item sat in the backlog labelled "known".

Both credentials must still be rotated; this only stops NEW ones landing.
Making the repository private did not un-publish what was already in it.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Paths allowed to contain a burned value, and exactly why. Anything not on
# this list that matches is a failure. Keep it short; every entry is a hole.
ALLOWED = {
    # The denylist itself. It has to name what it refuses.
    "app/services/auth_service.py",
    # The tests for that denylist, which must use the refused value.
    "tests/test_auth_service.py",
    # This file: it describes the patterns it hunts.
    "tests/test_no_secret_is_committed.py",
    # The smoke test whose entire job is to PROVE production refuses it. It
    # cannot ask that question without sending the value.
    "scripts/smoke/smoke-the-published-credentials-are-dead.ps1",
}

# SHA-256 of credentials known to be published. Fingerprints, not values --
# a test that carries the secret in order to look for it has not helped.
BURNED_SHA256 = {
    "bcb13d47a22bef955a858ccbc3abed3d36ff443f29b580eb71145ca400b66b1e":
        "the agent API key committed in 33 scripts until PR 26",
    "04201a232965b56063a7aee2b08b3fde780ab1f27b52d6e53ff2371bde2f8607":
        "the SuperAdmin password that defaulted in config.py until PR 26",
}

# Shapes, not values. A rotated key is a different string but the same shape.
FORBIDDEN_SHAPES = [
    (re.compile(r"iwk_[A-Za-z0-9_-]{20,}"),
     "an InvestWise agent API key. Scripts must call Get-AgentKey "
     "(scripts/agent-key.ps1), which reads $env:IW_AGENT_KEY or the "
     "git-ignored scripts/.agent-key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
     "a private key block"),
    (re.compile(r"postgres(?:ql)?://[^\s\"']*:[^\s\"'@]+@"),
     "a database URL with the password in it"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "an OpenAI-shaped API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key id"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"), "a GitHub personal access token"),
]

# Directories that are not ours to police.
SKIP_DIRS = ("frontend/node_modules/", "node_modules/", ".git/")
# Binary-ish extensions where a false positive is likelier than a real find.
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff",
                 ".woff2", ".ttf", ".zip", ".gz", ".pyc", ".db", ".sqlite")


def _tracked_files() -> list[str]:
    """Everything git would carry -- tracked files AND new, un-ignored ones.

    --others --exclude-standard is the load-bearing half and was NOT in the
    first version of this file. With plain `ls-files` I planted the retired key
    in a new script, ran this suite, and it passed: the file was untracked, so
    the check never saw it. It would have gone red in CI, one commit too late,
    which is the same "found it afterwards" this test exists to end. Now the
    file is caught the moment it exists on disk.

    Deliberately no pytest.skip anywhere in this module. PR 21 established that
    an unregistered skip fails the run, and a security check that can quietly
    not run is worse than none -- the report still says green. If git is
    missing entirely, the walk below covers it.
    """
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others",
                              "--exclude-standard"],
                             cwd=REPO, capture_output=True,
                             text=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().split("\n")
    except (OSError, subprocess.SubprocessError):
        pass
    # Fallback: walk the tree. Slower, and it sees untracked files too, which
    # for this purpose is not a problem.
    return [str(p.relative_to(REPO)).replace("\\", "/")
            for p in REPO.rglob("*") if p.is_file()]


def _candidate_files() -> list[str]:
    keep = []
    for rel in _tracked_files():
        rel = rel.replace("\\", "/")
        if rel.startswith(SKIP_DIRS) or any(d in rel for d in SKIP_DIRS):
            continue
        if rel.endswith(SKIP_SUFFIXES):
            continue
        keep.append(rel)
    return keep


def _read(rel: str) -> str | None:
    path = REPO / rel
    try:
        if path.stat().st_size > 4_000_000:
            return None
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def test_no_credential_shape_is_committed():
    """Fails on the SHAPE of a secret, so a rotated one is caught too."""
    hits = []
    for rel in _candidate_files():
        if rel in ALLOWED:
            continue
        text = _read(rel)
        if text is None:
            continue
        for pattern, what in FORBIDDEN_SHAPES:
            for m in pattern.finditer(text):
                line = text[:m.start()].count("\n") + 1
                hits.append(f"  {rel}:{line} looks like {what}")
    assert not hits, (
        "A credential-shaped literal is committed:\n" + "\n".join(sorted(hits))
        + "\n\nThis is exactly how the agent key ended up in 33 scripts. If the "
          "match is a false positive, add the path to ALLOWED in this file with "
          "a one-line reason -- do not widen the pattern.")


def test_no_known_burned_credential_is_committed():
    """Catches a published value even if it is reformatted or split up.

    The shape check above would miss a password like "changeme-dev", which has
    no distinctive shape at all. This one knows the exact strings by hash.
    """
    words = re.compile(r"[A-Za-z0-9_\-./+=]{6,120}")
    hits = []
    for rel in _candidate_files():
        if rel in ALLOWED:
            continue
        text = _read(rel)
        if text is None:
            continue
        for m in words.finditer(text):
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()
            if digest in BURNED_SHA256:
                line = text[:m.start()].count("\n") + 1
                hits.append(f"  {rel}:{line} is {BURNED_SHA256[digest]}")
    assert not hits, (
        "A credential known to be published is committed:\n" + "\n".join(sorted(hits))
        + "\n\nThese values can never be used again. See ROTATION.md.")


def test_the_private_key_file_is_git_ignored():
    """The escape hatch has to actually be an escape hatch.

    Get-AgentKey reads scripts/.agent-key. If that path is ever committable,
    the resolver stops being a fix and becomes a more convenient way to leak.
    """
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "scripts/.agent-key" in ignore, (
        "scripts/.agent-key is not in .gitignore, so the file Get-AgentKey "
        "tells people to create would be committed")


def test_config_ships_no_default_password():
    """The default IS the vulnerability, so assert on the default itself.

    A published default is not a weak password. It is a password everyone has,
    and it stayed live for five months because setting AUTH_PASSWORD did
    nothing (see auth_service.ensure_superadmin_credential).
    """
    from app.core.config import Settings

    assert Settings.model_fields["auth_password"].default == "", (
        "auth_password has a default again. Any literal here is published to "
        "everyone with repo access, and there is no in-app way to change a "
        "password once it is seeded.")
    assert Settings.model_fields["agent_api_key"].default == "", (
        "agent_api_key has a default again")
