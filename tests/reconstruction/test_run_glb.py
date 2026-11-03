"""First-GLB path: GLB writer, Z-up -> Y-up transform, run report honesty, manifest integrity."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from photogrammetry import glb
from photogrammetry.run import StageError, run, verify_manifest

HAS_PYCOLMAP = importlib.util.find_spec("pycolmap") is not None


def test_transform_round_trip_and_up_axis():
    assert np.allclose(glb.YUP_TO_ZUP @ glb.ZUP_TO_YUP, np.eye(4))
    assert np.isclose(np.linalg.det(glb.ZUP_TO_YUP[:3, :3]), 1.0)  # proper rotation, stays right-handed
    assert np.allclose(glb.apply(glb.ZUP_TO_YUP, [0, 0, 1]), [[0, 1, 0]])  # scene up -> glTF up


def test_known_1m_object_survives_export(tmp_path):
    """Software units only: a 1 m segment stays 1 m through GLB export (not a reconstruction-accuracy claim)."""
    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 0], [0, 0, 1], [0.3, 0.4, 0.5], [0.3, 0.4, 1.5]])
    glb.write_glb(tmp_path / "m.glb", pts, rgb=np.zeros((6, 3)))
    gltf, pos = glb.read_glb(tmp_path / "m.glb")
    for a, b in ((0, 1), (2, 3), (4, 5)):
        assert np.linalg.norm(pos[a] - pos[b]) == pytest.approx(1.0, abs=1e-6)
    back = glb.apply(glb.YUP_TO_ZUP, pos)
    assert np.allclose(back, pts, atol=1e-6)
    assert gltf["accessors"][0]["min"] == pos.min(axis=0).tolist()


def test_triangle_mesh_reloads(tmp_path):
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]])
    glb.write_glb(tmp_path / "t.glb", v, faces=[[0, 1, 2]])
    gltf, pos = glb.read_glb(tmp_path / "t.glb")
    assert gltf["meshes"][0]["primitives"][0]["mode"] == 4 and len(pos) == 3


def test_corrupted_output_detected(tmp_path):
    glb.write_glb(tmp_path / "model.glb", np.random.default_rng(0).random((50, 3)))
    from photogrammetry.run import sha256

    (tmp_path / "artifact_manifest.json").write_text(json.dumps({"uri": "model.glb", "sha256": sha256(tmp_path / "model.glb")}))
    assert verify_manifest(tmp_path) == []
    data = bytearray((tmp_path / "model.glb").read_bytes())
    data[-5] ^= 0xFF
    (tmp_path / "model.glb").write_bytes(bytes(data))
    assert "sha256 mismatch" in verify_manifest(tmp_path)


def test_missing_dataset_fails_without_false_success(tmp_path):
    with pytest.raises(StageError) as e:
        run(tmp_path / "nope", tmp_path / "out")
    assert e.value.cls == "input_invalid"
    rep = json.loads((tmp_path / "out" / "run_report.json").read_text())
    assert rep["status"] == "failed" and not (tmp_path / "out" / "model.glb").exists()


@pytest.mark.skipif(HAS_PYCOLMAP, reason="checks the no-COLMAP path")
def test_missing_colmap_is_reported(fixture_dir, tmp_path):
    with pytest.raises(StageError) as e:
        run(fixture_dir, tmp_path / "out", synthetic=True)
    assert e.value.cls == "missing_dependency"
    rep = json.loads((tmp_path / "out" / "run_report.json").read_text())
    assert rep["status"] == "failed" and "DATASET_MISSING" in rep["blockers"]
    assert not (tmp_path / "out" / "artifact_manifest.json").exists()


@pytest.mark.skipif(not HAS_PYCOLMAP, reason="pycolmap not installed (runs in CI)")
def test_synthetic_end_to_end_first_glb(tmp_path):
    import os

    import synthetic_scene
    from photogrammetry.sparse import SparseOptions

    images = synthetic_scene.render(tmp_path / "scene", 12)
    out = Path(os.environ.get("RECON_DEMO_OUT", tmp_path / "out"))
    rep = run(images, out, synthetic=True, force=True, sparse_opts=SparseOptions(camera_model="PINHOLE", timeout_s=1200))
    assert rep["status"] == "succeeded_with_fallback"
    m = json.loads((out / "artifact_manifest.json").read_text())
    assert verify_manifest(out) == []
    assert m["synthetic"] is True and m["scale_status"] == "arbitrary" and m["mesh_acceptance"] == "not_met"
    assert m["mime_type"] == "model/gltf-binary" and m["unit"] == "m"
    assert [s["status"] for s in rep["stages"]] == ["succeeded", "succeeded", "succeeded", "unavailable", "unavailable", "succeeded"]
    rep2 = run(images, out, synthetic=True)  # idempotent: nothing recomputed
    assert all(s.get("skipped_cached") for s in rep2["stages"])
