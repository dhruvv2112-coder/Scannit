"""Upload limits and file-type sniffing. Extensions are never trusted alone."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

IMAGE_EXTS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".tif": "tiff", ".tiff": "tiff"}
VIDEO_EXTS = {".mp4": "isobmff", ".mov": "isobmff"}


@dataclass(frozen=True)
class Limits:
    max_image_bytes: int = 200 * 1024**2
    max_video_bytes: int = 8 * 1024**3
    max_image_pixels: int = 120_000_000  # ~120 MP; guards against decompression bombs
    max_video_duration_s: float = 3600.0
    max_video_side_px: int = 8192
    max_sources: int = 10_000

    def to_dict(self) -> dict:
        return asdict(self)


class RejectedInput(Exception):
    """Input is refused; message is safe to show to the user."""


def sniff(path: Path) -> str | None:
    """Return the container family from magic bytes: jpeg/png/tiff/isobmff, or None."""
    with open(path, "rb") as f:
        head = f.read(16)
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if len(head) >= 8 and head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free"):
        return "isobmff"
    return None


def classify(path: Path, limits: Limits) -> str:
    """Validate one input file and return 'image' or 'video'. Raises RejectedInput."""
    if path.is_symlink():
        raise RejectedInput("symlinks are not accepted")
    if not path.is_file():
        raise RejectedInput("not a regular file")
    ext = path.suffix.lower()
    expected = IMAGE_EXTS.get(ext) or VIDEO_EXTS.get(ext)
    if expected is None:
        raise RejectedInput(f"unsupported extension {ext or '(none)'}")
    actual = sniff(path)
    if actual != expected:
        raise RejectedInput(f"content does not match extension {ext} (detected {actual or 'unknown'})")
    size = path.stat().st_size
    kind = "image" if ext in IMAGE_EXTS else "video"
    cap = limits.max_image_bytes if kind == "image" else limits.max_video_bytes
    if size > cap:
        raise RejectedInput(f"file is {size} bytes; limit for {kind} is {cap}")
    if size == 0:
        raise RejectedInput("empty file")
    return kind
