// XR-002 failure paths: each must show a readable state, keep the page alive and never display a wrong model.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { viewer, waitPhase, watchErrors } from './helpers';

const PUB = resolve(__dirname, '../../../apps/web/public');
const MANIFEST = JSON.parse(readFileSync(`${PUB}/fixtures/sample-site.manifest.json`, 'utf8'));
const GLB = readFileSync(`${PUB}/samples/sample-site.glb`);

const routeManifest = (page: Page, body: unknown, status = 200) =>
  page.route('**/fixtures/sample-site.manifest.json', (r) => r.fulfill({ status, contentType: 'application/json', body: typeof body === 'string' ? body : JSON.stringify(body) }));

async function expectNoModel(page: Page) {
  const v = (await viewer(page))!;
  expect(v.loaded).toBeNull();
  expect(v.lifecycle).toBe('failed');
  await expect(page.getByTestId('lifecycle')).toHaveAttribute('data-state', 'failed');
  await expect(page.locator('canvas')).toBeVisible();
}

test('manifest 404', async ({ page }) => {
  const errors = watchErrors(page, { allowResourceErrors: true });
  await routeManifest(page, 'nope', 404);
  await page.goto('/');
  await waitPhase(page, 'manifest-error');
  await expect(page.getByTestId('status')).toContainText('not found');
  await expectNoModel(page);
  expect(errors).toEqual([]);
});

test('manifest in feet is refused, not converted', async ({ page }) => {
  const errors = watchErrors(page);
  await routeManifest(page, { ...MANIFEST, unit: 'FEET' });
  await page.goto('/');
  await waitPhase(page, 'manifest-error');
  await expect(page.getByTestId('status')).toContainText('refuses to convert');
  await expectNoModel(page);
  expect(errors).toEqual([]);
});

test('corrupt GLB fails the sha256 check before display', async ({ page }) => {
  const errors = watchErrors(page);
  const bad = Buffer.from(GLB); bad[bad.length - 10] ^= 0xff;
  await page.route('**/samples/sample-site.glb', (r) => r.fulfill({ status: 200, contentType: 'model/gltf-binary', body: bad }));
  await page.goto('/');
  await waitPhase(page, 'artifact-error');
  await expect(page.getByTestId('status')).toContainText('hash_mismatch');
  await expectNoModel(page);
  expect(errors).toEqual([]);
});

test('non-GLB payload', async ({ page }) => {
  const errors = watchErrors(page);
  await page.route('**/samples/sample-site.glb', (r) => r.fulfill({ status: 200, contentType: 'text/html', body: '<html>login page</html>' }));
  await page.goto('/');
  await waitPhase(page, 'artifact-error');
  await expect(page.getByTestId('status')).toContainText('not_glb');
  expect(errors).toEqual([]);
});

test('expired signed URL (403)', async ({ page }) => {
  const errors = watchErrors(page, { allowResourceErrors: true });
  await page.route('**/samples/sample-site.glb', (r) => r.fulfill({ status: 403, body: 'AccessDenied' }));
  await page.goto('/');
  await waitPhase(page, 'artifact-error');
  await expect(page.getByTestId('status')).toContainText('expired');
  expect(errors).toEqual([]);
});

test('network / CORS failure, then retry recovers', async ({ page }) => {
  const errors = watchErrors(page, { allowResourceErrors: true });
  let fail = true;
  await page.route('**/samples/sample-site.glb', (r) => (fail ? r.abort('failed') : r.continue()));
  await page.goto('/');
  await waitPhase(page, 'artifact-error');
  await expect(page.getByTestId('status')).toContainText('network');
  fail = false;
  await page.getByRole('button', { name: 'Retry' }).click();
  await waitPhase(page, 'ready');
  expect(errors).toEqual([]);
});

test('scene index unreachable', async ({ page }) => {
  const errors = watchErrors(page, { allowResourceErrors: true });
  await page.route('**/fixtures/index.json', (r) => r.fulfill({ status: 500, body: 'boom' }));
  await page.goto('/');
  await waitPhase(page, 'scenes-error');
  await expect(page.getByRole('alert')).toContainText('Could not load scenes');
  expect(errors).toEqual([]);
});

test('WebGL2 unavailable shows the fallback instead of a blank page', async ({ page }) => {
  const errors = watchErrors(page);
  await page.addInitScript(() => {
    const orig = HTMLCanvasElement.prototype.getContext;
    // @ts-expect-error test override
    HTMLCanvasElement.prototype.getContext = function (type: string, ...rest: unknown[]) { return type === 'webgl2' ? null : orig.call(this, type, ...rest); };
  });
  await page.goto('/');
  await expect(page.getByTestId('fallback')).toContainText('3D view unavailable');
  expect(errors).toEqual([]);
});
