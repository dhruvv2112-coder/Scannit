"""Smoke test for the health endpoint."""

from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_healthz_returns_ok():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
