"""Every v0 schema is valid JSON Schema 2020-12 and its example instance validates."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "v0"
SCHEMAS = {p.name: json.loads(p.read_text()) for p in SCHEMA_DIR.glob("*.schema.json")}
REGISTRY = Registry().with_resources(
    (s["$id"], Resource.from_contents(s)) for s in SCHEMAS.values()
)
EXPECTED = {
    "common", "transform", "camera_model", "reconstruction_job", "mesh_artifact",
    "texture_artifact", "spatial_tile", "quality_report", "ingest_manifest",
}


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(SCHEMAS[f"{name}.schema.json"], registry=REGISTRY, format_checker=FormatChecker())


def test_all_expected_schemas_present():
    assert {n.removesuffix(".schema.json") for n in SCHEMAS} == EXPECTED


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_valid(name):
    Draft202012Validator.check_schema(SCHEMAS[name])


@pytest.mark.parametrize("name", sorted(EXPECTED - {"common"}))
def test_example_validates(name):
    example = json.loads((SCHEMA_DIR / "examples" / f"{name}.example.json").read_text())
    validator(name).validate(example)


def test_units_are_meters_only():
    example = json.loads((SCHEMA_DIR / "examples" / "mesh_artifact.example.json").read_text())
    example["units"] = "mm"
    assert not validator("mesh_artifact").is_valid(example)


def test_scale_status_required_on_mesh():
    example = json.loads((SCHEMA_DIR / "examples" / "mesh_artifact.example.json").read_text())
    del example["scale_status"]
    assert not validator("mesh_artifact").is_valid(example)


def test_uri_rejects_query_strings_and_credentials():
    example = json.loads((SCHEMA_DIR / "examples" / "mesh_artifact.example.json").read_text())
    example["uri"] = "s3://bucket/a.ply?X-Amz-Signature=abc"
    assert not validator("mesh_artifact").is_valid(example)


def test_zero_scale_rejected():
    example = json.loads((SCHEMA_DIR / "examples" / "transform.example.json").read_text())
    example["scale"] = 0
    assert not validator("transform").is_valid(example)
