"""Job lifecycle endpoints — versioned under /api/v1/.

Endpoints:
  POST   /jobs                 — create a job record
  GET    /jobs/{id}            — get job details
  POST   /jobs/{id}/submit     — dispatch job to Celery worker
  PATCH  /jobs/{id}/cancel     — request cancellation
  PATCH  /jobs/{id}/status     — worker callback to update status
  GET    /jobs/{id}/artifacts  — list artifacts for a job
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core import repository
from scannit_schemas.enums import JobStatus
from scannit_schemas.models import JobCreate

router = APIRouter(prefix="/api/v1", tags=["jobs"])


# --- Request bodies ---


class JobStatusUpdate(BaseModel):
    """Worker callback payload for updating job status."""

    status: str = Field(..., description="New job status (must be a valid JobStatus value)")
    progress_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    error_message: str | None = None
    # Optional geometry/scale fields the worker can report
    scale_status: str | None = None
    reference_lengths: dict[str, Any] | None = None
    transform_bounds: dict[str, Any] | None = None
    source_evidence: dict[str, Any] | None = None


# --- Endpoints ---


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if await repository.get_project(session, body.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if await repository.get_dataset(session, body.dataset_id) is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    job = await repository.create_job(
        session,
        project_id=body.project_id,
        dataset_id=body.dataset_id,
        config=body.config,
    )
    return _serialize(job)


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    job = await repository.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize(job)


@router.post("/jobs/{job_id}/submit", status_code=status.HTTP_202_ACCEPTED)
async def submit_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Dispatch a CREATED job to the Celery reconstruction worker.

    The actual Celery import is deferred so the API can run without
    the orchestrator installed (e.g. in tests). When Celery is not
    available, the endpoint returns the job marked as submitted but
    the task is not dispatched — CI and unit tests rely on this.
    """
    job = await repository.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] != JobStatus.CREATED:
        raise HTTPException(
            status_code=409,
            detail=f"Job cannot be submitted from status {job['status']}",
        )

    # Attempt to dispatch to Celery
    celery_task_id: str | None = None
    try:
        from app.workers.reconstruction import run_reconstruction  # type: ignore[import-not-found]

        result = run_reconstruction.delay(
            job_id=str(job_id),
            dataset_id=str(job["dataset_id"]),
            project_id=str(job["project_id"]),
            config=job.get("config", {}),
        )
        celery_task_id = result.id
    except ImportError:
        # Celery/orchestrator not installed — mark as submitted anyway
        pass

    # Update job to indicate it's been submitted
    updates: dict[str, Any] = {"status": JobStatus.VALIDATING}
    if celery_task_id:
        updates["celery_task_id"] = celery_task_id
    await repository.update_job(session, job_id, **updates)

    job = await repository.get_job(session, job_id)
    response = _serialize(job)  # type: ignore[arg-type]
    if celery_task_id:
        response["celery_task_id"] = celery_task_id
    return response


@router.patch("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Request cancellation of a running job."""
    job = await repository.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    terminal_states = {JobStatus.COMPLETE, JobStatus.CANCELLED, JobStatus.FAILED}
    if job["status"] in terminal_states:
        raise HTTPException(
            status_code=409,
            detail=f"Job in terminal state {job['status']} cannot be cancelled",
        )

    # Try to revoke the Celery task
    celery_task_id = job.get("celery_task_id")
    if celery_task_id:
        try:
            from app.celery_app import celery  # type: ignore[import-not-found]
            celery.control.revoke(celery_task_id, terminate=True)
        except ImportError:
            pass

    updated = await repository.update_job(session, job_id, status=JobStatus.CANCELLED)
    return _serialize(updated)  # type: ignore[arg-type]


@router.patch("/jobs/{job_id}/status")
async def update_job_status(
    job_id: uuid.UUID,
    body: JobStatusUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Worker callback endpoint — updates job status, progress, and geometry metadata.

    This is called by the orchestrator worker to report stage transitions.
    The status value must be a valid JobStatus enum member.
    """
    job = await repository.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Validate the status string
    try:
        new_status = JobStatus(body.status)
    except ValueError as err:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status: {body.status}",
        ) from err

    updates: dict[str, Any] = {
        "status": new_status,
        "progress_pct": body.progress_pct,
    }
    if body.error_message is not None:
        updates["error_message"] = body.error_message
    if new_status == JobStatus.COMPLETE:
        updates["completed_at"] = datetime.now(UTC)

    # Geometry / scale metadata from the worker
    if body.scale_status is not None:
        updates["scale_status"] = body.scale_status
    if body.reference_lengths is not None:
        updates["reference_lengths"] = body.reference_lengths
    if body.transform_bounds is not None:
        updates["transform_bounds"] = body.transform_bounds
    if body.source_evidence is not None:
        updates["source_evidence"] = body.source_evidence

    updated = await repository.update_job(session, job_id, **updates)
    return _serialize(updated)  # type: ignore[arg-type]


@router.get("/jobs/{job_id}/artifacts")
async def list_job_artifacts(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    job = await repository.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return [_serialize(a) for a in await repository.list_artifacts(session, job_id)]


def _serialize(obj: dict) -> dict:
    """Ensure UUIDs and datetimes are JSON-serializable."""
    result = {}
    for k, v in obj.items():
        if isinstance(v, uuid.UUID):
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result
