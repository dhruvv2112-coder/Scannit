"""`scannit-recon run-stage` contract with account2's orchestrator worker (PR #3)."""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "infra" / "workers" / "scannit-recon"
HAS_PYCOLMAP = importlib.util.find_spec("pycolmap") is not None


def stage(name, ds, work, *extra):
    """Invoke exactly as the worker does: explicit arg list, no shell, cwd=work_dir."""
    work.mkdir(parents=True, exist_ok=True)
    args = [
        str(CLI),
        "run-stage",
        "--stage",
        name,
        "--job-id",
        "job-t",
        "--dataset-path",
        str(ds),
        "--work-dir",
        str(work),
        *extra,
    ]
    return subprocess.run(args, capture_output=True, text=True, timeout=1500, cwd=work)


@pytest.fixture
def ds(fixture_dir, tmp_path):
    d = tmp_path / "ds"
    d.mkdir()
    for name in ("a_sharp.jpg", "b_rotated.jpg", "c_distinct.png"):
        shutil.copy(fixture_dir / name, d / name)
    return d


def test_validating_and_ingesting_and_idempotence(ds, tmp_path):
    w = tmp_path / "w"
    assert stage("validating", ds, w).returncode == 0
    r = stage("ingesting", ds, w)
    assert r.returncode == 0, r.stderr
    assert (w / "ingesting" / "manifest.json").exists()
    assert not (w / "ingesting" / "stage.json").exists()  # bookkeeping stays out of artifact dirs
    again = stage("ingesting", ds, w)
    assert again.returncode == 0 and '"cached"' in again.stdout
    report = json.loads((w / "_recon" / "run_report.json").read_text())
    assert set(report["stages"]) == {"validating", "ingesting"}


def test_missing_dataset_and_bad_format(tmp_path, ds):
    r = stage("validating", tmp_path / "nope", tmp_path / "w1")
    assert r.returncode == 2 and "not found" in r.stderr
    r = stage("validating", ds, tmp_path / "w2", "--output-format", "obj")
    assert r.returncode == 2 and "only 'glb'" in r.stderr


def test_dense_default_fails_honestly_and_dense_false_skips(ds, tmp_path):
    r = stage("dense_mvs", ds, tmp_path / "w")
    assert r.returncode == 6 and "dense=false" in r.stderr
    assert stage("dense_mvs", ds, tmp_path / "w2", "--dense", "false").returncode == 0
    assert stage("mesh", ds, tmp_path / "w2", "--dense", "false").returncode == 0
    rec = json.loads((tmp_path / "w2" / "_recon" / "mesh.json").read_text())
    assert rec["status"] == "skipped"


def test_out_of_order_stage_is_a_precondition_failure(ds, tmp_path):
    r = stage("exporting", ds, tmp_path / "w", "--dense", "false")
    assert r.returncode == 8


def test_worker_config_flags_are_accepted(ds, tmp_path):
    flags = [
        "--mesh-quality",
        "high",
        "--texture-resolution",
        "4096",
        "--coordinate-system",
        "LOCAL",
        "--lod-levels",
        "3",
        "--skip-materials",
        "true",
        "--skip-tiling",
        "true",
        "--max-frames",
        "100",
    ]
    assert stage("validating", ds, tmp_path / "w", *flags).returncode == 0


@pytest.mark.skipif(HAS_PYCOLMAP, reason="checks the no-COLMAP path")
def test_missing_colmap_reported(ds, tmp_path):
    w = tmp_path / "w"
    stage("validating", ds, w)
    stage("ingesting", ds, w)
    r = stage("pose_estimation", ds, w)
    assert r.returncode == 6 and "pycolmap" in r.stderr


@pytest.mark.skipif(not HAS_PYCOLMAP, reason="pycolmap not installed (runs in CI)")
def test_full_job_sparse_preview(tmp_path):
    import synthetic_scene

    images = synthetic_scene.render(tmp_path / "scene", 12)
    (images / "SCANNIT_SYNTHETIC").write_text("synthetic fixture\n")
    w = tmp_path / "w"
    for s in (
        "validating",
        "ingesting",
        "pose_estimation",
        "dense_mvs",
        "mesh",
        "materials",
        "tiling",
        "exporting",
        "qc",
    ):
        r = stage(s, images, w, "--dense", "false")
        assert r.returncode == 0, (s, r.stderr)
    m = json.loads((w / "exporting" / "artifact_manifest.json").read_text())
    assert m["artifact_origin"] == "synthetic_fixture" and m["scale_status"] == "arbitrary"
    assert sorted(p.name for p in (w / "exporting").iterdir()) == ["artifact_manifest.json", "model.glb"]
    assert "database.db" not in [p.name for p in (w / "pose_estimation").iterdir()]
    assert json.loads((w / "qc" / "qc_report.json").read_text())["artifact_verified"] is True
    r = stage("qc", images, w, "--dense", "false", "--scale-reference-path", "/refs.json", "--force")
    assert r.returncode == 7 and "REC-004" in r.stderr
