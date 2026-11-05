"""Schema conformance tests for shared Pydantic v2 models.

Verifies:
- All models are importable
- Round-trip JSON serialisation
- Schema version is present on every model
- Enum values match the documented state machine
- Invalid data is rejected
"""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from scannit_schemas import (
    PIPELINE_STAGES,
    ArtifactManifest,
    Asset,
    CameraPose,
    Dataset,
    DatasetCreate,
    Export,
    ExportRequest,
    Job,
    JobCreate,
    JobStatus,
    Observation,
    ObservationCreate,
    PresignedUploadResponse,
    Project,
    ProjectCreate,
    Tile,
)
from scannit_schemas.models import SCHEMA_VERSION

NOW = datetime.now(tz=UTC)
PROJECT_ID = uuid.uuid4()
DATASET_ID = uuid.uuid4()
JOB_ID = uuid.uuid4()
ARTIFACT_ID = uuid.uuid4()
ASSET_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _round_trip(model_cls, data: dict) -> dict:
    """Serialise to JSON and back; return the re-parsed dict."""
    obj = model_cls.model_validate(data)
    json_str = obj.model_dump_json()
    return model_cls.model_validate_json(json_str).model_dump()


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

ALL_MODELS = [
    ProjectCreate,
    Project,
    DatasetCreate,
    Dataset,
    PresignedUploadResponse,
    JobCreate,
    Job,
    ArtifactManifest,
    Asset,
    Tile,
    CameraPose,
    ObservationCreate,
    Observation,
    ExportRequest,
    Export,
]


@pytest.mark.parametrize("model_cls", ALL_MODELS, ids=lambda c: c.__name__)
def test_schema_version_present(model_cls):
    """Every model must carry a schema_version field with a default."""
    fields = model_cls.model_fields
    assert "schema_version" in fields
    assert fields["schema_version"].default == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


def test_project_round_trip():
    data = {
        "id": PROJECT_ID,
        "name": "Test Site",
        "description": "A test project",
        "created_at": NOW,
        "updated_at": NOW,
    }
    result = _round_trip(Project, data)
    assert result["name"] == "Test Site"
    assert result["schema_version"] == SCHEMA_VERSION


def test_dataset_round_trip():
    data = {
        "id": DATASET_ID,
        "project_id": PROJECT_ID,
        "name": "Flight 001",
        "source_type": "images",
        "created_at": NOW,
    }
    result = _round_trip(Dataset, data)
    assert result["source_type"] == "images"


def test_job_round_trip():
    data = {
        "id": JOB_ID,
        "project_id": PROJECT_ID,
        "dataset_id": DATASET_ID,
        "status": JobStatus.CREATED,
        "created_at": NOW,
        "updated_at": NOW,
    }
    result = _round_trip(Job, data)
    assert result["status"] == "CREATED"
    assert result["progress_pct"] == 0.0


def test_artifact_manifest_round_trip():
    data = {
        "id": ARTIFACT_ID,
        "job_id": JOB_ID,
        "stage": JobStatus.MESH,
        "content_hash": "sha256:abc123",
        "software_version": "0.1.0",
        "s3_key": "artifacts/abc123.glb",
        "byte_size": 1024,
        "created_at": NOW,
    }
    result = _round_trip(ArtifactManifest, data)
    assert result["content_hash"] == "sha256:abc123"
    assert result["units"] == "METRES"


def test_camera_pose_round_trip():
    data = {
        "id": uuid.uuid4(),
        "dataset_id": DATASET_ID,
        "image_filename": "IMG_0001.jpg",
        "position": [1.0, 2.0, 3.0],
        "rotation": [1.0, 0.0, 0.0, 0.0],
    }
    result = _round_trip(CameraPose, data)
    assert result["position"] == [1.0, 2.0, 3.0]


def test_observation_round_trip():
    data = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "position": [10.0, 20.0, 5.0],
        "category": "crack",
        "description": "Hairline crack on north wall",
        "created_at": NOW,
        "updated_at": NOW,
    }
    result = _round_trip(Observation, data)
    assert result["category"] == "crack"


def test_export_round_trip():
    data = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "format": "glb",
        "created_at": NOW,
    }
    result = _round_trip(Export, data)
    assert result["format"] == "glb"
    assert result["status"] == "pending"


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def test_pipeline_stages_order():
    """Pipeline stages must start at CREATED and end at COMPLETE."""
    assert PIPELINE_STAGES[0] == JobStatus.CREATED
    assert PIPELINE_STAGES[-1] == JobStatus.COMPLETE
    assert len(PIPELINE_STAGES) == 11


def test_all_pipeline_statuses_are_valid():
    """Every pipeline stage must be a valid JobStatus member."""
    all_statuses = set(JobStatus)
    for stage in PIPELINE_STAGES:
        assert stage in all_statuses


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_project_create_rejects_empty_name():
    with pytest.raises(ValidationError):
        ProjectCreate(name="")


def test_job_progress_clamped():
    with pytest.raises(ValidationError):
        Job(
            id=JOB_ID,
            project_id=PROJECT_ID,
            dataset_id=DATASET_ID,
            progress_pct=101.0,
            created_at=NOW,
            updated_at=NOW,
        )


def test_camera_pose_rejects_wrong_length():
    with pytest.raises(ValidationError):
        CameraPose(
            id=uuid.uuid4(),
            dataset_id=DATASET_ID,
            image_filename="test.jpg",
            position=[1.0, 2.0],  # needs 3
            rotation=[1.0, 0.0, 0.0, 0.0],
        )
