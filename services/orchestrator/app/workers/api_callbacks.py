"""HTTP-based job callbacks — posts status and artifacts to the Platform API.

Replaces the local-state fallback in JobCallbacks. Uses synchronous httpx
(Celery workers are sync). Retries transient failures with exponential backoff.

Safety:
  - Timeouts prevent hanging on unresponsive API
  - Retries only on 5xx and connection errors
  - Logs every callback for audit trail
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from scannit_schemas.enums import JobStatus

logger = logging.getLogger(__name__)

# Callback retry configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds
REQUEST_TIMEOUT = 30.0  # seconds


class ApiCallbackError(Exception):
    """Raised when a callback to the Platform API fails after retries."""


class ApiJobCallbacks:
    """Production callback handler — posts job updates to the Platform API.

    Each method performs a synchronous HTTP request (Celery workers are sync)
    with retry logic for transient failures. If the API is unreachable after
    retries, the error is logged but does not crash the worker — the local
    state machine remains the source of truth for pipeline progression.
    """

    def __init__(self, api_base_url: str | None = None) -> None:
        self.api_base_url = (
            api_base_url or os.environ.get("SCANNIT_API_URL", "http://api:8000")
        ).rstrip("/")
        self._client = httpx.Client(timeout=REQUEST_TIMEOUT)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def _post_with_retry(
        self, url: str, json: dict[str, Any], method: str = "PATCH"
    ) -> dict[str, Any] | None:
        """Execute an HTTP request with retry logic for transient failures."""
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                if method == "POST":
                    resp = self._client.post(url, json=json)
                else:
                    resp = self._client.patch(url, json=json)

                if resp.status_code < 500:
                    if resp.status_code >= 400:
                        logger.warning(
                            "API callback %s returned %d: %s",
                            url,
                            resp.status_code,
                            resp.text[:200],
                        )
                    return resp.json() if resp.status_code < 400 else None
                # 5xx — retry
                logger.warning(
                    "API callback %s returned %d (attempt %d/%d)",
                    url,
                    resp.status_code,
                    attempt + 1,
                    MAX_RETRIES,
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning(
                    "API callback %s failed (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                )

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_DELAY * (2**attempt))

        logger.error(
            "API callback %s failed after %d retries: %s",
            url,
            MAX_RETRIES,
            last_exc,
        )
        return None

    def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        progress_pct: float,
        error_message: str | None = None,
        *,
        scale_status: str | None = None,
        reference_lengths: dict[str, Any] | None = None,
        transform_bounds: dict[str, Any] | None = None,
        source_evidence: dict[str, Any] | None = None,
    ) -> None:
        """Update job status via the Platform API callback endpoint."""
        url = f"{self.api_base_url}/api/v1/jobs/{job_id}/status"
        payload: dict[str, Any] = {
            "status": status.value,
            "progress_pct": progress_pct,
        }
        if error_message is not None:
            payload["error_message"] = error_message
        if scale_status is not None:
            payload["scale_status"] = scale_status
        if reference_lengths is not None:
            payload["reference_lengths"] = reference_lengths
        if transform_bounds is not None:
            payload["transform_bounds"] = transform_bounds
        if source_evidence is not None:
            payload["source_evidence"] = source_evidence

        logger.info("Job %s → %s (%.1f%%)", job_id, status.value, progress_pct)
        self._post_with_retry(url, payload, method="PATCH")

    def register_artifact(
        self,
        job_id: str,
        stage: JobStatus,
        artifact_path: str,
        content_hash: str,
        byte_size: int,
        s3_key: str,
        *,
        software_version: str = "scannit-recon/0.1.0",
        units: str = "METRES",
        coordinate_system: str = "LOCAL",
        privacy_classification: str = "INTERNAL",
    ) -> str | None:
        """Register an artifact via the Platform API.

        Returns the artifact ID if successful, None otherwise.
        """
        url = f"{self.api_base_url}/api/v1/artifacts"
        payload = {
            "job_id": job_id,
            "stage": stage.value,
            "content_hash": f"sha256:{content_hash}",
            "software_version": software_version,
            "transforms": {},
            "units": units,
            "coordinate_system": coordinate_system,
            "privacy_classification": privacy_classification,
            "fidelity_warnings": [],
            "s3_key": s3_key,
            "byte_size": byte_size,
        }

        logger.info(
            "Registering artifact: job=%s stage=%s hash=%s",
            job_id,
            stage.value,
            content_hash[:12],
        )
        result = self._post_with_retry(url, payload, method="POST")
        if result and "id" in result:
            return result["id"]
        return None

    def mark_complete(self, job_id: str) -> None:
        """Mark job as complete with timestamp."""
        self.update_job_status(job_id, JobStatus.COMPLETE, 100.0)

    def mark_failed(self, job_id: str, error: str) -> None:
        """Mark job as failed with error message."""
        self.update_job_status(job_id, JobStatus.FAILED, 0.0, error)
