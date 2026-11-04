# ADR-0001 — Canonical interchange contracts, units, frames and tracker structure

| Field        | Value                                             |
|--------------|---------------------------------------------------|
| Status       | **Accepted**                                      |
| Date         | 2026-11-04                                        |
| Authors      | Account 2 (platform integration lead)             |
| Reviewers    | Account 1 (reconstruction), Account 3 (XR/viewer) |

## Context

Three independent workers share the Scannit repo. Each introduced schemas,
CI, and tracker entries on separate branches (PR #1 PLAN-000, PR #3 PLT-001,
PR #4 XR-001). Without a single canonical contract, merge conflicts multiply
and semantic drift silently breaks the pipeline.

## Decision

### 1. Canonical interchange format

JSON Schema 2020-12 files under `schemas/v0/` are the single source of truth.
Both PR #1 and PR #3 carry **byte-identical** copies of these schemas; this ADR
formalises that fact.

| Schema                          | Scope                                               |
|---------------------------------|-----------------------------------------------------|
| `common.schema.json`            | Shared definitions: UUIDs, timestamps, enums         |
| `camera_model.schema.json`      | Intrinsic/extrinsic per camera                       |
| `ingest_manifest.schema.json`   | Ingested frames, blur/duplicate metadata              |
| `reconstruction_job.schema.json`| Job state, stages, parameters, failure classification |
| `mesh_artifact.schema.json`     | Textured mesh, LOD, bounding box, hash                |
| `texture_artifact.schema.json`  | UV-mapped texture atlas metadata                      |
| `spatial_tile.schema.json`      | Hierarchical tile bounds + transforms                 |
| `quality_report.schema.json`    | Scale error, checkpoint metrics, confidence           |
| `transform.schema.json`         | 4×4 affine or TRS with named frame                    |

Contract tests live in `tests/schemas/test_schemas.py` and validate every
schema plus every `schemas/v0/examples/*.example.json` fixture.

### 2. Generated / validated Pydantic v2 models

`packages/shared-schemas/scannit_schemas/models.py` provides Pydantic v2
models that map 1:1 to the JSON Schemas. Cross-validation tests
(`tests/schemas/test_cross_validation.py`) assert round-trip fidelity.

When generation cannot preserve behaviour, the adapter is documented in the
model's docstring and tested explicitly.

### 3. Units, frames and geometry

| Property              | Convention                                         |
|-----------------------|----------------------------------------------------|
| Linear unit           | **Meters**, everywhere                             |
| Internal 3D frame     | **Z-up** (right-handed)                            |
| Exported GLB frame    | **Y-up** (per glTF spec)                           |
| Pose direction        | Explicit `direction` field in transform schema      |
| Frame transforms      | Full 4×4 affine; TRS shorthand with named `frame`   |
| `scale_status`        | Tri-state enum: `unscaled`, `reference_scaled`, `survey_scaled` |
| Tile bounds           | 3D axis-aligned bounding box; optional 2D WGS-84 footprint |

### 4. ID namespaces

| Prefix | Workstream     | Example        |
|--------|----------------|----------------|
| `PLT-` | Platform       | `PLT-001`      |
| `REC-` | Reconstruction | `REC-001`      |
| `XR-`  | Viewer / XR    | `XR-001`       |

Legacy `VWR-` IDs are aliased to `XR-` equivalents. No duplicate IDs across
namespaces.

### 5. Tracker structure

Canonical tracker: `docs/project_plan/features.csv`.

Required columns (PR #3 superset wins):

```
ID, Workstream, Feature, Owner, Priority, Dependencies, Status,
Acceptance, Tests, PR, Commit, Demo, Blocker, Updated_IST
```

Additional audit columns when needed: `Evidence_URL`, `Acceptance_Status`,
`Merge_SHA`, `Last_Verified_IST`.

Status values: `NOT_STARTED`, `IN_PROGRESS`, `IN_REVIEW`, `DONE`, `BLOCKED`.

`DONE` requires: merged SHA + passing required CI + acceptance artifact or
test evidence. `IN_REVIEW` is never `DONE`.

### 6. CI workflow ownership

`ci.yml` is owned by Account 2 (platform). It defines these required check
names:

- `lint` — ruff check + format
- `test-json-schemas` — canonical JSON Schema validation
- `test-pydantic-schemas` — Pydantic model tests
- `test-api` — API endpoint tests
- `test-orchestrator` — state machine + worker tests
- `build` — Docker image build

Reconstruction and XR use **separate workflow files**:
- `.github/workflows/recon-demo.yml` (Account 1)
- `.github/workflows/xr-web.yml` (Account 3)

### 7. Handoff convention

All handoffs under `docs/handoffs/account{1,2,3}/YYYY-MM-DD.md`.

### 8. Merge order for current PR stack

1. PR #1 (PLAN-000) — contracts and schemas (base: main)
2. PR #3 (PLT-001) — platform skeleton (base: main → rebase after #1 merges)
3. PR #4 (XR-001) — viewer skeleton (base: main, independent — can merge any time after #1)
4. PR #2 (REC-001) — ingest (base: #1 → retarget to main after #1 merges)
5. PR #5 (REC-002) — sparse recon (base: #2 → retarget after #2)
6. PR #7 (XR-002) — manifest loader (base: #4 → retarget after #4)
7. PR #8 (REC-003) — first GLB (base: #5 → retarget after #5)
8. PR #6 (HANDOFF) — docs only (base: main)

Conflict resolution at step 2: PLT-001's `ci.yml`, `features.csv` and
`test_schemas.py` win; they are strict supersets of PLAN-000's versions.

## Consequences

- One JSON Schema source, one tracker CSV, one CI owner per workflow.
- Merge conflicts are confined to the first two merges; the rest stack cleanly.
- XR branch is fully independent and unblocked.
- Contract tests catch schema drift before merge.
