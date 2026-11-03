// XR-002: viewer-side artifact manifest adapter.
// Field names follow the sprint contract; enum values follow schemas/v0 (PR #1) and
// scannit_schemas (PR #3). This is a PROPOSAL until Account 2 publishes the canonical
// fixture — see docs/xr/manifest-proposal.md. No field here is ever used to rescale geometry.

export type ScaleStatus = 'arbitrary' | 'metric_unvalidated' | 'metric_validated';
export const SCALE_STATUSES: readonly ScaleStatus[] = ['arbitrary', 'metric_unvalidated', 'metric_validated'];

export type Vec3 = [number, number, number];

export interface ReferenceLength {
  id: string;
  length_m: number;
  /** Two endpoints in `frame_id` (meters). */
  endpoints_m: [Vec3, Vec3];
  frame_id: string;
}

export interface ArtifactManifest {
  schema_version: string;
  artifact_id: string;
  uri: string;
  sha256: string;
  mime_type: 'model/gltf-binary';
  byte_size?: number;
  /** Row-major 4x4 mapping points in `source_frame_id` (GLB node space) to `target_frame_id`. */
  transform_4x4: number[];
  source_frame_id: string;
  target_frame_id: string;
  /** Axis-aligned bounds in `target_frame_id`, meters. */
  bounds_m: { min: Vec3; max: Vec3 };
  unit: 'm';
  scale_status: ScaleStatus;
  reference_lengths?: ReferenceLength[];
  /** Provenance (optional but shown when present). */
  dataset_id?: string;
  job_id?: string;
  synthetic?: boolean;
  fidelity_warnings?: string[];
}

export type ParseResult = { ok: true; manifest: ArtifactManifest; warnings: string[] } | { ok: false; errors: string[] };

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isVec3 = (v: unknown): v is Vec3 => Array.isArray(v) && v.length === 3 && v.every(isNum);
const isStr = (v: unknown): v is string => typeof v === 'string' && v.length > 0;

/** Units the viewer accepts as meters without conversion. Anything else is refused, never converted. */
const METER_UNITS = new Set(['m', 'METRES', 'meters', 'metres']);

export function parseManifest(input: unknown): ParseResult {
  const errors: string[] = [];
  const warnings: string[] = [];
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

  const t = m.transform_4x4;
  if (!Array.isArray(t) || t.length !== 16 || !t.every(isNum)) errors.push('transform_4x4: required array of 16 finite numbers (row-major)');
  else if (t[12] !== 0 || t[13] !== 0 || t[14] !== 0 || t[15] !== 1) errors.push('transform_4x4: last row must be [0, 0, 0, 1] (affine only)');

  const b = m.bounds_m as Record<string, unknown> | undefined;
  if (!b || !isVec3(b.min) || !isVec3(b.max)) errors.push('bounds_m: required {min:[x,y,z], max:[x,y,z]}');
  else if (b.min.some((v, i) => v > (b.max as Vec3)[i])) errors.push('bounds_m: min must be <= max on every axis');

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
    });
  }

  if (errors.length) return { ok: false, errors };
  if (m.scale_status !== 'metric_validated') warnings.push(`scale_status is ${m.scale_status}: distances are shown but not verified against surveyed checkpoints`);
  if (m.synthetic === true) warnings.push('synthetic artifact: not evidence of camera reconstruction');
  return { ok: true, manifest: { ...(m as unknown as ArtifactManifest), unit: 'm' }, warnings };
}

/** Resolve a manifest `uri` against the manifest's own URL (relative paths) — s3:// etc. need an API-signed URL. */
export function resolveArtifactUrl(uri: string, manifestUrl: string): string {
  if (/^(s3|gs|file):\/\//.test(uri)) throw new Error(`uri scheme not browser-loadable: ${uri.split(':')[0]}:// (needs a signed https URL from the API)`);
  return new URL(uri, manifestUrl).toString();
}
