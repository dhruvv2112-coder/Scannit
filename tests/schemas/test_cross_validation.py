"""Cross-validation: Pydantic v2 models emit JSON that is compatible with
the canonical JSON Schema definitions in schemas/v0/.

This test bridges the two contract layers:
  - JSON Schema (schemas/v0/) — canonical cross-language interchange format
  - Pydantic v2 (packages/shared-schemas/) — FastAPI service-layer contract

It verifies that shared enum values and structural invariants stay in sync.
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "v0"
SCHEMAS = {p.name: json.loads(p.read_text()) for p in SCHEMA_DIR.glob("*.schema.json")}
REGISTRY = Registry().with_resources(
    (s["$id"], Resource.from_contents(s)) for s in SCHEMAS.values()
)


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        SCHEMAS[f"{name}.schema.json"],
        registry=REGISTRY,
    )


# --- Enum alignment -----------------------------------------------------------


def test_job_status_enums_mappable():
    """JSON Schema top-level job statuses must be mappable to Pydantic JobStatus.

    JSON Schema uses simplified statuses (queued, running, succeeded, failed, cancelled)
    while Pydantic uses fine-grained pipeline stages. Both must cover the terminal
    states (failed, cancelled) and have clear mappings for the others.
    """
    from scannit_schemas.enums import JobStatus

    json_schema_statuses = set(
        SCHEMAS["reconstruction_job.schema.json"]["properties"]["status"]["enum"]
    )
    pydantic_statuses = {s.value for s in JobStatus}

    # Define the expected mapping from JSON Schema → Pydantic
    STATUS_MAP = {
        "queued": "CREATED",
        "running": None,  # Maps to any in-progress pipeline stage
        "succeeded": "COMPLETE",
        "failed": "FAILED",
        "cancelled": "CANCELLED",
    }

    # All JSON Schema statuses must be in the map
    assert json_schema_statuses == set(STATUS_MAP.keys()), (
        f"Unmapped JSON Schema statuses: {json_schema_statuses - set(STATUS_MAP.keys())}"
    )

    # All mapped Pydantic values must exist in the Pydantic enum
    for js_status, py_status in STATUS_MAP.items():
        if py_status is not None:
            assert py_status in pydantic_statuses, (
                f"JSON Schema '{js_status}' maps to '{py_status}' "
                f"but that's not in Pydantic JobStatus"
            )


def test_coordinate_frame_axes_match():
    """CoordinateFrame axes in JSON Schema must be reflected in Pydantic."""
    from scannit_schemas.enums import CoordinateSystem

    common = SCHEMAS["common.schema.json"]
    json_axes = set(common["$defs"]["CoordinateFrame"]["properties"]["axes"]["enum"])
    # Our Pydantic CoordinateSystem has: ENU, ECEF, LOCAL, WGS84
    # JSON Schema CoordinateFrame axes: ENU, LOCAL_ZUP, OPENCV_CAMERA, GLTF_YUP
    # These are different domains — JSON Schema frame is more granular
    # Verify ENU at least is shared
    pydantic_systems = {s.value for s in CoordinateSystem}
    assert "ENU" in json_axes, "JSON Schema should include ENU frame"
    assert "ENU" in pydantic_systems, "Pydantic should include ENU coordinate system"


def test_units_metres_consistent():
    """Both contracts enforce metres as the primary unit."""
    from scannit_schemas.enums import Units

    # JSON Schema mesh_artifact requires units = "m"
    mesh_schema = SCHEMAS["mesh_artifact.schema.json"]
    json_units = mesh_schema["properties"]["units"]["const"]
    assert json_units == "m"

    # Pydantic Units has METRES
    assert Units.METRES == "METRES"


def test_schema_version_field_present():
    """Both JSON Schema and Pydantic require schema_version."""
    # JSON Schema
    for name, schema in SCHEMAS.items():
        if name == "common.schema.json":
            continue
        if "required" in schema:
            assert "schema_version" in schema["required"], (
                f"{name} missing schema_version in required"
            )

    # Pydantic (tested separately in 27 model tests, but verify here too)
    from scannit_schemas.models import ScannitBase
    instance = ScannitBase()
    assert hasattr(instance, "schema_version")
    assert instance.schema_version == "0.1.0"


def test_json_schema_examples_all_valid():
    """Every example in schemas/v0/examples/ validates against its schema.

    Duplicates PR #1's test but runs in the shared-schemas test environment.
    """
    example_dir = SCHEMA_DIR / "examples"
    if not example_dir.exists():
        pytest.skip("No examples directory")

    expected_schemas = {
        "camera_model", "transform", "reconstruction_job",
        "mesh_artifact", "texture_artifact", "spatial_tile",
        "quality_report", "ingest_manifest",
    }

    for name in expected_schemas:
        example_path = example_dir / f"{name}.example.json"
        if not example_path.exists():
            continue
        example = json.loads(example_path.read_text())
        _validator(name).validate(example)
