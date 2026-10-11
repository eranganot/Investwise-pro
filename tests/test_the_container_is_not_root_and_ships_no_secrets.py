"""C8's other half. The image had none of the usual hardening, including a
`.dockerignore` -- there was no such file at all.

These are cheap, static assertions about two files. That is deliberate: this
repo has no Docker daemon in its gates, so the build cannot be exercised here,
and a test that cannot run the thing should at least pin the properties whose
absence is invisible until the day they matter. Railway builds the image; PR 49's
healthcheck means an image that builds wrong, or boots wrong, never replaces the
deployment that is serving.

Not financial advice.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
DOCKERIGNORE = REPO / ".dockerignore"


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_the_build_context_excludes_the_agent_key_and_the_repo_history():
    """The context is not the image, and that is exactly why this was skipped.

    The Dockerfile COPYs only `app/`, `alembic/`, `alembic.ini` and
    `requirements.txt`, so `scripts/.agent-key` never reached a layer. But the
    build CONTEXT -- everything the daemon uploads, caches and ships to the
    builder -- was the entire working tree: `.git`, the two source tarballs,
    `.env`, and `scripts/` with the key in it. One `COPY . .` written in a hurry
    is the whole distance between that and a key in a layer, permanently.
    """
    assert DOCKERIGNORE.exists(), (
        ".dockerignore is gone. Without it the build context is the whole "
        "working tree, including scripts/.agent-key and .env.")

    body = DOCKERIGNORE.read_text(encoding="utf-8")
    lines = {ln.strip() for ln in body.splitlines()
             if ln.strip() and not ln.strip().startswith("#")}

    for required in ("scripts/", ".env", ".git"):
        assert required in lines, (
            f"{required!r} is not excluded from the build context. "
            f"Excluded: {sorted(lines)}")

    assert any(ln.startswith("scripts/.agent-key") for ln in lines), (
        "the agent key is not named explicitly. `scripts/` already covers it, "
        "and it is named anyway so that narrowing that line later cannot "
        "silently re-admit the key.")


def test_the_container_does_not_run_as_root():
    """It never needed to: port 8000 is unprivileged and it writes nothing.

    `PYTHONDONTWRITEBYTECODE` is set, so it does not even drop .pyc files beside
    its own source. Root was the default, not a requirement.
    """
    src = _dockerfile()
    users = re.findall(r"^\s*USER\s+(\S+)", src, re.MULTILINE)
    assert users, "the Dockerfile has no USER instruction, so the app runs as root"
    assert users[-1] != "root", f"the final USER is {users[-1]!r}"

    # After the COPYs, so application files stay root-owned and world-readable:
    # readable by the process, not rewritable by it.
    assert src.index("USER ") > src.rindex("COPY "), (
        "USER appears before the last COPY, which would leave the copied files "
        "owned by the runtime user and writable by the process serving traffic.")


def test_the_base_image_is_pinned_to_something_that_cannot_move():
    """`python:3.12-slim` built a different image on different days.

    A build that started failing could not be told apart from a base image that
    changed underneath it. Pinning makes the bump a commit.
    """
    src = _dockerfile()
    bases = re.findall(r"^\s*FROM\s+(\S+)", src, re.MULTILINE)
    assert bases, "no FROM instruction"
    for base in bases:
        assert base != "python:3.12-slim", (
            "the base image is back to a moving tag")
        assert re.search(r"@sha256:[0-9a-f]{64}$", base) or \
            re.search(r":\d+\.\d+\.\d+", base), (
            f"{base!r} is not pinned to a patch version or a digest")


def test_the_migration_boot_hack_has_not_come_back():
    """PR 47 put `alembic upgrade head &&` in front of uvicorn. Two hours of 502s.

    The migrations belong in `preDeployCommand` (PR 52), a separate container
    that must exit 0 before this one starts, so a failed migration fails the
    DEPLOY rather than the process -- while PR 49's healthcheck keeps the
    previous deployment serving.
    """
    src = _dockerfile()
    cmd = [ln for ln in src.splitlines() if ln.strip().startswith("CMD")]
    assert cmd, "the Dockerfile has no CMD"
    joined = " ".join(cmd)
    assert "alembic" not in joined, (
        f"alembic is back in the container's CMD: {joined!r}. That is PR 47, "
        "which took production down for two hours. It belongs in "
        ".railway/railway.ts's preDeployCommand.")
