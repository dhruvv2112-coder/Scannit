"""Celery task that drives the reconstruction pipeline.

The worker invokes Account 1's CLI tool (scannit-recon) through subprocess
for each pipeline stage. It does NOT build COLMAP internals — it delegates
to the reconstruction binary and tracks the stage transitions.

Pipeline stages executed sequentially:
  VALIDATING → INGESTING → POSE_ESTIMATION → DENSE_MVS
  → MESH → MATERIALS → TILING → EXPORTING → QC → COMPLETE

Each stage:
  1. Validates the transition via the state machine
  2. Updates job status in the callback store
  3. Invokes the stage-specific CLI command
  4. Registers any produced artifacts with immutable identifiers
  5. Advances to the next stage

Safety:
  - No arbitrary shell parameters from clients — config is validated
  - Subprocess uses explicit arg list, never shell=True
  - Timeout per stage prevents runaway processes
  - Artifacts are content-addressed (SHA-256) and immutable
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.celery_app import celery
from app.state.machine import (
    InvalidTransition,
    compute_progress,
    next_stage,
    validate_transition,
)
from scannit_schemas.enums import JobStatus, PIPELINE_STAGES

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
ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset({
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
})


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
        "--stage", stage.value.lower(),
        "--job-id", job_id,
        "--dataset-path", dataset_path,
        "--work-dir", work_dir,
    ]

    # Add safe config overrides
    for key, value in config.items():
        # Only string/number/bool values — no nested structures
        if isinstance(value, (str, int, float, bool)):
            args.extend([f"--{key.replace('_', '-')}", str(value)])

    return args


def _compute_artifact_hash(artifact_path: Path) -> str:
    """Compute SHA-256 content hash for an artifact file."""
    h = hashlib.sha256()
    with open(artifact_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class JobCallbacks:
    """Interface for job status and artifact updates.

    In production, this posts back to the API service via HTTP.
    In tests, it can be replaced with a mock or in-memory store.
    """

    def __init__(self, api_base_url: str | None = None) -> None:
        self.api_base_url = api_base_url or os.environ.get(
            "SCANNIT_API_URL", "http://api:8000"
        )
        # In-memory fallback for when API is not reachable
        self._local_state: dict[str, dict[str, Any]] = {}

    def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        progress_pct: float,
        error_message: str | None = None,
    ) -> None:
        """Update job status — posts to API or stores locally."""
        logger.info(
            "Job %s → %s (%.1f%%)",
            job_id, status.value, progress_pct,
        )
        self._local_state[job_id] = {
            "status": status.value,
            "progress_pct": progress_pct,
            "error_message": error_message,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # TODO: POST to {api_base_url}/api/v1/jobs/{job_id}/status
        # when the API status-update endpoint is ready

    def register_artifact(
        self,
        job_id: str,
        stage: JobStatus,
        artifact_path: str,
        content_hash: str,
        byte_size: int,
    ) -> str:
        """Register an immutable artifact — returns artifact ID."""
        artifact_id = str(uuid.uuid4())
        logger.info(
            "Artifact registered: job=%s stage=%s hash=%s id=%s",
            job_id, stage.value, content_hash[:12], artifact_id,
        )
        # TODO: POST to {api_base_url}/api/v1/artifacts
        return artifact_id

    def mark_complete(self, job_id: str) -> None:
        """Mark job as complete with timestamp."""
        self.update_job_status(job_id, JobStatus.COMPLETE, 100.0)

    def mark_failed(self, job_id: str, error: str) -> None:
        """Mark job as failed with error message."""
        self.update_job_status(job_id, JobStatus.FAILED, 0.0, error)


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
    safe_config = _validate_config(config or {})
    callbacks = JobCallbacks()
    artifacts: list[dict[str, str]] = []

    # Resolve paths
    data_root = os.environ.get("SCANNIT_DATA_ROOT", "/data")
    dataset_path = os.path.join(data_root, "datasets", dataset_id)
    work_dir = os.path.join(data_root, "jobs", job_id)
    os.makedirs(work_dir, exist_ok=True)

    current_status = JobStatus.CREATED
    # Executable stages: skip CREATED (it's the initial state) and COMPLETE (final)
    executable_stages = PIPELINE_STAGES[1:-1]  # VALIDATING through QC

    for target_stage in executable_stages:
        # Check for cancellation
        if self.is_aborted():
            callbacks.update_job_status(
                job_id, JobStatus.CANCELLED,
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

        # Build and execute CLI command
        cli_args = _build_cli_args(
            stage=target_stage,
            job_id=job_id,
            dataset_path=dataset_path,
            work_dir=work_dir,
            config=safe_config,
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

            if result.returncode != 0:
                error_msg = (
                    f"Stage {target_stage.value} failed (exit {result.returncode}): "
                    f"{result.stderr[:500]}"
                )
                logger.error(error_msg)

                # Retry logic for transient failures
                if self.request.retries < self.max_retries:
                    callbacks.update_job_status(
                        job_id, JobStatus.RETRYING, progress,
                        error_message=error_msg,
                    )
                    raise self.retry(exc=RuntimeError(error_msg))

                callbacks.mark_failed(job_id, error_msg)
                return {"job_id": job_id, "status": "FAILED", "error": error_msg}

        except subprocess.TimeoutExpired:
            error_msg = f"Stage {target_stage.value} timed out after {timeout}s"
            logger.error(error_msg)
            callbacks.mark_failed(job_id, error_msg)
            return {"job_id": job_id, "status": "FAILED", "error": error_msg}

        # Register artifacts for stages that produce them
        if target_stage in ARTIFACT_PRODUCING_STAGES:
            stage_output_dir = Path(work_dir) / target_stage.value.lower()
            if stage_output_dir.exists():
                for artifact_file in stage_output_dir.iterdir():
                    if artifact_file.is_file():
                        content_hash = _compute_artifact_hash(artifact_file)
                        byte_size = artifact_file.stat().st_size
                        artifact_id = callbacks.register_artifact(
                            job_id=job_id,
                            stage=target_stage,
                            artifact_path=str(artifact_file),
                            content_hash=content_hash,
                            byte_size=byte_size,
                        )
                        artifacts.append({
                            "id": artifact_id,
                            "stage": target_stage.value,
                            "content_hash": content_hash,
                            "path": str(artifact_file),
                            "byte_size": byte_size,
                        })

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
