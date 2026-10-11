# PINNED, C8 / PR 57. This was `python:3.12-slim`, which is a moving target:
# the same commit built a different base image on different days, so a build
# that started failing could not be told apart from a base image that changed
# underneath it. `3.12.14-slim-bookworm` names both the patch and the Debian
# release. Bumping it is now a commit, which is the point.
FROM python:3.12.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic

# NON-ROOT, C8 / PR 57. The container ran as root, which it never needed: the
# app opens a socket on 8000 (above 1024, so no privileged bind), reads its own
# code, and talks to Postgres. PYTHONDONTWRITEBYTECODE is already set, so it
# does not even write .pyc files beside its source.
#
# Created AFTER the COPYs so the files stay root-owned and world-readable --
# the process can read them and cannot rewrite them, which is the arrangement
# worth having. `alembic upgrade head` runs in this same image as Railway's
# pre-deploy step and needs no write access either; it writes to the database.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
USER appuser

EXPOSE 8000

# Uses the stdlib, because adding curl to a slim image to ask a question the
# image can already ask is how a container grows. Railway does not read this --
# it has its own `healthcheckPath` in .railway/railway.ts (PR 49; railway.json until PR 119), which is the check
# that actually gates traffic. This one is for `docker run` locally, so that a
# container which boots but cannot serve says so there too.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4).status == 200 else 1)"]

# THE BOOT IS ONE THING AGAIN. PR 47 put `alembic upgrade head &&` in front of
# uvicorn so the migrations would run where the database is reachable. Against
# production it did not complete, and the `&&` did exactly what it was written
# to do: the app never started, and every endpoint returned 502 for two hours.
# PR 48 reverted it.
#
# PR 52 put the migrations back, in the right place: `preDeployCommand` in
# .railway/railway.ts (railway.json until PR 119), a separate container that must exit 0 before this one starts.
# A migration that fails now fails the DEPLOY instead of the process, and PR 49's
# healthcheck means the previous deployment keeps serving while it does.
#
# C8 / PR 57 finished it: `auto_create_tables` now defaults to FALSE, so this
# container no longer builds its own schema from the models at boot. The
# migrations are the only thing that writes it, `/health.schema_revision` says
# where they got to, and `/health.builds_own_schema` says nothing else is
# quietly helping.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
