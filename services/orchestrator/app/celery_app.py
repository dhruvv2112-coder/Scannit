"""Celery application for the Scannit orchestrator.

Decision: Celery over RQ — mature, battle-tested for long-running
reconstruction jobs, supports task chaining / groups / chords, and has
first-class Redis broker support.
"""

from celery import Celery

celery = Celery(
    "scannit-orchestrator",
    broker="redis://redis:6379/0",
    backend="redis://redis:6379/1",
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # one job at a time per worker
)
