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
scene list (fixtures index or `GET /v0/jobs`) → poll the job until it reaches a terminal state (bounded, cancellable) → manifest (`parseManifest`) → GLB fetch (abort, size cap, GLB header check, **sha256 must match**) → parse → integrity report (transform rigidity, bounds vs geometry, reference endpoints on geometry) → render untouched.

## Conventions
- GLB is shown in its own node space (+Y up, meters). Nothing rescales or normalises the scene; "Fit to scene" moves only the camera.
- Points and distances are reported in the manifest's `target_frame_id` (scene, +Z up). The transform and its inverse are unit-tested.
- "Verified metric scale" appears only for `scale_status = metric_validated` with no integrity issues. Synthetic data is always flagged.
- "Download original GLB" serves the produced artifact as-is. It is not a new export.
- Manifest contract proposal: [manifest-proposal.md](manifest-proposal.md).

## Keys
Drag orbit · right-drag pan · wheel zoom · `F` fly (WASD, Q/E, Shift) · `M` measure (snaps to vertices) · `Esc` clear · `R` fit · `+`/`-` zoom.
