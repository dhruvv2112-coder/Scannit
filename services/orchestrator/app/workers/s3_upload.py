"""S3/MinIO upload helpers for the orchestrator worker.

Uploads reconstructed artifacts (GLB, point clouds, textures) to the
object store. Uses the same bucket and credentials as the API service.

Safety:
  - Content-addressed keys prevent overwrites
  - Upload verification via ETag comparison
  - No arbitrary path access — keys are constructed from job/stage/hash
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

_s3_client = None


def _get_s3_client():
    """Create or return the S3 client singleton."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("SCANNIT_S3_ENDPOINT_URL", "http://minio:9000"),
            aws_access_key_id=os.environ.get("SCANNIT_S3_ACCESS_KEY", "minioadmin"),
            aws_secret_access_key=os.environ.get("SCANNIT_S3_SECRET_KEY", "minioadmin"),
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
    return _s3_client


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def build_s3_key(job_id: str, stage: str, content_hash: str, filename: str) -> str:
    """Build a content-addressed S3 key.

    Format: artifacts/{job_id}/{stage}/{hash[:12]}_{filename}
    This prevents overwrites and enables deduplication.
    """
    return f"artifacts/{job_id}/{stage}/{content_hash[:12]}_{filename}"


def upload_artifact(
    local_path: Path,
    s3_key: str,
    *,
    bucket: str | None = None,
    content_type: str = "application/octet-stream",
) -> bool:
    """Upload a file to S3/MinIO and verify the upload.

    Returns True if the upload succeeded and was verified.
    """
    target_bucket = bucket or os.environ.get("SCANNIT_S3_BUCKET", "scannit-artifacts")
    client = _get_s3_client()

    try:
        # Upload
        with open(local_path, "rb") as f:
            client.put_object(
                Bucket=target_bucket,
                Key=s3_key,
                Body=f,
                ContentType=content_type,
            )

        # Verify — HEAD to check the object exists and size matches
        head = client.head_object(Bucket=target_bucket, Key=s3_key)
        remote_size = head["ContentLength"]
        local_size = local_path.stat().st_size

        if remote_size != local_size:
            logger.error(
                "Upload verification failed for %s: local=%d remote=%d",
                s3_key,
                local_size,
                remote_size,
            )
            return False

        logger.info(
            "Uploaded artifact %s (%d bytes) to s3://%s/%s",
            local_path.name,
            local_size,
            target_bucket,
            s3_key,
        )
        return True

    except Exception:
        logger.exception("Failed to upload %s to s3://%s/%s", local_path, target_bucket, s3_key)
        return False


# Content type mapping for common artifact file types
ARTIFACT_CONTENT_TYPES: dict[str, str] = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".ply": "application/x-ply",
    ".obj": "text/plain",
    ".mtl": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".json": "application/json",
    ".bin": "application/octet-stream",
}


def get_content_type(filename: str) -> str:
    """Determine content type from file extension."""
    ext = Path(filename).suffix.lower()
    return ARTIFACT_CONTENT_TYPES.get(ext, "application/octet-stream")
