"""ORM models package — import tables to register them with SQLAlchemy metadata."""

from app.models.tables import ArtifactRow, DatasetRow, JobRow, ProjectRow

__all__ = ["ArtifactRow", "DatasetRow", "JobRow", "ProjectRow"]