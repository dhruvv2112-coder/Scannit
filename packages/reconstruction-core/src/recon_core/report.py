"""Build a QualityReport from whatever stages have run. Unmeasured fields are null, never guessed.

Usage: python -m recon_core.report INGEST_MANIFEST --job-id ID --out quality.json
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from recon_core import SCHEMA_VERSION


def from_ingest(manifest: dict, job_id: str, generated_at: str | None = None) -> dict:
    s = manifest["summary"]
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "images_total": s["frames_total"],
        "images_rejected_blur": s["rejected_blur"],
        "images_rejected_duplicate": s["rejected_duplicate"],
        "images_registered": 0,
        "registered_fraction": 0.0,
        "reprojection_error_px": None,
        "sparse_points": None,
        "scale_status": "arbitrary",
        "control_residuals_mm": None,
        "checkpoint_errors_mm": None,
        "dense_point_density_per_m2": None,
        "texture_coverage_fraction": None,
        "unreconstructed_area_fraction": None,
        "stage_timings_s": {},
        "peak_memory_mb": None,
        "environment": {
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "tools": {k: str(v) for k, v in manifest.get("options", {}).get("tools", {}).items()},
        },
        "notes": ["Ingest only: no reconstruction has run, so registration is 0 and scale is arbitrary."],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="recon_core.report")
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    report = from_ingest(json.loads(a.manifest.read_text()), a.job_id)
    a.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
