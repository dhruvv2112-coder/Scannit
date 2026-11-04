// XR-004 / XR-006 / XR-003 (minimal): walk + collision + teleport, reduced motion, inspect,
// LOCAL PREVIEW, metadata JSON, two-tile progressive load with GPU disposal.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { maybeThrottle, viewer, waitReady, watchErrors } from './helpers';

test.beforeEach(async ({ page }) => { await maybeThrottle(page); });

const PUB = resolve(__dirname, '../../../apps/web/public');
const cam = (page: Page) => page.evaluate(() => window.__viewer!.camera);

test('walk: teleport onto the ground, then walking into the shed is blocked', async ({ page }) => {
  test.setTimeout(90_000);
  const errors = watchErrors(page);
  await page.goto('/');
  await waitReady(page);
  // Fly mode keeps the overview, so ground 2.5 m in front of the shed is visible to double-click
  // (shed spans x 3..15, z -7..-1 in viewer space; (9, 0, 1.5) has a clear line of sight past the pipe rack).
  await page.keyboard.press('f');
  await expect.poll(async () => (await viewer(page))!.nav).toBe('fly');
  const p = await page.evaluate(() => window.__viewer!.project([9, 0, 1.5]));
  await page.mouse.dblclick(p!.x, p!.y);
  await expect(page.getByTestId('nav-msg')).toContainText('Teleported');
  await page.keyboard.press('g');
  await expect.poll(async () => (await viewer(page))!.nav).toBe('walk');
  await expect(page.getByTestId('nav-msg')).toContainText('1.7 m eye height');
  await expect.poll(async () => (await cam(page))[1], { timeout: 5_000 }).toBeCloseTo(1.7, 1);
  const start = await cam(page);
  expect(start[0]).toBeCloseTo(9, 0);
  expect(start[2]).toBeCloseTo(1.5, 0);

  // Hold W until the walker has stopped (blocked) rather than for a fixed time: software-GL CI runners
  // stall for whole seconds, so wall-clock time is not walking time.
  await page.keyboard.down('w');
  const trace: string[] = [];
  let prev = start, still = 0;
  for (let i = 0; i < 60 && still < 4; i++) {
    await page.waitForTimeout(500);
    const now = await cam(page), v = (await viewer(page))!;
    trace.push(`${(i + 1) * 0.5}s cam=${now.map((x) => x.toFixed(2)).join(',')} fps=${v.stats.fps} nav=${v.nav}`);
    const progressed = Math.hypot(now[0] - start[0], now[2] - start[2]) > 0.5;
    still = progressed && Math.hypot(now[0] - prev[0], now[2] - prev[2]) < 0.005 ? still + 1 : 0;
    prev = now;
  }
  await page.keyboard.up('w');
  const end = await cam(page);
  console.log(`walk trace (start ${start.map((x) => x.toFixed(2)).join(',')}):\n  ${trace.join('\n  ')}`);
  const moved = Math.hypot(end[0] - start[0], end[2] - start[2]);
  expect(moved).toBeGreaterThan(2);                                   // it walked
  const insideShed = end[0] > 3 && end[0] < 15 && end[2] > -7 && end[2] < -1;
  expect(insideShed).toBe(false);                                      // but not through the wall
  expect(end[1]).toBeCloseTo(1.7, 1);                                  // stayed on the ground
  expect(errors).toEqual([]);
});

test('walk falls back to fly on a points-only artifact', async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto('/?scene=account1-pr8-synthetic');
  await waitReady(page);
  await page.keyboard.press('g');
  await expect(page.getByTestId('nav-msg')).toContainText('points only');
  expect((await viewer(page))!.nav).toBe('fly');
  expect(errors).toEqual([]);
});

test('reduced motion follows the OS setting and can be toggled', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await waitReady(page);
  await expect(page.getByTestId('reduced-motion')).toBeChecked();
  expect((await viewer(page))!.reducedMotion).toBe(true);
  await page.getByTestId('reduced-motion').uncheck();
  expect((await viewer(page))!.reducedMotion).toBe(false);
});

test('inspect panel shows node, asset and scene-frame position', async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto('/');
  await waitReady(page);
  const pt = await page.evaluate(() => window.__viewer!.project([-8, 4, -3]));
  await page.mouse.click(pt!.x, pt!.y);
  await expect(page.getByTestId('inspect-node')).toContainText('TANK_A');
  const ins = (await viewer(page))!.inspect!;
  expect(ins.artifact_id).toBe('synthetic-sample-site-v1');
  expect(ins.scene[2]).toBeGreaterThan(0); // scene frame is Z-up: a point on the tank is above ground
  expect(errors).toEqual([]);
});

test('point-cloud inspect links the source image manifest', async ({ page }) => {
  await page.goto('/?scene=account1-pr8-synthetic');
  await waitReady(page);
  const b = readFileSync(`${PUB}/fixtures/account1-pr8/model.glb`);
  const jl = b.readUInt32LE(12); const g = JSON.parse(b.subarray(20, 20 + jl).toString());
  const off = 20 + jl + 8 + (g.bufferViews[g.accessors[g.meshes[0].primitives[0].attributes.POSITION].bufferView].byteOffset ?? 0) + 500 * 12;
  const pt = await page.evaluate((p) => window.__viewer!.project(p), [b.readFloatLE(off), b.readFloatLE(off + 4), b.readFloatLE(off + 8)]);
  await page.mouse.click(pt!.x, pt!.y);
  await expect(page.getByTestId('inspect')).toBeVisible();
  expect((await viewer(page))!.inspect!.pointIndex).not.toBeNull();
  await expect(page.getByTestId('inspect-source')).toHaveAttribute('href', /account1-pr8\/ingest\/manifest\.json$/);
});

test('LOCAL PREVIEW: a GLB without a manifest is labelled, unverified and arbitrary-scale', async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto('/');
  await waitReady(page);
  await page.getByTestId('local-file').setInputFiles(`${PUB}/samples/sample-site.glb`);
  await expect(page.getByTestId('local-banner')).toHaveText('LOCAL PREVIEW — not an integrated result');
  await waitReady(page);
  const v = (await viewer(page))!;
  expect(v.local).toBe(true);
  expect(v.loaded!.verified).toBe(false);
  expect(v.loaded!.scale_status).toBe('arbitrary');
  expect(v.loaded!.origin).toBe('unknown');
  expect(v.lifecycle).toBe('unvalidated_scale');
  await expect(page.getByTestId('scale-badge')).toHaveText('Arbitrary scale — not metric');
  await expect(page.getByTestId('download')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('LOCAL PREVIEW with a manifest verifies the hash (mismatch is refused)', async ({ page }) => {
  await page.goto('/');
  await waitReady(page);
  await page.getByTestId('local-file').setInputFiles([`${PUB}/fixtures/account1-pr8/model.glb`, `${PUB}/fixtures/account1-pr8/artifact_manifest.json`]);
  await waitReady(page);
  expect((await viewer(page))!.loaded!.verified).toBe(true);
  await page.getByTestId('local-file').setInputFiles([`${PUB}/samples/sample-site.glb`, `${PUB}/fixtures/account1-pr8/artifact_manifest.json`]);
  await expect(page.getByTestId('status')).toContainText('hash_mismatch');
  expect((await viewer(page))!.loaded).toBeNull();
});

test('metadata JSON download describes the verified artifact and its transform', async ({ page }) => {
  await page.goto('/?scene=account1-pr8-synthetic');
  await waitReady(page);
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByTestId('download-metadata').click()]);
  expect(dl.suggestedFilename()).toBe('glb-78e25b013f1f129f.metadata.json');
  const meta = JSON.parse(readFileSync((await dl.path())!, 'utf8'));
  const t = meta.tiles[0];
  expect(t.sha256).toBe('78e25b013f1f129f89f5433cfd6800174e013f7c59d601f05e6036fcdde36606');
  expect(t.sha256_verified_against_manifest).toBe(true);
  expect(t.scale_status).toBe('arbitrary');
  expect(t.frames.glb).toBe('gltf_yup');
  expect(t.frames.glb_to_scene_row_major).toEqual([1, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1]);
  expect(t.geometry.points).toBe(12497);
  expect(meta.note).toMatch(/Not a new export/);
});

test('two tiles load progressively with their own transforms; switching scenes frees GPU geometry', async ({ page }) => {
  const errors = watchErrors(page);
  // Fresh baseline for the points scene.
  await page.goto('/?scene=account1-pr8-synthetic');
  await waitReady(page);
  await expect.poll(async () => (await viewer(page))!.stats.geometries, { timeout: 10_000 }).toBeGreaterThan(0);
  await page.waitForTimeout(800);
  const baseline = (await viewer(page))!.stats.geometries;

  await page.goto('/?scene=two-tiles');
  await waitReady(page);
  await expect(page.getByTestId('tiles')).toHaveText('Tiles 2 / 2');
  const v = (await viewer(page))!;
  expect(v.tiles.map((t) => t.artifact_id)).toEqual(['synthetic-tile-w-v1', 'synthetic-tile-e-v1']);
  expect(v.tiles.every((t) => t.placed)).toBe(true);       // per-tile translation applied from manifests
  expect(v.tiles.every((t) => t.issues.length === 0)).toBe(true);
  // Tile W's tank (local x=+2) must land at scene x = -10 + 2 = -8.
  const pt = await page.evaluate(() => window.__viewer!.project([-8, 4, -6]));
  await page.mouse.click(pt!.x, pt!.y);
  await expect(page.getByTestId('inspect-node')).toContainText('TANK_W');
  await page.screenshot({ path: 'test-results/xr-003-two-tiles.png' });
  await expect.poll(async () => (await viewer(page))!.stats.geometries, { timeout: 10_000 }).toBeGreaterThan(baseline);
  const withTiles = (await viewer(page))!.stats.geometries;

  await page.getByTestId('scene-select').selectOption('account1-pr8-synthetic');
  await waitReady(page);
  await expect.poll(async () => (await viewer(page))!.stats.geometries, { timeout: 10_000 }).toBe(baseline);
  expect(withTiles).toBeGreaterThan(baseline);
  expect(errors).toEqual([]);
});
