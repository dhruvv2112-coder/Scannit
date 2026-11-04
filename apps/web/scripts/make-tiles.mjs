// XR-003 (minimal): two synthetic tiles, each a GLB in its OWN local frame (origin at the tile centre),
// placed in the shared scene frame only by the translation recorded in its manifest. Proves the
// viewer applies per-tile transforms from manifest evidence and disposes tiles on scene change.
// Usage: node scripts/make-tiles.mjs [outDir=public/fixtures/tiles]
import { writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { BoxGeometry, CylinderGeometry, PlaneGeometry } from 'three';

const outDir = process.argv[2] ?? 'public/fixtures/tiles';
const materials = [
  { name: 'concrete', pbrMetallicRoughness: { baseColorFactor: [0.45, 0.45, 0.43, 1], metallicFactor: 0, roughnessFactor: 0.95 } },
  { name: 'steel_painted', pbrMetallicRoughness: { baseColorFactor: [0.6, 0.65, 0.7, 1], metallicFactor: 0.6, roughnessFactor: 0.45 } },
  { name: 'cladding', pbrMetallicRoughness: { baseColorFactor: [0.25, 0.35, 0.45, 1], metallicFactor: 0.3, roughnessFactor: 0.6 } },
];

function writeGlb(parts, extras) {
  const chunks = []; let byteLength = 0; const bufferViews = [], accessors = [], meshes = [], nodes = [];
  const pushView = (typed, target) => {
    const pad = (4 - (byteLength % 4)) % 4; if (pad) { chunks.push(new Uint8Array(pad)); byteLength += pad; }
    const bytes = new Uint8Array(typed.buffer, typed.byteOffset, typed.byteLength);
    chunks.push(bytes); bufferViews.push({ buffer: 0, byteOffset: byteLength, byteLength: bytes.byteLength, target }); byteLength += bytes.byteLength;
    return bufferViews.length - 1;
  };
  const vec3 = (arr) => {
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < arr.length; i += 3) for (let k = 0; k < 3; k++) { min[k] = Math.min(min[k], arr[i + k]); max[k] = Math.max(max[k], arr[i + k]); }
    accessors.push({ bufferView: pushView(arr, 34962), componentType: 5126, count: arr.length / 3, type: 'VEC3', min, max });
    return accessors.length - 1;
  };
  let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const p of parts) {
    const pos = new Float32Array(p.geo.attributes.position.array), nrm = new Float32Array(p.geo.attributes.normal.array), idx = new Uint16Array(p.geo.index.array);
    const POSITION = vec3(pos), NORMAL = vec3(nrm);
    accessors.push({ bufferView: pushView(idx, 34963), componentType: 5123, count: idx.length, type: 'SCALAR' });
    meshes.push({ name: p.name, primitives: [{ attributes: { POSITION, NORMAL }, indices: accessors.length - 1, material: p.mat }] });
    nodes.push({ name: p.name, mesh: meshes.length - 1, translation: p.t });
    const a = accessors[POSITION];
    for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], a.min[k] + p.t[k]); hi[k] = Math.max(hi[k], a.max[k] + p.t[k]); }
  }
  const gltf = { asset: { version: '2.0', generator: 'xr-003 make-tiles', extras }, scene: 0, scenes: [{ nodes: nodes.map((_, i) => i) }], nodes, meshes, materials, accessors, bufferViews, buffers: [{ byteLength }] };
  const bin = new Uint8Array(byteLength + ((4 - (byteLength % 4)) % 4)); let o = 0; for (const c of chunks) { bin.set(c, o); o += c.byteLength; }
  let json = new TextEncoder().encode(JSON.stringify(gltf)); json = Uint8Array.from([...json, ...new Array((4 - (json.length % 4)) % 4).fill(0x20)]);
  const total = 28 + json.length + bin.length, glb = new Uint8Array(total), dv = new DataView(glb.buffer);
  dv.setUint32(0, 0x46546c67, true); dv.setUint32(4, 2, true); dv.setUint32(8, total, true);
  dv.setUint32(12, json.length, true); dv.setUint32(16, 0x4e4f534a, true); glb.set(json, 20);
  dv.setUint32(20 + json.length, bin.length, true); dv.setUint32(24 + json.length, 0x004e4942, true); glb.set(bin, 28 + json.length);
  return { glb, lo, hi };
}

const r6 = (v) => Math.round(v * 1e6) / 1e6;
// Each tile is 20 x 40 m. Local GLB coords are centred on the tile; scene centre is (cx, 0, 0).
const tiles = [
  { id: 'tile-w', cx: -10, parts: [
    { name: 'GROUND_W_20x40m', geo: new PlaneGeometry(20, 40).rotateX(-Math.PI / 2), t: [0, 0, 0], mat: 0 },
    { name: 'TANK_W_d6m_h8m', geo: new CylinderGeometry(3, 3, 8, 48), t: [2, 4, -6], mat: 1 },
  ] },
  { id: 'tile-e', cx: 10, parts: [
    { name: 'GROUND_E_20x40m', geo: new PlaneGeometry(20, 40).rotateX(-Math.PI / 2), t: [0, 0, 0], mat: 0 },
    { name: 'SHED_E_12x6x5m', geo: new BoxGeometry(12, 5, 6), t: [-1, 2.5, -4], mat: 2 },
  ] },
];
mkdirSync(outDir, { recursive: true });
for (const tile of tiles) {
  const { glb, lo, hi } = writeGlb(tile.parts, { units: 'meters', upAxis: '+Y', synthetic: true, tile: tile.id });
  writeFileSync(join(outDir, `${tile.id}.glb`), glb);
  // scene_zup -> tile-local gltf_yup:  local = R·scene − R·c,  R = Z-up→Y-up, c = (cx, 0, 0)_scene  ⇒  translation (−cx, 0, 0).
  const manifest = {
    schema_version: '0.1.0', artifact_id: `synthetic-${tile.id}-v1`, artifact_type: 'mesh', artifact_origin: 'synthetic',
    uri: `${tile.id}.glb`, sha256: createHash('sha256').update(glb).digest('hex'), mime_type: 'model/gltf-binary', byte_size: glb.length,
    source_frame_id: 'scene_zup', target_frame_id: `gltf_yup_${tile.id}`,
    transform_4x4: [[1, 0, 0, -tile.cx], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]],
    bounds_m: { frame_id: `gltf_yup_${tile.id}`, min: lo.map(r6), max: hi.map(r6) },
    unit: 'm', scale_status: 'metric_unvalidated', reference_lengths: [], dataset_id: 'synthetic-tiles', job_id: 'none', synthetic: true, mesh_acceptance: 'met',
    warnings: ['Synthetic tile; not a camera reconstruction.'],
  };
  writeFileSync(join(outDir, `${tile.id}.manifest.json`), JSON.stringify(manifest, null, 2) + '\n');
  console.log(`wrote ${tile.id}.glb (${glb.length} B) sha256=${manifest.sha256.slice(0, 12)}`);
}
