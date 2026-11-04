"""Artifact endpoints — versioned under /api/v1/.

Endpoints:
  GET    /artifacts/{id}           — get artifact details
  GET    /artifacts/{id}/manifest  — full artifact manifest with download URL
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core import repository
from app.core.s3 import generate_presigned_download_url

router = APIRouter(prefix="/api/v1", tags=["artifacts"])


class ArtifactManifestResponse(BaseModel):
    """Full artifact manifest including a time-limited download URL."""
    id: str
    job_id: str
    stage: str
    content_hash: str
    software_version: str
    transforms: dict
    units: str
    coordinate_system: str
    privacy_classification: str
    fidelity_warnings: list[str]
    s3_key: str
    byte_size: int
    download_url: str = Field(..., description="Pre-signed GET URL, valid for 1 hour")
    created_at: str


@router.get("/artifacts/{artifact_id}")
async def get_artifact(
    artifact_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    artifact = await repository.get_artifact(session, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return _serialize(artifact)


@router.get("/artifacts/{artifact_id}/manifest")
async def get_artifact_manifest(
    artifact_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ArtifactManifestResponse:
    """Return the full artifact manifest with a pre-signed download URL.

    This is the primary endpoint the XR viewer uses to fetch reconstructed
    GLB assets. The download URL is valid for 1 hour.
    """
    artifact = await repository.get_artifact(session, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    download_url = generate_presigned_download_url(
        key=artifact["s3_key"],
        expires_in=3600,
    )

    serialized = _serialize(artifact)
    serialized["download_url"] = download_url

    return ArtifactManifestResponse(**serialized)


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
