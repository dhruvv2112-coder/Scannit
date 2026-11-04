"""SQLAlchemy ORM models for Scannit platform entities.

Maps 1:1 to the Pydantic models in ``scannit_schemas.models`` and to the
JSON Schemas under ``schemas/v0/``.
"""

import uuid
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at_col, schema_version_col, updated_at_col, uuid_pk


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = uuid_pk()
    schema_version: Mapped[str] = schema_version_col()
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    location_wkt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped = created_at_col()
    updated_at: Mapped = updated_at_col()

    # Relationships
    datasets: Mapped[list["DatasetRow"]] = relationship(back_populates="project", lazy="selectin")
    jobs: Mapped[list["JobRow"]] = relationship(back_populates="project", lazy="selectin")


class DatasetRow(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = uuid_pk()
    schema_version: Mapped[str] = schema_version_col()
    project_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="images")
    upload_status: Mapped[str] = mapped_column(String(32), default="pending")
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped = created_at_col()

    # Relationships
    project: Mapped["ProjectRow"] = relationship(back_populates="datasets")


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    schema_version: Mapped[str] = schema_version_col()
    project_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="CREATED")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Scale / geometry metadata
    scale_status: Mapped[str] = mapped_column(String(32), default="unscaled")
    reference_lengths: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    transform_bounds: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    source_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped = created_at_col()
    updated_at: Mapped = updated_at_col()
    completed_at: Mapped = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    project: Mapped["ProjectRow"] = relationship(back_populates="jobs")
    artifacts: Mapped[list["ArtifactRow"]] = relationship(back_populates="job", lazy="selectin")


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    schema_version: Mapped[str] = schema_version_col()
    job_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    software_version: Mapped[str] = mapped_column(String(64), nullable=False)
    transforms: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    units: Mapped[str] = mapped_column(String(16), default="METRES")
    coordinate_system: Mapped[str] = mapped_column(String(16), default="LOCAL")
    privacy_classification: Mapped[str] = mapped_column(String(16), default="INTERNAL")
    fidelity_warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped = created_at_col()

    # Relationships
    job: Mapped["JobRow"] = relationship(back_populates="artifacts")
