// XR-002: named frames and authored transforms.
// Scene frame: right-handed, +Z up, meters. Delivered GLB: right-handed, +Y up, meters (glTF 2.0).
// Viewer world = the scene frame turned so +Z points up the screen (three.js is +Y up):
//   world = SCENE_TO_VIEW · scene,  scene = VIEW_TO_SCENE · world.
// A GLB is placed with   object matrix = SCENE_TO_VIEW · glb_to_scene   (from the manifest).
// For the standard Z-up -> Y-up export that product is the identity. Nothing here rescales.
import { Matrix4, Vector3 } from 'three';

/** Row-major: maps glTF (+Y up) coordinates to scene (+Z up). (x, y, z)_gltf -> (x, -z, y)_scene. */
export const GLTF_Y_UP_TO_SCENE_Z_UP: readonly number[] = [
  1, 0, 0, 0,
  0, 0, -1, 0,
  0, 1, 0, 0,
  0, 0, 0, 1,
];
/** Row-major: scene (+Z up) -> glTF (+Y up). Same as Account 1's ZUP_TO_YUP. */
export const SCENE_Z_UP_TO_GLTF_Y_UP: readonly number[] = [
  1, 0, 0, 0,
  0, 0, 1, 0,
  0, -1, 0, 0,
  0, 0, 0, 1,
];

export function matrixFromRowMajor(rows: readonly number[]): Matrix4 {
  if (rows.length !== 16) throw new Error('expected 16 numbers');
  return new Matrix4().set(...(rows as [number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number]));
}

export const SCENE_TO_VIEW = (): Matrix4 => matrixFromRowMajor(SCENE_Z_UP_TO_GLTF_Y_UP);
export const VIEW_TO_SCENE = (): Matrix4 => matrixFromRowMajor(GLTF_Y_UP_TO_SCENE_Z_UP);

/** Object matrix placing GLB node space in viewer world. */
export function placementMatrix(glbToSceneRowMajor: readonly number[]): Matrix4 {
  return SCENE_TO_VIEW().multiply(matrixFromRowMajor(glbToSceneRowMajor));
}

export function isIdentity(m: Matrix4, tol = 1e-9): boolean {
  const id = new Matrix4().elements;
  return m.elements.every((v, i) => Math.abs(v - id[i]) <= tol);
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
