"""REC-002: reproducible sparse reconstruction with PyCOLMAP.

`run_sparse()` runs COLMAP in a child process (so a hang or crash cannot take the worker down and a
timeout is enforceable), then exports the largest model as:

  cameras.json   list of CameraModel (schemas/v0)
  images.json    per registered image: cam_from_world Transform (schemas/v0) + camera_id
  points.ply     sparse points, ASCII PLY, SfM units (scale_status = arbitrary)
  sparse.json    summary + failure classification

Reproducibility: single-threaded feature extraction/matching/mapping and a fixed random seed.
Same input + same COLMAP build => same registered set and poses (numerically, not bit-for-bit
across different CPUs). Scale is arbitrary until REC-004 applies metric references.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA_VERSION = "0.1.0"
EXIT = {
    "ok": 0,
    "input_invalid": 2,
    "insufficient_overlap": 3,
    "registration_failed": 3,
    "tool_crash": 4,
    "timeout": 5,
    "internal": 1,
}


@dataclass(frozen=True)
class SparseOptions:
    camera_model: str = "OPENCV"
    single_camera: bool = True
    matcher: str = "exhaustive"  # exhaustive | sequential
    min_registered_fraction: float = 0.5
    seed: int = 0
    num_threads: int = 1
    max_image_size: int = 3200
    timeout_s: float = 3600.0

    def __post_init__(self):
        if self.matcher not in ("exhaustive", "sequential"):
            raise ValueError("matcher must be 'exhaustive' or 'sequential'")
        if self.camera_model not in ("SIMPLE_RADIAL", "OPENCV", "OPENCV_FISHEYE", "PINHOLE"):
            raise ValueError(f"unsupported camera_model {self.camera_model}")
        if not 0 < self.min_registered_fraction <= 1:
            raise ValueError("min_registered_fraction must be in (0, 1]")


class SparseFailed(RuntimeError):
    def __init__(self, error_class: str, message: str):
        super().__init__(message)
        self.error_class = error_class


# ---------------------------------------------------------------- helpers (no pycolmap needed)


def rotmat_to_wxyz(R) -> list[float]:
    """Rotation matrix -> unit quaternion [w, x, y, z] with w >= 0."""
    import numpy as np

    R = np.asarray(R, dtype=float)
    tr = np.trace(R)
    if tr > 0:
        s = 2.0 * np.sqrt(tr + 1.0)
        q = [0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    else:
        i = int(np.argmax(np.diag(R)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = 2.0 * np.sqrt(1.0 + R[i, i] - R[j, j] - R[k, k])
        q = [0.0] * 4
        q[0] = (R[k, j] - R[j, k]) / s
        q[1 + i] = 0.25 * s
        q[1 + j] = (R[j, i] + R[i, j]) / s
        q[1 + k] = (R[k, i] + R[i, k]) / s
    q = np.asarray(q)
    q /= np.linalg.norm(q)
    if q[0] < 0:
        q = -q
    return [round(float(x), 12) for x in q]


def classify_result(n_images: int, n_registered: int, opts: SparseOptions) -> tuple[str, str]:
    if n_images < 2:
        return "input_invalid", f"need at least 2 images, got {n_images}"
    if n_registered == 0:
        return "insufficient_overlap", "COLMAP produced no model (images may not overlap or lack texture)"
    frac = n_registered / n_images
    if frac < opts.min_registered_fraction:
        return "registration_failed", f"only {n_registered}/{n_images} images registered ({frac:.0%})"
    return "ok", ""


def write_ply(path: Path, xyz, rgb) -> None:
    lines = [
        "ply",
        "format ascii 1.0",
        f"element vertex {len(xyz)}",
        "property float x",
        "property float y",
        "property float z",
        "property uchar red",
        "property uchar green",
        "property uchar blue",
        "end_header",
    ]
    lines += [
        f"{x:.6f} {y:.6f} {z:.6f} {int(r)} {int(g)} {int(b)}" for (x, y, z), (r, g, b) in zip(xyz, rgb, strict=True)
    ]
    path.write_text("\n".join(lines) + "\n")


def _call(obj, name):
    """PyCOLMAP changed several properties into methods across 3.x; accept either."""
    v = getattr(obj, name)
    return v() if callable(v) else v


# ---------------------------------------------------------------- the COLMAP part (child process)


def _child(image_dir: str, work_dir: str, opts_d: dict, q) -> None:
    try:
        q.put(("ok", _reconstruct(Path(image_dir), Path(work_dir), SparseOptions(**opts_d))))
    except SparseFailed as e:
        q.put(("fail", (e.error_class, str(e))))
    except Exception:  # noqa: BLE001 - classified as tool_crash, traceback kept for the log
        q.put(("fail", ("tool_crash", traceback.format_exc()[-2000:])))


def _reconstruct(image_dir: Path, work: Path, opts: SparseOptions) -> dict:
    import numpy as np
    import pycolmap

    if hasattr(pycolmap, "set_random_seed"):
        pycolmap.set_random_seed(opts.seed)
    images = sorted(
        p.name for p in image_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff")
    )
    cls, msg = classify_result(len(images), 1, opts) if len(images) < 2 else ("ok", "")
    if cls != "ok":
        raise SparseFailed(cls, msg)

    db = work / "database.db"
    if db.exists():
        db.unlink()
    model_dir = work / "colmap_model"
    model_dir.mkdir(parents=True, exist_ok=True)

    ext = (
        pycolmap.FeatureExtractionOptions()
        if hasattr(pycolmap, "FeatureExtractionOptions")
        else pycolmap.SiftExtractionOptions()
    )
    for target in (ext, getattr(ext, "sift", None)):
        if target is not None and hasattr(target, "max_image_size"):
            target.max_image_size = opts.max_image_size
        if target is not None and hasattr(target, "num_threads"):
            target.num_threads = opts.num_threads
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = opts.camera_model
    mode = pycolmap.CameraMode.SINGLE if opts.single_camera else pycolmap.CameraMode.AUTO
    kw = {"extraction_options": ext} if hasattr(pycolmap, "FeatureExtractionOptions") else {"sift_options": ext}
    pycolmap.extract_features(db, image_dir, camera_mode=mode, reader_options=reader, device=pycolmap.Device.cpu, **kw)
    if opts.matcher == "sequential":
        pycolmap.match_sequential(db, device=pycolmap.Device.cpu)
    else:
        pycolmap.match_exhaustive(db, device=pycolmap.Device.cpu)

    mapper = pycolmap.IncrementalPipelineOptions()
    if hasattr(mapper, "num_threads"):
        mapper.num_threads = opts.num_threads
    models = pycolmap.incremental_mapping(db, image_dir, model_dir, options=mapper)
    if not models:
        raise SparseFailed("insufficient_overlap", "COLMAP produced no model (images may not overlap or lack texture)")
    rec = max(models.values(), key=lambda r: r.num_reg_images())
    rec.write(model_dir)

    cameras = []
    for cid, cam in sorted(rec.cameras.items()):
        cameras.append(
            {
                "schema_version": SCHEMA_VERSION,
                "camera_id": f"cam-{cid}",
                "model": str(_call(cam, "model_name") if hasattr(cam, "model_name") else cam.model.name),
                "width_px": int(cam.width),
                "height_px": int(cam.height),
                "params": [float(x) for x in cam.params],
                "source": "estimated",
            }
        )
    poses = []
    for _iid, img in sorted(rec.images.items(), key=lambda kv: kv[1].name):
        if hasattr(img, "has_pose") and not _call(img, "has_pose"):
            continue
        if hasattr(img, "registered") and not _call(img, "registered"):
            continue
        cfw = _call(img, "cam_from_world")
        R = cfw.rotation.matrix()
        poses.append(
            {
                "image": img.name,
                "camera_id": f"cam-{img.camera_id}",
                "cam_from_world": {
                    "schema_version": SCHEMA_VERSION,
                    "from_frame": "sfm",
                    "to_frame": f"camera-{img.name}"[:128],
                    "rotation_wxyz": rotmat_to_wxyz(R),
                    "translation_m": [float(x) for x in cfw.translation],
                    "scale": 1.0,
                    "source": "sfm",
                },
            }
        )
    xyz = [p.xyz for _, p in sorted(rec.points3D.items())]
    rgb = [p.color for _, p in sorted(rec.points3D.items())]
    write_ply(work / "points.ply", xyz, rgb)
    errs = np.array([p.error for p in rec.points3D.values()]) if rec.points3D else np.array([0.0])
    (work / "cameras.json").write_text(json.dumps(cameras, indent=2) + "\n")
    (work / "images.json").write_text(json.dumps(poses, indent=2) + "\n")
    return {
        "images_total": len(images),
        "images_registered": len(poses),
        "registered": [p["image"] for p in poses],
        "sparse_points": len(xyz),
        "reprojection_error_px": {
            "mean": float(errs.mean()),
            "median": float(np.median(errs)),
            "p95": float(np.percentile(errs, 95)),
        },
        "pycolmap_version": getattr(pycolmap, "__version__", "unknown"),
    }


# ---------------------------------------------------------------- public entry point


def run_sparse(image_dir: Path, work_dir: Path, opts: SparseOptions | None = None) -> dict:
    """Run sparse SfM; always writes work_dir/sparse.json. Raises SparseFailed on any failure."""
    opts = opts or SparseOptions()
    work_dir.mkdir(parents=True, exist_ok=True)
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_child, args=(str(image_dir), str(work_dir), asdict(opts), q), daemon=True)
    proc.start()
    proc.join(opts.timeout_s)
    if proc.is_alive():
        proc.kill()
        proc.join()
        status, payload = "fail", ("timeout", f"sparse reconstruction exceeded {opts.timeout_s:.0f}s")
    elif q.empty():
        status, payload = "fail", ("tool_crash", f"COLMAP process exited with code {proc.exitcode} and no result")
    else:
        status, payload = q.get()

    if status == "ok":
        cls, msg = classify_result(payload["images_total"], payload["images_registered"], opts)
        payload["registered_fraction"] = round(payload["images_registered"] / payload["images_total"], 6)
    else:
        (cls, msg), payload = payload, {}
    summary = {
        "status": "succeeded" if cls == "ok" else "failed",
        "error": None
        if cls == "ok"
        else {"class": cls, "message": msg[:2000], "retryable": cls in ("timeout", "tool_crash")},
        "scale_status": "arbitrary",
        "options": asdict(opts),
        **payload,
    }
    (work_dir / "sparse.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if cls != "ok":
        raise SparseFailed(cls, msg)
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="photogrammetry.sparse", description="COLMAP sparse reconstruction")
    ap.add_argument("images", type=Path, help="directory of frames (e.g. ingest DIR/frames)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--matcher", default="exhaustive", choices=["exhaustive", "sequential"])
    ap.add_argument("--camera-model", default="OPENCV")
    ap.add_argument("--timeout", type=float, default=3600)
    a = ap.parse_args(argv)
    try:
        s = run_sparse(
            a.images, a.out, SparseOptions(camera_model=a.camera_model, matcher=a.matcher, timeout_s=a.timeout)
        )
    except ValueError as e:
        print(f"sparse: {e}", file=sys.stderr)
        return EXIT["input_invalid"]
    except SparseFailed as e:
        print(f"sparse: {e.error_class}: {str(e).splitlines()[-1] if str(e) else ''}", file=sys.stderr)
        return EXIT.get(e.error_class, 1)
    print(
        json.dumps({k: s[k] for k in ("images_total", "images_registered", "sparse_points", "reprojection_error_px")})
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
