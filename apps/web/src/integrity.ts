// XR-002: post-load checks comparing what the manifest claims with what the geometry contains.
// All comparisons happen in the manifest's target frame; nothing is corrected, only reported.
import { Box3, Matrix4, Vector3, type Mesh, type Object3D } from 'three';
import type { ArtifactManifest } from './manifest';
import { checkTransform, matrixFromRowMajor, toFrame } from './frames';

export interface RefCheck { id: string; declared_m: number; endpointsOnGeometry: boolean | null; endpointGap_m: [number, number] | null; manifestDistance_m: number }
export interface IntegrityReport {
  transform: ReturnType<typeof checkTransform>;
  geometryBounds_m: { min: number[]; max: number[] };
  boundsMismatch_m: number;
  references: RefCheck[];
  vertexCount: number;
  issues: string[];
}

const MAX_VERTICES_FOR_REF_SCAN = 5_000_000;

export function inspect(root: Object3D, manifest: ArtifactManifest, tol_m = 0.01): IntegrityReport {
  const toTarget = matrixFromRowMajor(manifest.transform_4x4);
  const transform = checkTransform(toTarget);
  root.updateMatrixWorld(true);

  // Geometry bounds in target frame (transform the 8 corners of each mesh's world box).
  const tb = new Box3();
  let vertexCount = 0;
  const meshes: Mesh[] = [];
  root.traverse((o) => { const m = o as Mesh; if (m.isMesh && m.geometry?.attributes?.position) { meshes.push(m); vertexCount += m.geometry.attributes.position.count; } });
  for (const m of meshes) {
    const b = new Box3().setFromObject(m);
    for (let i = 0; i < 8; i++) tb.expandByPoint(toFrame(new Vector3(i & 1 ? b.max.x : b.min.x, i & 2 ? b.max.y : b.min.y, i & 4 ? b.max.z : b.min.z), toTarget));
  }
  const mn = new Vector3(...manifest.bounds_m.min), mx = new Vector3(...manifest.bounds_m.max);
  const boundsMismatch_m = tb.isEmpty() ? Infinity : Math.max(tb.min.distanceTo(mn), tb.max.distanceTo(mx));

  const inv = toTarget.clone().invert();
  const references: RefCheck[] = (manifest.reference_lengths ?? []).map((r) => {
    const manifestDistance_m = new Vector3(...r.endpoints_m[0]).distanceTo(new Vector3(...r.endpoints_m[1]));
    if (r.frame_id !== manifest.target_frame_id || vertexCount > MAX_VERTICES_FOR_REF_SCAN) {
      return { id: r.id, declared_m: r.length_m, endpointsOnGeometry: null, endpointGap_m: null, manifestDistance_m };
    }
    const gaps = r.endpoints_m.map((e) => nearestVertexDistance(meshes, toFrame(e, inv))) as [number, number];
    return { id: r.id, declared_m: r.length_m, endpointsOnGeometry: gaps.every((g) => g <= 0.001), endpointGap_m: gaps, manifestDistance_m };
  });

  const issues = [...transform.issues];
  if (boundsMismatch_m > tol_m) issues.push(`geometry bounds differ from manifest bounds_m by ${boundsMismatch_m.toFixed(3)} m`);
  for (const r of references) {
    if (Math.abs(r.manifestDistance_m - r.declared_m) > 1e-4) issues.push(`reference ${r.id}: endpoints are ${r.manifestDistance_m.toFixed(4)} m apart but length_m says ${r.declared_m}`);
    if (r.endpointsOnGeometry === false) issues.push(`reference ${r.id}: endpoints are not on the delivered geometry (gaps ${r.endpointGap_m!.map((g) => g.toFixed(4)).join(', ')} m)`);
    if (r.endpointsOnGeometry === null) issues.push(`reference ${r.id}: not checked against geometry (different frame or scene too large)`);
  }
  return { transform, geometryBounds_m: { min: tb.min.toArray(), max: tb.max.toArray() }, boundsMismatch_m, references, vertexCount, issues };
}

function nearestVertexDistance(meshes: Mesh[], p: Vector3): number {
  let best = Infinity;
  const v = new Vector3();
  for (const m of meshes) {
    const pos = m.geometry.attributes.position;
    const w: Matrix4 = m.matrixWorld;
    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i).applyMatrix4(w);
      const d = v.distanceToSquared(p);
      if (d < best) best = d;
    }
  }
  return Math.sqrt(best);
}
