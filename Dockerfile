FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic

EXPOSE 8000
# Migrations run where the database is reachable: inside the deploy.
#
# Running alembic by hand against production failed twice -- once against the
# sqlite URL in a local .env, once on DNS, because Railway's DATABASE_URL names
# a private host that does not resolve from outside their network. That is why
# `main.py` grew a hand-maintained list of ALTER TABLE ... IF NOT EXISTS
# statements and why two migrations never reached production at all.
#
# `sh -c` because the `&&` needs a shell: a failed migration must stop the boot
# rather than start an app against a schema it does not have.
#
# Kept identical to `railway.json`'s startCommand, which overrides this one on
# Railway; they are the same line so `docker run` behaves like the deploy.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
