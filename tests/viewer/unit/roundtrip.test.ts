// XR-007 (early): the Blender-exported copy of the fixture (tests/viewer/roundtrip/out, produced by
// blender_roundtrip.py with Blender 5.0.1) still satisfies the original manifest in the browser stack:
// same frames, 1.000 m reference on geometry, tank 8 m tall, bounds within 1 cm, rigid transform.
// Only sha256/uri change (new bytes). asset.extras is NOT carried by Blender, so scale/frame metadata
// must travel in the manifest, never in GLB extras.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { type Group } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { parseManifest } from '../../../apps/web/src/manifest';
import { isIdentity, placementMatrix } from '../../../apps/web/src/frames';
import { inspect } from '../../../apps/web/src/integrity';
import { checkReferenceScale, measureNodeExtent } from '../../../apps/web/src/scale';

const ROOT = resolve(__dirname, '../../..');
const exported = readFileSync(`${ROOT}/tests/viewer/roundtrip/out/sample-site.blender.glb`);
const report = JSON.parse(readFileSync(`${ROOT}/tests/viewer/roundtrip/out/report.json`, 'utf8'));
const rawManifest = JSON.parse(readFileSync(`${ROOT}/apps/web/public/fixtures/sample-site.manifest.json`, 'utf8'));

describe('Blender round-trip (sample site)', () => {
  it('Blender report: geometry, names and PBR factors pass; extras are dropped', () => {
    const r = report.results.find((x: { input: string; use_mesh_vertices: boolean }) => x.input === 'sample-site.glb');
    const byName = Object.fromEntries(r.checks.map((c: { check: string; ok: boolean }) => [c.check, c.ok]));
    expect(byName['overall bounds equal (glTF Y-up, meters)']).toBe(true);
    expect(byName['per-node world bounds equal (position, size, up axis)']).toBe(true);
    expect(byName['triangle count preserved']).toBe(true);
    expect(byName['PBR factors preserved (baseColor, metallic, roughness)']).toBe(true);
    expect(byName['asset.extras (units/up-axis/provenance) preserved']).toBe(false); // documented limitation
  });

  it('exported GLB still satisfies the original manifest in the viewer stack', async () => {
    const buf = exported.buffer.slice(exported.byteOffset, exported.byteOffset + exported.byteLength) as ArrayBuffer;
    const pr = parseManifest({ ...rawManifest, sha256: createHash('sha256').update(exported).digest('hex'), uri: 'sample-site.blender.glb', byte_size: exported.byteLength });
    expect(pr.ok).toBe(true);
    if (!pr.ok) return;
    const g = await new Promise<{ scene: Group }>((ok, fail) => new GLTFLoader().parse(buf, '', ok as never, fail));
    const place = placementMatrix(pr.manifest.glb_to_scene);
    expect(isIdentity(place)).toBe(true);
    g.scene.updateMatrixWorld(true);
    const rep = inspect(g.scene, pr.manifest);
    expect(rep.issues).toEqual([]);
    expect(rep.references[0].endpointsOnGeometry).toBe(true);
    expect(checkReferenceScale(g.scene).measured_m).toBeCloseTo(1.0, 5);
    expect(measureNodeExtent(g.scene, 'TANK_A_d6m_h8m')!.y).toBeCloseTo(8, 5);
    expect(measureNodeExtent(g.scene, 'PIPE_RACK_24m')!.x).toBeCloseTo(24.4, 5);
  });
});
