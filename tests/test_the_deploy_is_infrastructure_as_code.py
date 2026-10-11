"""PR 119: the deploy lives in `.railway/railway.ts` and keeps every setting.

Railway retires Config as Code (railway.json) on 2026-12-01. `railway config
migrate` emitted start / healthcheck / preDeploy only and left the builder as a
comment, while the service's own builder setting is RAILPACK -- so a straight
migrate would have built the app WITHOUT our Dockerfile (non-root user, no
secrets in the image) and silently dropped the restart policy and the
pre-deploy timeout. This pins what the migration had to keep.
"""
from tests._deploy_config import REPO, build, deploy


def test_the_dockerfile_builds_the_app():
    b = build()
    assert b.get("builder") == "DOCKERFILE", b
    assert b.get("dockerfilePath") == "Dockerfile" and (REPO / "Dockerfile").exists()


def test_a_crash_restarts_and_migrations_get_their_time():
    """ON_FAILURE is Railway's default and its API stores the default as null,
    so the file states only the retry count (a declared type is a permanent
    `plan` diff). It must never be set to anything else."""
    d = deploy()
    assert d.get("restartPolicyType") in (None, "ON_FAILURE") and d.get("restartPolicyMaxRetries") == 3, d
    assert d.get("preDeployTimeoutSeconds") == 300, d


def test_there_is_one_deploy_definition():
    """railway.json would be read as well, and two definitions drift."""
    assert not (REPO / "railway.json").exists() and not (REPO / "railway.toml").exists()
