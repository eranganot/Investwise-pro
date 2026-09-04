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
# REVERTED 2026-09-04. PR 47 put `alembic upgrade head &&` in front of uvicorn so
# the migrations would run where the database is reachable. Against production it
# did not complete, and the `&&` did exactly what it was written to do: the app
# never started. Every endpoint returned 502 for two hours.
#
# The migration ran clean twice against a production-shaped postgres:16 in the
# sandbox -- create_all-built, never stamped -- so the sandbox is not a faithful
# model of that database and the difference is still unidentified. It goes back
# only once the deploy log says why.
#
# Keeping the boot simple is not the same as fixing it: production's schema is
# still maintained by create_all plus the ALTER TABLE list in main.py, which is
# the problem PR 46 and PR 47 exist to close.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
