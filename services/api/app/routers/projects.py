"""Project + Dataset endpoints — versioned under /api/v1/.

Endpoints:
  POST   /projects                            — create a project
  GET    /projects                            — list all projects
  GET    /projects/{id}                       — get project details
  POST   /projects/{id}/datasets              — create a dataset
  GET    /projects/{id}/datasets              — list datasets
  GET    /datasets/{id}                       — get dataset details
  POST   /datasets/{id}/upload-url            — presigned upload URL
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core import repository
from app.core.s3 import generate_presigned_upload_url
from app.core.config import settings

router = APIRouter(prefix="/api/v1", tags=["projects"])


# --- Request / Response bodies ---


class UploadUrlRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=512)
    content_type: str = Field(default="application/octet-stream")


class UploadUrlResponse(BaseModel):
    dataset_id: uuid.UUID
    upload_url: str
    s3_key: str
    expires_at: datetime


# --- Projects ---


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: dict,  # accept flexible body; ProjectCreate validated upstream
    session: AsyncSession = Depends(get_session),
) -> dict:
    from scannit_schemas.models import ProjectCreate
    data = ProjectCreate(**body)
    project = await repository.create_project(
        session,
        name=data.name,
        description=data.description,
        location_wkt=data.location_wkt,
    )
    return _serialize(project)


@router.get("/projects")
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[dict]:
    return [_serialize(p) for p in await repository.list_projects(session)]


@router.get("/projects/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await repository.get_project(session, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _serialize(project)


# --- Datasets ---


@router.post("/projects/{project_id}/datasets", status_code=status.HTTP_201_CREATED)
async def create_dataset(
    project_id: uuid.UUID,
    body: dict,
    session: AsyncSession = Depends(get_session),
) -> dict:
    from scannit_schemas.models import DatasetCreate
    data = DatasetCreate(**body)
    if await repository.get_project(session, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    dataset = await repository.create_dataset(
        session,
        project_id=project_id,
        name=data.name,
        source_type=data.source_type,
    )
    return _serialize(dataset)


@router.get("/projects/{project_id}/datasets")
async def list_datasets(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    if await repository.get_project(session, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return [_serialize(d) for d in await repository.list_datasets(session, project_id)]


@router.get("/datasets/{dataset_id}")
async def get_dataset(
    dataset_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    dataset = await repository.get_dataset(session, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return _serialize(dataset)


# --- Presigned upload URL ---


@router.post("/datasets/{dataset_id}/upload-url", status_code=status.HTTP_201_CREATED)
async def create_upload_url(
    dataset_id: uuid.UUID,
    body: UploadUrlRequest,
    session: AsyncSession = Depends(get_session),
) -> UploadUrlResponse:
    """Generate a presigned S3 PUT URL for uploading a file to a dataset."""
    dataset = await repository.get_dataset(session, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    s3_key = f"datasets/{dataset_id}/{body.filename}"
    expires_in = 3600  # 1 hour

    upload_url = generate_presigned_upload_url(
        key=s3_key,
        content_type=body.content_type,
        expires_in=expires_in,
    )

    return UploadUrlResponse(
        dataset_id=dataset_id,
        upload_url=upload_url,
        s3_key=s3_key,
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
    )


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
