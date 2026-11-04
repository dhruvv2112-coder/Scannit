// Two-point measurement: points are picked in viewer world, snapped to the hit triangle's nearest
// vertex (meshes) or taken as the exact hit point record (point clouds), and stored in the scene
// frame (VIEW_TO_SCENE). Distances are computed in the scene frame. For `arbitrary` scale the
// number is labelled model units, never meters.
import { Matrix4, Vector3, type Intersection, type Mesh } from 'three';
import { toFrame } from './frames';
import type { ScaleStatus } from './manifest';

export interface PickedPoint { viewer: Vector3; target: Vector3; snapped: boolean; objectName: string; markerR?: number; pointIndex?: number }

/** Nearest vertex of the hit face (mesh) or the hit point record (points) within `radius`; else the raw hit. */
export function snapToFaceVertex(hit: Intersection, radius: number): { point: Vector3; snapped: boolean; index?: number } {
  const obj = hit.object as Mesh & { isPoints?: boolean };
  const pos = obj.geometry?.attributes?.position;
  if (!pos) return { point: hit.point.clone(), snapped: false };
  obj.updateMatrixWorld();
  if (obj.isPoints && hit.index != null) {
    return { point: new Vector3().fromBufferAttribute(pos, hit.index).applyMatrix4(obj.matrixWorld), snapped: true, index: hit.index };
  }
  if (!hit.face) return { point: hit.point.clone(), snapped: false };
  let best: Vector3 | null = null;
  let bestD = radius;
  for (const idx of [hit.face.a, hit.face.b, hit.face.c]) {
    const v = new Vector3().fromBufferAttribute(pos, idx).applyMatrix4(obj.matrixWorld);
    const d = v.distanceTo(hit.point);
    if (d <= bestD) { bestD = d; best = v; }
  }
  return best ? { point: best, snapped: true } : { point: hit.point.clone(), snapped: false };
}

export function makePoint(viewer: Vector3, viewToScene: Matrix4, snapped: boolean, objectName: string): PickedPoint {
  return { viewer: viewer.clone(), target: toFrame(viewer, viewToScene), snapped, objectName };
}

export function distanceM(a: PickedPoint, b: PickedPoint): number {
  return a.target.distanceTo(b.target);
}

const num = (d: number) => (d < 10 ? d.toFixed(3) : d < 1000 ? d.toFixed(2) : d.toFixed(1));
export const fmtM = (d: number): string => `${num(d)} m`;
/** Length label that never claims meters for arbitrary scale. */
export const fmtLength = (d: number, s: ScaleStatus): string => (s === 'arbitrary' ? `${num(d)} model units` : `${num(d)} m`);
const z = (n: number) => (Math.abs(n) < 5e-4 ? 0 : n).toFixed(3);
export const fmtVec = (v: Vector3): string => `${z(v.x)}, ${z(v.y)}, ${z(v.z)}`;
