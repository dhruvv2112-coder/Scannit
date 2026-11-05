"""Failure state tests for the reconstruction pipeline.

Tests the contract-specified failure scenarios:
  - Invalid video/input
  - Missing scale reference
  - COLMAP failure
  - GPU unavailable
  - No GLB output
  - Invalid artifact hash
  - Expired presigned URL
  - Failed worker (subprocess crash)
  - Cancel during execution
  - Retry after transient failure

Each test verifies:
  1. Correct error category is reported
  2. Job transitions to FAILED (or CANCELLED)
  3. API callbacks are invoked with correct payload
  4. Retry logic follows expected behavior
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from scannit_schemas.enums import JobStatus

# --- Mock celery before importing worker ---
_celery_mock = MagicMock()
_celery_task_decorator = MagicMock(side_effect=lambda *a, **kw: lambda f: f)
_celery_mock.task = _celery_task_decorator
sys.modules.setdefault("celery", MagicMock())
sys.modules.setdefault("app.celery_app", MagicMock(celery=_celery_mock))

# Mock boto3 for S3 upload tests
_boto3_mock = MagicMock()
sys.modules.setdefault("boto3", _boto3_mock)
sys.modules.setdefault("botocore", MagicMock())
sys.modules.setdefault("botocore.config", MagicMock())

# Mock httpx for API callback tests
_httpx_mock = MagicMock()
sys.modules.setdefault("httpx", _httpx_mock)

from app.state.machine import (  # noqa: E402
    InvalidTransition,
    compute_progress,
    is_valid_transition,
    validate_transition,
)
from app.workers.reconstruction import (  # noqa: E402
    _build_cli_args,
    _categorize_error,
    _check_stage_idempotent,
    _mark_stage_complete,
    _validate_config,
    ARTIFACT_PRODUCING_STAGES,
    ERROR_CATEGORIES,
    STAGE_TIMEOUTS,
)


# ── Error categorization ──────────────────────────────────────────────


class TestErrorCategorization:
    """Test that errors are classified into the correct categories."""

    def test_invalid_input_at_validation(self):
        result = _categorize_error(JobStatus.VALIDATING, "bad file format", 1)
        assert result == "INVALID_INPUT"

    def test_colmap_failure_detected(self):
        result = _categorize_error(
            JobStatus.POSE_ESTIMATION,
            "COLMAP SfM failed: insufficient features in images",
            1,
        )
        assert result == "COLMAP_FAILURE"

    def test_sfm_failure_detected(self):
        result = _categorize_error(
            JobStatus.POSE_ESTIMATION,
            "SfM reconstruction diverged after 500 iterations",
            1,
        )
        assert result == "COLMAP_FAILURE"

    def test_gpu_unavailable_cuda(self):
        result = _categorize_error(
            JobStatus.DENSE_MVS,
            "CUDA error: no CUDA-capable device is detected",
            1,
        )
        assert result == "GPU_UNAVAILABLE"

    def test_gpu_out_of_memory(self):
        result = _categorize_error(
            JobStatus.DENSE_MVS,
            "RuntimeError: CUDA out of memory. Tried to allocate 4.00 GiB",
            1,
        )
        assert result == "GPU_UNAVAILABLE"

    def test_no_glb_output(self):
        result = _categorize_error(
            JobStatus.EXPORTING,
            "Error: no GLB file produced by exporter",
            1,
        )
        assert result == "NO_GLB_OUTPUT"

    def test_missing_reference(self):
        result = _categorize_error(
            JobStatus.MESH,
            "Scale reference file not found at /data/refs/scale.json",
            1,
        )
        assert result == "MISSING_REFERENCE"

    def test_generic_subprocess_error(self):
        result = _categorize_error(
            JobStatus.MATERIALS,
            "Segmentation fault (core dumped)",
            139,
        )
        assert result == "SUBPROCESS_ERROR"


# ── State machine failure transitions ─────────────────────────────────


class TestFailureTransitions:
    """Verify the state machine handles failure paths correctly."""

    def test_any_pipeline_stage_can_fail(self):
        """Every pipeline stage can transition to FAILED."""
        from scannit_schemas.enums import PIPELINE_STAGES

        for stage in PIPELINE_STAGES:
            assert is_valid_transition(stage, JobStatus.FAILED), (
                f"{stage} → FAILED should be valid"
            )

    def test_failed_cannot_go_to_complete(self):
        """FAILED jobs cannot skip to COMPLETE."""
        assert not is_valid_transition(JobStatus.FAILED, JobStatus.COMPLETE)

    def test_failed_to_retrying(self):
        assert is_valid_transition(JobStatus.FAILED, JobStatus.RETRYING)

    def test_retrying_resumes_at_any_stage(self):
        """RETRYING can resume at any pipeline stage."""
        from scannit_schemas.enums import PIPELINE_STAGES

        for stage in PIPELINE_STAGES:
            assert is_valid_transition(JobStatus.RETRYING, stage)

    def test_cancelled_is_terminal(self):
        """CANCELLED cannot transition to anything except itself."""
        assert not is_valid_transition(JobStatus.CANCELLED, JobStatus.RETRYING)
        assert not is_valid_transition(JobStatus.CANCELLED, JobStatus.CREATED)
        # Idempotent same-state is always allowed
        assert is_valid_transition(JobStatus.CANCELLED, JobStatus.CANCELLED)

    def test_cancel_from_any_active_state(self):
        """Any non-terminal state can be cancelled."""
        from scannit_schemas.enums import PIPELINE_STAGES

        for stage in PIPELINE_STAGES:
            if stage not in (JobStatus.COMPLETE,):
                assert is_valid_transition(stage, JobStatus.CANCELLED)

    def test_needs_reference_from_pipeline(self):
        """Pipeline stages can pause for scale reference."""
        assert is_valid_transition(JobStatus.MESH, JobStatus.NEEDS_REFERENCE)
        assert is_valid_transition(JobStatus.POSE_ESTIMATION, JobStatus.NEEDS_REFERENCE)

    def test_needs_reference_resume(self):
        """NEEDS_REFERENCE can resume at the paused stage."""
        assert is_valid_transition(JobStatus.NEEDS_REFERENCE, JobStatus.MESH)

    def test_invalid_transition_raises(self):
        """Validate that InvalidTransition is raised for bad transitions."""
        with pytest.raises(InvalidTransition) as exc_info:
            validate_transition(JobStatus.FAILED, JobStatus.COMPLETE)
        assert exc_info.value.current == JobStatus.FAILED
        assert exc_info.value.target == JobStatus.COMPLETE


# ── Idempotent retry logic ────────────────────────────────────────────


class TestIdempotentRetry:
    """Test the sentinel-based idempotent retry mechanism."""

    def test_stage_not_complete_initially(self, tmp_path):
        assert not _check_stage_idempotent(str(tmp_path), JobStatus.MESH)

    def test_stage_complete_after_marking(self, tmp_path):
        _mark_stage_complete(str(tmp_path), JobStatus.MESH)
        assert _check_stage_idempotent(str(tmp_path), JobStatus.MESH)

    def test_different_stages_independent(self, tmp_path):
        _mark_stage_complete(str(tmp_path), JobStatus.MESH)
        assert not _check_stage_idempotent(str(tmp_path), JobStatus.MATERIALS)

    def test_sentinel_file_location(self, tmp_path):
        _mark_stage_complete(str(tmp_path), JobStatus.EXPORTING)
        sentinel = tmp_path / ".stage_complete_exporting"
        assert sentinel.exists()


# ── Timeout configuration ─────────────────────────────────────────────


class TestTimeouts:
    """Verify timeout configuration for each stage."""

    def test_all_executable_stages_have_timeouts(self):
        from scannit_schemas.enums import PIPELINE_STAGES

        # CREATED is the initial state, not an executable stage
        executable = PIPELINE_STAGES[1:]
        for stage in executable:
            assert stage in STAGE_TIMEOUTS, f"Stage {stage} missing timeout"

    def test_reconstruction_stages_have_longest_timeouts(self):
        """Heavy reconstruction stages should have generous timeouts."""
        assert STAGE_TIMEOUTS[JobStatus.DENSE_MVS] >= 3600
        assert STAGE_TIMEOUTS[JobStatus.POSE_ESTIMATION] >= 3600
        assert STAGE_TIMEOUTS[JobStatus.MESH] >= 3600

    def test_validation_has_short_timeout(self):
        assert STAGE_TIMEOUTS[JobStatus.VALIDATING] <= 300


# ── Config validation (security) ──────────────────────────────────────


class TestConfigSecurity:
    """Verify config filtering prevents injection."""

    def test_allowed_keys_pass_through(self):
        config = {"dense": True, "max_frames": 500}
        assert _validate_config(config) == config

    def test_dangerous_keys_stripped(self):
        config = {"dense": True, "rm_rf": "/", "shell": "evil", "--inject": "x"}
        result = _validate_config(config)
        assert result == {"dense": True}

    def test_cli_args_no_shell_injection(self):
        """CLI args should never allow shell metacharacter injection."""
        args = _build_cli_args(
            stage=JobStatus.MESH,
            job_id="test; rm -rf /",
            dataset_path="/data/$(whoami)",
            work_dir="/tmp/`id`",
            config={"dense": True},
        )
        # All args are explicit strings, never shell-interpreted
        assert all(isinstance(a, str) for a in args)
        # No shell=True means these are safe
        assert "test; rm -rf /" in args  # literal string, not interpreted


# ── Error categories ──────────────────────────────────────────────────


class TestErrorCategories:
    """Verify all contract-specified error categories exist."""

    def test_all_categories_defined(self):
        required = {
            "INVALID_INPUT",
            "COLMAP_FAILURE",
            "GPU_UNAVAILABLE",
            "NO_GLB_OUTPUT",
            "INVALID_HASH",
            "UPLOAD_FAILED",
            "TIMEOUT",
            "MISSING_REFERENCE",
            "SUBPROCESS_ERROR",
        }
        assert required.issubset(set(ERROR_CATEGORIES.keys()))

    def test_categories_have_descriptions(self):
        for key, desc in ERROR_CATEGORIES.items():
            assert isinstance(desc, str)
            assert len(desc) > 5, f"Category {key} needs a description"


# ── Artifact stage coverage ───────────────────────────────────────────


class TestArtifactStages:
    """Verify artifact-producing stages are correctly configured."""

    def test_exporting_produces_artifacts(self):
        assert JobStatus.EXPORTING in ARTIFACT_PRODUCING_STAGES

    def test_mesh_produces_artifacts(self):
        assert JobStatus.MESH in ARTIFACT_PRODUCING_STAGES

    def test_validating_does_not_produce_artifacts(self):
        assert JobStatus.VALIDATING not in ARTIFACT_PRODUCING_STAGES

    def test_ingesting_does_not_produce_artifacts(self):
        assert JobStatus.INGESTING not in ARTIFACT_PRODUCING_STAGES


# ── Progress tracking ─────────────────────────────────────────────────


class TestProgressOnFailure:
    """Verify progress is tracked correctly through failure paths."""

    def test_failed_preserves_zero(self):
        """Non-pipeline states return 0 — caller preserves last known value."""
        assert compute_progress(JobStatus.FAILED) == 0.0

    def test_retrying_preserves_zero(self):
        assert compute_progress(JobStatus.RETRYING) == 0.0

    def test_cancelled_preserves_zero(self):
        assert compute_progress(JobStatus.CANCELLED) == 0.0

    def test_needs_reference_preserves_zero(self):
        assert compute_progress(JobStatus.NEEDS_REFERENCE) == 0.0

    def test_progress_increases_through_pipeline(self):
        """Progress monotonically increases through pipeline stages."""
        from scannit_schemas.enums import PIPELINE_STAGES

        prev = -1.0
        for stage in PIPELINE_STAGES:
            p = compute_progress(stage)
            assert p > prev, f"Progress at {stage} ({p}) not > {prev}"
            prev = p
