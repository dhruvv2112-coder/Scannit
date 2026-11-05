"""Celery task that drives the reconstruction pipeline.

The worker invokes Account 1's CLI tool (scannit-recon) through subprocess
for each pipeline stage. It does NOT build COLMAP internals — it delegates
to the reconstruction binary and tracks the stage transitions.

Pipeline stages executed sequentially:
  VALIDATING → INGESTING → POSE_ESTIMATION → DENSE_MVS
  → MESH → MATERIALS → TILING → EXPORTING → QC → COMPLETE

Each stage:
  1. Validates the transition via the state machine
  2. Updates job status via the Platform API callback
  3. Invokes the stage-specific CLI command
  4. Uploads produced artifacts to S3/MinIO
  5. Registers artifacts with the Platform API
  6. Advances to the next stage

Safety:
  - No arbitrary shell parameters from clients — config is validated
  - Subprocess uses explicit arg list, never shell=True
  - Timeout per stage prevents runaway processes
  - Artifacts are content-addressed (SHA-256) and immutable
  - Idempotent retries — stages check for existing output before re-running
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from app.celery_app import celery
from app.state.machine import (
    InvalidTransition,
    compute_progress,
    validate_transition,
)
from scannit_schemas.enums import PIPELINE_STAGES, JobStatus

logger = logging.getLogger(__name__)

# CLI binary — configurable via environment
RECON_CLI = os.environ.get("SCANNIT_RECON_CLI", "scannit-recon")

# Stage-specific timeout in seconds (generous defaults for reconstruction)
STAGE_TIMEOUTS: dict[JobStatus, int] = {
    JobStatus.VALIDATING: 120,
    JobStatus.INGESTING: 600,
    JobStatus.POSE_ESTIMATION: 3600,
    JobStatus.DENSE_MVS: 7200,
    JobStatus.MESH: 3600,
    JobStatus.MATERIALS: 1800,
    JobStatus.TILING: 1800,
    JobStatus.EXPORTING: 900,
    JobStatus.QC: 600,
    JobStatus.COMPLETE: 60,
}

# Stages that produce artifacts
ARTIFACT_PRODUCING_STAGES: set[JobStatus] = {
    JobStatus.POSE_ESTIMATION,
    JobStatus.DENSE_MVS,
    JobStatus.MESH,
    JobStatus.MATERIALS,
    JobStatus.TILING,
    JobStatus.EXPORTING,
}

# Allowed config keys — reject anything not in this set
ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "dense",
        "max_frames",
        "mesh_quality",
        "texture_resolution",
        "scale_reference_path",
        "coordinate_system",
        "output_format",
        "lod_levels",
        "skip_materials",
        "skip_tiling",
    }
)

# Error categories for structured failure reporting
ERROR_CATEGORIES: dict[str, str] = {
    "INVALID_INPUT": "Input validation failed",
    "COLMAP_FAILURE": "COLMAP reconstruction failed",
    "GPU_UNAVAILABLE": "GPU not available or insufficient memory",
    "NO_GLB_OUTPUT": "Exporter produced no GLB file",
    "INVALID_HASH": "Artifact content hash mismatch after upload",
    "UPLOAD_FAILED": "Failed to upload artifact to object store",
    "TIMEOUT": "Stage exceeded maximum allowed time",
    "MISSING_REFERENCE": "Scale reference data not found",
    "SUBPROCESS_ERROR": "Reconstruction subprocess exited with error",
}


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Filter config to only allowed keys, preventing injection."""
    return {k: v for k, v in config.items() if k in ALLOWED_CONFIG_KEYS}


def _build_cli_args(
    stage: JobStatus,
    job_id: str,
    dataset_path: str,
    work_dir: str,
    config: dict[str, Any],
) -> list[str]:
    """Build the CLI argument list for a pipeline stage.

    Never uses shell=True. All arguments are explicit strings.
    """
    args = [
        RECON_CLI,
        "run-stage",
        "--stage",
        stage.value.lower(),
        "--job-id",
        job_id,
        "--dataset-path",
        dataset_path,
        "--work-dir",
        work_dir,
    ]

    # Add safe config overrides
    for key, value in config.items():
        # Only string/number/bool values — no nested structures
        if isinstance(value, (str, int, float, bool)):
            args.extend([f"--{key.replace('_', '-')}", str(value)])

    return args


def _categorize_error(stage: JobStatus, stderr: str, returncode: int) -> str:
    """Categorize a stage failure for structured error reporting."""
    stderr_lower = stderr.lower()

    if stage == JobStatus.VALIDATING:
        return "INVALID_INPUT"
    if "colmap" in stderr_lower or "sfm" in stderr_lower:
        return "COLMAP_FAILURE"
    if "cuda" in stderr_lower or "gpu" in stderr_lower or "out of memory" in stderr_lower:
        return "GPU_UNAVAILABLE"
    if stage == JobStatus.EXPORTING and ("no glb" in stderr_lower or "no output" in stderr_lower):
        return "NO_GLB_OUTPUT"
    if "reference" in stderr_lower and "not found" in stderr_lower:
        return "MISSING_REFERENCE"
    return "SUBPROCESS_ERROR"


def _check_stage_idempotent(work_dir: str, stage: JobStatus) -> bool:
    """Check if a stage has already completed (idempotent retry check).

    Looks for a sentinel file that indicates the stage ran successfully.
    """
    sentinel = Path(work_dir) / f".stage_complete_{stage.value.lower()}"
    return sentinel.exists()


def _mark_stage_complete(work_dir: str, stage: JobStatus) -> None:
    """Write a sentinel file indicating a stage completed successfully."""
    sentinel = Path(work_dir) / f".stage_complete_{stage.value.lower()}"
    sentinel.touch()


def _collect_and_upload_artifacts(
    job_id: str,
    stage: JobStatus,
    work_dir: str,
    callbacks: ApiJobCallbacks,
) -> list[dict[str, str]]:
    """Collect artifacts from a stage, upload to S3, and register with API.

    Returns list of artifact metadata dicts.
    """
    from app.workers.s3_upload import (
        build_s3_key,
        compute_file_hash,
        get_content_type,
        upload_artifact,
    )

    artifacts: list[dict[str, str]] = []
    stage_output_dir = Path(work_dir) / stage.value.lower()

    if not stage_output_dir.exists():
        return artifacts

    for artifact_file in stage_output_dir.iterdir():
        if not artifact_file.is_file():
            continue

        content_hash = compute_file_hash(artifact_file)
        byte_size = artifact_file.stat().st_size
        content_type = get_content_type(artifact_file.name)

        # Build content-addressed S3 key
        s3_key = build_s3_key(
            job_id=job_id,
            stage=stage.value.lower(),
            content_hash=content_hash,
            filename=artifact_file.name,
        )

        # Upload to S3/MinIO
        upload_ok = upload_artifact(
            artifact_file,
            s3_key,
            content_type=content_type,
        )

        if not upload_ok:
            logger.error(
                "Failed to upload artifact %s for job %s",
                artifact_file.name,
                job_id,
            )
            continue

        # Register with Platform API
        artifact_id = callbacks.register_artifact(
            job_id=job_id,
            stage=stage,
            artifact_path=str(artifact_file),
            content_hash=content_hash,
            byte_size=byte_size,
            s3_key=s3_key,
        )

        artifacts.append(
            {
                "id": artifact_id or "unknown",
                "stage": stage.value,
                "content_hash": content_hash,
                "s3_key": s3_key,
                "path": str(artifact_file),
                "byte_size": str(byte_size),
            }
        )

    return artifacts


@celery.task(
    bind=True,
    name="scannit.reconstruct",
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
)
def run_reconstruction(
    self,  # noqa: ANN001 — Celery task self
    job_id: str,
    dataset_id: str,
    project_id: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the full reconstruction pipeline for a job.

    Args:
        job_id: UUID of the reconstruction job
        dataset_id: UUID of the source dataset
        project_id: UUID of the parent project
        config: Pipeline configuration overrides (filtered to allowed keys)

    Returns:
        Dict with job_id, final status, and list of artifact IDs produced
    """
    from app.workers.api_callbacks import ApiJobCallbacks

    safe_config = _validate_config(config or {})
    callbacks = ApiJobCallbacks()
    artifacts: list[dict[str, str]] = []

    try:
        return _execute_pipeline(
            self, job_id, dataset_id, project_id, safe_config, callbacks, artifacts
        )
    finally:
        callbacks.close()


def _execute_pipeline(
    task,  # noqa: ANN001
    job_id: str,
    dataset_id: str,
    project_id: str,
    config: dict[str, Any],
    callbacks: ApiJobCallbacks,
    artifacts: list[dict[str, str]],
) -> dict[str, Any]:
    """Core pipeline execution — extracted for testability."""
    # Resolve paths
    data_root = os.environ.get("SCANNIT_DATA_ROOT", "/data")
    dataset_path = os.path.join(data_root, "datasets", dataset_id)
    work_dir = os.path.join(data_root, "jobs", job_id)
    os.makedirs(work_dir, exist_ok=True)

    # Write stage log
    stage_log_path = Path(work_dir) / "stage_log.jsonl"

    current_status = JobStatus.CREATED
    # Executable stages: skip CREATED (initial) and COMPLETE (final)
    executable_stages = PIPELINE_STAGES[1:-1]  # VALIDATING through QC

    for target_stage in executable_stages:
        # Check for cancellation
        if task.is_aborted():
            callbacks.update_job_status(
                job_id,
                JobStatus.CANCELLED,
                compute_progress(current_status),
            )
            return {"job_id": job_id, "status": "CANCELLED", "artifacts": artifacts}

        # Validate and transition
        try:
            validate_transition(current_status, target_stage)
        except InvalidTransition as exc:
            callbacks.mark_failed(job_id, str(exc))
            return {"job_id": job_id, "status": "FAILED", "error": str(exc)}

        current_status = target_stage
        progress = compute_progress(current_status)
        callbacks.update_job_status(job_id, current_status, progress)

        # Idempotent check — skip if stage already completed (retry scenario)
        if _check_stage_idempotent(work_dir, target_stage):
            logger.info(
                "Stage %s already completed for job %s (idempotent skip)",
                target_stage.value,
                job_id,
            )
            # Still collect artifacts from completed stage
            if target_stage in ARTIFACT_PRODUCING_STAGES:
                stage_artifacts = _collect_and_upload_artifacts(
                    job_id, target_stage, work_dir, callbacks
                )
                artifacts.extend(stage_artifacts)
            continue

        # Build and execute CLI command
        cli_args = _build_cli_args(
            stage=target_stage,
            job_id=job_id,
            dataset_path=dataset_path,
            work_dir=work_dir,
            config=config,
        )

        timeout = STAGE_TIMEOUTS.get(target_stage, 1800)

        try:
            result = subprocess.run(  # noqa: S603 — args are built safely above
                cli_args,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=work_dir,
            )

            # Log stage output
            _append_stage_log(
                stage_log_path,
                target_stage,
                result.returncode,
                result.stdout[-500:] if result.stdout else "",
                result.stderr[-500:] if result.stderr else "",
            )

            if result.returncode != 0:
                error_category = _categorize_error(
                    target_stage, result.stderr, result.returncode
                )
                error_msg = (
                    f"[{error_category}] Stage {target_stage.value} failed "
                    f"(exit {result.returncode}): {result.stderr[:500]}"
                )
                logger.error(error_msg)

                # Retry logic for transient failures
                if task.request.retries < task.max_retries:
                    callbacks.update_job_status(
                        job_id,
                        JobStatus.RETRYING,
                        progress,
                        error_message=error_msg,
                    )
                    raise task.retry(exc=RuntimeError(error_msg))

                callbacks.mark_failed(job_id, error_msg)
                return {
                    "job_id": job_id,
                    "status": "FAILED",
                    "error": error_msg,
                    "error_category": error_category,
                }

        except subprocess.TimeoutExpired:
            error_msg = f"[TIMEOUT] Stage {target_stage.value} timed out after {timeout}s"
            logger.error(error_msg)
            callbacks.mark_failed(job_id, error_msg)
            return {
                "job_id": job_id,
                "status": "FAILED",
                "error": error_msg,
                "error_category": "TIMEOUT",
            }

        # Mark stage as complete (for idempotent retries)
        _mark_stage_complete(work_dir, target_stage)

        # Collect and upload artifacts for producing stages
        if target_stage in ARTIFACT_PRODUCING_STAGES:
            stage_artifacts = _collect_and_upload_artifacts(
                job_id, target_stage, work_dir, callbacks
            )
            artifacts.extend(stage_artifacts)

            # Verify GLB output at EXPORTING stage
            if target_stage == JobStatus.EXPORTING:
                glb_found = any(
                    a.get("s3_key", "").endswith(".glb") for a in stage_artifacts
                )
                if not glb_found:
                    error_msg = (
                        "[NO_GLB_OUTPUT] EXPORTING stage completed but no GLB "
                        "artifact was produced"
                    )
                    logger.error(error_msg)
                    callbacks.mark_failed(job_id, error_msg)
                    return {
                        "job_id": job_id,
                        "status": "FAILED",
                        "error": error_msg,
                        "error_category": "NO_GLB_OUTPUT",
                    }

    # Transition to COMPLETE
    try:
        validate_transition(current_status, JobStatus.COMPLETE)
        callbacks.mark_complete(job_id)
    except InvalidTransition as exc:
        callbacks.mark_failed(job_id, str(exc))
        return {"job_id": job_id, "status": "FAILED", "error": str(exc)}

    return {
        "job_id": job_id,
        "status": "COMPLETE",
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
    }


def _append_stage_log(
    log_path: Path,
    stage: JobStatus,
    returncode: int,
    stdout_tail: str,
    stderr_tail: str,
) -> None:
    """Append a stage execution record to the job's stage log."""
    import json
    from datetime import UTC, datetime

    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "stage": stage.value,
        "returncode": returncode,
        "stdout_tail": stdout_tail.strip(),
        "stderr_tail": stderr_tail.strip(),
    }
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        logger.warning("Could not write stage log to %s", log_path)
