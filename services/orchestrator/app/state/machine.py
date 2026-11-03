"""Job state machine — enforces valid transitions and tracks pipeline progress.

The reconstruction pipeline progresses through ordered stages:
  CREATED → VALIDATING → INGESTING → POSE_ESTIMATION → DENSE_MVS
  → MESH → MATERIALS → TILING → EXPORTING → QC → COMPLETE

Special transitions:
  Any stage → FAILED | CANCELLED
  FAILED → RETRYING → (resume from the failed stage)
  Any stage → NEEDS_REFERENCE (awaiting external scale reference)
"""

from __future__ import annotations

from scannit_schemas.enums import PIPELINE_STAGES, JobStatus

# Percentage weight for each pipeline stage (sums to 100)
STAGE_WEIGHTS: dict[JobStatus, float] = {
    JobStatus.CREATED: 0.0,
    JobStatus.VALIDATING: 2.0,
    JobStatus.INGESTING: 5.0,
    JobStatus.POSE_ESTIMATION: 20.0,
    JobStatus.DENSE_MVS: 25.0,
    JobStatus.MESH: 20.0,
    JobStatus.MATERIALS: 8.0,
    JobStatus.TILING: 8.0,
    JobStatus.EXPORTING: 5.0,
    JobStatus.QC: 5.0,
    JobStatus.COMPLETE: 2.0,
}


class InvalidTransition(Exception):
    """Raised when a job status transition is not allowed."""

    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition: {current} → {target}")


def _stage_index(status: JobStatus) -> int:
    """Return the index of a status in the pipeline, or -1 for non-pipeline states."""
    try:
        return PIPELINE_STAGES.index(status)
    except ValueError:
        return -1


def is_valid_transition(current: JobStatus, target: JobStatus) -> bool:
    """Check whether a state transition is allowed.

    Rules:
      1. Any pipeline stage → next sequential pipeline stage
      2. Any stage → FAILED or CANCELLED
      3. FAILED → RETRYING
      4. Any pipeline stage → NEEDS_REFERENCE
      5. NEEDS_REFERENCE → resume at the stage that paused
      6. RETRYING → any pipeline stage (resume)
      7. Same state → same state (idempotent, no-op)
    """
    if current == target:
        return True

    # Any state can fail or be cancelled
    if target in (JobStatus.FAILED, JobStatus.CANCELLED):
        return True

    # FAILED can move to RETRYING
    if current == JobStatus.FAILED and target == JobStatus.RETRYING:
        return True

    # RETRYING can resume at any pipeline stage
    if current == JobStatus.RETRYING and _stage_index(target) >= 0:
        return True

    # Any pipeline stage can pause for scale reference
    if target == JobStatus.NEEDS_REFERENCE and _stage_index(current) >= 0:
        return True

    # NEEDS_REFERENCE can resume at any pipeline stage
    if current == JobStatus.NEEDS_REFERENCE and _stage_index(target) >= 0:
        return True

    # Normal forward progression: next sequential stage
    curr_idx = _stage_index(current)
    tgt_idx = _stage_index(target)
    return curr_idx >= 0 and tgt_idx == curr_idx + 1


def validate_transition(current: JobStatus, target: JobStatus) -> None:
    """Validate a state transition, raising InvalidTransition if not allowed."""
    if not is_valid_transition(current, target):
        raise InvalidTransition(current, target)


def compute_progress(status: JobStatus) -> float:
    """Compute cumulative progress percentage for a given pipeline stage.

    Returns the sum of weights for all completed stages up to and
    including the current one. Non-pipeline states (FAILED, CANCELLED,
    RETRYING, NEEDS_REFERENCE) return 0.0 — the caller should preserve
    the last known progress value.
    """
    idx = _stage_index(status)
    if idx < 0:
        return 0.0

    return sum(
        STAGE_WEIGHTS.get(PIPELINE_STAGES[i], 0.0)
        for i in range(idx + 1)
    )


def next_stage(current: JobStatus) -> JobStatus | None:
    """Return the next pipeline stage, or None if at COMPLETE or non-pipeline."""
    idx = _stage_index(current)
    if idx < 0 or idx >= len(PIPELINE_STAGES) - 1:
        return None
    return PIPELINE_STAGES[idx + 1]
