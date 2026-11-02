"""REC-001 acceptance: deterministic frame manifest; dedupe/blur/security fixture tests."""
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ingest.__main__ import main as cli
from ingest.core import IngestOptions, NoUsableFrames, run
from recon_core.hashing import dhash, min_hamming
from recon_core.limits import Limits, RejectedInput, classify

ROOT = Path(__file__).resolve().parents[2]


def by_name(manifest):
    names = {s["source_id"]: s["name"] for s in manifest["sources"]}
    out = {}
    for f in manifest["frames"]:
        out.setdefault(names[f["source_id"]], []).append(f)
    return out


@pytest.fixture(scope="module")
def ingested(fixture_dir, tmp_path_factory):
    out = tmp_path_factory.mktemp("ingest_a")
    manifest = run(sorted(fixture_dir.iterdir()), out, IngestOptions(frame_interval_s=0.25))
    return out, manifest


def test_manifest_matches_schema(ingested):
    _, manifest = ingested
    schemas = [json.loads(p.read_text()) for p in (ROOT / "schemas" / "v0").glob("*.schema.json")]
    reg = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    schema = next(s for s in schemas if s["title"] == "IngestManifest")
    Draft202012Validator(schema, registry=reg).validate(manifest)


def test_deterministic_manifest_and_frames(fixture_dir, ingested, tmp_path):
    out_a, _ = ingested
    # Same inputs, given in a different order, into a different directory.
    run(sorted(fixture_dir.iterdir(), reverse=True), tmp_path, IngestOptions(frame_interval_s=0.25))
    assert (out_a / "manifest.json").read_bytes() == (tmp_path / "manifest.json").read_bytes()


def test_near_duplicate_still_rejected(ingested):
    _, m = ingested
    frames = by_name(m)
    pair = frames["a_sharp.jpg"] + frames["a_neardup.jpg"]
    assert sorted(f["status"] for f in pair) == ["kept", "rejected_duplicate"]
    dup = next(f for f in pair if f["status"] == "rejected_duplicate")
    kept = next(f for f in pair if f["status"] == "kept")
    assert dup["duplicate_of"] == kept["frame_id"]


def test_frozen_video_frames_rejected_as_duplicates(ingested):
    _, m = ingested
    video = by_name(m)["pan.mp4"]
    statuses = [f["status"] for f in video]
    assert statuses.count("kept") >= 10
    assert statuses[-3:] == ["rejected_duplicate"] * 3  # the 1 s freeze at the end
    assert [f["timestamp_s"] for f in video[:3]] == [0.0, 0.25, 0.5]


def test_blurred_still_rejected(ingested):
    _, m = ingested
    (blur,) = by_name(m)["a_blur.jpg"]
    assert blur["status"] == "rejected_blur"
    assert blur["sharpness"] < 60 and blur["file"].startswith("rejected/")


def test_exif_orientation_normalised_and_exif_recorded(ingested, fixture_dir):
    out, m = ingested
    (rot,) = by_name(m)["b_rotated.jpg"]
    assert (rot["width_px"], rot["height_px"]) == (640, 480)
    assert rot["exif"]["orientation"] == 6 and rot["exif"]["make"] == "ScannitFixture"
    assert rot["exif"]["focal_length_mm"] == pytest.approx(4.2)
    assert rot["file"].endswith(".png")
    from PIL import Image

    upright = np.asarray(Image.open(fixture_dir / "b_rotated.jpg").transpose(Image.Transpose.ROTATE_270), np.int16)
    ingested_px = np.asarray(Image.open(out / rot["file"]), np.int16)
    assert np.abs(upright - ingested_px).mean() < 1.0  # rotated clockwise, not counter-clockwise


def test_still_without_rotation_keeps_original_bytes(ingested, fixture_dir):
    out, m = ingested
    (c,) = by_name(m)["c_distinct.png"]
    assert (out / c["file"]).read_bytes() == (fixture_dir / "c_distinct.png").read_bytes()


def test_kept_files_exist_with_matching_checksums(ingested):
    import hashlib

    out, m = ingested
    for f in m["frames"]:
        assert hashlib.sha256((out / f["file"]).read_bytes()).hexdigest() == f["checksum"]["value"]
    assert len(list((out / "frames").iterdir())) == m["summary"]["frames_kept"]
    assert not (out / "_tmp").exists()


def test_fake_jpeg_rejected(ingested):
    _, m = ingested
    src = next(s for s in m["sources"] if s["name"] == "not_an_image.jpg")
    assert src["status"] == "rejected" and "does not match" in src["reject_reason"]


# --- security / limits -------------------------------------------------------

def test_symlink_rejected(fixture_dir, tmp_path):
    link = tmp_path / "link.jpg"
    os.symlink(fixture_dir / "a_sharp.jpg", link)
    with pytest.raises(RejectedInput, match="symlink"):
        classify(link, Limits())


def test_size_limit(fixture_dir):
    with pytest.raises(RejectedInput, match="limit"):
        classify(fixture_dir / "a_sharp.jpg", Limits(max_image_bytes=100))


def test_pixel_limit_rejects_decompression_bomb(fixture_dir, tmp_path):
    with pytest.raises(NoUsableFrames):
        run([fixture_dir / "c_distinct.png", fixture_dir / "a_sharp.jpg"], tmp_path,
            IngestOptions(limits=Limits(max_image_pixels=1000)))  # every image is "too big"
    m = json.loads((tmp_path / "manifest.json").read_text())  # manifest still written for diagnosis
    assert [s["status"] for s in m["sources"]] == ["rejected", "rejected"]
    assert all("pixel" in s["reject_reason"] for s in m["sources"])


def test_unsupported_extension(tmp_path):
    p = tmp_path / "x.gif"
    p.write_bytes(b"GIF89a")
    with pytest.raises(RejectedInput, match="unsupported"):
        classify(p, Limits())


def test_option_validation():
    with pytest.raises(ValueError):
        IngestOptions(frame_interval_s=0)
    with pytest.raises(ValueError):
        IngestOptions(dedupe_max_hamming=65)


# --- primitives --------------------------------------------------------------

def test_dhash_hamming():
    rng = np.random.default_rng(0)
    img = (rng.random((64, 64)) * 255).astype(np.uint8)
    h1, h2 = dhash(img), dhash(cv2.add(img, 3))
    h3 = dhash(np.ascontiguousarray(img[:, ::-1]))
    d_same, _ = min_hamming(h1, np.array([h2], dtype=np.uint64))
    d_diff, _ = min_hamming(h1, np.array([h3], dtype=np.uint64))
    assert d_same <= 4 < d_diff


# --- CLI ---------------------------------------------------------------------

def test_cli_exit_codes(fixture_dir, tmp_path, capsys):
    assert cli([str(fixture_dir / "c_distinct.png"), "--out", str(tmp_path / "ok")]) == 0
    assert json.loads(capsys.readouterr().out)["frames_kept"] == 1
    assert cli([str(fixture_dir / "a_blur.jpg"), "--out", str(tmp_path / "blur")]) == 3
    assert cli([str(fixture_dir / "c_distinct.png"), "--out", str(tmp_path / "bad"), "--interval", "0"]) == 2
