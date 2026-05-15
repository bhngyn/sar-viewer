"""Celery application factory for sar-viewer.

Broker and result-backend are both Redis. The Redis URL is read from the
REDIS_URL environment variable (defaulting to the docker-compose service name
so the worker finds Redis inside the Docker network).

Task autodiscovery finds all tasks registered in ``services.worker.src.tasks``.

Import side-effects are minimal: nothing at module level performs IO or
network calls, so the module is safe to import in tests that set
``CELERY_TASK_ALWAYS_EAGER = True`` (or Celery 5.x equivalent).
"""

from __future__ import annotations

import os

from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

app = Celery(
    "sar_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["services.worker.src.tasks"],
)

app.conf.update(
    # Serialise with JSON so task args are human-readable in Redis.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # UTC everywhere.
    enable_utc=True,
    timezone="UTC",
    # Acknowledgement only after the task finishes, so a worker crash
    # re-queues the task rather than losing it.
    task_acks_late=True,
    # Prefetch one task at a time; SAR processing tasks are heavy.
    worker_prefetch_multiplier=1,
)
