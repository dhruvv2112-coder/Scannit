// XR-002 unit tests: manifest adapter (both conventions), frame transform + inverse, loader failure
// kinds, bounded polling, integrity report, and measurement through the placement model.
// Fixtures: our synthetic mesh site and Account 1's real PR #8 output (synthetic sparse points).
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { Matrix4, Raycaster, Vector3, type Group } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { flattenMatrix, invertRowMajor, parseManifest, resolveArtifactUrl, type ArtifactManifest } from '../../../apps/web/src/manifest';
import { GLTF_Y_UP_TO_SCENE_Z_UP, SCENE_Z_UP_TO_GLTF_Y_UP, VIEW_TO_SCENE, checkTransform, isIdentity, matrixFromRowMajor, placementMatrix, toFrame } from '../../../apps/web/src/frames';
import { ArtifactError, assertGlb, loadVerifiedGlb, sha256Hex } from '../../../apps/web/src/loader';
import { apiSource, pollUntilTerminal, type JobStatus } from '../../../apps/web/src/source';
import { distanceM, fmtLength, makePoint, snapToFaceVertex } from '../../../apps/web/src/measure';
import { inspect } from '../../../apps/web/src/integrity';
import { lifecycleFromJob, lifecycleWhenLoaded } from '../../../apps/web/src/lifecycle';

const PUB = resolve(__dirname, '../../../apps/web/public');
const RAW = JSON.parse(readFileSync(`${PUB}/fixtures/sample-site.manifest.json`, 'utf8'));
const RAW_A1 = JSON.parse(readFileSync(`${PUB}/fixtures/account1-pr8/artifact_manifest.json`, 'utf8'));
const buf = (p: string) => { const b = readFileSync(p); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer; };
const GLB = () => buf(`${PUB}/samples/sample-site.glb`);
const GLB_A1 = () => buf(`${PUB}/fixtures/account1-pr8/model.glb`);
const norm = (raw: unknown): ArtifactManifest => { const r = parseManifest(raw); if (!r.ok) throw new Error(r.errors.join('; ')); return r.manifest; };
const parseGlb = (b: ArrayBuffer) => new Promise<{ scene: Group }>((ok, fail) => new GLTFLoader().parse(b, '', ok as never, fail));
async function placed(b: ArrayBuffer, m: ArtifactManifest) {
  const g = await parseGlb(b);
  const p = placementMatrix(m.glb_to_scene);
  if (!isIdentity(p)) { g.scene.matrixAutoUpdate = false; g.scene.matrix.copy(p); }
  g.scene.updateMatrixWorld(true);
  return g.scene;
}

describe('manifest adapter', () => {
  it('accepts our fixture (producer convention) and flags it synthetic + unvalidated', () => {
    const r = parseManifest(RAW);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.warnings.join(' ')).toMatch(/synthetic/);
    expect(r.warnings.join(' ')).toMatch(/metric_unvalidated/);
    expect(r.notes.join(' ')).toMatch(/uses its inverse/);
  });

  it("accepts Account 1's real PR #8 manifest unchanged", async () => {
    const r = parseManifest(RAW_A1);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    const m = r.manifest;
    expect(m.glb_frame_id).toBe('gltf_yup');
    expect(m.scene_frame_id).toBe('scene_zup');
    expect(m.glb_to_scene).toEqual(GLTF_Y_UP_TO_SCENE_Z_UP.map((v) => v + 0));
    expect(m.artifact_origin).toBe('synthetic');
    expect(m.producer_warnings.join(' ')).toMatch(/do not measure/);
    expect(r.warnings.join(' ')).toMatch(/arbitrary: lengths are model units/);
    expect(r.warnings.join(' ')).toMatch(/mesh acceptance: not_met/);
    // and the sha256 in it matches the bytes it ships with
    expect(await sha256Hex(GLB_A1())).toBe(m.sha256);
  });

  it('treats flat and nested matrices identically', () => {
    expect(flattenMatrix(RAW_A1.transform_4x4)).toEqual(SCENE_Z_UP_TO_GLTF_Y_UP.map((v) => v + 0));
    const flat = norm({ ...RAW, transform_4x4: SCENE_Z_UP_TO_GLTF_Y_UP });
    expect(flat.glb_to_scene).toEqual(norm(RAW).glb_to_scene);
  });

  it('accepts the older gltf->scene direction as written', () => {
    const r = parseManifest({ ...RAW, source_frame_id: 'gltf_y_up', target_frame_id: 'scene_z_up', transform_4x4: GLTF_Y_UP_TO_SCENE_Z_UP, bounds_m: { ...RAW.bounds_m, frame_id: 'gltf_y_up' }, reference_lengths: RAW.reference_lengths.map((x: { frame_id: string }) => ({ ...x, frame_id: 'scene_z_up' })) });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.notes.join(' ')).toMatch(/used as written/);
  });

  it('never presents unknown origin as camera-derived', () => {
    const r = parseManifest({ ...RAW, synthetic: false, artifact_origin: undefined });
    expect(r.ok && r.manifest.artifact_origin).toBe('unknown');
    const both = parseManifest({ ...RAW, synthetic: true, artifact_origin: 'camera' });
    expect(both.ok && both.manifest.artifact_origin).toBe('synthetic');
  });

  it.each([
    ['unit feet', { unit: 'FEET' }, /refuses to convert/],
    ['bad hash', { sha256: 'abc' }, /sha256/],
    ['ply', { mime_type: 'model/ply' }, /gltf-binary/],
    ['bad scale status', { scale_status: 'exact' }, /scale_status/],
    ['short matrix', { transform_4x4: [1, 0, 0] }, /transform_4x4/],
    ['projective', { transform_4x4: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 1] }, /affine/],
    ['ambiguous frames', { source_frame_id: 'site', target_frame_id: 'local' }, /cannot tell which/],
    ['bounds in unknown frame', { bounds_m: { frame_id: 'ecef', min: [0, 0, 0], max: [1, 1, 1] } }, /neither/],
    ['inverted bounds', { bounds_m: { frame_id: 'gltf_yup', min: [1, 0, 0], max: [0, 1, 1] } }, /min must be/],
    ['bad reference', { reference_lengths: [{ id: 'r', length_m: -1, endpoints_m: [[0, 0, 0]], frame_id: 'scene_zup' }] }, /length_m/],
  ])('rejects %s', (_n, patch, msg) => {
    const r = parseManifest({ ...RAW, ...patch });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors.join('\n')).toMatch(msg);
  });

  it('refuses non-browser storage URIs instead of guessing a URL', () => {
    expect(() => resolveArtifactUrl('s3://bucket/a.glb', 'https://x/m.json')).toThrow(/signed https URL/);
    expect(resolveArtifactUrl('../samples/a.glb', 'https://x/fixtures/m.json')).toBe('https://x/samples/a.glb');
  });
});

describe('frames', () => {
  const g2s = matrixFromRowMajor(GLTF_Y_UP_TO_SCENE_Z_UP);
  const s2g = matrixFromRowMajor(SCENE_Z_UP_TO_GLTF_Y_UP);
  it('maps glTF +Y up to scene +Z up and back', () => {
    expect(toFrame([0, 1, 0], g2s).toArray()).toEqual([0, 0, 1]);
    expect(toFrame([0, 0, 1], s2g).toArray()).toEqual([0, 1, 0]);
  });
  it('the two constants are exact inverses (and match Account 1 ZUP_TO_YUP)', () => {
    expect(isIdentity(g2s.clone().multiply(s2g))).toBe(true);
    expect(flattenMatrix(RAW_A1.transform_4x4)).toEqual(SCENE_Z_UP_TO_GLTF_Y_UP.map((v) => v + 0));
  });
  it('invertRowMajor matches three.js inverse, including translation', () => {
    const m = [0, -1, 0, 12.5, 1, 0, 0, -3, 0, 0, 1, 0.25, 0, 0, 0, 1];
    const inv = matrixFromRowMajor(invertRowMajor(m)!);
    const ref = matrixFromRowMajor(m).invert();
    inv.elements.forEach((v, i) => expect(v).toBeCloseTo(ref.elements[i], 12));
  });
  it('standard export places the GLB with identity (no hidden rotation)', () => {
    expect(isIdentity(placementMatrix(norm(RAW_A1).glb_to_scene))).toBe(true);
    expect(isIdentity(placementMatrix(norm(RAW).glb_to_scene))).toBe(true);
  });
  it('flags scale, shear and reflection', () => {
    expect(checkTransform(g2s).rigid).toBe(true);
    expect(checkTransform(new Matrix4().makeScale(0.3048, 0.3048, 0.3048)).issues.join()).toMatch(/not 1/);
    expect(checkTransform(new Matrix4().makeScale(1, 1, -1)).issues.join()).toMatch(/mirrors/);
    expect(checkTransform(new Matrix4().makeShear(0.2, 0, 0, 0, 0, 0)).rigid).toBe(false);
  });
});

describe('loader', () => {
  const respond = (body: BodyInit | null, init: ResponseInit = {}) => vi.fn(async () => new Response(body, init)) as unknown as typeof fetch;
  it('verifies both fixtures against their manifest sha256', async () => {
    expect((await loadVerifiedGlb('https://x/a.glb', RAW.sha256, { fetchImpl: respond(GLB()) })).sha256).toBe(RAW.sha256);
    expect((await loadVerifiedGlb('https://x/a.glb', RAW_A1.sha256, { fetchImpl: respond(GLB_A1()) })).sha256).toBe(RAW_A1.sha256);
  });
  it.each([
    ['hash_mismatch', () => respond(GLB()), '0'.repeat(64)],
    ['expired', () => respond('no', { status: 403 }), RAW.sha256],
    ['expired', () => respond('gone', { status: 410 }), RAW.sha256],
    ['not_found', () => respond('x', { status: 404 }), RAW.sha256],
    ['http', () => respond('x', { status: 500 }), RAW.sha256],
    ['not_glb', () => respond('{"not":"a glb"} padding padding'), RAW.sha256],
    ['empty', () => respond(new ArrayBuffer(0)), RAW.sha256],
    ['network', () => vi.fn(async () => { throw new TypeError('Failed to fetch'); }) as unknown as typeof fetch, RAW.sha256],
  ])('reports %s', async (kind, mk, sha) => {
    await expect(loadVerifiedGlb('https://x/a.glb', sha, { fetchImpl: mk() })).rejects.toMatchObject({ kind });
  });
  it('rejects a truncated GLB', () => { expect(() => assertGlb(GLB().slice(0, 1000))).toThrow(ArtifactError); });
  it('enforces the size cap from content-length and from the stream', async () => {
    const len = String(GLB().byteLength);
    await expect(loadVerifiedGlb('https://x/a.glb', RAW.sha256, { maxBytes: 1000, fetchImpl: respond(GLB(), { headers: { 'content-length': len } }) })).rejects.toMatchObject({ kind: 'too_large' });
    await expect(loadVerifiedGlb('https://x/a.glb', RAW.sha256, { maxBytes: 1000, fetchImpl: respond(GLB()) })).rejects.toMatchObject({ kind: 'too_large' });
  });
  it('reports a cancelled load as aborted', async () => {
    const ac = new AbortController(); ac.abort();
    const f = vi.fn(async () => { throw new DOMException('aborted', 'AbortError'); }) as unknown as typeof fetch;
    await expect(loadVerifiedGlb('https://x/a.glb', RAW.sha256, { signal: ac.signal, fetchImpl: f })).rejects.toMatchObject({ kind: 'aborted' });
  });
});

describe('api source polling + lifecycle', () => {
  const statuses: JobStatus[] = ['DENSE_MVS', 'MESH', 'EXPORTING', 'COMPLETE'];
  const jobFetch = () => { let i = 0; return vi.fn(async () => new Response(JSON.stringify({ id: 'j1', status: statuses[Math.min(i++, statuses.length - 1)], progress_pct: i * 25 }))) as unknown as typeof fetch; };
  const noSleep = async () => {};
  it('stops at COMPLETE and exposes the manifest URL', async () => {
    const r = await pollUntilTerminal(apiSource('https://api.test/', jobFetch()), 'j1', { sleep: noSleep });
    expect(r.timedOut).toBe(false);
    expect(r.entry.manifestUrl).toBe('https://api.test/v0/jobs/j1/artifacts/viewer');
  });
  it('gives up after the attempt budget', async () => {
    const r = await pollUntilTerminal(apiSource('https://api.test', jobFetch()), 'j1', { sleep: noSleep, maxAttempts: 2 });
    expect(r.timedOut).toBe(true);
  });
  it('stops when cancelled', async () => {
    const ac = new AbortController();
    const sleep = async (_ms: number, s?: AbortSignal) => { ac.abort(); if (s?.aborted) throw new DOMException('aborted', 'AbortError'); };
    await expect(pollUntilTerminal(apiSource('https://api.test', jobFetch()), 'j1', { signal: ac.signal, sleep })).rejects.toThrow(/aborted/);
  });
  it('maps job and load states to the six lifecycle states', () => {
    expect(lifecycleFromJob('CREATED')).toBe('uploaded');
    expect(lifecycleFromJob('VALIDATING')).toBe('queued');
    expect(lifecycleFromJob('DENSE_MVS')).toBe('processing');
    expect(lifecycleFromJob('NEEDS_REFERENCE')).toBe('failed');
    expect(lifecycleWhenLoaded('metric_validated', 0)).toBe('ready');
    expect(lifecycleWhenLoaded('metric_validated', 1)).toBe('unvalidated_scale');
    expect(lifecycleWhenLoaded('arbitrary', 0)).toBe('unvalidated_scale');
  });
});

describe('integrity report', () => {
  it('our fixture: bounds and reference endpoints match the geometry', async () => {
    const m = norm(RAW);
    const r = inspect(await placed(GLB(), m), m);
    expect(r.issues).toEqual([]);
    expect(r.boundsMismatch_m).toBeLessThan(1e-4);
    expect(r.references[0].endpointsOnGeometry).toBe(true);
    expect(r.triangleCount).toBeGreaterThan(0);
  });
  it("Account 1's output: point cloud, bounds (in gltf_yup) match, no transform issues", async () => {
    const m = norm(RAW_A1);
    const r = inspect(await placed(GLB_A1(), m), m);
    expect(r.issues).toEqual([]);
    expect(r.pointCount).toBe(12497);
    expect(r.triangleCount).toBe(0);
    expect(r.boundsMismatch_m).toBeLessThan(1e-4);
  });
  it('reports wrong bounds and off-geometry reference endpoints', async () => {
    const m = norm({ ...RAW, bounds_m: { frame_id: 'scene_zup', min: [-10, -10, 0], max: [10, 10, 8] }, reference_lengths: [{ ...RAW.reference_lengths[0], endpoints_m: [[0, 0, 5], [1, 0, 5]] }] });
    const r = inspect(await placed(GLB(), m), m);
    expect(r.issues.join('\n')).toMatch(/bounds differ/);
    expect(r.issues.join('\n')).toMatch(/not on the delivered geometry/);
  });
  it('reports a scaled transform instead of silently correcting it', async () => {
    const m = norm({ ...RAW, transform_4x4: [[0.3048, 0, 0, 0], [0, 0, 0.3048, 0], [0, -0.3048, 0, 0], [0, 0, 0, 1]] });
    const r = inspect(await placed(GLB(), m), m);
    expect(r.transform.rigid).toBe(false);
    expect(r.issues.join('\n')).toMatch(/would change lengths/);
  });
});

describe('measurement', () => {
  it('snapped reference endpoints measure 1.000 m in the scene frame', async () => {
    const m = norm(RAW);
    const root = await placed(GLB(), m);
    const ends = inspect(root, m).references[0].endpointsWorld;
    const view2scene = VIEW_TO_SCENE();
    const picks = ends.map((w, k) => {
      const inward = -Math.sign(w[0]) * 0.004;
      const ray = new Raycaster(new Vector3(w[0] + inward, w[1] + 0.004, w[2] - 1), new Vector3(0, 0, 1));
      const hit = ray.intersectObject(root, true).find((h) => h.object.name.startsWith('REF_1m'))!;
      expect(hit).toBeTruthy();
      const s = snapToFaceVertex(hit, 0.05);
      const p = makePoint(s.point, view2scene, s.snapped, hit.object.name);
      p.target.toArray().forEach((v, i) => expect(v).toBeCloseTo(m.reference_lengths![0].endpoints_m[k][i], 6));
      return p;
    });
    expect(picks.every((p) => p.snapped)).toBe(true);
    expect(distanceM(picks[0], picks[1])).toBeCloseTo(1.0, 6);
  });
  it('picks exact point records on a point cloud', async () => {
    const m = norm(RAW_A1);
    const root = await placed(GLB_A1(), m);
    let pts: import('three').Points | null = null;
    root.traverse((o) => { if ((o as import('three').Points).isPoints) pts = o as import('three').Points; });
    expect(pts).not.toBeNull();
    const pos = pts!.geometry.attributes.position;
    const target = new Vector3().fromBufferAttribute(pos, 100).applyMatrix4(pts!.matrixWorld);
    const ray = new Raycaster(target.clone().add(new Vector3(0, 0, 50)), new Vector3(0, 0, -1));
    ray.params.Points = { threshold: 0.001 };
    const hit = ray.intersectObject(pts!, false)[0];
    const s = snapToFaceVertex(hit, 1);
    expect(s.snapped).toBe(true);
    expect(s.point.distanceTo(new Vector3().fromBufferAttribute(pos, s.index!).applyMatrix4(pts!.matrixWorld))).toBe(0);
  });
  it('labels arbitrary-scale lengths as model units, never meters', () => {
    expect(fmtLength(1.23456, 'arbitrary')).toBe('1.235 model units');
    expect(fmtLength(1.23456, 'metric_unvalidated')).toBe('1.235 m');
  });
});
