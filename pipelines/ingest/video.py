"""Thin, bounded wrappers around ffprobe/ffmpeg."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from recon_core.limits import Limits, RejectedInput


class ToolError(RuntimeError):
    """External tool missing, crashed or timed out."""


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    if shutil.which(cmd[0]) is None:
        raise ToolError(f"{cmd[0]} not found on PATH")
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"{cmd[0]} timed out after {timeout}s") from e
    return proc


def tool_version(name: str) -> str:
    try:
        out = _run([name, "-hide_banner", "-version"], timeout=20).stdout.decode(errors="replace")
    except ToolError:
        return "unavailable"
    first = out.splitlines()[0] if out else ""
    parts = first.split()
    return parts[2] if len(parts) > 2 else first


def probe(path: Path, limits: Limits) -> dict:
    """Return video stream facts; raise RejectedInput for unreadable or over-limit videos."""
    proc = _run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format", "-of", "json", str(path)],
        timeout=60,
    )
    if proc.returncode != 0:
        raise RejectedInput("video could not be read by ffprobe")
    data = json.loads(proc.stdout or b"{}")
    streams = data.get("streams") or []
    if not streams:
        raise RejectedInput("no video stream")
    s = streams[0]
    width, height = int(s.get("width", 0)), int(s.get("height", 0))
    duration = float(s.get("duration") or data.get("format", {}).get("duration") or 0)
    num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if float(den or 0) else 0.0
    rotation = 0
    for sd in s.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    if "rotate" in (s.get("tags") or {}):
        rotation = int(s["tags"]["rotate"])
    if width <= 0 or height <= 0:
        raise RejectedInput("video has no valid dimensions")
    if max(width, height) > limits.max_video_side_px:
        raise RejectedInput(f"video side {max(width, height)}px exceeds {limits.max_video_side_px}px")
    if duration > limits.max_video_duration_s:
        raise RejectedInput(f"video is {duration:.0f}s; limit is {limits.max_video_duration_s:.0f}s")
    return {
        "codec": s.get("codec_name", "unknown"),
        "width_px": width,
        "height_px": height,
        "fps": round(fps, 6),
        "duration_s": round(duration, 6),
        "rotation_deg": rotation,
    }


def extract_frames(
    path: Path, out_dir: Path, interval_s: float, max_frames: int, timeout_s: float = 3600
) -> list[Path]:
    """Extract one frame every `interval_s` seconds (rotation applied by ffmpeg) as bit-exact JPEGs."""
    if max_frames <= 0:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "%06d.jpg"
    proc = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-vf",
            f"fps=1/{interval_s}",
            "-frames:v",
            str(max_frames),
            "-q:v",
            "2",
            "-threads",
            "1",
            "-map_metadata",
            "-1",
            "-fflags",
            "+bitexact",
            "-flags:v",
            "+bitexact",
            str(pattern),
        ],
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace").strip().splitlines()[-1:] or ["unknown error"]
        raise ToolError(f"ffmpeg frame extraction failed: {msg[0][:300]}")
    return sorted(out_dir.glob("*.jpg"))
