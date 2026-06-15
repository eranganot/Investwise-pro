"""Celery application (Section AD).

Uses Redis as broker/result backend when REDIS_URL is set; otherwise runs in
EAGER mode (synchronous, in-process) so the app works with no worker - heavy
jobs still execute, just inline. Point REDIS_URL at the Railway Redis service and
run a worker to offload them off the request thread.
"""
from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

_settings = get_settings()
_broker = _settings.redis_url or "memory://"
_backend = _settings.redis_url or "cache+memory://"

celery_app = Celery("investwise", broker=_broker, backend=_backend)
celery_app.conf.update(
    task_always_eager=not bool(_settings.redis_url),
    task_eager_propagates=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
)

import app.worker.tasks  # noqa: E402,F401  (register tasks)
