"""Shared Pydantic v2 models — the data contract between all Scannit services.

Every model carries a `schema_version` field so consumers can detect
breaking changes across service boundaries.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .enums import (
    CoordinateSystem,
    JobStatus,
    PrivacyClassification,
    Units,
)

SCHEMA_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class ScannitBase(BaseModel):
    """Base model with schema version and standard fields."""

    schema_version: str = Field(default=SCHEMA_VERSION, description="Data contract version")

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class ProjectCreate(ScannitBase):
    name: str = Field(..., min_length=1, max_length=256)
    description: str = Field(default="")
    location_wkt: str | None = Field(
        default=None,
        description="WKT geometry for the project area (POINT or POLYGON, SRID 4326)",
    )


class Project(ScannitBase):
    id: uuid.UUID
    name: str
    description: str
    location_wkt: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


class DatasetCreate(ScannitBase):
    project_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=256)
    source_type: str = Field(default="images", description="images | video | lidar")


class Dataset(ScannitBase):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    source_type: str
    upload_status: str = "pending"  # pending | uploading | complete | failed
    file_count: int = 0
    total_bytes: int = 0
    created_at: datetime


class PresignedUploadResponse(ScannitBase):
    dataset_id: uuid.UUID
    upload_url: str = Field(..., description="Pre-signed S3 PUT URL")
    fields: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


class JobCreate(ScannitBase):
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    config: dict[str, Any] = Field(default_factory=dict, description="Pipeline configuration overrides")


class Job(ScannitBase):
    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    status: JobStatus = JobStatus.CREATED
    config: dict[str, Any] = Field(default_factory=dict)
    progress_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    error_message: str | None = None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Artifact (immutable, content-addressed pipeline output)
# ---------------------------------------------------------------------------


class ArtifactManifest(ScannitBase):
    id: uuid.UUID
    job_id: uuid.UUID
    stage: JobStatus = Field(..., description="Pipeline stage that produced this artifact")
    content_hash: str = Field(..., description="SHA-256 of the artifact content")
    software_version: str = Field(..., description="Version of the producing software")
    transforms: dict[str, Any] = Field(
        default_factory=dict,
        description="Coordinate transforms applied",
    )
    units: Units = Units.METRES
    coordinate_system: CoordinateSystem = CoordinateSystem.LOCAL
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    fidelity_warnings: list[str] = Field(default_factory=list)
    s3_key: str = Field(..., description="Object storage key")
    byte_size: int = Field(..., ge=0)
    created_at: datetime


# ---------------------------------------------------------------------------
# Asset (catalog entry for a deliverable resource)
# ---------------------------------------------------------------------------


class Asset(ScannitBase):
    id: uuid.UUID
    project_id: uuid.UUID
    artifact_id: uuid.UUID | None = None
    asset_type: str = Field(..., description="glb | texture | pointcloud | mesh | material | metadata")
    name: str
    s3_key: str
    byte_size: int = Field(..., ge=0)
    mime_type: str = "application/octet-stream"
    created_at: datetime


# ---------------------------------------------------------------------------
# Tile (spatial subdivision of a scene)
# ---------------------------------------------------------------------------


class Tile(ScannitBase):
    id: uuid.UUID
    project_id: uuid.UUID
    scene_version: int = Field(..., ge=1)
    lod_level: int = Field(..., ge=0)
    bbox_wkt: str = Field(..., description="WKT POLYGON bounding box")
    asset_id: uuid.UUID
    vertex_count: int = Field(default=0, ge=0)
    created_at: datetime


# ---------------------------------------------------------------------------
# Camera Pose
# ---------------------------------------------------------------------------


class CameraPose(ScannitBase):
    id: uuid.UUID
    dataset_id: uuid.UUID
    image_filename: str
    position: list[float] = Field(..., min_length=3, max_length=3, description="[x, y, z]")
    rotation: list[float] = Field(..., min_length=4, max_length=4, description="Quaternion [w, x, y, z]")
    intrinsics: dict[str, float] = Field(
        default_factory=dict,
        description="Camera intrinsics: fx, fy, cx, cy, k1, k2, ...",
    )
    units: Units = Units.METRES
    coordinate_system: CoordinateSystem = CoordinateSystem.LOCAL


# ---------------------------------------------------------------------------
# Observation (inspection note pinned to a 3D location)
# ---------------------------------------------------------------------------


class ObservationCreate(ScannitBase):
    project_id: uuid.UUID
    position: list[float] = Field(..., min_length=3, max_length=3)
    category: str = Field(default="general")
    description: str = Field(default="")
    severity: str | None = None
    evidence_image_ids: list[uuid.UUID] = Field(default_factory=list)


class Observation(ScannitBase):
    id: uuid.UUID
    project_id: uuid.UUID
    position: list[float]
    category: str
    description: str
    severity: str | None = None
    evidence_image_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ExportRequest(ScannitBase):
    project_id: uuid.UUID
    format: str = Field(default="glb", description="glb | obj | las | e57 | ortho")
    include_textures: bool = True
    include_metadata: bool = True
    bbox_wkt: str | None = Field(default=None, description="Optional spatial filter")


class Export(ScannitBase):
    id: uuid.UUID
    project_id: uuid.UUID
    format: str
    status: str = "pending"  # pending | generating | complete | failed
    download_url: str | None = None
    byte_size: int | None = None
    created_at: datetime
    completed_at: datetime | None = None
