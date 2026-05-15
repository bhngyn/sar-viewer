# syntax=docker/dockerfile:1.7
# Phase 0: worker is a placeholder. Celery wiring lands in Phase 1 (Agent C).
FROM python:3.11-slim

RUN useradd --create-home --uid 10002 appuser
USER appuser

WORKDIR /app

CMD ["python", "-c", "print('worker ok'); import time; time.sleep(3)"]
