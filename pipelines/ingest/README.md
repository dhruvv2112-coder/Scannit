# Ingest (REC-001)

```
make recon-smoke                        # synthetic fixture -> work/recon/smoke/manifest.json
PYTHONPATH=packages/reconstruction-core/src:pipelines \
  python -m ingest PATH... --out DIR [--interval 0.5] [--max-frames 2000] [--blur-threshold 60] [--dedupe-hamming 4]
```

Exit codes: `0` ok · `2` invalid arguments or options · `3` no usable frames · `4` ffmpeg/ffprobe failure.

What it does, in order, for inputs sorted by (file name, sha256):

1. **Validate**: extension must be jpg/jpeg/png/tif/tiff/mp4/mov **and** match magic bytes; no symlinks; size, pixel-count (decompression bomb), video duration and resolution limits (`recon_core.limits.Limits`). Rejected sources stay in the manifest with a reason.
2. **Stage**: stills are copied byte-for-byte (EXIF preserved); stills with EXIF orientation ≠ 1 are written upright as lossless PNG so COLMAP sees the true geometry. Videos: one frame per `--interval` seconds via ffmpeg (rotation applied, bit-exact JPEG q2). Timestamps are `index × interval`.
3. **Score**: variance of Laplacian on a ≤1024 px grayscale copy (sharpness) and a 64-bit dHash.
4. **Reject**: sharpness below `--blur-threshold` → `rejected/`; dHash within `--dedupe-hamming` bits of any already-kept frame → `rejected/` with `duplicate_of`.
5. **Manifest**: `DIR/manifest.json` (schema `schemas/v0/ingest_manifest.schema.json`), sorted keys, no wall-clock fields. Same inputs + options + tool versions ⇒ byte-identical manifest and frames.

Known limits (to revisit with real data): the blur threshold of 60 is a starting heuristic and must be calibrated on real captures; dedupe compares against every kept frame (fine to a few thousand frames, will need windowing beyond that); video timestamps are nominal, not decoded PTS.
