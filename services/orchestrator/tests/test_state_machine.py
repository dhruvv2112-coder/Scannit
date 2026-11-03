"""Tests for the job state machine — transition validation and progress tracking."""

import pytest

from app.state.machine import (
    InvalidTransition,
    compute_progress,
    is_valid_transition,
    next_stage,
    validate_transition,
)
from scannit_schemas.enums import PIPELINE_STAGES, JobStatus


class TestValidTransitions:
    """Tests for forward pipeline progression."""

    def test_created_to_validating(self):
        assert is_valid_transition(JobStatus.CREATED, JobStatus.VALIDATING)

    def test_full_pipeline_forward(self):
        """Every consecutive pair in PIPELINE_STAGES is a valid transition."""
        for i in range(len(PIPELINE_STAGES) - 1):
            assert is_valid_transition(PIPELINE_STAGES[i], PIPELINE_STAGES[i + 1]), (
                f"{PIPELINE_STAGES[i]} → {PIPELINE_STAGES[i + 1]} should be valid"
            )

    def test_skip_stage_rejected(self):
        """Skipping a stage is not allowed."""
        assert not is_valid_transition(JobStatus.CREATED, JobStatus.POSE_ESTIMATION)
        assert not is_valid_transition(JobStatus.VALIDATING, JobStatus.MESH)

    def test_backward_rejected(self):
        """Going backward in the pipeline is not allowed."""
        assert not is_valid_transition(JobStatus.MESH, JobStatus.VALIDATING)
        assert not is_valid_transition(JobStatus.COMPLETE, JobStatus.CREATED)

    def test_same_state_idempotent(self):
        """Transitioning to the same state is always allowed (no-op)."""
        for s in JobStatus:
            assert is_valid_transition(s, s)


class TestSpecialTransitions:
    """Tests for failure, cancellation, retry, and needs-reference flows."""

    def test_any_to_failed(self):
        """Any state can transition to FAILED."""
        for s in JobStatus:
            if s != JobStatus.FAILED:
                assert is_valid_transition(s, JobStatus.FAILED), (
                    f"{s} → FAILED should be valid"
                )

    def test_any_to_cancelled(self):
        """Any state can transition to CANCELLED."""
        for s in JobStatus:
            if s != JobStatus.CANCELLED:
                assert is_valid_transition(s, JobStatus.CANCELLED), (
                    f"{s} → CANCELLED should be valid"
                )

    def test_failed_to_retrying(self):
        assert is_valid_transition(JobStatus.FAILED, JobStatus.RETRYING)

    def test_retrying_to_pipeline_stage(self):
        """RETRYING can resume at any pipeline stage."""
        for s in PIPELINE_STAGES:
            assert is_valid_transition(JobStatus.RETRYING, s), (
                f"RETRYING → {s} should be valid"
            )

    def test_cancelled_cannot_retry(self):
        """CANCELLED is terminal — cannot move to RETRYING."""
        assert not is_valid_transition(JobStatus.CANCELLED, JobStatus.RETRYING)

    def test_any_pipeline_to_needs_reference(self):
        """Any pipeline stage can pause for scale reference."""
        for s in PIPELINE_STAGES:
            assert is_valid_transition(s, JobStatus.NEEDS_REFERENCE)

    def test_needs_reference_to_pipeline_stage(self):
        """NEEDS_REFERENCE can resume at any pipeline stage."""
        for s in PIPELINE_STAGES:
            assert is_valid_transition(JobStatus.NEEDS_REFERENCE, s)

    def test_failed_to_created_rejected(self):
        """FAILED cannot go directly back to a pipeline stage (must RETRY first)."""
        assert not is_valid_transition(JobStatus.FAILED, JobStatus.CREATED)


class TestValidateTransition:
    """Tests for the validate_transition function that raises on invalid."""

    def test_valid_does_not_raise(self):
        validate_transition(JobStatus.CREATED, JobStatus.VALIDATING)

    def test_invalid_raises(self):
        with pytest.raises(InvalidTransition) as exc_info:
            validate_transition(JobStatus.CREATED, JobStatus.MESH)
        assert exc_info.value.current == JobStatus.CREATED
        assert exc_info.value.target == JobStatus.MESH


class TestProgress:
    """Tests for progress percentage computation."""

    def test_created_is_zero(self):
        assert compute_progress(JobStatus.CREATED) == 0.0

    def test_complete_is_100(self):
        assert compute_progress(JobStatus.COMPLETE) == 100.0

    def test_monotonically_increasing(self):
        """Progress must increase at each pipeline stage."""
        prev = -1.0
        for stage in PIPELINE_STAGES:
            p = compute_progress(stage)
            assert p > prev, f"Progress at {stage} ({p}) should be > {prev}"
            prev = p

    def test_non_pipeline_returns_zero(self):
        """Non-pipeline states return 0 — caller preserves last known value."""
        assert compute_progress(JobStatus.FAILED) == 0.0
        assert compute_progress(JobStatus.CANCELLED) == 0.0
        assert compute_progress(JobStatus.RETRYING) == 0.0
        assert compute_progress(JobStatus.NEEDS_REFERENCE) == 0.0


class TestNextStage:
    """Tests for next_stage helper."""

    def test_created_next_is_validating(self):
        assert next_stage(JobStatus.CREATED) == JobStatus.VALIDATING

    def test_qc_next_is_complete(self):
        assert next_stage(JobStatus.QC) == JobStatus.COMPLETE

    def test_complete_has_no_next(self):
        assert next_stage(JobStatus.COMPLETE) is None

    def test_failed_has_no_next(self):
        assert next_stage(JobStatus.FAILED) is None

    def test_all_pipeline_stages_chain(self):
        """Walking next_stage from CREATED reaches COMPLETE."""
        current = JobStatus.CREATED
        visited = [current]
        while True:
            nxt = next_stage(current)
            if nxt is None:
                break
            visited.append(nxt)
            current = nxt
        assert visited == list(PIPELINE_STAGES)
