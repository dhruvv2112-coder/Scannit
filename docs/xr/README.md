# XR viewer (account 3)

## Run
```bash
cd apps/web
npm ci
npm run sample      # regenerate the synthetic GLB + fixture manifest (sha256 computed)
npm run dev         # http://localhost:5173 — bundled fixtures
# against an API:   http://localhost:5173/?source=api&api=https://<account-2-api>
npm run lint && npm test && npm run build
npm run test:e2e    # set PW_CHROME_PATH=/path/to/chrome if Playwright's Chromium isn't installed
```
URL options: `source=fixtures|api`, `api=<base>`, `scene=<id>`, `poll=<ms>` (default 5000), `pollMax=<n>` (default 120). Env: `VITE_SCANNIT_API`.

## Data flow
scene list (fixtures index or `GET /v0/jobs`) → lifecycle (uploaded / queued / processing / ready / failed / unvalidated_scale) → poll the job until it reaches a terminal state (bounded, cancellable) → manifest (`parseManifest`) → GLB fetch (abort, size cap, GLB header check, **sha256 must match**) → parse → integrity report (transform rigidity, bounds vs geometry, reference endpoints on geometry) → render untouched.

## Conventions
- Viewer world is the scene frame turned Y-up. Each GLB is placed with `SCENE_TO_VIEW · glb_to_scene` from its manifest, which is the identity for the standard Z-up→Y-up export. Nothing rescales or normalises the scene; "Fit to scene" moves only the camera.
- Points and distances are reported in the scene frame (+Z up). Transforms and inverses are unit-tested. Arbitrary-scale artifacts report *model units*, never meters.
- Meshes and point clouds are both supported (point picks are exact point records).
- "Verified metric scale" appears only for `scale_status = metric_validated` with no integrity issues. Synthetic data is always flagged.
- "Download original GLB" serves the produced artifact as-is. It is not a new export.
- Manifest adapter (accepts Account 1 PR #8 output as-is): [manifest-adapter.md](manifest-adapter.md).

## Keys
Drag orbit/look · right-drag pan · wheel zoom · `G` walk (WASD/arrows, Shift; 1.7 m eye height, collision, follows ground) · `F` fly (+Q/E) · double-click: teleport (walk/fly) or re-centre (orbit) · `M` measure (snaps to vertices/points) · click: inspect · `Esc` clear · `R` reset · Reduced-motion toggle (defaults to the OS setting).

## Other tools
- **Open local file…**: a `.glb` (optionally with its manifest `.json`) from this computer, shown as **LOCAL PREVIEW — not an integrated result**. Without a manifest: hash unverified, scale arbitrary, origin unknown.
- **Download metadata JSON**: what the viewer received and verified (sha256, frames, transforms, bounds, warnings, raw manifest). Not a new export.
- **Tiled scenes**: `index.json` entries with `tiles: [...]` load progressively, each placed by its own manifest transform; GPU memory is released when the scene changes.
