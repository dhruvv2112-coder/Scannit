"""Tests for the v1 API endpoints: projects, datasets, jobs."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.storage import InMemoryStore, store
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_store():
    """Reset in-memory store between tests."""
    store.projects.clear()
    store.datasets.clear()
    store.jobs.clear()
    store.artifacts.clear()
    yield


# --- Projects ---


class TestProjects:
    def test_create_project(self):
        resp = client.post(
            "/api/v1/projects",
            json={"name": "Test Site", "description": "A test project"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Site"
        assert "id" in data
        assert "created_at" in data

    def test_create_project_empty_name_rejected(self):
        resp = client.post("/api/v1/projects", json={"name": ""})
        assert resp.status_code == 422

    def test_list_projects_empty(self):
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_projects_returns_created(self):
        client.post("/api/v1/projects", json={"name": "Site A"})
        client.post("/api/v1/projects", json={"name": "Site B"})
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_project_by_id(self):
        create_resp = client.post("/api/v1/projects", json={"name": "Site"})
        pid = create_resp.json()["id"]
        resp = client.get(f"/api/v1/projects/{pid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Site"

    def test_get_project_not_found(self):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/projects/{fake_id}")
        assert resp.status_code == 404

    def test_create_project_with_location(self):
        resp = client.post(
            "/api/v1/projects",
            json={"name": "Geo Site", "location_wkt": "POINT(77.5 12.9)"},
        )
        assert resp.status_code == 201
        assert resp.json()["location_wkt"] == "POINT(77.5 12.9)"


# --- Datasets ---


class TestDatasets:
    def _create_project(self) -> str:
        resp = client.post("/api/v1/projects", json={"name": "P"})
        return resp.json()["id"]

    def test_create_dataset(self):
        pid = self._create_project()
        resp = client.post(
            f"/api/v1/projects/{pid}/datasets",
            json={"project_id": pid, "name": "Drone Capture 1", "source_type": "images"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Drone Capture 1"
        assert data["upload_status"] == "pending"

    def test_create_dataset_project_not_found(self):
        fake_id = str(uuid.uuid4())
        resp = client.post(
            f"/api/v1/projects/{fake_id}/datasets",
            json={"project_id": fake_id, "name": "D"},
        )
        assert resp.status_code == 404

    def test_list_datasets(self):
        pid = self._create_project()
        client.post(
            f"/api/v1/projects/{pid}/datasets",
            json={"project_id": pid, "name": "D1"},
        )
        client.post(
            f"/api/v1/projects/{pid}/datasets",
            json={"project_id": pid, "name": "D2"},
        )
        resp = client.get(f"/api/v1/projects/{pid}/datasets")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_dataset_by_id(self):
        pid = self._create_project()
        create_resp = client.post(
            f"/api/v1/projects/{pid}/datasets",
            json={"project_id": pid, "name": "D1"},
        )
        did = create_resp.json()["id"]
        resp = client.get(f"/api/v1/datasets/{did}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "D1"


# --- Jobs ---


class TestJobs:
    def _create_project_and_dataset(self) -> tuple[str, str]:
        pid = client.post("/api/v1/projects", json={"name": "P"}).json()["id"]
        did = client.post(
            f"/api/v1/projects/{pid}/datasets",
            json={"project_id": pid, "name": "D"},
        ).json()["id"]
        return pid, did

    def test_create_job(self):
        pid, did = self._create_project_and_dataset()
        resp = client.post(
            "/api/v1/jobs",
            json={"project_id": pid, "dataset_id": did},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "CREATED"
        assert data["progress_pct"] == 0.0

    def test_create_job_project_not_found(self):
        fake_pid = str(uuid.uuid4())
        fake_did = str(uuid.uuid4())
        resp = client.post(
            "/api/v1/jobs",
            json={"project_id": fake_pid, "dataset_id": fake_did},
        )
        assert resp.status_code == 404

    def test_get_job(self):
        pid, did = self._create_project_and_dataset()
        jid = client.post(
            "/api/v1/jobs",
            json={"project_id": pid, "dataset_id": did},
        ).json()["id"]
        resp = client.get(f"/api/v1/jobs/{jid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == jid

    def test_get_job_not_found(self):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/jobs/{fake_id}")
        assert resp.status_code == 404

    def test_list_job_artifacts_empty(self):
        pid, did = self._create_project_and_dataset()
        jid = client.post(
            "/api/v1/jobs",
            json={"project_id": pid, "dataset_id": did},
        ).json()["id"]
        resp = client.get(f"/api/v1/jobs/{jid}/artifacts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_job_with_config(self):
        pid, did = self._create_project_and_dataset()
        config = {"dense": False, "max_frames": 500}
        resp = client.post(
            "/api/v1/jobs",
            json={"project_id": pid, "dataset_id": did, "config": config},
        )
        assert resp.status_code == 201
        assert resp.json()["config"] == config


# --- CORS ---


class TestCORS:
    def test_cors_allows_vite_origin(self):
        resp = client.options(
            "/api/v1/projects",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_cors_rejects_unknown_origin(self):
        resp = client.options(
            "/api/v1/projects",
            headers={
                "Origin": "http://evil.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.headers.get("access-control-allow-origin") is None
