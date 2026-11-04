// XR-002: viewer-side artifact manifest ADAPTER (not a competing schema).
// Accepts the shape Account 1 actually emits (PR #8 photogrammetry.run: nested row-major 4x4 mapping
// scene_zup -> gltf_yup, bounds_m with frame_id, producer warnings) and the earlier viewer proposal
// (flat 16, gltf -> scene). Everything is normalised to "GLB node space -> scene frame" and every
// interpretation is reported as a note. Nothing here rescales geometry; units other than meters
// are refused. Replace with Account 2's canonical schema once its ADR lands.

export type ScaleStatus = 'arbitrary' | 'metric_unvalidated' | 'metric_validated';
export const SCALE_STATUSES: readonly ScaleStatus[] = ['arbitrary', 'metric_unvalidated', 'metric_validated'];
export type Vec3 = [number, number, number];
export type ArtifactOrigin = 'camera' | 'synthetic' | 'unknown';

export interface ReferenceLength { id: string; length_m: number; endpoints_m: [Vec3, Vec3]; frame_id: string }

/** Normalised manifest used by the viewer. Raw producer fields are kept for display/export. */
export interface ArtifactManifest {
  schema_version: string;
  artifact_id: string;
  uri: string;
  sha256: string;
  mime_type: 'model/gltf-binary';
  byte_size?: number;
  /** Row-major 16, maps GLB node coordinates -> scene frame (derived; see notes). */
  glb_to_scene: number[];
  glb_frame_id: string;
  scene_frame_id: string;
  /** As written by the producer. */
  source_frame_id: string;
  target_frame_id: string;
  transform_4x4: number[];
  bounds_m: { frame_id: string; min: Vec3; max: Vec3 };
  unit: 'm';
  scale_status: ScaleStatus;
  reference_lengths?: ReferenceLength[];
  dataset_id?: string;
  job_id?: string;
  synthetic?: boolean;
  artifact_origin: ArtifactOrigin;
  artifact_type?: string;
  mesh_acceptance?: string;
  source_image_manifest_uri?: string;
  producer_warnings: string[];
  fidelity_warnings?: string[];
  raw: Record<string, unknown>;
}

export type ParseResult =
  | { ok: true; manifest: ArtifactManifest; warnings: string[]; notes: string[] }
  | { ok: false; errors: string[] };

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isVec3 = (v: unknown): v is Vec3 => Array.isArray(v) && v.length === 3 && v.every(isNum);
const isStr = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
const METER_UNITS = new Set(['m', 'METRES', 'meters', 'metres']);
/** A frame id that names glTF/GLB node space. */
export const isGlbFrame = (id: string): boolean => /gltf|glb|y_?up/i.test(id);

/** Accepts flat 16 or nested 4x4, both row-major. Returns flat row-major or null. */
export function flattenMatrix(t: unknown): number[] | null {
  if (Array.isArray(t) && t.length === 16 && t.every(isNum)) return t as number[];
  if (Array.isArray(t) && t.length === 4 && t.every((r) => Array.isArray(r) && r.length === 4 && r.every(isNum))) return (t as number[][]).flat();
  return null;
}

/** Inverse of a row-major affine 4x4 (rigid or not); null if singular. */
export function invertRowMajor(m: number[]): number[] | null {
  const [a, b, c, tx, d, e, f, ty, g, h, i, tz] = m;
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (Math.abs(det) < 1e-12) return null;
  const r = [
    A / det, -(b * i - c * h) / det, (b * f - c * e) / det,
    B / det, (a * i - c * g) / det, -(a * f - c * d) / det,
    C / det, -(a * h - b * g) / det, (a * e - b * d) / det,
  ];
  const t = [
    -(r[0] * tx + r[1] * ty + r[2] * tz),
    -(r[3] * tx + r[4] * ty + r[5] * tz),
    -(r[6] * tx + r[7] * ty + r[8] * tz),
  ];
  return [r[0], r[1], r[2], t[0], r[3], r[4], r[5], t[1], r[6], r[7], r[8], t[2], 0, 0, 0, 1].map((v) => (v === 0 ? 0 : v)); // no -0
}

export function parseManifest(input: unknown): ParseResult {
  const errors: string[] = [];
  const warnings: string[] = [];
  const notes: string[] = [];
  if (typeof input !== 'object' || input === null || Array.isArray(input)) return { ok: false, errors: ['manifest is not a JSON object'] };
  const m = input as Record<string, unknown>;

  for (const k of ['schema_version', 'artifact_id', 'uri', 'sha256', 'mime_type', 'source_frame_id', 'target_frame_id', 'scale_status'] as const) {
    if (!isStr(m[k])) errors.push(`${k}: required string`);
  }
  if (isStr(m.sha256) && !/^[0-9a-f]{64}$/.test(m.sha256)) errors.push('sha256: must be 64 lowercase hex characters');
  if (isStr(m.mime_type) && m.mime_type !== 'model/gltf-binary') errors.push(`mime_type: viewer loads model/gltf-binary only (got ${m.mime_type})`);
  if (isStr(m.uri) && /[?#]/.test(m.uri) && !/^https?:\/\//.test(m.uri)) errors.push('uri: query strings or fragments not allowed on storage paths');
  if (!isStr(m.unit)) errors.push('unit: required');
  else if (!METER_UNITS.has(m.unit)) errors.push(`unit: "${m.unit}" is not meters; the viewer refuses to convert units silently`);
  if (isStr(m.scale_status) && !SCALE_STATUSES.includes(m.scale_status as ScaleStatus)) errors.push(`scale_status: must be one of ${SCALE_STATUSES.join(', ')}`);

  const flat = flattenMatrix(m.transform_4x4);
  if (!flat) errors.push('transform_4x4: required 16 numbers, flat or 4x4 nested (row-major)');
  else if (flat[12] !== 0 || flat[13] !== 0 || flat[14] !== 0 || flat[15] !== 1) errors.push('transform_4x4: last row must be [0, 0, 0, 1] (affine only)');

  // Which side of the transform is GLB node space?
  let glbToScene: number[] | null = null;
  let glbFrame = '', sceneFrame = '';
  if (flat && isStr(m.source_frame_id) && isStr(m.target_frame_id) && !errors.length) {
    const sGlb = isGlbFrame(m.source_frame_id), tGlb = isGlbFrame(m.target_frame_id);
    if (sGlb === tGlb) errors.push(`frames: cannot tell which of "${m.source_frame_id}" / "${m.target_frame_id}" is the GLB frame (expected exactly one id naming gltf/glb/yup)`);
    else if (sGlb) { glbToScene = flat; glbFrame = m.source_frame_id; sceneFrame = m.target_frame_id; notes.push(`transform maps ${glbFrame} (GLB) → ${sceneFrame} (scene); used as written`); }
    else {
      glbToScene = invertRowMajor(flat);
      glbFrame = m.target_frame_id; sceneFrame = m.source_frame_id;
      if (!glbToScene) errors.push('transform_4x4: singular, cannot invert');
      else notes.push(`transform maps ${sceneFrame} (scene) → ${glbFrame} (GLB); viewer uses its inverse to place the GLB`);
    }
  }

  const b = m.bounds_m as Record<string, unknown> | undefined;
  let boundsFrame = '';
  if (!b || !isVec3(b.min) || !isVec3(b.max)) errors.push('bounds_m: required {frame_id?, min:[x,y,z], max:[x,y,z]}');
  else {
    if (b.min.some((v, i) => v > (b.max as Vec3)[i])) errors.push('bounds_m: min must be <= max on every axis');
    if (isStr(b.frame_id)) boundsFrame = b.frame_id;
    else if (isStr(m.target_frame_id)) { boundsFrame = m.target_frame_id; notes.push(`bounds_m has no frame_id; assumed target frame ${boundsFrame}`); }
    if (boundsFrame && glbFrame && boundsFrame !== glbFrame && boundsFrame !== sceneFrame) errors.push(`bounds_m.frame_id "${boundsFrame}" is neither ${glbFrame} nor ${sceneFrame}`);
  }

  if (m.byte_size !== undefined && !(isNum(m.byte_size) && m.byte_size >= 0)) errors.push('byte_size: must be a non-negative number');

  if (m.reference_lengths !== undefined) {
    if (!Array.isArray(m.reference_lengths)) errors.push('reference_lengths: must be an array');
    else m.reference_lengths.forEach((r, i) => {
      const ref = r as Record<string, unknown>;
      if (!isStr(ref?.id)) errors.push(`reference_lengths[${i}].id: required`);
      if (!(isNum(ref?.length_m) && ref.length_m > 0)) errors.push(`reference_lengths[${i}].length_m: positive number required`);
      const e = ref?.endpoints_m;
      if (!(Array.isArray(e) && e.length === 2 && e.every(isVec3))) errors.push(`reference_lengths[${i}].endpoints_m: two [x,y,z] points required`);
      if (!isStr(ref?.frame_id)) errors.push(`reference_lengths[${i}].frame_id: required`);
      else if (glbFrame && ref.frame_id !== glbFrame && ref.frame_id !== sceneFrame) errors.push(`reference_lengths[${i}].frame_id "${ref.frame_id}" is neither ${glbFrame} nor ${sceneFrame}`);
    });
  }
  if (errors.length) return { ok: false, errors };

  const producerWarnings = Array.isArray(m.warnings) ? (m.warnings as unknown[]).filter(isStr) : [];
  const synthetic = m.synthetic === true;
  const origin: ArtifactOrigin = synthetic ? 'synthetic' : m.artifact_origin === 'camera' ? 'camera' : m.artifact_origin === 'synthetic' ? 'synthetic' : 'unknown';
  if (synthetic && m.artifact_origin === 'camera') warnings.push('manifest says both synthetic and camera-derived; treated as synthetic');
  if (m.scale_status === 'arbitrary') warnings.push('scale_status is arbitrary: lengths are model units, not meters');
  else if (m.scale_status !== 'metric_validated') warnings.push(`scale_status is ${m.scale_status}: distances are shown but not verified against surveyed checkpoints`);
  if (origin === 'synthetic') warnings.push('synthetic artifact: not evidence of camera reconstruction');
  if (origin === 'unknown') warnings.push('artifact origin not stated: not presented as camera-derived');
  if (isStr(m.mesh_acceptance) && m.mesh_acceptance !== 'met') warnings.push(`mesh acceptance: ${m.mesh_acceptance}`);

  const manifest: ArtifactManifest = {
    schema_version: m.schema_version as string,
    artifact_id: m.artifact_id as string,
    uri: m.uri as string,
    sha256: m.sha256 as string,
    mime_type: 'model/gltf-binary',
    byte_size: m.byte_size as number | undefined,
    glb_to_scene: glbToScene!,
    glb_frame_id: glbFrame,
    scene_frame_id: sceneFrame,
    source_frame_id: m.source_frame_id as string,
    target_frame_id: m.target_frame_id as string,
    transform_4x4: flat!,
    bounds_m: { frame_id: boundsFrame, min: (b as { min: Vec3 }).min, max: (b as { max: Vec3 }).max },
    unit: 'm',
    scale_status: m.scale_status as ScaleStatus,
    reference_lengths: m.reference_lengths as ReferenceLength[] | undefined,
    dataset_id: isStr(m.dataset_id) ? m.dataset_id : undefined,
    job_id: isStr(m.job_id) ? m.job_id : undefined,
    synthetic,
    artifact_origin: origin,
    artifact_type: isStr(m.artifact_type) ? m.artifact_type : undefined,
    mesh_acceptance: isStr(m.mesh_acceptance) ? m.mesh_acceptance : undefined,
    source_image_manifest_uri: isStr(m.source_image_manifest_uri) ? m.source_image_manifest_uri : undefined,
    producer_warnings: producerWarnings,
    fidelity_warnings: Array.isArray(m.fidelity_warnings) ? (m.fidelity_warnings as unknown[]).filter(isStr) : undefined,
    raw: m,
  };
  return { ok: true, manifest, warnings, notes };
}

/** Resolve a manifest `uri` against the manifest's own URL (relative paths) — s3:// etc. need an API-signed URL. */
export function resolveArtifactUrl(uri: string, manifestUrl: string): string {
  if (/^(s3|gs|file):\/\//.test(uri)) throw new Error(`uri scheme not browser-loadable: ${uri.split(':')[0]}:// (needs a signed https URL from the API)`);
  return new URL(uri, manifestUrl).toString();
}
