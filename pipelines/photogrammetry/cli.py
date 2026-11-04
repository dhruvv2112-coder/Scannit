"""`scannit-recon run-stage`: the per-stage CLI that account2's orchestrator worker calls.

    scannit-recon run-stage --stage STAGE --job-id ID --dataset-path DIR --work-dir DIR [config flags]

Contract (matches services/orchestrator/app/workers/reconstruction.py in PR #3):
  * STAGE is the lower-cased JobStatus: validating, ingesting, pose_estimation, dense_mvs, mesh,
    materials, tiling, exporting, qc.
  * Files written directly in WORK_DIR/<stage>/ are registered by the worker as artifacts.
    Bookkeeping (stage.json, run_report.json) lives in WORK_DIR/_recon/ so it is not registered.
  * Exit 0 = stage done (or deliberately skipped); non-zero = failed (codes in EXIT).
  * Idempotent: a stage already succeeded/skipped is not recomputed unless --force.

Honesty rules:
  * Dense MVS / mesh are not built yet. With --dense true (the default) those stages FAIL with
    exit 6 (missing_dependency); a job only completes as a sparse preview if it asked for --dense false.
  * --scale-reference-path makes qc FAIL (exit 7) until REC-004 lands, rather than completing with
    an arbitrary scale the caller did not ask for.
  * A dataset folder containing a file named SCANNIT_SYNTHETIC is labelled artifact_origin=synthetic_fixture.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

STAGES = [
    "validating",
    "ingesting",
    "pose_estimation",
    "dense_mvs",
    "mesh",
    "materials",
    "tiling",
    "exporting",
    "qc",
]
EXIT = {
    "input_invalid": 2,
    "insufficient_overlap": 3,
    "registration_failed": 3,
    "tool_crash": 4,
    "timeout": 5,
    "missing_dependency": 6,
    "acceptance_not_met": 7,
    "precondition": 8,
    "internal": 1,
}
SYNTHETIC_MARKER = "SCANNIT_SYNTHETIC"


class StageFailed(RuntimeError):
    def __init__(self, cls: str, msg: str):
        super().__init__(msg)
        self.cls = cls


def _truthy(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _require(work: Path, stage: str) -> dict:
    marker = work / "_recon" / f"{stage}.json"
    if not marker.exists():
        raise StageFailed("precondition", f"stage '{stage}' has not run in {work}")
    rec = json.loads(marker.read_text())
    if rec["status"] not in ("succeeded", "skipped"):
        raise StageFailed("precondition", f"stage '{stage}' is {rec['status']}")
    return rec


# ---------------------------------------------------------------- stage bodies


def _validating(a, out: Path) -> dict:
    ds = Path(a.dataset_path)
    if not ds.is_dir():
        raise StageFailed("input_invalid", f"dataset path not found or not a directory: {ds}")
    files = sorted(p for p in ds.iterdir() if p.is_file() and p.name != SYNTHETIC_MARKER)
    if len(files) < 2:
        raise StageFailed("input_invalid", f"need at least 2 images or 1 video; found {len(files)} files")
    if (a.output_format or "glb").lower() != "glb":
        raise StageFailed("input_invalid", f"unsupported output format {a.output_format!r}; only 'glb'")
    return {"files": len(files), "synthetic": (ds / SYNTHETIC_MARKER).exists()}


def _ingesting(a, out: Path) -> dict:
    from ingest.core import IngestOptions, NoUsableFrames
    from ingest.core import run as ingest_run
    from recon_core.limits import RejectedInput

    ds = Path(a.dataset_path)
    files = sorted(p for p in ds.iterdir() if p.is_file() and p.name != SYNTHETIC_MARKER)
    opts = IngestOptions(max_frames=int(a.max_frames)) if a.max_frames else IngestOptions()
    try:
        return ingest_run(files, out, opts)["summary"]
    except (NoUsableFrames, RejectedInput) as e:
        raise StageFailed("input_invalid", str(e)) from e


def _pose_estimation(a, out: Path) -> dict:
    from .sparse import SparseFailed, run_sparse

    work = Path(a.work_dir)
    _require(work, "ingesting")
    try:
        s = run_sparse(work / "ingesting" / "frames", out)
    except SparseFailed as e:
        cls = "missing_dependency" if "No module named 'pycolmap'" in str(e) else e.error_class
        raise StageFailed(cls, str(e)) from e
    db = out / "database.db"
    if db.exists():  # keep the COLMAP database out of the registered artifacts
        db.replace(work / "_recon" / "colmap_database.db")
    return {
        k: s.get(k)
        for k in (
            "images_total",
            "images_registered",
            "registered_fraction",
            "sparse_points",
            "reprojection_error_px",
            "pycolmap_version",
        )
    }


def _dense_mvs(a, out: Path) -> dict:
    if not _truthy(a.dense, True):
        return {"_skipped": "dense disabled by job config (--dense false); output will be a sparse preview"}
    raise StageFailed(
        "missing_dependency",
        "dense MVS backend not installed (needs OpenMVS CPU image or CUDA COLMAP); "
        "rerun with job config dense=false for a sparse preview",
    )


def _mesh(a, out: Path) -> dict:
    if not _truthy(a.dense, True):
        return {"_skipped": "no dense cloud (dense=false)"}
    raise StageFailed("missing_dependency", "meshing depends on dense MVS, which is not installed")


def _materials(a, out: Path) -> dict:
    return {"_skipped": "PBR/material extraction is not in account1's scope; texture comes from REC-003"}


def _tiling(a, out: Path) -> dict:
    return {"_skipped": "tiling/LOD (REC-005) not implemented; single GLB exported"}


def _exporting(a, out: Path) -> dict:
    from . import glb
    from .run import read_ply_ascii, sha256

    work = Path(a.work_dir)
    v = _require(work, "validating")
    _require(work, "pose_estimation")
    dense = _truthy(a.dense, True)
    if dense:
        raise StageFailed("precondition", "dense=true but no mesh exists; refusing to export a point cloud as a mesh")
    xyz, rgb = read_ply_ascii(work / "pose_estimation" / "points.ply")
    if len(xyz) == 0:
        raise StageFailed("insufficient_overlap", "sparse model has no points")
    synthetic = bool(v["result"].get("synthetic"))
    origin = "synthetic_fixture" if synthetic else "camera_reconstruction"
    extras = {"scannit": {"artifact_origin": origin, "scale_status": "arbitrary", "fallback": "sparse_points"}}
    bounds = glb.write_glb(out / "model.glb", xyz, rgb, extras=extras)
    warnings = ["Sparse point-cloud preview: mesh acceptance NOT MET.", "Scale is arbitrary; do not measure."]
    if synthetic:
        warnings.insert(0, "SYNTHETIC FIXTURE: not a camera reconstruction.")
    manifest = {
        "schema_version": "0.1.0",
        "job_id": a.job_id,
        "dataset_id": Path(a.dataset_path).name,
        "artifact_id": "glb-" + sha256(out / "model.glb")[:16],
        "artifact_type": "point_cloud_fallback",
        "artifact_origin": origin,
        "synthetic": synthetic,
        "mime_type": "model/gltf-binary",
        "uri": "model.glb",
        "sha256": sha256(out / "model.glb"),
        "source_frame_id": "scene_zup",
        "target_frame_id": "gltf_yup",
        "transform_4x4": glb.ZUP_TO_YUP.tolist(),
        "unit": "m",
        "scale_status": "arbitrary",
        "reference_lengths": [],
        "checkpoint_summary": None,
        "bounds_m": {"frame_id": "gltf_yup", **bounds},
        "mesh_acceptance": "not_met",
        "warnings": warnings,
        "source_image_manifest_uri": "../ingesting/manifest.json",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {"points": int(len(xyz)), "bytes": (out / "model.glb").stat().st_size, "artifact_origin": origin}


def _qc(a, out: Path) -> dict:
    from .run import verify_manifest

    work = Path(a.work_dir)
    _require(work, "exporting")
    problems = verify_manifest(work / "exporting")
    if problems:
        raise StageFailed("acceptance_not_met", "exported artifact failed verification: " + "; ".join(problems))
    if a.scale_reference_path:
        raise StageFailed(
            "acceptance_not_met",
            "a scale reference was supplied but metric scaling (REC-004) is not implemented; "
            "refusing to complete with arbitrary scale",
        )
    pose = json.loads((work / "_recon" / "pose_estimation.json").read_text())["result"]
    report = {
        "job_id": a.job_id,
        "images_registered": pose.get("images_registered"),
        "registered_fraction": pose.get("registered_fraction"),
        "reprojection_error_px": pose.get("reprojection_error_px"),
        "sparse_points": pose.get("sparse_points"),
        "scale_status": "arbitrary",
        "checkpoint_errors_mm": None,
        "mesh_acceptance": "not_met",
        "artifact_verified": True,
    }
    (out / "qc_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


BODIES = {s: globals()[f"_{s}"] for s in STAGES}


# ---------------------------------------------------------------- driver


def run_stage(a) -> int:
    work = Path(a.work_dir)
    book = work / "_recon"
    book.mkdir(parents=True, exist_ok=True)
    marker = book / f"{a.stage}.json"
    if marker.exists() and not a.force and json.loads(marker.read_text())["status"] in ("succeeded", "skipped"):
        print(json.dumps({"stage": a.stage, "status": "cached"}))
        return 0
    out = work / a.stage
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    rec = {"stage": a.stage, "job_id": a.job_id, "status": "succeeded", "exit_code": 0, "error": None}
    try:
        result = BODIES[a.stage](a, out)
        if isinstance(result, dict) and "_skipped" in result:
            rec["status"], result = "skipped", {"reason": result["_skipped"]}
        rec["result"] = result
    except StageFailed as e:
        rec.update(status="failed", exit_code=EXIT.get(e.cls, 1), error={"class": e.cls, "message": str(e)[:2000]})
    except Exception as e:  # noqa: BLE001 - classified, never silent
        rec.update(status="failed", exit_code=EXIT["internal"], error={"class": "internal", "message": repr(e)[:2000]})
    rec["wall_s"] = round(time.monotonic() - t0, 3)
    marker.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    report_path = work / "_recon" / "run_report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {"job_id": a.job_id, "stages": {}}
    report["stages"][a.stage] = rec
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if rec["status"] == "failed":
        print(f"scannit-recon: {a.stage}: {rec['error']['class']}: {rec['error']['message']}", file=sys.stderr)
    else:
        print(json.dumps({"stage": a.stage, "status": rec["status"]}))
    return rec["exit_code"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scannit-recon")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rs = sub.add_parser("run-stage", help="run one pipeline stage for the orchestrator")
    rs.add_argument("--stage", required=True, choices=STAGES)
    rs.add_argument("--job-id", required=True)
    rs.add_argument("--dataset-path", required=True)
    rs.add_argument("--work-dir", required=True)
    rs.add_argument("--force", action="store_true")
    # Config keys the worker may forward (ALLOWED_CONFIG_KEYS in PR #3). Unused ones are accepted and recorded.
    for key in (
        "dense",
        "max-frames",
        "mesh-quality",
        "texture-resolution",
        "scale-reference-path",
        "coordinate-system",
        "output-format",
        "lod-levels",
        "skip-materials",
        "skip-tiling",
    ):
        rs.add_argument(f"--{key}")
    a = ap.parse_args(argv)
    return run_stage(a)


if __name__ == "__main__":
    sys.exit(main())
