"""S3/MinIO client singleton and helpers for presigned URLs."""

from __future__ import annotations

import boto3
from botocore.config import Config

from app.core.config import settings

_s3_client = None


def get_s3_client():
    """Lazily create a thread-safe boto3 S3 client pointing at MinIO or AWS."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
    return _s3_client


def generate_presigned_upload_url(
    key: str,
    *,
    bucket: str | None = None,
    expires_in: int = 3600,
    content_type: str = "application/octet-stream",
) -> str:
    """Return a presigned PUT URL for uploading an object to S3/MinIO."""
    client = get_s3_client()
    return client.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": bucket or settings.s3_bucket,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=expires_in,
    )


def generate_presigned_download_url(
    key: str,
    *,
    bucket: str | None = None,
    expires_in: int = 3600,
) -> str:
    """Return a presigned GET URL for downloading an object from S3/MinIO."""
    client = get_s3_client()
    return client.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": bucket or settings.s3_bucket,
            "Key": key,
        },
        ExpiresIn=expires_in,
    )
