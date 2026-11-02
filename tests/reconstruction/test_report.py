import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ingest.core import IngestOptions, run
from recon_core.report import from_ingest

ROOT = Path(__file__).resolve().parents[2]


def test_ingest_report_is_valid_and_honest(fixture_dir, tmp_path):
    m = run([fixture_dir / "a_sharp.jpg", fixture_dir / "a_blur.jpg"], tmp_path, IngestOptions())
    r = from_ingest(m, "job-test", generated_at="2026-11-03T00:00:00Z")
    schemas = [json.loads(p.read_text()) for p in (ROOT / "schemas" / "v0").glob("*.schema.json")]
    reg = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    Draft202012Validator(next(s for s in schemas if s["title"] == "QualityReport"), registry=reg).validate(r)
    assert r["images_total"] == 2 and r["images_rejected_blur"] == 1
    assert r["scale_status"] == "arbitrary" and r["checkpoint_errors_mm"] is None
