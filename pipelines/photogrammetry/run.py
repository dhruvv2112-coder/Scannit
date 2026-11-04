"""End-to-end reconstruction run: validate -> ingest -> sparse -> dense -> mesh -> export.

  python -m photogrammetry.run --input IMAGES_OR_VIDEO_DIR --output OUT [--synthetic] [--force]

Writes OUT/run_report.json always, and on success OUT/model.glb + OUT/artifact_manifest.json.
Stages are idempotent: a stage with a succeeded OUT/<stage>/stage.json is skipped unless --force.

Honesty rules:
  * Dense MVS / meshing are reported `unavailable` until an OpenMVS (CPU) or CUDA COLMAP build is
    wired in. The GLB is then a sparse point-cloud fallback and `mesh_acceptance` is `not_met`.
  * No metric references -> scale_status `arbitrary`, checkpoint_summary null.
  * Synthetic inputs are flagged and raise blocker DATASET_MISSING.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import struct
import sys
import time
from pathlib import Path

import numpy as np

from . import glb
from .sparse import SparseFailed, SparseOptions, run_sparse

SCHEMA_VERSION = "0.1.0"
STAGES = ["validate", "ingest", "sparse", "dense", "mesh", "export"]
EXIT = {
    "ok": 0,
    "input_invalid": 2,
    "insufficient_overlap": 3,
    "registration_failed": 3,
    "tool_crash": 4,
    "timeout": 5,
    "missing_dependency": 6,
    "internal": 1,
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_ply_ascii(path: Path) -> tuple[np.ndarray, np.ndarray]:
    lines = path.read_text().splitlines()
    n = int(next(ln for ln in lines if ln.startswith("element vertex")).split()[-1])
    start = lines.index("end_header") + 1
    arr = np.array([ln.split() for ln in lines[start : start + n]], dtype=np.float64).reshape(-1, 6)
    return arr[:, :3], arr[:, 3:].astype(np.uint8)


def verify_manifest(out: Path) -> list[str]:
    """Return problems found (empty list = artifact matches its manifest)."""
    m = json.loads((out / "artifact_manifest.json").read_text())
    problems = []
    f = out / m["uri"]
    if not f.exists():
        return [f"missing {m['uri']}"]
    if sha256(f) != m["sha256"]:
        problems.append("sha256 mismatch")
    try:
        glb.read_glb(f)
    except (ValueError, KeyError, struct.error) as e:
        problems.append(f"GLB does not reload: {e}")
    return problems


class Run:
    def __init__(self, out: Path, force: bool):
        self.out, self.force = out, force
        out.mkdir(parents=True, exist_ok=True)
        self.report = {
            "schema_version": SCHEMA_VERSION,
            "status": "running",
            "stages": [],
            "warnings": [],
            "blockers": [],
            "metrics": {},
            "environment": {
                "os": f"{platform.system()} {platform.release()}",
                "python": platform.python_version(),
                "cpu": platform.processor() or platform.machine(),
                "gpu": None,
                "tools": {},
            },
        }

    def stage(self, name: str, fn):
        d = self.out / name
        marker = d / "stage.json"
        if marker.exists() and not self.force:
            prev = json.loads(marker.read_text())
            if prev["status"] in ("succeeded", "unavailable"):
                self.report["stages"].append({**prev, "skipped_cached": True})
                return prev.get("result")
        d.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        rec = {"name": name, "status": "succeeded", "exit_code": 0, "error": None}
        try:
            rec["result"] = fn(d)
            if isinstance(rec["result"], dict) and rec["result"].get("_unavailable"):
                rec["status"] = "unavailable"
        except StageError as e:
            rec.update(status="failed", exit_code=EXIT.get(e.cls, 1), error={"class": e.cls, "message": str(e)[:2000]})
        rec["wall_s"] = round(time.monotonic() - t0, 3)
        marker.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
        self.report["stages"].append(rec)
        if rec["status"] == "failed":
            raise e_from(rec)
        return rec.get("result")

    def finish(self, status: str):
        self.report["status"] = status
        self.report["metrics"]["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        self.report["metrics"]["peak_rss_children_mb"] = round(
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1
        )
        (self.out / "run_report.json").write_text(json.dumps(self.report, indent=2, sort_keys=True) + "\n")


class StageError(RuntimeError):
    def __init__(self, cls: str, msg: str):
        super().__init__(msg)
        self.cls = cls


def e_from(rec):
    return StageError(rec["error"]["class"], rec["error"]["message"])


def run(
    input_path: Path,
    out: Path,
    synthetic: bool = False,
    force: bool = False,
    dataset_id: str | None = None,
    sparse_opts: SparseOptions | None = None,
) -> dict:
    from ingest.core import IngestOptions, NoUsableFrames
    from ingest.core import run as ingest_run
    from recon_core.limits import RejectedInput

    r = Run(out, force)
    t_start = time.monotonic()
    if synthetic:
        r.report["warnings"].append("SYNTHETIC INPUT: not a real scan; nothing here validates real-world accuracy.")
        r.report["blockers"].append("DATASET_MISSING")
    try:

        def validate(_d):
            if not input_path.exists():
                raise StageError("input_invalid", f"input not found: {input_path}")
            files = sorted(p for p in (input_path.iterdir() if input_path.is_dir() else [input_path]) if p.is_file())
            if not files:
                raise StageError("input_invalid", "input contains no files")
            return {"files": len(files)}

        r.stage("validate", validate)

        def ingest(d):
            files = sorted(p for p in (input_path.iterdir() if input_path.is_dir() else [input_path]) if p.is_file())
            try:
                m = ingest_run(files, d, IngestOptions())
            except (NoUsableFrames, RejectedInput) as e:
                raise StageError("input_invalid", str(e)) from e
            return m["summary"]

        ing = r.stage("ingest", ingest)

        def sparse(d):
            try:
                return run_sparse(out / "ingest" / "frames", d, sparse_opts or SparseOptions())
            except SparseFailed as e:
                cls = "missing_dependency" if "No module named 'pycolmap'" in str(e) else e.error_class
                raise StageError(cls, str(e)) from e

        sp = r.stage("sparse", sparse)
        r.report["environment"]["tools"]["pycolmap"] = sp.get("pycolmap_version")

        r.stage(
            "dense",
            lambda d: {
                "_unavailable": True,
                "reason": "No dense MVS backend in this build (needs OpenMVS CPU image or CUDA COLMAP).",
            },
        )
        r.stage("mesh", lambda d: {"_unavailable": True, "reason": "Depends on dense stage."})
        r.report["warnings"].append("Mesh acceptance NOT MET: model.glb is a sparse point-cloud fallback.")

        def export(d):
            xyz, rgb = read_ply_ascii(out / "sparse" / "points.ply")
            if len(xyz) == 0:
                raise StageError("insufficient_overlap", "sparse model has no points")
            extras = {"scannit": {"synthetic": synthetic, "scale_status": "arbitrary", "fallback": "sparse_points"}}
            bounds = glb.write_glb(out / "model.glb", xyz, rgb, extras=extras)
            return {"points": int(len(xyz)), "bounds_yup": bounds, "bytes": (out / "model.glb").stat().st_size}

        ex = r.stage("export", export)
        ingest_manifest = out / "ingest" / "manifest.json"
        did = dataset_id or "ds-" + sha256(ingest_manifest)[:16]
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "job_id": "job-" + hashlib.sha256(did.encode()).hexdigest()[:16],
            "dataset_id": did,
            "artifact_id": "glb-" + sha256(out / "model.glb")[:16],
            "artifact_type": "point_cloud_fallback",
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
            "bounds_m": {"frame_id": "gltf_yup", "min": ex["bounds_yup"]["min"], "max": ex["bounds_yup"]["max"]},
            "warnings": list(r.report["warnings"]) + ["Scale is arbitrary SfM units labelled 'm'; do not measure."],
            "source_image_manifest_uri": "ingest/manifest.json",
            "synthetic": synthetic,
            "artifact_origin": "synthetic_fixture" if synthetic else "camera_reconstruction",
            "mesh_acceptance": "not_met",
        }
        (out / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        r.report["metrics"].update(
            {
                "wall_s": round(time.monotonic() - t_start, 3),
                "images_total": sp.get("images_total"),
                "frames_kept": ing.get("frames_kept"),
                "images_registered": sp.get("images_registered"),
                "registered_fraction": sp.get("registered_fraction"),
                "reprojection_error_px": sp.get("reprojection_error_px"),
                "sparse_points": sp.get("sparse_points"),
                "mesh_faces": None,
                "output_bytes": ex["bytes"],
            }
        )
        r.finish("succeeded_with_fallback")
    except StageError as e:
        r.report["error"] = {"class": e.cls, "message": str(e)[:2000]}
        r.finish("failed")
        raise
    return r.report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="photogrammetry.run")
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--synthetic", action="store_true", help="flag the input as synthetic (raises DATASET_MISSING)")
    ap.add_argument("--force", action="store_true", help="re-run stages even if already succeeded")
    ap.add_argument("--dataset-id")
    a = ap.parse_args(argv)
    try:
        rep = run(a.input, a.output, a.synthetic, a.force, a.dataset_id)
    except StageError as e:
        print(f"recon-run: {e.cls}: {str(e).splitlines()[-1] if str(e) else ''}", file=sys.stderr)
        return EXIT.get(e.cls, 1)
    print(
        json.dumps(
            {
                "status": rep["status"],
                **{k: rep["metrics"].get(k) for k in ("images_registered", "sparse_points", "output_bytes", "wall_s")},
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
