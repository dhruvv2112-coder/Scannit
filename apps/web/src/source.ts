// XR-002: where scenes come from. Two interchangeable sources:
//  - fixtures: static JSON next to the app (synthetic sample; offline dev and CI)
//  - api: Account 2 backend. Routes below are a PROPOSAL (PR #3 currently exposes only /healthz);
//    see docs/xr/manifest-proposal.md. Polling is bounded and cancellable.

export type JobStatus =
  | 'CREATED' | 'VALIDATING' | 'INGESTING' | 'POSE_ESTIMATION' | 'DENSE_MVS' | 'MESH' | 'MATERIALS'
  | 'TILING' | 'EXPORTING' | 'QC' | 'COMPLETE' | 'FAILED' | 'CANCELLED' | 'RETRYING' | 'NEEDS_REFERENCE';

export const TERMINAL: ReadonlySet<JobStatus> = new Set(['COMPLETE', 'FAILED', 'CANCELLED', 'NEEDS_REFERENCE']);

export interface SceneEntry {
  id: string;
  label: string;
  /** Absolute URL of the artifact manifest, when one exists. */
  manifestUrl: string | null;
  job?: { id: string; status: JobStatus; progress_pct?: number; error_message?: string | null };
}

export interface DataSource {
  kind: 'fixtures' | 'api';
  listScenes(signal?: AbortSignal): Promise<SceneEntry[]>;
  /** Re-read one scene's job status (api only). */
  refresh?(id: string, signal?: AbortSignal): Promise<SceneEntry>;
}

async function getJson(url: string, signal?: AbortSignal, fetchImpl: typeof fetch = fetch): Promise<unknown> {
  let res: Response;
  try { res = await fetchImpl(url, { signal, headers: { accept: 'application/json' } }); }
  catch (e) {
    if (signal?.aborted) throw new DOMException('aborted', 'AbortError');
    throw new Error(`network or CORS failure: ${url} (${(e as Error)?.message ?? e})`, { cause: e });
  }
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${new URL(url).pathname}`);
  try { return await res.json(); } catch { throw new Error(`invalid JSON from ${new URL(url).pathname}`); }
}

/** index.json: { scenes: [{ id, label, manifest }] } with manifest paths relative to index.json. */
export function fixtureSource(indexUrl: string, fetchImpl?: typeof fetch): DataSource {
  return {
    kind: 'fixtures',
    async listScenes(signal) {
      const data = (await getJson(indexUrl, signal, fetchImpl)) as { scenes?: { id: string; label: string; manifest: string }[] };
      if (!Array.isArray(data?.scenes)) throw new Error('fixture index has no "scenes" array');
      return data.scenes.map((s) => ({ id: s.id, label: s.label, manifestUrl: new URL(s.manifest, indexUrl).toString() }));
    },
  };
}

/**
 * Proposed API:
 *   GET {base}/v0/jobs                         -> [{ id, project_id, label?, status, progress_pct, error_message }]
 *   GET {base}/v0/jobs/{id}                    -> same shape, one job
 *   GET {base}/v0/jobs/{id}/artifacts/viewer   -> artifact manifest (404 until the GLB exists)
 */
export function apiSource(base: string, fetchImpl?: typeof fetch): DataSource {
  const root = base.replace(/\/+$/, '');
  type JobRow = { id: string; label?: string; project_id?: string; status: JobStatus; progress_pct?: number; error_message?: string | null };
  const toEntry = (j: JobRow): SceneEntry => ({
    id: j.id,
    label: j.label ?? `Job ${j.id.slice(0, 8)}`,
    manifestUrl: j.status === 'COMPLETE' ? `${root}/v0/jobs/${encodeURIComponent(j.id)}/artifacts/viewer` : null,
    job: { id: j.id, status: j.status, progress_pct: j.progress_pct, error_message: j.error_message },
  });
  return {
    kind: 'api',
    async listScenes(signal) {
      const rows = await getJson(`${root}/v0/jobs`, signal, fetchImpl);
      if (!Array.isArray(rows)) throw new Error('jobs endpoint did not return an array');
      return (rows as JobRow[]).map(toEntry);
    },
    async refresh(id, signal) {
      return toEntry((await getJson(`${root}/v0/jobs/${encodeURIComponent(id)}`, signal, fetchImpl)) as JobRow);
    },
  };
}

export interface PollOptions { intervalMs?: number; maxAttempts?: number; signal?: AbortSignal; onUpdate?: (e: SceneEntry) => void; sleep?: (ms: number, signal?: AbortSignal) => Promise<void> }

const defaultSleep = (ms: number, signal?: AbortSignal) => new Promise<void>((ok, fail) => {
  const t = setTimeout(ok, ms);
  signal?.addEventListener('abort', () => { clearTimeout(t); fail(new DOMException('aborted', 'AbortError')); }, { once: true });
});

/** Poll until the job reaches a terminal state, the attempt budget runs out, or the signal aborts. */
export async function pollUntilTerminal(src: DataSource, id: string, opts: PollOptions = {}): Promise<{ entry: SceneEntry; timedOut: boolean }> {
  if (!src.refresh) throw new Error('source does not support polling');
  const { intervalMs = 5000, maxAttempts = 120, signal, onUpdate, sleep = defaultSleep } = opts;
  let entry = await src.refresh(id, signal);
  onUpdate?.(entry);
  for (let i = 1; i < maxAttempts && !TERMINAL.has(entry.job!.status); i++) {
    await sleep(intervalMs, signal);
    entry = await src.refresh(id, signal);
    onUpdate?.(entry);
  }
  return { entry, timedOut: !TERMINAL.has(entry.job!.status) };
}

/** Reads ?source=api&api=<base> (or VITE_SCANNIT_API) — defaults to bundled fixtures. */
export function sourceFromLocation(loc: Location, env: Record<string, string | undefined> = {}): DataSource {
  const q = new URLSearchParams(loc.search);
  const api = q.get('api') ?? env.VITE_SCANNIT_API;
  if (q.get('source') === 'api' || (q.get('source') !== 'fixtures' && api)) {
    if (!api) throw new Error('source=api needs ?api=<base URL> or VITE_SCANNIT_API');
    return apiSource(api);
  }
  return fixtureSource(new URL(`${import.meta.env?.BASE_URL ?? '/'}fixtures/index.json`, loc.href).toString());
}
