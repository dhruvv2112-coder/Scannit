// User-facing lifecycle: one of six states, derived from the API job status and the viewer's own
// load/verification phase. Kept separate from the internal phase so wording stays consistent.
import type { JobStatus } from './source';
import type { ScaleStatus } from './manifest';

export type Lifecycle = 'uploaded' | 'queued' | 'processing' | 'ready' | 'failed' | 'unvalidated_scale';

export const LIFECYCLE_LABEL: Record<Lifecycle, string> = {
  uploaded: 'Uploaded — waiting to start',
  queued: 'Queued',
  processing: 'Processing',
  ready: 'Ready — metric scale validated',
  failed: 'Failed',
  unvalidated_scale: 'Ready — scale not validated',
};

export function lifecycleFromJob(s: JobStatus): Lifecycle {
  switch (s) {
    case 'CREATED': return 'uploaded';
    case 'VALIDATING': case 'RETRYING': return 'queued';
    case 'FAILED': case 'CANCELLED': case 'NEEDS_REFERENCE': return 'failed';
    case 'COMPLETE': return 'processing'; // still needs manifest + GLB + verification in the viewer
    default: return 'processing';
  }
}

/** After the viewer has loaded and verified the artifact. */
export function lifecycleWhenLoaded(scale: ScaleStatus, integrityIssues: number): Lifecycle {
  return scale === 'metric_validated' && integrityIssues === 0 ? 'ready' : 'unvalidated_scale';
}
