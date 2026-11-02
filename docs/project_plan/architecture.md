# Architecture (proposal v0, account1)

Status: **proposal**. Accounts 2 and 3 should edit or approve via PR before major implementation builds on it.

## Pipeline

```
upload ─▶ ingest ─▶ features/matching ─▶ sparse SfM ─▶ metric scale ─▶ dense MVS ─▶ mesh ─▶ texture ─▶ tiling/LOD ─▶ quality report
 (gw)    REC-001        REC-002            REC-002       REC-004        REC-003    REC-003  REC-003    REC-005        REC-007
                                                                                                         │
                                                                                     GLB export + viewer (account3)
```

Each stage writes its outputs under `work/<job_id>/<stage>/` plus a `stage.json` completion marker with input/output checksums. A stage is skipped on resume if its marker exists and its input checksums still match (REC-006).

Tools are wrapped, not reimplemented: FFmpeg/ffprobe (video), OpenCV (blur, hashing), PyCOLMAP/COLMAP (SfM, MVS), OpenMVS (mesh refine + texturing where compatible), Open3D (point/mesh utilities).

## Coordinate conventions (binding once schemas/v0 merges)

- **Units:** meters everywhere in interchange files. Millimetres appear only in fields whose name ends in `_mm` (QA errors).
- **Scene frame:** right-handed, **Z-up**. When georeferenced, the scene frame is local ENU (X=east, Y=north, Z=up) about a stated WGS84 origin; otherwise origin and orientation are arbitrary but stated in `CoordinateFrame.origin_description`.
- **Camera frame:** OpenCV/COLMAP: X right, Y down, Z forward.
- **Transforms:** `p_to = scale · R · p_from + t`, with `R` given as a unit quaternion `[w, x, y, z]` and `t` in meters. Every transform names `from_frame` and `to_frame`.
- **glTF/GLB is Y-up.** The export step (account3) converts Z-up → Y-up and records that in the export manifest; reconstruction outputs never pre-rotate.

## Scale honesty

Every geometry artifact carries `scale_status`:

- `arbitrary`: SfM output with no metric reference. Must not be presented as true scale.
- `metric_unvalidated`: scaled from references, but no independent checkpoints.
- `metric_validated`: scaled, and checkpoint errors reported in the QualityReport.

Monocular imagery without a metric reference is rejected for "true scale" requests.

Texture/colour: `color_validation` is `unvalidated` unless a colour chart was captured and checked; colours are never described as exact otherwise.

## Proposed reconstruction API (account2 implements the gateway)

| Method | Path | Body / result |
|---|---|---|
| POST | `/v1/reconstruction/jobs` | `ReconstructionJob` (inputs + options) → job with `job_id` |
| GET | `/v1/reconstruction/jobs/{id}` | `ReconstructionJob` with stage status |
| GET | `/v1/reconstruction/jobs/{id}/artifacts` | list of `MeshArtifact`, `TextureArtifact`, `SpatialTile` |
| GET | `/v1/reconstruction/jobs/{id}/quality` | `QualityReport` |

account1 supplies importable worker routines in `services/reconstruction/` (enqueue, run stage, read status) so the gateway does not shell out.

## Data handling

Source imagery and job outputs stay out of Git (`.gitignore` covers `data/`, `work/`, `outputs/`, video and DNG). Object storage paths are versioned (`<bucket>/<project>/<dataset>/v<N>/`). Test fixtures are generated synthetically or come from permissively licensed sources recorded in a `LICENSE`/`SOURCE` note next to them.
