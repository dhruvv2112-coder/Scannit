import { Box3, Vector3, type Object3D } from 'three';

export const REFERENCE_NODE = 'REF_1m';

/** World-space axis-aligned extent of a named node, in scene units (meters per contract). */
export function measureNodeExtent(root: Object3D, name: string): Vector3 | null {
  const node = root.getObjectByName(name);
  if (!node) return null;
  root.updateMatrixWorld(true);
  return new Box3().setFromObject(node).getSize(new Vector3());
}

export interface ScaleCheck {
  found: boolean;
  expected_m: number;
  measured_m: number | null;
  ok: boolean;
}

/**
 * Software scaling check only: verifies the viewer did not rescale the asset.
 * It says nothing about physical reconstruction accuracy (account 1's surveyed checkpoints).
 */
export function checkReferenceScale(root: Object3D, expected_m = 1.0, tolerance_m = 1e-4): ScaleCheck {
  const ext = measureNodeExtent(root, REFERENCE_NODE);
  if (!ext) return { found: false, expected_m, measured_m: null, ok: false };
  return { found: true, expected_m, measured_m: ext.x, ok: Math.abs(ext.x - expected_m) <= tolerance_m };
}
