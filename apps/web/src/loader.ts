// XR-002: remote GLB loading with abort, size cap, integrity check and typed failures.
// Bytes are verified against the manifest sha256 BEFORE parsing, so a stale/corrupt/swapped file
// is never displayed as the artifact the manifest describes.

export type ArtifactErrorKind =
  | 'aborted' | 'network' | 'expired' | 'not_found' | 'http' | 'too_large'
  | 'not_glb' | 'hash_mismatch' | 'parse' | 'empty';

export class ArtifactError extends Error {
  constructor(public kind: ArtifactErrorKind, message: string, public status?: number) { super(message); this.name = 'ArtifactError'; }
}

/** Default cap for a single in-browser GLB. Larger scenes need tiling (XR-003), not a bigger cap. */
export const DEFAULT_MAX_BYTES = 512 * 1024 * 1024;

export interface FetchOptions {
  signal?: AbortSignal;
  maxBytes?: number;
  onProgress?: (loaded: number, total: number | null) => void;
  fetchImpl?: typeof fetch;
}

export async function fetchBytes(url: string, opts: FetchOptions = {}): Promise<ArrayBuffer> {
  const { signal, maxBytes = DEFAULT_MAX_BYTES, onProgress, fetchImpl = fetch } = opts;
  let res: Response;
  try {
    res = await fetchImpl(url, { signal, credentials: 'omit', cache: 'no-store' });
  } catch (e) {
    if (signal?.aborted || (e as Error)?.name === 'AbortError') throw new ArtifactError('aborted', 'load cancelled');
    // Browsers report CORS and offline failures identically, as a TypeError.
    throw new ArtifactError('network', `network or CORS failure fetching artifact (${(e as Error)?.message ?? e})`);
  }
  if (res.status === 403 || res.status === 410) throw new ArtifactError('expired', `artifact URL refused (HTTP ${res.status}); a signed URL may have expired`, res.status);
  if (res.status === 404) throw new ArtifactError('not_found', 'artifact not found (HTTP 404)', 404);
  if (!res.ok) throw new ArtifactError('http', `HTTP ${res.status} fetching artifact`, res.status);

  const lenHeader = res.headers.get('content-length');
  const total = lenHeader ? Number(lenHeader) : null;
  if (total !== null && total > maxBytes) throw new ArtifactError('too_large', `artifact is ${fmtBytes(total)}, above the ${fmtBytes(maxBytes)} single-file limit; needs tiled delivery`);

  if (!res.body) {
    const buf = await res.arrayBuffer();
    if (buf.byteLength > maxBytes) throw new ArtifactError('too_large', `artifact exceeds ${fmtBytes(maxBytes)}`);
    onProgress?.(buf.byteLength, total);
    return buf;
  }
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      loaded += value.byteLength;
      if (loaded > maxBytes) { await reader.cancel(); throw new ArtifactError('too_large', `artifact exceeds ${fmtBytes(maxBytes)}; needs tiled delivery`); }
      chunks.push(value);
      onProgress?.(loaded, total);
    }
  } catch (e) {
    if (e instanceof ArtifactError) throw e;
    if (signal?.aborted) throw new ArtifactError('aborted', 'load cancelled');
    throw new ArtifactError('network', `connection dropped after ${fmtBytes(loaded)}`);
  }
  const out = new Uint8Array(loaded);
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.byteLength; }
  return out.buffer;
}

export function assertGlb(buf: ArrayBuffer): void {
  if (buf.byteLength === 0) throw new ArtifactError('empty', 'artifact is empty (0 bytes)');
  if (buf.byteLength < 20) throw new ArtifactError('not_glb', 'artifact too short to be a GLB');
  const dv = new DataView(buf);
  if (dv.getUint32(0, true) !== 0x46546c67) throw new ArtifactError('not_glb', 'not a GLB file (bad magic)');
  if (dv.getUint32(4, true) !== 2) throw new ArtifactError('not_glb', `unsupported glTF container version ${dv.getUint32(4, true)}`);
  if (dv.getUint32(8, true) !== buf.byteLength) throw new ArtifactError('not_glb', 'GLB length header does not match file size (truncated or corrupt)');
}

export async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, '0')).join('');
}

export async function loadVerifiedGlb(url: string, expectedSha256: string, opts: FetchOptions = {}): Promise<{ bytes: ArrayBuffer; sha256: string }> {
  const bytes = await fetchBytes(url, opts);
  assertGlb(bytes);
  const sha = await sha256Hex(bytes);
  if (sha !== expectedSha256) throw new ArtifactError('hash_mismatch', `sha256 mismatch: manifest ${expectedSha256.slice(0, 12)}…, file ${sha.slice(0, 12)}…`);
  return { bytes, sha256: sha };
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
