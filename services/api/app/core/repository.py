"""Async repository — all database operations for the API layer.

Each function takes an ``AsyncSession`` (injected by FastAPI's dependency
system) and returns Pydantic-compatible dicts so the existing router
serialization logic keeps working without changes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import ArtifactRow, DatasetRow, JobRow, ProjectRow


def _to_dict(row) -> dict[str, Any]:
    """Convert an ORM row to a plain dict for JSON serialization."""
    return {c.key: getattr(row, c.key) for c in row.__table__.columns}


# ── Projects ────────────────────────────────────────────────────────────


async def create_project(
    session: AsyncSession,
    name: str,
    description: str = "",
    location_wkt: str | None = None,
) -> dict[str, Any]:
    row = ProjectRow(name=name, description=description, location_wkt=location_wkt)
    session.add(row)
    await session.flush()
    return _to_dict(row)


async def get_project(session: AsyncSession, project_id: uuid.UUID) -> dict[str, Any] | None:
    row = await session.get(ProjectRow, project_id)
    return _to_dict(row) if row else None


async def list_projects(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(select(ProjectRow).order_by(ProjectRow.created_at.desc()))
    return [_to_dict(r) for r in result.scalars().all()]


# ── Datasets ────────────────────────────────────────────────────────────


async def create_dataset(
    session: AsyncSession,
    project_id: uuid.UUID,
    name: str,
    source_type: str = "images",
) -> dict[str, Any]:
    row = DatasetRow(project_id=project_id, name=name, source_type=source_type)
    session.add(row)
    await session.flush()
    return _to_dict(row)


async def get_dataset(session: AsyncSession, dataset_id: uuid.UUID) -> dict[str, Any] | None:
    row = await session.get(DatasetRow, dataset_id)
    return _to_dict(row) if row else None


async def list_datasets(session: AsyncSession, project_id: uuid.UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        select(DatasetRow).where(DatasetRow.project_id == project_id)
    )
    return [_to_dict(r) for r in result.scalars().all()]


async def update_dataset(
    session: AsyncSession, dataset_id: uuid.UUID, **updates: Any
) -> dict[str, Any] | None:
    row = await session.get(DatasetRow, dataset_id)
    if row is None:
        return None
    for k, v in updates.items():
        setattr(row, k, v)
    await session.flush()
    return _to_dict(row)


# ── Jobs ────────────────────────────────────────────────────────────────


async def create_job(
    session: AsyncSession,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = JobRow(
        project_id=project_id,
        dataset_id=dataset_id,
        config=config or {},
    )
    session.add(row)
    await session.flush()
    return _to_dict(row)


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> dict[str, Any] | None:
    row = await session.get(JobRow, job_id)
    return _to_dict(row) if row else None


async def update_job(
    session: AsyncSession, job_id: uuid.UUID, **updates: Any
) -> dict[str, Any] | None:
    row = await session.get(JobRow, job_id)
    if row is None:
        return None
    updates["updated_at"] = datetime.now(UTC)
    for k, v in updates.items():
        setattr(row, k, v)
    await session.flush()
    return _to_dict(row)


async def list_jobs(session: AsyncSession, project_id: uuid.UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        select(JobRow).where(JobRow.project_id == project_id).order_by(JobRow.created_at.desc())
    )
    return [_to_dict(r) for r in result.scalars().all()]


# ── Artifacts ───────────────────────────────────────────────────────────


async def create_artifact(
    session: AsyncSession, job_id: uuid.UUID, **fields: Any
) -> dict[str, Any]:
    row = ArtifactRow(job_id=job_id, **fields)
    session.add(row)
    await session.flush()
    return _to_dict(row)


async def get_artifact(session: AsyncSession, artifact_id: uuid.UUID) -> dict[str, Any] | None:
    row = await session.get(ArtifactRow, artifact_id)
    return _to_dict(row) if row else None


async def list_artifacts(session: AsyncSession, job_id: uuid.UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        select(ArtifactRow).where(ArtifactRow.job_id == job_id)
    )
    return [_to_dict(r) for r in result.scalars().all()]
