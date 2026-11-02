"""Generate the REC-001 synthetic fixture (no third-party imagery; safe for the repo and CI).

Usage: python tests/reconstruction/make_fixture.py OUT_DIR

Produces, from a seeded random texture:
  a_sharp.jpg       sharp still with EXIF (make/model/focal length)
  a_neardup.jpg     same view, re-encoded and slightly brighter -> duplicate of a_sharp
  a_blur.jpg        same view, Gaussian blurred -> rejected for blur
  b_rotated.jpg     different view, stored sideways with EXIF Orientation=6
  c_distinct.png    different view
  pan.mp4           4 s panning video at 10 fps with a 1 s freeze -> duplicate frames
  not_an_image.jpg  text file with a .jpg extension -> rejected by content sniffing
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

SEED = 20261103
W, H = 640, 480


def texture(rng: np.random.Generator, w: int, h: int) -> np.ndarray:
    """Multi-scale noise: strong gradients at several scales, so views are sharp and distinguishable."""
    img = np.zeros((h, w, 3), np.float32)
    for cell in (4, 16, 64):
        small = rng.random((h // cell + 1, w // cell + 1, 3), dtype=np.float32)
        img += cv2.resize(small, (w + cell, h + cell), interpolation=cv2.INTER_NEAREST)[:h, :w]
    img = (img / 3 * 255).clip(0, 255).astype(np.uint8)
    return img


def exif_bytes(orientation: int = 1) -> bytes:
    """Hand-built little-endian EXIF block, so the fixture is identical on every Pillow version.

    IFD0: Make, Model, Orientation, ExifIFD pointer. Exif IFD: FocalLength 4.2 mm, FocalLengthIn35mmFilm 26.
    """
    import struct

    make, model = b"ScannitFixture\x00", b"Synthetic-1\x00"
    ifd0_off, n0 = 8, 4
    exif_off = ifd0_off + 2 + n0 * 12 + 4
    n1 = 2
    data_off = exif_off + 2 + n1 * 12 + 4
    make_off, model_off = data_off, data_off + len(make)
    focal_off = model_off + len(model)

    def entry(tag, typ, count, value: bytes) -> bytes:
        return struct.pack("<HHI", tag, typ, count) + value.ljust(4, b"\x00")

    ifd0 = struct.pack("<H", n0) + b"".join([
        entry(0x010F, 2, len(make), struct.pack("<I", make_off)),
        entry(0x0110, 2, len(model), struct.pack("<I", model_off)),
        entry(0x0112, 3, 1, struct.pack("<H", orientation)),
        entry(0x8769, 4, 1, struct.pack("<I", exif_off)),
    ]) + struct.pack("<I", 0)
    exif_ifd = struct.pack("<H", n1) + b"".join([
        entry(0x920A, 5, 1, struct.pack("<I", focal_off)),
        entry(0xA405, 3, 1, struct.pack("<H", 26)),
    ]) + struct.pack("<I", 0)
    tiff = b"II*\x00" + struct.pack("<I", ifd0_off) + ifd0 + exif_ifd + make + model + struct.pack("<II", 42, 10)
    return b"Exif\x00\x00" + tiff


def build(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    canvas = texture(rng, W * 4, H)  # wide canvas to pan across

    def view(x: int) -> Image.Image:
        return Image.fromarray(np.ascontiguousarray(canvas[:, x : x + W]))

    a = view(0)
    a.save(out / "a_sharp.jpg", quality=95, exif=exif_bytes())
    bright = np.clip(np.asarray(a, np.int16) + 6, 0, 255).astype(np.uint8)
    Image.fromarray(bright).save(out / "a_neardup.jpg", quality=80)
    Image.fromarray(cv2.GaussianBlur(np.asarray(a), (0, 0), 6)).save(out / "a_blur.jpg", quality=95)
    # Stored rotated 90deg CCW; Orientation=6 tells readers to rotate 90deg CW to make it upright.
    view(W).transpose(Image.Transpose.ROTATE_90).save(out / "b_rotated.jpg", quality=95, exif=exif_bytes(6))
    view(2 * W).save(out / "c_distinct.png")
    (out / "not_an_image.jpg").write_text("this is not a jpeg\n")

    frames_dir = out / "_video_frames"
    frames_dir.mkdir(exist_ok=True)
    n = 0
    xs = list(np.linspace(0, 3 * W - 1, 30).astype(int)) + [3 * W - 1] * 10  # pan then 1 s freeze
    for x in xs:
        cv2.imwrite(str(frames_dir / f"{n:04d}.png"), canvas[:, x : x + W])
        n += 1
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", "10", "-i", str(frames_dir / "%04d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12", "-threads", "1",
         "-fflags", "+bitexact", "-flags:v", "+bitexact", "-map_metadata", "-1", str(out / "pan.mp4")],
        check=True, timeout=120,
    )
    for f in frames_dir.iterdir():
        f.unlink()
    frames_dir.rmdir()
    return sorted(p for p in out.iterdir() if p.is_file())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    for p in build(Path(sys.argv[1])):
        print(p)
