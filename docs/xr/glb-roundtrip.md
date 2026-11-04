# GLB round-trip: Blender 5.0.1 ↔ browser (XR-007, early)

Run 2026-11-04 with `bpy` 5.0.1 (Python 3.11):
`python tests/viewer/roundtrip/blender_roundtrip.py <out> <glb…>` imports each GLB into a factory-reset Blender, exports it again as GLB (+Y up), and compares the original and exported files with an **independent** GLB parser (no Blender code in the comparison). Full output: [`tests/viewer/roundtrip/out/report.json`](../../tests/viewer/roundtrip/out/report.json). Browser side: `tests/viewer/unit/roundtrip.test.ts` loads the Blender-exported file through the viewer's own manifest, placement and integrity code.

## Results
| Input | Geometry (bounds, per-node size/position, up axis) | Counts | Attributes | Materials / PBR factors | `asset.extras` |
|---|---|---|---|---|---|
| `sample-site.glb` (synthetic mesh) | ✅ max Δ 9.5e-8 m | ✅ 482 tris | ✅ POSITION, NORMAL | ✅ names; baseColor/metallic/roughness Δ ≤ 2.4e-8 | ❌ dropped |
| `tile-w.glb` (synthetic tile) | ✅ Δ 0 | ✅ 194 tris | ✅ | ⚠️ unused material `cladding` removed (not referenced by any mesh) | ❌ dropped |
| Account 1 `model.glb` (PR #8, 12,497 points), default export | ❌ **mesh omitted entirely** | ❌ 12,497 → 0 points | ❌ | n/a | ❌ dropped |
| same, `use_mesh_vertices=True` | ✅ Δ 0 | ✅ 12,497 points | ✅ POSITION, COLOR_0 | ⚠️ `DefaultMaterial` added | ❌ dropped |

Browser check of the Blender-exported sample: the original manifest (sha256/uri updated) passes with **no integrity issues**; `REF_1m` = 1.000 m on geometry, tank 8 m, pipe rack 24.4 m, placement identity.

## What this means
- **Meter scale, handedness and the Y-up convention survive a Blender round-trip** for meshes, to float precision.
- **Scale/frame metadata must not live in GLB `asset.extras`.** Blender strips it. The viewer never reads GLB extras; it uses the sidecar manifest, and "Download metadata JSON" captures what the viewer verified. Producers should treat extras as best-effort only.
- **Point clouds:** a Blender user must enable *Include → Loose Points* (`use_mesh_vertices`), or the sparse fallback disappears from the export.
- **Supported material channels confirmed:** baseColorFactor, metallicFactor, roughnessFactor. **Not yet tested:** textures (baseColor, normal, metallic-roughness, occlusion, emissive), KTX2, Draco. No producer emits textured GLBs yet.
- Not covered: USD/USDZ (deferred by the brief), animation, skinning.
