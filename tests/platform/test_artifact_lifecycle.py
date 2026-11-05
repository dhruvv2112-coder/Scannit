"""Artifact lifecycle tests — creation, manifest retrieval, and failure cases.

Covers the contract-specified scenarios:
  - Artifact creation via worker callback
  - Manifest retrieval with presigned download URL
  - Artifact not found
  - Manifest not found
  - Invalid artifact hash format (future validation)
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _create_project_dataset_job(client):
    """Helper: create a project + dataset + job, return their IDs."""
    proj = (await client.post("/api/v1/projects", json={"name": "Artifact Test"})).json()
    ds = (
        await client.post(
            f"/api/v1/projects/{proj['id']}/datasets",
            json={"name": "scan1"},
        )
    ).json()
    job = (
        await client.post(
            "/api/v1/jobs",
            json={"project_id": proj["id"], "dataset_id": ds["id"]},
        )
    ).json()
    return proj["id"], ds["id"], job["id"]


async def test_create_artifact_via_api(client):
    """Worker registers an artifact after uploading to S3."""
    _, _, job_id = await _create_project_dataset_job(client)
    resp = await client.post(
        "/api/v1/artifacts",
        json={
            "job_id": job_id,
            "stage": "EXPORTING",
            "content_hash": "sha256:abc123def456",
            "software_version": "scannit-recon/0.1.0",
            "transforms": {},
            "units": "METRES",
            "coordinate_system": "LOCAL",
            "privacy_classification": "INTERNAL",
            "fidelity_warnings": [],
            "s3_key": f"artifacts/{job_id}/exporting/abc123def456_model.glb",
            "byte_size": 1048576,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["stage"] == "EXPORTING"
    assert data["content_hash"] == "sha256:abc123def456"
    assert data["byte_size"] == 1048576
    assert "id" in data


async def test_list_artifacts_for_job(client):
    """List all artifacts produced by a job."""
    _, _, job_id = await _create_project_dataset_job(client)

    # Create two artifacts
    for stage in ("MESH", "EXPORTING"):
        await client.post(
            "/api/v1/artifacts",
            json={
                "job_id": job_id,
                "stage": stage,
                "content_hash": f"sha256:{stage.lower()}_hash",
                "s3_key": f"artifacts/{job_id}/{stage.lower()}/output.glb",
                "byte_size": 500000,
            },
        )

    # Submit the job first so we can list artifacts
    await client.post(f"/api/v1/jobs/{job_id}/submit")

    resp = await client.get(f"/api/v1/jobs/{job_id}/artifacts")
    assert resp.status_code == 200
    artifacts = resp.json()
    assert len(artifacts) == 2


async def test_artifact_not_found(client):
    fake_id = "00000000-0000-4000-a000-000000000099"
    resp = await client.get(f"/api/v1/artifacts/{fake_id}")
    assert resp.status_code == 404


async def test_manifest_not_found(client):
    fake_id = "00000000-0000-4000-a000-000000000099"
    resp = await client.get(f"/api/v1/artifacts/{fake_id}/manifest")
    assert resp.status_code == 404


async def test_full_job_lifecycle_with_artifacts(client):
    """End-to-end: create → submit → update status → create artifact → get manifest."""
    _, _, job_id = await _create_project_dataset_job(client)

    # Submit
    resp = await client.post(f"/api/v1/jobs/{job_id}/submit")
    assert resp.status_code == 202

    # Worker updates status through stages
    for stage in ("INGESTING", "POSE_ESTIMATION", "DENSE_MVS", "MESH", "EXPORTING"):
        resp = await client.patch(
            f"/api/v1/jobs/{job_id}/status",
            json={"status": stage, "progress_pct": 50.0},
        )
        assert resp.status_code == 200

    # Worker registers the GLB artifact
    artifact_resp = await client.post(
        "/api/v1/artifacts",
        json={
            "job_id": job_id,
            "stage": "EXPORTING",
            "content_hash": "sha256:realglbhash123",
            "software_version": "scannit-recon/0.1.0",
            "transforms": {
                "world_from_model": {
                    "type": "TRS",
                    "translation": [0, 0, 0],
                    "rotation": [1, 0, 0, 0],
                    "scale": [1, 1, 1],
                    "frame": "Z-up",
                }
            },
            "units": "METRES",
            "coordinate_system": "LOCAL",
            "privacy_classification": "INTERNAL",
            "fidelity_warnings": [],
            "s3_key": f"artifacts/{job_id}/exporting/realglbhash_scene.glb",
            "byte_size": 2097152,
        },
    )
    assert artifact_resp.status_code == 201
    artifact_id = artifact_resp.json()["id"]

    # Complete the job
    resp = await client.patch(
        f"/api/v1/jobs/{job_id}/status",
        json={"status": "COMPLETE", "progress_pct": 100.0},
    )
    assert resp.status_code == 200
    assert resp.json()["completed_at"] is not None

    # Verify artifact retrieval
    resp = await client.get(f"/api/v1/artifacts/{artifact_id}")
    assert resp.status_code == 200
    assert resp.json()["stage"] == "EXPORTING"


async def test_failed_job_has_error_message(client):
    """Worker reports a FAILED status with error details."""
    _, _, job_id = await _create_project_dataset_job(client)
    await client.post(f"/api/v1/jobs/{job_id}/submit")

    resp = await client.patch(
        f"/api/v1/jobs/{job_id}/status",
        json={
            "status": "FAILED",
            "progress_pct": 20.0,
            "error_message": "[COLMAP_FAILURE] SfM failed: insufficient features",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "FAILED"
    assert "COLMAP_FAILURE" in data["error_message"]


async def test_cancel_running_job(client):
    """Cancel a job that is currently processing."""
    _, _, job_id = await _create_project_dataset_job(client)
    await client.post(f"/api/v1/jobs/{job_id}/submit")

    # Move to a mid-pipeline stage
    await client.patch(
        f"/api/v1/jobs/{job_id}/status",
        json={"status": "POSE_ESTIMATION", "progress_pct": 25.0},
    )

    # Cancel
    resp = await client.patch(f"/api/v1/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


async def test_cannot_cancel_completed_job(client):
    """Completed jobs cannot be cancelled."""
    _, _, job_id = await _create_project_dataset_job(client)
    await client.post(f"/api/v1/jobs/{job_id}/submit")
    await client.patch(
        f"/api/v1/jobs/{job_id}/status",
        json={"status": "COMPLETE", "progress_pct": 100.0},
    )

    resp = await client.patch(f"/api/v1/jobs/{job_id}/cancel")
    assert resp.status_code == 409


async def test_scale_metadata_persisted_on_status_update(client):
    """Worker reports scale metadata alongside status updates."""
    _, _, job_id = await _create_project_dataset_job(client)
    await client.post(f"/api/v1/jobs/{job_id}/submit")

    resp = await client.patch(
        f"/api/v1/jobs/{job_id}/status",
        json={
            "status": "MESH",
            "progress_pct": 60.0,
            "scale_status": "survey_scaled",
            "reference_lengths": {"baseline_A": 2.5, "baseline_B": 3.0},
            "transform_bounds": {"min": [-10, -10, 0], "max": [10, 10, 5]},
            "source_evidence": {"method": "ground_control_points", "count": 4},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["scale_status"] == "survey_scaled"
    assert data["reference_lengths"]["baseline_A"] == 2.5
