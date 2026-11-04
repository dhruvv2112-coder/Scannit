// XR-001/XR-002 smoke: fixture manifest -> verified GLB -> scale/reference checks -> navigation -> 1 m measurement.
import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { viewer, waitReady, watchErrors } from './helpers';

/** POSITION of vertex `i` from a GLB under public/fixtures (first primitive, tightly packed float32 VEC3). */
function glbPoint(rel: string, i: number): number[] {
  const b = readFileSync(resolve(__dirname, '../../../apps/web/public/fixtures', rel));
  const jl = b.readUInt32LE(12);
  const g = JSON.parse(b.subarray(20, 20 + jl).toString('utf8'));
  const acc = g.accessors[g.meshes[0].primitives[0].attributes.POSITION];
  const off = 20 + jl + 8 + (g.bufferViews[acc.bufferView].byteOffset ?? 0) + (acc.byteOffset ?? 0) + i * 12;
  return [b.readFloatLE(off), b.readFloatLE(off + 4), b.readFloatLE(off + 8)];
}

test('loads the fixture manifest, verifies it and keeps authored scale', async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto('/');
  await waitReady(page);

  const v = (await viewer(page))!;
  expect(v.loaded!.artifact_id).toBe('synthetic-sample-site-v1');
  expect(v.loaded!.issues).toEqual([]);
  expect(v.loaded!.refOk).toBe(true);
  expect(v.lifecycle).toBe('unvalidated_scale');
  await expect(page.getByTestId('origin')).toContainText('synthetic');
  await expect(page.getByTestId('scale')).toContainText('REF_1m 1.000 m on geometry');
  // Synthetic + metric_unvalidated must never show the verified badge.
  await expect(page.getByTestId('scale-badge')).toHaveText('Metric scale not validated');
  await expect(page.getByTestId('warnings')).toContainText('synthetic artifact');
  await expect(page.getByTestId('download')).toHaveAttribute('href', /samples\/sample-site\.glb$/);
  await expect.poll(async () => (await viewer(page))!.stats.tris, { timeout: 10_000 }).toBeGreaterThan(0);
  await page.screenshot({ path: 'test-results/xr-002-fixture-loaded.png' });
  expect(errors).toEqual([]);
});

test('pointer orbit, fly keys and R re-centre the camera', async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto('/');
  await waitReady(page);
  await expect.poll(async () => (await viewer(page))!.stats.camTarget, { timeout: 10_000 }).not.toBe('—');
  const home = (await viewer(page))!.stats.camTarget;

  const box = (await page.locator('canvas').boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5);
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(box.x + box.width * 0.6 + 150, box.y + box.height * 0.5 + 60, { steps: 10 });
  await page.mouse.up({ button: 'right' });
  await expect.poll(async () => (await viewer(page))!.stats.camTarget, { timeout: 5_000 }).not.toBe(home);

  await page.keyboard.press('r');
  await expect.poll(async () => (await viewer(page))!.stats.camTarget, { timeout: 5_000 }).toBe(home);

  await page.keyboard.press('f');
  await page.keyboard.down('w');
  await page.waitForTimeout(600);
  await page.keyboard.up('w');
  await expect.poll(async () => (await viewer(page))!.stats.camTarget, { timeout: 5_000 }).not.toBe(home);
  await page.keyboard.press('f');
  await page.keyboard.press('r');
  await expect.poll(async () => (await viewer(page))!.stats.camTarget, { timeout: 5_000 }).toBe(home);
  expect(errors).toEqual([]);
});

test('two-point measurement of the 1 m reference reads 1.000 m in the scene frame', async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto('/');
  await waitReady(page);
  const ends = (await viewer(page))!.refEndpointsViewer;
  expect(ends).toHaveLength(2);

  // Camera only: look at the bar from the front, close enough to click its corners.
  await page.evaluate(() => window.__viewer!.frameOn([-0.6, -0.05, 1.9], [0.6, 0.15, 2.05], [0.15, 0.35, -1]));
  await page.waitForTimeout(300);
  await page.keyboard.press('m');
  await expect(page.getByTestId('measure')).toBeVisible();

  for (const e of ends) {
    // Aim 1 cm inside the bottom corner (toward the bar centre, 1 cm up); snapping must land on the corner.
    const aim = [e[0] - Math.sign(e[0]) * 0.01, e[1] + 0.01, e[2]];
    const pt = await page.evaluate((p) => window.__viewer!.project(p), aim);
    expect(pt).not.toBeNull();
    await page.mouse.click(pt!.x, pt!.y);
  }
  await expect.poll(async () => (await viewer(page))!.measurement.distance_m, { timeout: 5_000 }).not.toBeNull();
  const m = (await viewer(page))!.measurement;
  expect(m.snapped).toEqual([true, true]);
  expect(m.distance_m!).toBeCloseTo(1.0, 6);
  await expect(page.getByTestId('distance')).toContainText('1.000 m');
  await expect(page.getByTestId('distance')).toContainText('not validated');
  await page.screenshot({ path: 'test-results/xr-002-measure-1m.png' });

  await page.keyboard.press('Escape');
  await expect.poll(async () => (await viewer(page))!.measurement.points.length).toBe(0);
  expect(errors).toEqual([]);
});

test('clicking an object selects and highlights it', async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto('/');
  await waitReady(page);
  // Tank A centre in viewer space is (-8, 4, -6).
  const pt = await page.evaluate(() => window.__viewer!.project([-8, 4, -3]));
  await page.mouse.click(pt!.x, pt!.y);
  await expect(page.getByTestId('selected')).toContainText('TANK_A');
  expect(errors).toEqual([]);
});

test("Account 1's real PR #8 output loads: point cloud, arbitrary scale, synthetic provenance", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto('/?scene=account1-pr8-synthetic');
  await waitReady(page);
  const v = (await viewer(page))!;
  expect(v.lifecycle).toBe('unvalidated_scale');
  expect(v.loaded!.artifact_id).toBe('glb-78e25b013f1f129f');
  expect(v.loaded!.sha256).toBe('78e25b013f1f129f89f5433cfd6800174e013f7c59d601f05e6036fcdde36606');
  expect(v.loaded!.origin).toBe('synthetic');
  expect(v.loaded!.points).toBe(12497);
  expect(v.loaded!.sourceImages).toBe(12);
  expect(v.loaded!.placed).toBe(false); // standard Z-up -> Y-up export: no extra rotation applied
  expect(v.loaded!.issues).toEqual([]);
  await expect(page.getByTestId('lifecycle')).toHaveText(/scale not validated/);
  await expect(page.getByTestId('scale-badge')).toHaveText('Arbitrary scale — not metric');
  await expect(page.getByTestId('origin')).toHaveText('synthetic · point_cloud_fallback');
  await expect(page.getByTestId('notes')).toContainText('uses its inverse');
  await expect(page.getByTestId('warnings')).toContainText('do not measure');
  await expect(page.getByTestId('warnings')).toContainText('mesh acceptance: not_met');
  await expect.poll(async () => (await viewer(page))!.stats.calls, { timeout: 10_000 }).toBeGreaterThan(0);
  await page.screenshot({ path: 'test-results/xr-002-account1-pr8-points.png' });

  // Measuring on arbitrary scale reports model units, never meters.
  await page.keyboard.press('m');
  // Click the projected positions of two real points read straight from the GLB (identity placement).
  for (const idx of [100, 9000]) {
    const pt = await page.evaluate((p) => window.__viewer!.project(p), glbPoint('account1-pr8/model.glb', idx));
    expect(pt).not.toBeNull();
    await page.mouse.click(pt!.x, pt!.y);
  }
  await expect.poll(async () => (await viewer(page))!.measurement.distance_m, { timeout: 5_000 }).not.toBeNull();
  await expect(page.getByTestId('distance')).toContainText('model units');
  await expect(page.getByTestId('distance')).not.toContainText(/\d m\b/);
  expect(errors).toEqual([]);
});
