// XR-006 (two-point measurement, pulled into the sprint): points are picked in viewer space
// (= GLB node space, meters), optionally snapped to the hit triangle's nearest vertex, and stored
// in the manifest's target frame. Distance is computed in the target frame so a non-rigid
// transform can never hide behind a viewer-space number.
import { Matrix4, Vector3, type Intersection, type Mesh } from 'three';
import { toFrame } from './frames';

export interface PickedPoint { viewer: Vector3; target: Vector3; snapped: boolean; objectName: string; /** marker radius, viewer meters (display only) */ markerR?: number }

/** Nearest vertex of the hit face, if within `radius` (meters, viewer space); else the hit point itself. */
export function snapToFaceVertex(hit: Intersection, radius: number): { point: Vector3; snapped: boolean } {
  const mesh = hit.object as Mesh;
  const pos = mesh.geometry?.attributes?.position;
  if (!hit.face || !pos) return { point: hit.point.clone(), snapped: false };
  mesh.updateMatrixWorld();
  let best: Vector3 | null = null;
  let bestD = radius;
  for (const idx of [hit.face.a, hit.face.b, hit.face.c]) {
    const v = new Vector3().fromBufferAttribute(pos, idx).applyMatrix4(mesh.matrixWorld);
    const d = v.distanceTo(hit.point);
    if (d <= bestD) { bestD = d; best = v; }
  }
  return best ? { point: best, snapped: true } : { point: hit.point.clone(), snapped: false };
}

export function makePoint(viewer: Vector3, toTarget: Matrix4, snapped: boolean, objectName: string): PickedPoint {
  return { viewer: viewer.clone(), target: toFrame(viewer, toTarget), snapped, objectName };
}

export function distanceM(a: PickedPoint, b: PickedPoint): number {
  return a.target.distanceTo(b.target);
}

export const fmtM = (d: number): string => (d < 10 ? d.toFixed(3) : d < 1000 ? d.toFixed(2) : d.toFixed(1)) + ' m';
const z = (n: number) => (Math.abs(n) < 5e-4 ? 0 : n).toFixed(3);
export const fmtVec = (v: Vector3): string => `${z(v.x)}, ${z(v.y)}, ${z(v.z)}`;
