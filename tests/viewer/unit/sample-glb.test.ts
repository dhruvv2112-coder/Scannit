// XR-001 verification gate (software scale): the 1.000 m reference must measure 1.000 m after load.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { checkReferenceScale, measureNodeExtent } from '../../../apps/web/src/scale';

const GLB = resolve(__dirname, '../../../apps/web/public/samples/sample-site.glb');

function load(): Promise<GLTF> {
  const buf = readFileSync(GLB);
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  return new Promise((ok, fail) => new GLTFLoader().parse(ab, '', ok, fail));
}

describe('sample-site.glb', () => {
  it('declares meters, +Y up and synthetic origin', async () => {
    const g = await load();
    expect(g.asset.extras).toMatchObject({ units: 'meters', upAxis: '+Y', synthetic: true });
  });

  it('1 m reference measures 1.000 m in viewer units', async () => {
    const g = await load();
    const r = checkReferenceScale(g.scene);
    expect(r.found).toBe(true);
    expect(r.measured_m).toBeCloseTo(1.0, 6);
    expect(r.ok).toBe(true);
  });

  it('known feature dimensions survive load (tank A height 8 m, pipe rack 24.4 m)', async () => {
    const g = await load();
    expect(measureNodeExtent(g.scene, 'TANK_A_d6m_h8m')!.y).toBeCloseTo(8, 6);
    expect(measureNodeExtent(g.scene, 'PIPE_RACK_24m')!.x).toBeCloseTo(24.4, 6);
  });

  it('flags a rescaled scene', async () => {
    const g = await load();
    g.scene.scale.setScalar(0.3048); // feet-for-meters mistake
    expect(checkReferenceScale(g.scene).ok).toBe(false);
  });
});
