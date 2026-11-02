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
    ex = Image.Exif()
    ex[0x010F] = "ScannitFixture"
    ex[0x0110] = "Synthetic-1"
    ex[0x0112] = orientation
    ex.get_ifd(0x8769)[0x920A] = 4.2  # FocalLength
    ex.get_ifd(0x8769)[0xA405] = 26  # FocalLengthIn35mmFilm
    return ex.tobytes()


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
