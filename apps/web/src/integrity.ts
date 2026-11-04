// XR-002: post-load checks comparing what the manifest claims with what the geometry contains.
// `root` must already carry its placement matrix (SCENE_TO_VIEW · glb_to_scene). All comparisons are
// done in the scene frame; nothing is corrected, only reported. Meshes and point clouds both count.
import { Box3, Vector3, type BufferGeometry, type Object3D } from 'three';
import type { ArtifactManifest, Vec3 } from './manifest';
import { SCENE_TO_VIEW, VIEW_TO_SCENE, checkTransform, matrixFromRowMajor, toFrame } from './frames';

export interface RefCheck { id: string; declared_m: number; endpointsOnGeometry: boolean | null; endpointGap_m: [number, number] | null; manifestDistance_m: number; endpointsWorld: [number[], number[]] }
export interface IntegrityReport {
  transform: ReturnType<typeof checkTransform>;
  geometryBoundsScene: { min: number[]; max: number[] };
  boundsMismatch_m: number;
  references: RefCheck[];
  vertexCount: number;
  pointCount: number;
  triangleCount: number;
  issues: string[];
}

const MAX_VERTICES_FOR_REF_SCAN = 5_000_000;
type Geo = Object3D & { geometry: BufferGeometry; isMesh?: boolean; isPoints?: boolean };

export function geometryObjects(root: Object3D): Geo[] {
  const out: Geo[] = [];
  root.traverse((o) => { const g = o as Geo; if ((g.isMesh || g.isPoints) && g.geometry?.attributes?.position) out.push(g); });
  return out;
}

/** AABB (in the frame `m` maps to) of an axis-aligned box given in another frame. */
function mapBox(min: Vec3 | number[], max: Vec3 | number[], m: ReturnType<typeof matrixFromRowMajor>): Box3 {
  const b = new Box3();
  for (let i = 0; i < 8; i++) b.expandByPoint(toFrame([i & 1 ? max[0] : min[0], i & 2 ? max[1] : min[1], i & 4 ? max[2] : min[2]], m));
  return b;
}

export function inspect(root: Object3D, manifest: ArtifactManifest, tol_m = 0.01): IntegrityReport {
  const glbToScene = matrixFromRowMajor(manifest.glb_to_scene);
  const transform = checkTransform(glbToScene);
  const viewToScene = VIEW_TO_SCENE();
  const sceneToView = SCENE_TO_VIEW();
  root.updateMatrixWorld(true);

  const objs = geometryObjects(root);
  let vertexCount = 0, pointCount = 0, triangleCount = 0;
  const geomScene = new Box3();
  for (const o of objs) {
    const n = o.geometry.attributes.position.count;
    vertexCount += n;
    if (o.isPoints) pointCount += n; else triangleCount += o.geometry.index ? o.geometry.index.count / 3 : n / 3;
    const wb = new Box3().setFromObject(o);
    geomScene.union(mapBox(wb.min.toArray(), wb.max.toArray(), viewToScene));
  }

  const toScene = (frame: string) => (frame === manifest.glb_frame_id ? glbToScene : null);
  const mb = toScene(manifest.bounds_m.frame_id) ? mapBox(manifest.bounds_m.min, manifest.bounds_m.max, glbToScene) : new Box3(new Vector3(...manifest.bounds_m.min), new Vector3(...manifest.bounds_m.max));
  const boundsMismatch_m = geomScene.isEmpty() ? Infinity : Math.max(geomScene.min.distanceTo(mb.min), geomScene.max.distanceTo(mb.max));

  const references: RefCheck[] = (manifest.reference_lengths ?? []).map((r) => {
    const m = toScene(r.frame_id);
    const scenePts = r.endpoints_m.map((e) => (m ? toFrame(e, m) : new Vector3(...e)));
    const world = scenePts.map((p) => toFrame(p, sceneToView));
    const manifestDistance_m = scenePts[0].distanceTo(scenePts[1]);
    const endpointsWorld: [number[], number[]] = [world[0].toArray(), world[1].toArray()];
    if (vertexCount > MAX_VERTICES_FOR_REF_SCAN) return { id: r.id, declared_m: r.length_m, endpointsOnGeometry: null, endpointGap_m: null, manifestDistance_m, endpointsWorld };
    const gaps = world.map((w) => nearestVertexDistance(objs, w)) as [number, number];
    return { id: r.id, declared_m: r.length_m, endpointsOnGeometry: gaps.every((g) => g <= 0.001), endpointGap_m: gaps, manifestDistance_m, endpointsWorld };
  });

  const issues = [...transform.issues];
  if (boundsMismatch_m > tol_m) issues.push(`geometry bounds differ from manifest bounds_m by ${boundsMismatch_m.toFixed(3)} m`);
  for (const r of references) {
    if (Math.abs(r.manifestDistance_m - r.declared_m) > 1e-4) issues.push(`reference ${r.id}: endpoints are ${r.manifestDistance_m.toFixed(4)} m apart but length_m says ${r.declared_m}`);
    if (r.endpointsOnGeometry === false) issues.push(`reference ${r.id}: endpoints are not on the delivered geometry (gaps ${r.endpointGap_m!.map((g) => g.toFixed(4)).join(', ')} m)`);
    if (r.endpointsOnGeometry === null) issues.push(`reference ${r.id}: not checked against geometry (scene too large)`);
  }
  if (!objs.length) issues.push('GLB contains no mesh or point geometry');
  return { transform, geometryBoundsScene: { min: geomScene.min.toArray(), max: geomScene.max.toArray() }, boundsMismatch_m, references, vertexCount, pointCount, triangleCount, issues };
}

function nearestVertexDistance(objs: Geo[], p: Vector3): number {
  let best = Infinity;
  const v = new Vector3();
  for (const o of objs) {
    const pos = o.geometry.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const d = v.fromBufferAttribute(pos, i).applyMatrix4(o.matrixWorld).distanceToSquared(p);
      if (d < best) best = d;
    }
  }
  return Math.sqrt(best);
}
