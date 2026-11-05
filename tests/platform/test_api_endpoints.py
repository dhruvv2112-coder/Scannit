"""Integration tests for the Scannit Platform API.

Covers the full CRUD lifecycle: project → dataset → upload-url → job →
submit → status update → artifacts → manifest.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


# ── Projects ────────────────────────────────────────────────────────────


async def test_create_project(client):
    resp = await client.post("/api/v1/projects", json={
        "name": "Test Site Alpha",
        "description": "A warehouse scan",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Site Alpha"
    assert "id" in data


async def test_list_projects(client):
    await client.post("/api/v1/projects", json={"name": "P1"})
    await client.post("/api/v1/projects", json={"name": "P2"})
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


async def test_get_project_not_found(client):
    resp = await client.get("/api/v1/projects/00000000-0000-4000-a000-000000000099")
    assert resp.status_code == 404


# ── Datasets ────────────────────────────────────────────────────────────


async def test_create_dataset(client):
    proj = (await client.post("/api/v1/projects", json={"name": "DS Test"})).json()
    resp = await client.post(f"/api/v1/projects/{proj['id']}/datasets", json={
        "name": "Drone Run 1",
        "source_type": "images",
    })
    assert resp.status_code == 201
    assert resp.json()["source_type"] == "images"


async def test_list_datasets(client):
    proj = (await client.post("/api/v1/projects", json={"name": "DS List"})).json()
    await client.post(f"/api/v1/projects/{proj['id']}/datasets", json={"name": "A"})
    await client.post(f"/api/v1/projects/{proj['id']}/datasets", json={"name": "B"})
    resp = await client.get(f"/api/v1/projects/{proj['id']}/datasets")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ── Jobs ────────────────────────────────────────────────────────────────


async def _create_project_dataset(client):
    """Helper: create a project + dataset, return their IDs."""
    proj = (await client.post("/api/v1/projects", json={"name": "Job Test"})).json()
    ds = (await client.post(f"/api/v1/projects/{proj['id']}/datasets", json={"name": "run1"})).json()
    return proj["id"], ds["id"]


async def test_create_job(client):
    pid, did = await _create_project_dataset(client)
    resp = await client.post("/api/v1/jobs", json={
        "project_id": pid,
        "dataset_id": did,
        "config": {"quality": "high"},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "CREATED"
    assert data["config"]["quality"] == "high"


async def test_submit_job(client):
    pid, did = await _create_project_dataset(client)
    job = (await client.post("/api/v1/jobs", json={"project_id": pid, "dataset_id": did})).json()
    resp = await client.post(f"/api/v1/jobs/{job['id']}/submit")
    assert resp.status_code == 202
    assert resp.json()["status"] == "VALIDATING"


async def test_submit_already_submitted(client):
    pid, did = await _create_project_dataset(client)
    job = (await client.post("/api/v1/jobs", json={"project_id": pid, "dataset_id": did})).json()
    await client.post(f"/api/v1/jobs/{job['id']}/submit")
    resp = await client.post(f"/api/v1/jobs/{job['id']}/submit")
    assert resp.status_code == 409


async def test_update_job_status(client):
    pid, did = await _create_project_dataset(client)
    job = (await client.post("/api/v1/jobs", json={"project_id": pid, "dataset_id": did})).json()
    await client.post(f"/api/v1/jobs/{job['id']}/submit")

    resp = await client.patch(f"/api/v1/jobs/{job['id']}/status", json={
        "status": "INGESTING",
        "progress_pct": 25.0,
        "scale_status": "reference_scaled",
        "reference_lengths": {"wall_A": 3.2, "wall_B": 4.5},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "INGESTING"
    assert data["progress_pct"] == 25.0
    assert data["scale_status"] == "reference_scaled"


async def test_cancel_job(client):
    pid, did = await _create_project_dataset(client)
    job = (await client.post("/api/v1/jobs", json={"project_id": pid, "dataset_id": did})).json()
    await client.post(f"/api/v1/jobs/{job['id']}/submit")
    resp = await client.patch(f"/api/v1/jobs/{job['id']}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


async def test_cancel_terminal_job_fails(client):
    pid, did = await _create_project_dataset(client)
    job = (await client.post("/api/v1/jobs", json={"project_id": pid, "dataset_id": did})).json()
    await client.post(f"/api/v1/jobs/{job['id']}/submit")
    await client.patch(f"/api/v1/jobs/{job['id']}/cancel")
    resp = await client.patch(f"/api/v1/jobs/{job['id']}/cancel")
    assert resp.status_code == 409


async def test_complete_job_sets_completed_at(client):
    pid, did = await _create_project_dataset(client)
    job = (await client.post("/api/v1/jobs", json={"project_id": pid, "dataset_id": did})).json()
    await client.post(f"/api/v1/jobs/{job['id']}/submit")
    resp = await client.patch(f"/api/v1/jobs/{job['id']}/status", json={
        "status": "COMPLETE",
        "progress_pct": 100.0,
    })
    data = resp.json()
    assert data["status"] == "COMPLETE"
    assert data["completed_at"] is not None


# ── Artifacts ───────────────────────────────────────────────────────────


async def test_artifact_not_found(client):
    resp = await client.get("/api/v1/artifacts/00000000-0000-4000-a000-000000000099")
    assert resp.status_code == 404


async def test_artifact_manifest_not_found(client):
    resp = await client.get("/api/v1/artifacts/00000000-0000-4000-a000-000000000099/manifest")
    assert resp.status_code == 404
