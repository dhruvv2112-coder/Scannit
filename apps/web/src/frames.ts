// XR-002: named frames and authored transforms.
// Scene frame: right-handed, +Z up, meters. Delivered GLB: right-handed, +Y up, meters (glTF 2.0).
// The viewer renders GLB node space untouched; points are converted to the target frame only for
// display and storage. Nothing here rescales the scene.
import { Matrix4, Vector3 } from 'three';

/** Row-major: maps GLB (+Y up) coordinates to scene (+Z up). (x, y, z)_gltf -> (x, -z, y)_scene. */
export const GLTF_Y_UP_TO_SCENE_Z_UP: readonly number[] = [
  1, 0, 0, 0,
  0, 0, -1, 0,
  0, 1, 0, 0,
  0, 0, 0, 1,
];

export function matrixFromRowMajor(rows: readonly number[]): Matrix4 {
  if (rows.length !== 16) throw new Error('expected 16 numbers');
  // Matrix4.set takes row-major arguments.
  return new Matrix4().set(...(rows as [number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number]));
}

export function toFrame(p: Vector3 | readonly number[], m: Matrix4): Vector3 {
  const v = p instanceof Vector3 ? p.clone() : new Vector3(p[0], p[1], p[2]);
  return v.applyMatrix4(m);
}

export interface TransformCheck { rigid: boolean; scale: [number, number, number]; determinant: number; issues: string[] }

/** Flags transforms that would change measured lengths (non-unit scale, shear, reflection). */
export function checkTransform(m: Matrix4, tol = 1e-6): TransformCheck {
  const e = m.elements; // column-major
  const cols = [new Vector3(e[0], e[1], e[2]), new Vector3(e[4], e[5], e[6]), new Vector3(e[8], e[9], e[10])];
  const scale = cols.map((c) => c.length()) as [number, number, number];
  const det = m.determinant();
  const issues: string[] = [];
  if (scale.some((s) => Math.abs(s - 1) > tol)) issues.push(`axis scale ${scale.map((s) => s.toFixed(6)).join(', ')} is not 1: transform would change lengths`);
  if (Math.abs(cols[0].dot(cols[1])) > tol || Math.abs(cols[0].dot(cols[2])) > tol || Math.abs(cols[1].dot(cols[2])) > tol) issues.push('transform has shear');
  if (det < 0) issues.push('transform mirrors the scene (determinant < 0): handedness would flip');
  return { rigid: issues.length === 0, scale, determinant: det, issues };
}
