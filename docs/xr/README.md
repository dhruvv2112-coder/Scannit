# XR viewer (account 3)

## Run
```bash
cd apps/web
npm ci
npm run sample      # regenerate public/samples/sample-site.glb
npm run dev         # http://localhost:5173
npm run lint && npm test && npm run build
npm run test:e2e    # set PW_CHROME_PATH=/path/to/chrome if Playwright's own Chromium isn't installed
```

## Conventions (current)
- Scene units are meters; +Y up; right-handed (glTF 2.0). The viewer must never rescale an asset silently.
- The `REF_1m` node is the software-scale gate: it must measure 1.000 m after load. This does not validate reconstruction accuracy.
- Sample data under `public/samples/` is synthetic and flagged `asset.extras.synthetic = true`.
