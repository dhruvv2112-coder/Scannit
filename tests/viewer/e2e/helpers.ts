import { expect, type Page } from '@playwright/test';

export type V = NonNullable<Window['__viewer']>;
export const viewer = (page: Page) => page.evaluate(() => {
  const v = window.__viewer; if (!v) return null;
  // Functions don't cross the bridge; drop them.
  const { project: _p, frameOn: _f, ...rest } = v;
  return rest as Omit<V, 'project' | 'frameOn'>;
});

/** Collects uncaught page errors and console errors (network 4xx/5xx resource logs are expected in failure tests). */
export function watchErrors(page: Page, { allowResourceErrors = false } = {}) {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    if (allowResourceErrors && /Failed to load resource|net::ERR_/.test(m.text())) return;
    errors.push(`console: ${m.text()}`);
  });
  return errors;
}

export async function waitReady(page: Page) {
  await expect.poll(async () => (await viewer(page))?.phase, { timeout: 30_000 }).toBe('ready');
}

export async function waitPhase(page: Page, phase: string) {
  await expect.poll(async () => (await viewer(page))?.phase, { timeout: 30_000 }).toBe(phase);
}

/** Opt-in slow-machine emulation (PW_CPU_THROTTLE=8) to reproduce CI-only timing issues locally. */
export async function maybeThrottle(page: Page) {
  const rate = Number(process.env.PW_CPU_THROTTLE ?? 0);
  if (rate > 1) { const cdp = await page.context().newCDPSession(page); await cdp.send('Emulation.setCPUThrottlingRate', { rate }); }
}
