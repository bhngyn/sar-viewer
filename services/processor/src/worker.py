"""Processor worker entry point (Phase 1 stub).

In Phase 1 the full Celery wiring is owned by Agent C (services/api/jobs.py
and shared/storage.py).  This module is the runnable entry point for the
processor container; it will be wired to the Celery task queue in Phase 1
Agent C's work.

For now it just logs a startup banner so ``docker compose up`` shows a healthy
processor container rather than an immediate exit.
"""

from __future__ import annotations

import sys
import time

import structlog

logger = structlog.get_logger(__name__)


def main() -> None:
    logger.info(
        "processor_worker_start",
        status="waiting_for_celery_wiring",
        note="Celery task queue connection is implemented by Agent C (phase-1/jobs).",
    )
    # Phase 1 placeholder — keep the container alive for docker-compose.
    # Agent C will replace this with Celery worker startup.
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("processor_worker_shutdown")
        sys.exit(0)


if __name__ == "__main__":
    main()
