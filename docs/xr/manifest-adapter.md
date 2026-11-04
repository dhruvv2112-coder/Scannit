# Viewer manifest adapter (not a schema)

Status: **adapter** (account 3, updated 2026-11-04). The canonical artifact schema is Account 2's to decide (ADR pending; JSON Schema in PR #1 vs Pydantic in PR #3). The viewer does **not** define a competing schema. `apps/web/src/manifest.ts` reads what producers actually emit, normalises it, and shows every interpretation in the UI as a note. When the ADR lands, the adapter is replaced by the canonical types.

## Accepted today
| Producer | Shape | Verified by |
|---|---|---|
| Account 1, PR #8 `photogrammetry.run` (`artifact_manifest.json`) | nested row-major 4×4 `transform_4x4` mapping `scene_zup → gltf_yup`; `bounds_m.frame_id = gltf_yup`; `warnings`, `artifact_type`, `mesh_acceptance`, `source_image_manifest_uri` | Real output fixture `apps/web/public/fixtures/account1-pr8/` (see PROVENANCE.md) in unit + e2e tests |
| Viewer synthetic fixture | same convention as Account 1 | `fixtures/sample-site.manifest.json` |
| Earlier viewer proposal | flat 16, `gltf → scene` | unit test |

## Normalisation rules
- **GLB frame**: of `source_frame_id` / `target_frame_id`, exactly one must name glTF/GLB/Y-up (`/gltf|glb|y_?up/i`); otherwise the manifest is **refused** (no guessing). If the transform maps scene → GLB, the viewer uses its inverse and says so.
- **Placement**: object matrix = `SCENE_TO_VIEW · glb_to_scene`. For the standard Z-up→Y-up export this is the identity, so no rotation is applied; any other transform is applied *from manifest evidence* and noted.
- **Bounds / references**: each carries a frame id that must be one of the two frames; compared against the geometry in the scene frame (1 cm bounds tolerance, 1 mm reference endpoints).
- **Units**: `m`/`METRES` only. Anything else is refused, never converted.
- **Scale**: `arbitrary` → lengths shown as *model units*; `metric_unvalidated` → meters marked "not validated"; the "Verified metric scale" badge only for `metric_validated` with no integrity issues.
- **Origin**: `synthetic: true` → synthetic; `artifact_origin: "camera"` → camera-derived; otherwise *unknown*, never presented as camera-derived.
- **Producer warnings** are shown verbatim alongside viewer warnings.

## Requests to Account 2 (for the ADR)
1. Keep Account 1's direction (`scene_zup → gltf_yup`) or flip it, but state it in the schema; add an explicit `glb_frame_id` so the viewer needn't infer it from names.
2. Add `artifact_origin: camera | synthetic` (required) and `source_image_count`.
3. Routes the viewer calls today (mocked in `tests/viewer/e2e/api.spec.ts`): `GET /v0/jobs`, `GET /v0/jobs/{id}`, `GET /v0/jobs/{id}/artifacts/viewer` (manifest; `uri` = signed https URL). CORS `GET` from the viewer origin; signed URLs ≥ 15 min.
