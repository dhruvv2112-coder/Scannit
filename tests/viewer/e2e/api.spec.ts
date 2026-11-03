// XR-002 against a mocked Account 2 API (routes proposed in docs/xr/manifest-proposal.md).
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { viewer, waitPhase, waitReady, watchErrors } from './helpers';

const PUB = resolve(__dirname, '../../../apps/web/public');
const MANIFEST = JSON.parse(readFileSync(`${PUB}/fixtures/sample-site.manifest.json`, 'utf8'));
const API = 'http://api.scannit.test';

async function mockApi(page: Page, jobs: Record<string, { status: string; error_message?: string }[]>) {
  const calls: Record<string, number> = {};
  const current = (id: string) => { const seq = jobs[id]; return seq[Math.min(calls[id] ?? 0, seq.length - 1)]; };
  await page.route(`${API}/v0/jobs`, (r) => r.fulfill({ json: Object.keys(jobs).map((id) => ({ id, label: `Job ${id}`, ...current(id) })), headers: { 'access-control-allow-origin': '*' } }));
  await page.route(new RegExp(`${API}/v0/jobs/([^/]+)$`), (r) => {
    const id = decodeURIComponent(r.request().url().split('/').pop()!);
    calls[id] = (calls[id] ?? 0) + 1;
    return r.fulfill({ json: { id, label: `Job ${id}`, progress_pct: Math.min(calls[id] * 30, 100), ...current(id) }, headers: { 'access-control-allow-origin': '*' } });
  });
  await page.route(new RegExp(`${API}/v0/jobs/([^/]+)/artifacts/viewer$`), (r) =>
    r.fulfill({ json: { ...MANIFEST, uri: 'http://localhost:4173/samples/sample-site.glb', job_id: 'j-done' }, headers: { 'access-control-allow-origin': '*' } }));
  return calls;
}

test('processing job is polled until COMPLETE, then the artifact loads', async ({ page }) => {
  const errors = watchErrors(page);
  await mockApi(page, { 'j-run': [{ status: 'DENSE_MVS' }, { status: 'MESH' }, { status: 'EXPORTING' }, { status: 'COMPLETE' }] });
  await page.goto(`/?source=api&api=${encodeURIComponent(API)}&poll=150`);
  await expect(page.getByTestId('status')).toContainText('Processing');
  await waitReady(page);
  const v = (await viewer(page))!;
  expect(v.loaded!.artifact_id).toBe('synthetic-sample-site-v1');
  await expect(page.locator('p.muted').first()).toContainText('Source: api');
  expect(errors).toEqual([]);
});

test('polling is bounded and stops with a "check again" option', async ({ page }) => {
  const errors = watchErrors(page);
  const calls = await mockApi(page, { 'j-slow': [{ status: 'DENSE_MVS' }] });
  await page.goto(`/?source=api&api=${encodeURIComponent(API)}&poll=100&pollMax=3`);
  await expect(page.getByTestId('status')).toContainText('stopped checking', { timeout: 10_000 });
  expect(calls['j-slow']).toBe(3);
  await page.waitForTimeout(500);
  expect(calls['j-slow']).toBe(3);
  expect(errors).toEqual([]);
});

test('failed job shows its error and no model', async ({ page }) => {
  const errors = watchErrors(page);
  await mockApi(page, { 'j-bad': [{ status: 'FAILED', error_message: 'dense reconstruction diverged' }] });
  await page.goto(`/?source=api&api=${encodeURIComponent(API)}`);
  await waitPhase(page, 'job-failed');
  await expect(page.getByRole('alert')).toContainText('dense reconstruction diverged');
  expect((await viewer(page))!.loaded).toBeNull();
  expect(errors).toEqual([]);
});

test('empty project shows an empty state', async ({ page }) => {
  const errors = watchErrors(page);
  await mockApi(page, {});
  await page.goto(`/?source=api&api=${encodeURIComponent(API)}`);
  await waitPhase(page, 'no-scenes');
  await expect(page.getByTestId('status')).toContainText('No scenes yet');
  expect(errors).toEqual([]);
});
