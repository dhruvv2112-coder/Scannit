"""In-memory storage backend for development and testing.

Provides a thin interface that can be swapped for SQLAlchemy + PostgreSQL
once the database layer is wired up via Docker Compose. Each entity uses
a simple dict keyed by UUID.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


class InMemoryStore:
    """Thread-safe-enough store for single-process dev/test."""

    def __init__(self) -> None:
        self.projects: dict[uuid.UUID, dict[str, Any]] = {}
        self.datasets: dict[uuid.UUID, dict[str, Any]] = {}
        self.jobs: dict[uuid.UUID, dict[str, Any]] = {}
        self.artifacts: dict[uuid.UUID, dict[str, Any]] = {}

    # --- Projects ---

    def create_project(self, name: str, description: str = "", location_wkt: str | None = None) -> dict[str, Any]:
        pid = uuid.uuid4()
        now = _now()
        project = {
            "id": pid,
            "name": name,
            "description": description,
            "location_wkt": location_wkt,
            "created_at": now,
            "updated_at": now,
        }
        self.projects[pid] = project
        return project

    def get_project(self, project_id: uuid.UUID) -> dict[str, Any] | None:
        return self.projects.get(project_id)

    def list_projects(self) -> list[dict[str, Any]]:
        return list(self.projects.values())

    # --- Datasets ---

    def create_dataset(
        self,
        project_id: uuid.UUID,
        name: str,
        source_type: str = "images",
    ) -> dict[str, Any] | None:
        if project_id not in self.projects:
            return None
        did = uuid.uuid4()
        now = _now()
        dataset = {
            "id": did,
            "project_id": project_id,
            "name": name,
            "source_type": source_type,
            "upload_status": "pending",
            "file_count": 0,
            "total_bytes": 0,
            "created_at": now,
        }
        self.datasets[did] = dataset
        return dataset

    def get_dataset(self, dataset_id: uuid.UUID) -> dict[str, Any] | None:
        return self.datasets.get(dataset_id)

    def list_datasets(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        return [d for d in self.datasets.values() if d["project_id"] == project_id]

    # --- Jobs ---

    def create_job(
        self,
        project_id: uuid.UUID,
        dataset_id: uuid.UUID,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if project_id not in self.projects:
            return None
        if dataset_id not in self.datasets:
            return None
        jid = uuid.uuid4()
        now = _now()
        job = {
            "id": jid,
            "project_id": project_id,
            "dataset_id": dataset_id,
            "status": "CREATED",
            "config": config or {},
            "progress_pct": 0.0,
            "error_message": None,
            "retry_count": 0,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        self.jobs[jid] = job
        return job

    def get_job(self, job_id: uuid.UUID) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    def update_job(self, job_id: uuid.UUID, **updates: Any) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job.update(updates)
        job["updated_at"] = _now()
        return job

    def list_jobs(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        return [j for j in self.jobs.values() if j["project_id"] == project_id]

    # --- Artifacts ---

    def create_artifact(self, job_id: uuid.UUID, **fields: Any) -> dict[str, Any] | None:
        if job_id not in self.jobs:
            return None
        aid = uuid.uuid4()
        artifact = {"id": aid, "job_id": job_id, "created_at": _now(), **fields}
        self.artifacts[aid] = artifact
        return artifact

    def list_artifacts(self, job_id: uuid.UUID) -> list[dict[str, Any]]:
        return [a for a in self.artifacts.values() if a["job_id"] == job_id]


# Singleton for the application
store = InMemoryStore()
