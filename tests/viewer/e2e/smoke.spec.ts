// XR-001/XR-002 smoke: fixture manifest -> verified GLB -> scale/reference checks -> navigation -> 1 m measurement.
import { expect, test } from '@playwright/test';
import { viewer, waitReady, watchErrors } from './helpers';

test('loads the fixture manifest, verifies it and keeps authored scale', async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto('/');
  await waitReady(page);

  const v = (await viewer(page))!;
  expect(v.loaded!.artifact_id).toBe('synthetic-sample-site-v1');
  expect(v.loaded!.issues).toEqual([]);
  expect(v.loaded!.refOk).toBe(true);
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
