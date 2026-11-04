"""Project + Dataset endpoints — versioned under /api/v1/."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.core.storage import store
from scannit_schemas.models import (
    DatasetCreate,
    ProjectCreate,
)

router = APIRouter(prefix="/api/v1", tags=["projects"])


# --- Projects ---


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate) -> dict:
    project = store.create_project(
        name=body.name,
        description=body.description,
        location_wkt=body.location_wkt,
    )
    return _serialize(project)


@router.get("/projects")
async def list_projects() -> list[dict]:
    return [_serialize(p) for p in store.list_projects()]


@router.get("/projects/{project_id}")
async def get_project(project_id: uuid.UUID) -> dict:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _serialize(project)


# --- Datasets ---


@router.post("/projects/{project_id}/datasets", status_code=status.HTTP_201_CREATED)
async def create_dataset(project_id: uuid.UUID, body: DatasetCreate) -> dict:
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    dataset = store.create_dataset(
        project_id=project_id,
        name=body.name,
        source_type=body.source_type,
    )
    if dataset is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _serialize(dataset)


@router.get("/projects/{project_id}/datasets")
async def list_datasets(project_id: uuid.UUID) -> list[dict]:
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return [_serialize(d) for d in store.list_datasets(project_id)]


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: uuid.UUID) -> dict:
    dataset = store.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return _serialize(dataset)


def _serialize(obj: dict) -> dict:
    """Ensure UUIDs and datetimes are JSON-serializable."""
    result = {}
    for k, v in obj.items():
        if isinstance(v, uuid.UUID):
            result[k] = str(v)
        else:
            result[k] = v
    return result
