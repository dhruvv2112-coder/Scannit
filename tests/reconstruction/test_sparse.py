"""REC-002: COLMAP wrapper. Unit tests run everywhere; the reconstruction test needs pycolmap (CI)."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator
from photogrammetry.sparse import SparseFailed, SparseOptions, classify_result, rotmat_to_wxyz, run_sparse, write_ply
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
HAS_PYCOLMAP = importlib.util.find_spec("pycolmap") is not None


def _validator(title):
    schemas = [json.loads(p.read_text()) for p in (ROOT / "schemas" / "v0").glob("*.schema.json")]
    reg = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    return Draft202012Validator(next(s for s in schemas if s["title"] == title), registry=reg)


@pytest.mark.parametrize(
    "axis,angle", [((0, 0, 1), 0.3), ((1, 0, 0), 2.9), ((0.3, -0.5, 0.8), 3.1), ((0, 1, 0), np.pi)]
)
def test_rotmat_to_quaternion_roundtrip(axis, angle):
    import cv2

    a = np.asarray(axis, float) / np.linalg.norm(axis) * angle
    R, _ = cv2.Rodrigues(a)
    w, x, y, z = rotmat_to_wxyz(R)
    R2 = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )
    assert np.allclose(R, R2, atol=1e-9) and w >= 0


def test_classification():
    o = SparseOptions(min_registered_fraction=0.5)
    assert classify_result(1, 0, o)[0] == "input_invalid"
    assert classify_result(10, 0, o)[0] == "insufficient_overlap"
    assert classify_result(10, 4, o)[0] == "registration_failed"
    assert classify_result(10, 5, o) == ("ok", "")


def test_option_validation():
    with pytest.raises(ValueError):
        SparseOptions(matcher="vocab_tree")
    with pytest.raises(ValueError):
        SparseOptions(camera_model="FOV")


def test_ply_writer(tmp_path):
    write_ply(tmp_path / "p.ply", [(0, 0, 0), (1.5, -2, 3)], [(255, 0, 0), (0, 128, 255)])
    lines = (tmp_path / "p.ply").read_text().splitlines()
    assert "element vertex 2" in lines and lines[-1] == "1.500000 -2.000000 3.000000 0 128 255"


def test_timeout_is_enforced_and_classified(tmp_path):
    with pytest.raises(SparseFailed) as e:
        run_sparse(tmp_path, tmp_path / "w", SparseOptions(timeout_s=0.001))
    assert e.value.error_class == "timeout"
    s = json.loads((tmp_path / "w" / "sparse.json").read_text())
    assert s["status"] == "failed" and s["error"]["retryable"] is True and s["scale_status"] == "arbitrary"


@pytest.mark.skipif(HAS_PYCOLMAP, reason="checks the no-pycolmap crash path")
def test_missing_colmap_is_a_classified_crash(tmp_path):
    with pytest.raises(SparseFailed) as e:
        run_sparse(tmp_path, tmp_path / "w", SparseOptions(timeout_s=120))
    assert e.value.error_class == "tool_crash" and "pycolmap" in str(e.value)


@pytest.mark.skipif(not HAS_PYCOLMAP, reason="pycolmap not installed (runs in CI)")
def test_too_few_images_rejected(tmp_path):
    (tmp_path / "imgs").mkdir()
    with pytest.raises(SparseFailed) as e:
        run_sparse(tmp_path / "imgs", tmp_path / "w", SparseOptions(timeout_s=300))
    assert e.value.error_class == "input_invalid"


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    import synthetic_scene

    out = tmp_path_factory.mktemp("scene")
    return synthetic_scene.render(out, 12), out


@pytest.mark.skipif(not HAS_PYCOLMAP, reason="pycolmap not installed (runs in CI)")
def test_reconstructs_synthetic_scene_reproducibly(scene, tmp_path):
    images, _ = scene
    opts = SparseOptions(camera_model="PINHOLE", timeout_s=1200)
    a = run_sparse(images, tmp_path / "a", opts)
    assert a["registered_fraction"] >= 0.9, a
    assert a["reprojection_error_px"]["mean"] < 1.5, a
    assert a["sparse_points"] > 500

    for cam in json.loads((tmp_path / "a" / "cameras.json").read_text()):
        _validator("CameraModel").validate(cam)
    poses = json.loads((tmp_path / "a" / "images.json").read_text())
    for p in poses:
        _validator("Transform").validate(p["cam_from_world"])

    b = run_sparse(images, tmp_path / "b", opts)  # same input -> same registered set and same relative poses
    assert a["registered"] == b["registered"]

    def to_R(q):
        w, x, y, z = q
        return np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ]
        )

    def relative(path):
        qs = {p["image"]: to_R(p["cam_from_world"]["rotation_wxyz"]) for p in json.loads(path.read_text())}
        ref = qs[sorted(qs)[0]]
        return {k: R @ ref.T for k, R in qs.items()}  # gauge-independent

    ra, rb = relative(tmp_path / "a" / "images.json"), relative(tmp_path / "b" / "images.json")
    worst_deg = max(np.degrees(np.arccos(np.clip((np.trace(ra[k].T @ rb[k]) - 1) / 2, -1, 1))) for k in ra)
    assert worst_deg < 0.1, worst_deg
