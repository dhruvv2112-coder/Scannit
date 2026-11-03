// XR-001: generates a synthetic, metrically scaled industrial sample scene as GLB.
// Units: meters (glTF 2.0 spec). Axes: +Y up, +Z toward viewer, right-handed (glTF convention).
// This is MOCK data for viewer development, not a reconstruction. Usage: node scripts/make-sample-glb.mjs <out.glb>
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { createHash } from 'node:crypto';
import { BoxGeometry, CylinderGeometry, PlaneGeometry } from 'three';

const out = process.argv[2] ?? 'public/samples/sample-site.glb';

/** Each entry becomes one mesh + node. Sizes in meters. */
const parts = [
  { name: 'GROUND_40x40m', geo: new PlaneGeometry(40, 40).rotateX(-Math.PI / 2), t: [0, 0, 0], mat: 0 },
  // The 1.000 m reference bar used by the scale verification gate. Its X extent must be exactly 1.0.
  { name: 'REF_1m', geo: new BoxGeometry(1.0, 0.05, 0.05), t: [0, 0.025, 2], mat: 1, extras: { referenceLength_m: 1.0, axis: 'x' } },
  { name: 'TANK_A_d6m_h8m', geo: new CylinderGeometry(3, 3, 8, 48), t: [-8, 4, -6], mat: 2 },
  { name: 'TANK_B_d4m_h6m', geo: new CylinderGeometry(2, 2, 6, 48), t: [-1, 3, -8], mat: 2 },
  { name: 'SHED_12x6x5m', geo: new BoxGeometry(12, 5, 6), t: [9, 2.5, -4], mat: 3 },
  ...[-12, -6, 0, 6, 12].map((x, i) => ({ name: `PILLAR_${i}_0.4x6m`, geo: new BoxGeometry(0.4, 6, 0.4), t: [x, 3, 8], mat: 4 })),
  { name: 'PIPE_RACK_24m', geo: new BoxGeometry(24.4, 0.3, 0.6), t: [0, 6.15, 8], mat: 4 },
];

// PBR metallic-roughness factors only (no textures yet; XR-005 covers textures + color management).
const materials = [
  { name: 'concrete', pbrMetallicRoughness: { baseColorFactor: [0.45, 0.45, 0.43, 1], metallicFactor: 0, roughnessFactor: 0.95 } },
  { name: 'reference_orange', pbrMetallicRoughness: { baseColorFactor: [1.0, 0.35, 0.05, 1], metallicFactor: 0, roughnessFactor: 0.6 } },
  { name: 'steel_painted', pbrMetallicRoughness: { baseColorFactor: [0.6, 0.65, 0.7, 1], metallicFactor: 0.6, roughnessFactor: 0.45 } },
  { name: 'cladding', pbrMetallicRoughness: { baseColorFactor: [0.25, 0.35, 0.45, 1], metallicFactor: 0.3, roughnessFactor: 0.6 } },
  { name: 'galvanized', pbrMetallicRoughness: { baseColorFactor: [0.7, 0.7, 0.7, 1], metallicFactor: 0.9, roughnessFactor: 0.35 } },
];

const chunks = [];
let byteLength = 0;
const bufferViews = [];
const accessors = [];
const meshes = [];
const nodes = [];

function pushView(typed, target) {
  const pad = (4 - (byteLength % 4)) % 4;
  if (pad) { chunks.push(new Uint8Array(pad)); byteLength += pad; }
  const bytes = new Uint8Array(typed.buffer, typed.byteOffset, typed.byteLength);
  chunks.push(bytes);
  bufferViews.push({ buffer: 0, byteOffset: byteLength, byteLength: bytes.byteLength, target });
  byteLength += bytes.byteLength;
  return bufferViews.length - 1;
}

function vec3Accessor(arr) {
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < arr.length; i += 3) for (let k = 0; k < 3; k++) {
    min[k] = Math.min(min[k], arr[i + k]); max[k] = Math.max(max[k], arr[i + k]);
  }
  accessors.push({ bufferView: pushView(arr, 34962), componentType: 5126, count: arr.length / 3, type: 'VEC3', min, max });
  return accessors.length - 1;
}

for (const p of parts) {
  const g = p.geo.index ? p.geo : p.geo; // three geometries are indexed
  const pos = new Float32Array(g.attributes.position.array);
  const nrm = new Float32Array(g.attributes.normal.array);
  const idxSrc = g.index.array;
  const idx = pos.length / 3 > 65535 ? new Uint32Array(idxSrc) : new Uint16Array(idxSrc);
  const POSITION = vec3Accessor(pos);
  const NORMAL = vec3Accessor(nrm);
  accessors.push({ bufferView: pushView(idx, 34963), componentType: idx instanceof Uint32Array ? 5125 : 5123, count: idx.length, type: 'SCALAR' });
  meshes.push({ name: p.name, primitives: [{ attributes: { POSITION, NORMAL }, indices: accessors.length - 1, material: p.mat }] });
  nodes.push({ name: p.name, mesh: meshes.length - 1, translation: p.t, ...(p.extras ? { extras: p.extras } : {}) });
}

const gltf = {
  asset: { version: '2.0', generator: 'xr-001 make-sample-glb', extras: { units: 'meters', upAxis: '+Y', handedness: 'right', synthetic: true } },
  scene: 0,
  scenes: [{ name: 'sample-site', nodes: nodes.map((_, i) => i) }],
  nodes, meshes, materials, accessors, bufferViews,
  buffers: [{ byteLength }],
};

const bin = new Uint8Array(byteLength + ((4 - (byteLength % 4)) % 4));
let o = 0; for (const c of chunks) { bin.set(c, o); o += c.byteLength; }
let json = new TextEncoder().encode(JSON.stringify(gltf));
const jpad = (4 - (json.length % 4)) % 4;
json = Uint8Array.from([...json, ...new Array(jpad).fill(0x20)]);

const total = 12 + 8 + json.length + 8 + bin.length;
const glb = new Uint8Array(total);
const dv = new DataView(glb.buffer);
dv.setUint32(0, 0x46546c67, true); dv.setUint32(4, 2, true); dv.setUint32(8, total, true);
dv.setUint32(12, json.length, true); dv.setUint32(16, 0x4e4f534a, true); glb.set(json, 20);
const b = 20 + json.length;
dv.setUint32(b, bin.length, true); dv.setUint32(b + 4, 0x004e4942, true); glb.set(bin, b + 8);

mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, glb);
console.log(`wrote ${out} (${total} bytes, ${nodes.length} nodes)`);

// ---- Fixture manifest (XR-002). Viewer contract proposal; see docs/xr/manifest-proposal.md.
// GLB space is +Y up; the scene frame is +Z up: (x, y, z)_gltf -> (x, -z, y)_scene.
const toScene = ([x, y, z]) => [x, -z, y];
let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
nodes.forEach((n, i) => {
  const a = accessors[meshes[i].primitives[0].attributes.POSITION];
  for (const corner of [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0], [1, 0, 1], [0, 1, 1], [1, 1, 1]]) {
    const g = corner.map((c, k) => (c ? a.max[k] : a.min[k]) + n.translation[k]);
    toScene(g).forEach((v, k) => { lo[k] = Math.min(lo[k], v); hi[k] = Math.max(hi[k], v); });
  }
});
const r6 = (v) => Math.round(v * 1e6) / 1e6;
const ref = parts.find((p) => p.name === 'REF_1m');
const refA = [ref.t[0] - 0.5, ref.t[1] - 0.025, ref.t[2] - 0.025];
const refB = [ref.t[0] + 0.5, ref.t[1] - 0.025, ref.t[2] - 0.025];
const fixturesDir = process.argv[3] ?? join(dirname(out), '..', 'fixtures');
const manifest = {
  schema_version: '0.1.0',
  artifact_id: 'synthetic-sample-site-v1',
  uri: relative(fixturesDir, out).split('\\').join('/'),
  sha256: createHash('sha256').update(glb).digest('hex'),
  mime_type: 'model/gltf-binary',
  byte_size: total,
  transform_4x4: [1, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1],
  source_frame_id: 'gltf_y_up',
  target_frame_id: 'scene_z_up',
  bounds_m: { min: lo.map(r6), max: hi.map(r6) },
  unit: 'm',
  scale_status: 'metric_unvalidated',
  reference_lengths: [{ id: 'REF_1m', length_m: 1.0, endpoints_m: [toScene(refA).map(r6), toScene(refB).map(r6)], frame_id: 'scene_z_up' }],
  dataset_id: 'synthetic',
  job_id: 'none',
  synthetic: true,
  fidelity_warnings: ['Synthetic geometry generated by make-sample-glb.mjs; not a camera reconstruction.'],
};
mkdirSync(fixturesDir, { recursive: true });
writeFileSync(join(fixturesDir, 'sample-site.manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
writeFileSync(join(fixturesDir, 'index.json'), JSON.stringify({ scenes: [{ id: 'sample-site', label: 'Synthetic sample site (40 x 40 m)', manifest: 'sample-site.manifest.json' }] }, null, 2) + '\n');
console.log(`wrote ${fixturesDir}/sample-site.manifest.json sha256=${manifest.sha256}`);
