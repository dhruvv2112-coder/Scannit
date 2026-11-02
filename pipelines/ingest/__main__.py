"""CLI: python -m ingest INPUT [INPUT ...] --out DIR

Exit codes: 0 ok, 2 invalid arguments/options, 3 no usable frames, 4 external tool failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from recon_core.limits import RejectedInput

from .core import IngestOptions, NoUsableFrames, run
from .video import ToolError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ingest", description="Ingest stills/video into a deduplicated frame set.")
    ap.add_argument("inputs", nargs="+", type=Path, help="image/video files or directories (non-recursive)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--interval", type=float, default=0.5, help="seconds between extracted video frames")
    ap.add_argument("--max-frames", type=int, default=2000)
    ap.add_argument("--blur-threshold", type=float, default=60.0)
    ap.add_argument("--dedupe-hamming", type=int, default=4)
    a = ap.parse_args(argv)

    files: list[Path] = []
    for p in a.inputs:
        files.extend(sorted(q for q in p.iterdir() if q.is_file()) if p.is_dir() else [p])
    try:
        opts = IngestOptions(a.interval, a.max_frames, a.blur_threshold, a.dedupe_hamming)
        manifest = run(files, a.out, opts)
    except (ValueError, RejectedInput) as e:
        print(f"ingest: {e}", file=sys.stderr)
        return 2
    except NoUsableFrames as e:
        print(f"ingest: {e}", file=sys.stderr)
        return 3
    except ToolError as e:
        print(f"ingest: {e}", file=sys.stderr)
        return 4
    print(json.dumps(manifest["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
