// XR-001 keyboard/pointer smoke test against the production build (vite preview).
import { expect, test } from '@playwright/test';

type V = { ready: boolean; stats: { cam: number[]; fps: number; tris: number }; scale: { ok: boolean; measured_m: number } | null };
const viewer = (page: import('@playwright/test').Page) => page.evaluate(() => (window as unknown as { __viewer?: V }).__viewer);

test('loads sample, keeps scale, orbits with pointer, resets with keyboard', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/');

  await expect.poll(async () => (await viewer(page))?.ready, { timeout: 20_000 }).toBe(true);
  await expect(page.getByTestId('scale')).toContainText('1.000 m');
  expect((await viewer(page))!.scale!.ok).toBe(true);

  await expect.poll(async () => (await viewer(page))!.stats.tris, { timeout: 10_000 }).toBeGreaterThan(0);
  const home = (await viewer(page))!.stats.cam;
  await page.screenshot({ path: 'test-results/xr-001-home.png' });

  // Pointer: left-drag orbits.
  const box = (await page.locator('canvas').boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 220, box.y + box.height / 2 + 40, { steps: 12 });
  await page.mouse.up();
  await expect.poll(async () => (await viewer(page))!.stats.cam.join(), { timeout: 5_000 }).not.toBe(home.join());
  await page.screenshot({ path: 'test-results/xr-001-orbited.png' });

  // Keyboard: R resets to the home pose.
  await page.locator('body').click({ position: { x: box.width - 10, y: box.height - 10 } });
  await page.keyboard.press('r');
  await expect.poll(async () => (await viewer(page))!.stats.cam.join(), { timeout: 5_000 }).toBe(home.join());

  // Keyboard-only users can reach the reset button.
  await expect(page.getByRole('button', { name: 'Reset camera' })).toBeVisible();
  expect(errors).toEqual([]);
});
