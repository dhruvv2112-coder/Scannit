"""Ingest stage: validate inputs, extract frames, reject blur/duplicates, write a deterministic manifest.

Determinism: sources are processed in (name, sha256) order, frame ids are assigned sequentially,
and the manifest has no wall-clock fields and is serialised with sorted keys. Identical inputs,
options and tool versions give a byte-identical manifest and identical frame files.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
from PIL import ExifTags, Image, ImageOps
from recon_core import SCHEMA_VERSION
from recon_core.hashing import dhash, min_hamming, sha256_file
from recon_core.limits import Limits, RejectedInput, classify

from . import video

ANALYSIS_LONG_SIDE = 1024


@dataclass(frozen=True)
class IngestOptions:
    frame_interval_s: float = 0.5
    max_frames: int = 2000
    blur_threshold: float = 60.0
    dedupe_max_hamming: int = 4
    limits: Limits = field(default_factory=Limits)

    def __post_init__(self):
        if self.frame_interval_s <= 0:
            raise ValueError("frame_interval_s must be > 0")
        if not 2 <= self.max_frames <= 20000:
            raise ValueError("max_frames must be in [2, 20000]")
        if self.blur_threshold < 0:
            raise ValueError("blur_threshold must be >= 0")
        if not 0 <= self.dedupe_max_hamming <= 64:
            raise ValueError("dedupe_max_hamming must be in [0, 64]")


class NoUsableFrames(RuntimeError):
    pass


def _num(v) -> float | None:
    try:
        return round(float(Fraction(v)) if not isinstance(v, (int, float)) else float(v), 6)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _gps(gps_ifd) -> dict:
    def deg(vals, ref):
        try:
            d, m, s = (float(x) for x in vals)
        except (TypeError, ValueError):
            return None
        v = d + m / 60 + s / 3600
        return round(-v if ref in ("S", "W") else v, 8)

    out = {}
    lat = deg(gps_ifd.get(2), gps_ifd.get(1))
    lon = deg(gps_ifd.get(4), gps_ifd.get(3))
    if lat is not None and lon is not None:
        out.update(lat_deg=lat, lon_deg=lon)
        alt = _num(gps_ifd.get(6))
        if alt is not None:
            out["alt_m"] = -alt if gps_ifd.get(5) == 1 else alt
    return out


def read_exif(img: Image.Image) -> dict:
    exif = img.getexif()
    if not exif:
        return {}
    sub = exif.get_ifd(ExifTags.IFD.Exif)
    out: dict = {}
    if (v := exif.get(0x010F)) and str(v).strip("\x00 "):
        out["make"] = str(v).strip("\x00 ")[:128]
    if (v := exif.get(0x0110)) and str(v).strip("\x00 "):
        out["model"] = str(v).strip("\x00 ")[:128]
    if (v := exif.get(0x0112)) is not None:
        out["orientation"] = int(v)
    if (v := _num(sub.get(0x920A))) is not None and v > 0:
        out["focal_length_mm"] = v
    if (v := _num(sub.get(0xA405))) is not None and v > 0:
        out["focal_length_35mm_equiv_mm"] = v
    if v := sub.get(0x9003):
        out["datetime_original"] = str(v)[:32]
    gps = _gps(exif.get_ifd(ExifTags.IFD.GPSInfo))
    if gps:
        out["gps"] = gps
    return out


def _analyse(path: Path) -> tuple[int, int, float, int]:
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise RejectedInput("image could not be decoded")
    h, w = gray.shape
    scale = ANALYSIS_LONG_SIDE / max(h, w)
    small = (
        cv2.resize(gray, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
        if scale < 1
        else gray
    )
    sharpness = float(cv2.Laplacian(small, cv2.CV_64F).var())
    return w, h, round(sharpness, 4), dhash(small)


def _stage_still(src: Path, dst_stem: Path, limits: Limits) -> tuple[Path, dict]:
    """Copy the original (keeps EXIF) or, if EXIF orientation != 1, write an upright lossless PNG."""
    Image.MAX_IMAGE_PIXELS = limits.max_image_pixels
    try:
        with Image.open(src) as img:
            if img.width * img.height > limits.max_image_pixels:
                raise RejectedInput(f"image has {img.width * img.height} pixels; limit is {limits.max_image_pixels}")
            exif = read_exif(img)
            if exif.get("orientation", 1) in (1, 0):
                dst = dst_stem.with_suffix(src.suffix.lower())
                shutil.copyfile(src, dst)
            else:
                dst = dst_stem.with_suffix(".png")
                ImageOps.exif_transpose(img).save(dst, format="PNG", optimize=False)
    except Image.DecompressionBombError as e:
        raise RejectedInput("image exceeds pixel limit") from e
    except (OSError, SyntaxError) as e:
        raise RejectedInput("image could not be decoded") from e
    return dst, exif


def run(inputs: list[Path], out_dir: Path, opts: IngestOptions | None = None) -> dict:
    """Run ingest and write `out_dir/manifest.json`. Returns the manifest dict."""
    opts = opts or IngestOptions()
    if len(inputs) > opts.limits.max_sources:
        raise RejectedInput(f"{len(inputs)} sources; limit is {opts.limits.max_sources}")
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir, rejected_dir, tmp_dir = out_dir / "frames", out_dir / "rejected", out_dir / "_tmp"
    for d in (frames_dir, rejected_dir, tmp_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir()

    keyed = []
    for p in inputs:
        p = Path(p)
        digest = sha256_file(p) if p.is_file() and not p.is_symlink() else "0" * 64
        keyed.append((p.name, digest, p))
    keyed.sort(key=lambda t: (t[0], t[1]))

    sources, frames = [], []
    kept_hashes: list[int] = []
    kept_ids: list[str] = []
    counts = {"rejected_blur": 0, "rejected_duplicate": 0}

    def add_frame(file: Path, source_id: str, ts: float | None, exif: dict | None):
        fid = f"f-{len(frames):06d}"
        w, h, sharp, dh = _analyse(file)
        status, dup = "kept", None
        if sharp < opts.blur_threshold:
            status = "rejected_blur"
        elif kept_hashes:
            dist, idx = min_hamming(dh, np.array(kept_hashes, dtype=np.uint64))
            if dist <= opts.dedupe_max_hamming:
                status, dup = "rejected_duplicate", kept_ids[idx]
        dest_dir = frames_dir if status == "kept" else rejected_dir
        final = dest_dir / f"{fid}{file.suffix}"
        shutil.move(str(file), final)
        if status == "kept":
            kept_hashes.append(dh)
            kept_ids.append(fid)
        else:
            counts[status] += 1
        rec = {
            "frame_id": fid,
            "source_id": source_id,
            "file": final.relative_to(out_dir).as_posix(),
            "timestamp_s": ts,
            "width_px": w,
            "height_px": h,
            "checksum": {"algorithm": "sha256", "value": sha256_file(final)},
            "sharpness": sharp,
            "dhash": f"{dh:016x}",
            "status": status,
        }
        if dup:
            rec["duplicate_of"] = dup
        if exif:
            rec["exif"] = exif
        frames.append(rec)

    for i, (name, digest, path) in enumerate(keyed):
        sid = f"src-{i:04d}"
        src = {
            "source_id": sid,
            "name": name[:255],
            "kind": "image",
            "bytes": 0,
            "checksum": {"algorithm": "sha256", "value": digest},
            "status": "accepted",
        }
        sources.append(src)
        try:
            kind = classify(path, opts.limits)
            src["kind"], src["bytes"] = kind, path.stat().st_size
            remaining = opts.max_frames - len(frames)
            if remaining <= 0:
                raise RejectedInput(f"max_frames ({opts.max_frames}) reached before this source")
            if kind == "image":
                staged, exif = _stage_still(path, tmp_dir / sid, opts.limits)
                add_frame(staged, sid, None, exif)
            else:
                src["video"] = video.probe(path, opts.limits)
                extracted = video.extract_frames(path, tmp_dir / sid, opts.frame_interval_s, remaining)
                if not extracted:
                    raise RejectedInput("no frames could be extracted")
                for n, f in enumerate(extracted):
                    add_frame(f, sid, round(n * opts.frame_interval_s, 6), None)
        except RejectedInput as e:
            src["status"], src["reject_reason"] = "rejected", str(e)[:500]
            if src["kind"] == "image" and path.suffix.lower() in (".mp4", ".mov"):
                src["kind"] = "video"

    shutil.rmtree(tmp_dir, ignore_errors=True)
    kept = sum(1 for f in frames if f["status"] == "kept")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "options": {
            "frame_interval_s": opts.frame_interval_s,
            "max_frames": opts.max_frames,
            "blur_threshold": opts.blur_threshold,
            "dedupe_max_hamming": opts.dedupe_max_hamming,
            "analysis_long_side_px": ANALYSIS_LONG_SIDE,
            "limits": opts.limits.to_dict(),
            "tools": {"ffmpeg": video.tool_version("ffmpeg"), "opencv": cv2.__version__},
        },
        "sources": sources,
        "frames": frames,
        "summary": {
            "frames_total": len(frames),
            "frames_kept": kept,
            "rejected_blur": counts["rejected_blur"],
            "rejected_duplicate": counts["rejected_duplicate"],
            "sources_rejected": sum(1 for s in sources if s["status"] == "rejected"),
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if kept == 0:
        raise NoUsableFrames("no usable frames: every input was rejected, blurred or duplicated")
    return manifest
