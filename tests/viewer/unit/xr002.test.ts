// XR-002 unit tests: manifest contract, frame transform + inverse, loader failure kinds,
// bounded polling, and the 1.000 m reference measured through the manifest transform.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { Matrix4, Raycaster, Vector3 } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { parseManifest, resolveArtifactUrl, type ArtifactManifest } from '../../../apps/web/src/manifest';
import { GLTF_Y_UP_TO_SCENE_Z_UP, checkTransform, matrixFromRowMajor, toFrame } from '../../../apps/web/src/frames';
import { ArtifactError, assertGlb, loadVerifiedGlb } from '../../../apps/web/src/loader';
import { apiSource, pollUntilTerminal, type JobStatus } from '../../../apps/web/src/source';
import { distanceM, makePoint, snapToFaceVertex } from '../../../apps/web/src/measure';

const WEB = resolve(__dirname, '../../../apps/web/public');
const MANIFEST = JSON.parse(readFileSync(`${WEB}/fixtures/sample-site.manifest.json`, 'utf8')) as ArtifactManifest;
const GLB = readFileSync(`${WEB}/samples/sample-site.glb`);
const glbBuf = () => GLB.buffer.slice(GLB.byteOffset, GLB.byteOffset + GLB.byteLength) as ArrayBuffer;

describe('manifest contract', () => {
  it('accepts the generated fixture and flags it as synthetic and unvalidated', () => {
    const r = parseManifest(MANIFEST);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.warnings.join(' ')).toMatch(/synthetic/);
    if (r.ok) expect(r.warnings.join(' ')).toMatch(/metric_unvalidated/);
  });

  it.each([
    ['unit feet', { unit: 'FEET' }, /refuses to convert/],
    ['bad hash', { sha256: 'abc' }, /sha256/],
    ['ply', { mime_type: 'model/ply' }, /gltf-binary/],
    ['bad scale status', { scale_status: 'exact' }, /scale_status/],
    ['short matrix', { transform_4x4: [1, 0, 0] }, /transform_4x4/],
    ['projective', { transform_4x4: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 1] }, /affine/],
    ['inverted bounds', { bounds_m: { min: [1, 0, 0], max: [0, 1, 1] } }, /min must be/],
    ['bad reference', { reference_lengths: [{ id: 'r', length_m: -1, endpoints_m: [[0, 0, 0]], frame_id: 'f' }] }, /length_m/],
  ])('rejects %s', (_n, patch, msg) => {
    const r = parseManifest({ ...MANIFEST, ...patch });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors.join('\n')).toMatch(msg);
  });

  it('refuses non-browser storage URIs instead of guessing a URL', () => {
    expect(() => resolveArtifactUrl('s3://bucket/a.glb', 'https://x/m.json')).toThrow(/signed https URL/);
    expect(resolveArtifactUrl('../samples/a.glb', 'https://x/fixtures/m.json')).toBe('https://x/samples/a.glb');
  });
});

describe('frames', () => {
  const m = matrixFromRowMajor(GLTF_Y_UP_TO_SCENE_Z_UP);
  it('maps glTF +Y up to scene +Z up', () => {
    expect(toFrame([0, 1, 0], m).toArray()).toEqual([0, 0, 1]);
    expect(toFrame([0, 0, 1], m).toArray().map((v) => v + 0)).toEqual([0, -1, 0]);
  });
  it('inverse round-trips every point exactly', () => {
    const inv = m.clone().invert();
    for (const p of [[3, 4, 5], [-12.5, 0.025, 2], [1e3, -2e3, 7]]) {
      const back = toFrame(toFrame(p, m), inv).toArray();
      back.forEach((v, i) => expect(v).toBeCloseTo(p[i], 12));
    }
  });
  it('is rigid (no length change, no mirror)', () => {
    expect(checkTransform(m).rigid).toBe(true);
    expect(checkTransform(m).determinant).toBeCloseTo(1, 12);
  });
  it('flags scale, shear and reflection', () => {
    expect(checkTransform(new Matrix4().makeScale(0.3048, 0.3048, 0.3048)).issues.join()).toMatch(/not 1/);
    expect(checkTransform(new Matrix4().makeScale(1, 1, -1)).issues.join()).toMatch(/mirrors/);
    expect(checkTransform(new Matrix4().makeShear(0.2, 0, 0, 0, 0, 0)).rigid).toBe(false);
  });
});

describe('loader', () => {
  const respond = (body: BodyInit | null, init: ResponseInit = {}) => vi.fn(async () => new Response(body, init)) as unknown as typeof fetch;

  it('verifies the fixture bytes against the manifest sha256', async () => {
    const r = await loadVerifiedGlb('https://x/a.glb', MANIFEST.sha256, { fetchImpl: respond(glbBuf()) });
    expect(r.sha256).toBe(MANIFEST.sha256);
  });
  it.each([
    ['hash_mismatch', () => respond(glbBuf()), '0'.repeat(64)],
    ['expired', () => respond('no', { status: 403 }), MANIFEST.sha256],
    ['expired', () => respond('gone', { status: 410 }), MANIFEST.sha256],
    ['not_found', () => respond('x', { status: 404 }), MANIFEST.sha256],
    ['http', () => respond('x', { status: 500 }), MANIFEST.sha256],
    ['not_glb', () => respond('{"not":"a glb"} padding padding'), MANIFEST.sha256],
    ['empty', () => respond(new ArrayBuffer(0)), MANIFEST.sha256],
    ['network', () => vi.fn(async () => { throw new TypeError('Failed to fetch'); }) as unknown as typeof fetch, MANIFEST.sha256],
  ])('reports %s', async (kind, mk, sha) => {
    await expect(loadVerifiedGlb('https://x/a.glb', sha, { fetchImpl: mk() })).rejects.toMatchObject({ kind });
  });
  it('rejects a truncated GLB', () => {
    expect(() => assertGlb(glbBuf().slice(0, 1000))).toThrow(ArtifactError);
  });
  it('enforces the size cap from content-length and from the stream', async () => {
    await expect(loadVerifiedGlb('https://x/a.glb', MANIFEST.sha256, { maxBytes: 1000, fetchImpl: respond(glbBuf(), { headers: { 'content-length': String(GLB.byteLength) } }) })).rejects.toMatchObject({ kind: 'too_large' });
    await expect(loadVerifiedGlb('https://x/a.glb', MANIFEST.sha256, { maxBytes: 1000, fetchImpl: respond(glbBuf()) })).rejects.toMatchObject({ kind: 'too_large' });
  });
  it('reports a cancelled load as aborted', async () => {
    const ac = new AbortController(); ac.abort();
    const f = vi.fn(async () => { throw new DOMException('aborted', 'AbortError'); }) as unknown as typeof fetch;
    await expect(loadVerifiedGlb('https://x/a.glb', MANIFEST.sha256, { signal: ac.signal, fetchImpl: f })).rejects.toMatchObject({ kind: 'aborted' });
  });
});

describe('api source polling', () => {
  const statuses: JobStatus[] = ['DENSE_MVS', 'MESH', 'EXPORTING', 'COMPLETE'];
  const jobFetch = () => {
    let i = 0;
    return vi.fn(async () => new Response(JSON.stringify({ id: 'j1', status: statuses[Math.min(i++, statuses.length - 1)], progress_pct: i * 25 }))) as unknown as typeof fetch;
  };
  const noSleep = async () => {};

  it('stops at COMPLETE and exposes the manifest URL', async () => {
    const r = await pollUntilTerminal(apiSource('https://api.test/', jobFetch()), 'j1', { sleep: noSleep });
    expect(r.timedOut).toBe(false);
    expect(r.entry.manifestUrl).toBe('https://api.test/v0/jobs/j1/artifacts/viewer');
  });
  it('gives up after the attempt budget', async () => {
    const r = await pollUntilTerminal(apiSource('https://api.test', jobFetch()), 'j1', { sleep: noSleep, maxAttempts: 2 });
    expect(r.timedOut).toBe(true);
    expect(r.entry.manifestUrl).toBeNull();
  });
  it('stops when cancelled', async () => {
    const ac = new AbortController();
    const sleep = async (_ms: number, s?: AbortSignal) => { ac.abort(); if (s?.aborted) throw new DOMException('aborted', 'AbortError'); };
    await expect(pollUntilTerminal(apiSource('https://api.test', jobFetch()), 'j1', { signal: ac.signal, sleep })).rejects.toThrow(/aborted/);
  });
});

describe('measurement through the manifest transform', () => {
  it('snapped reference endpoints measure 1.000 m and match the manifest reference', async () => {
    const gltf = await new Promise<{ scene: import('three').Group }>((ok, fail) => new GLTFLoader().parse(glbBuf(), '', ok as never, fail));
    gltf.scene.updateMatrixWorld(true);
    const toTarget = matrixFromRowMajor(MANIFEST.transform_4x4);
    const ref = MANIFEST.reference_lengths![0];
    const inv = toTarget.clone().invert();
    const picks = ref.endpoints_m.map((e) => {
      const viewer = toFrame(e, inv);
      // Ray along +Z into the bar's front face, 4 mm inside the bottom corner; snapping must land on that corner.
      const inward = -Math.sign(viewer.x) * 0.004;
      const ray = new Raycaster(viewer.clone().add(new Vector3(inward, 0.004, -1)), new Vector3(0, 0, 1));
      const hit = ray.intersectObject(gltf.scene, true).find((h) => h.object.name.startsWith('REF_1m'))!;
      expect(hit).toBeTruthy();
      const s = snapToFaceVertex(hit, 0.05);
      const p = makePoint(s.point, toTarget, s.snapped, hit.object.name);
      p.target.toArray().forEach((v, i) => expect(v).toBeCloseTo(e[i], 6));
      return p;
    });
    expect(picks.every((p) => p.snapped)).toBe(true);
    expect(distanceM(picks[0], picks[1])).toBeCloseTo(1.0, 6);
  });
});

describe('integrity report', () => {
  it('fixture geometry matches manifest bounds and reference endpoints', async () => {
    const { inspect } = await import('../../../apps/web/src/integrity');
    const gltf = await new Promise<{ scene: import('three').Group }>((ok, fail) => new GLTFLoader().parse(glbBuf(), '', ok as never, fail));
    const r = inspect(gltf.scene, MANIFEST);
    expect(r.issues).toEqual([]);
    expect(r.boundsMismatch_m).toBeLessThan(1e-4);
    expect(r.references[0].endpointsOnGeometry).toBe(true);
  });
  it('reports a manifest whose bounds or reference do not match the geometry', async () => {
    const { inspect } = await import('../../../apps/web/src/integrity');
    const gltf = await new Promise<{ scene: import('three').Group }>((ok, fail) => new GLTFLoader().parse(glbBuf(), '', ok as never, fail));
    const bad = { ...MANIFEST, bounds_m: { min: [-10, -10, 0], max: [10, 10, 8] } as ArtifactManifest['bounds_m'], reference_lengths: [{ ...MANIFEST.reference_lengths![0], endpoints_m: [[0, 0, 5], [1, 0, 5]] as [import('../../../apps/web/src/manifest').Vec3, import('../../../apps/web/src/manifest').Vec3] }] };
    const r = inspect(gltf.scene, bad);
    expect(r.issues.join('\n')).toMatch(/bounds differ/);
    expect(r.issues.join('\n')).toMatch(/not on the delivered geometry/);
  });
  it('reports a scaled transform instead of silently correcting it', async () => {
    const { inspect } = await import('../../../apps/web/src/integrity');
    const gltf = await new Promise<{ scene: import('three').Group }>((ok, fail) => new GLTFLoader().parse(glbBuf(), '', ok as never, fail));
    const r = inspect(gltf.scene, { ...MANIFEST, transform_4x4: [0.3048, 0, 0, 0, 0, 0, -0.3048, 0, 0, 0.3048, 0, 0, 0, 0, 0, 1] });
    expect(r.transform.rigid).toBe(false);
    expect(r.issues.join('\n')).toMatch(/would change lengths/);
  });
});
