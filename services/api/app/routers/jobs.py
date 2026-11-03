"""Job lifecycle endpoints — versioned under /api/v1/."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from scannit_schemas.models import JobCreate

from app.core.storage import store

router = APIRouter(prefix="/api/v1", tags=["jobs"])


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
async def create_job(body: JobCreate) -> dict:
    if store.get_project(body.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if store.get_dataset(body.dataset_id) is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    job = store.create_job(
        project_id=body.project_id,
        dataset_id=body.dataset_id,
        config=body.config,
    )
    if job is None:
        raise HTTPException(status_code=500, detail="Job creation failed")
    return _serialize(job)


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID) -> dict:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize(job)


@router.get("/jobs/{job_id}/artifacts")
async def list_job_artifacts(job_id: uuid.UUID) -> list[dict]:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return [_serialize(a) for a in store.list_artifacts(job_id)]


def _serialize(obj: dict) -> dict:
    """Ensure UUIDs and datetimes are JSON-serializable."""
    result = {}
    for k, v in obj.items():
        if isinstance(v, uuid.UUID):
            result[k] = str(v)
        else:
            result[k] = v
    return result
