"""Shared enumerations for the Scannit platform."""

from enum import StrEnum


class JobStatus(StrEnum):
    """Async job state machine.

    Valid transitions:
      CREATED → VALIDATING → INGESTING → POSE_ESTIMATION → DENSE_MVS
      → MESH → MATERIALS → TILING → EXPORTING → QC → COMPLETE

    Any state → FAILED, CANCELLED
    FAILED → RETRYING → (resume from failed state)
    Any state → NEEDS_REFERENCE (awaiting external input)
    """

    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    INGESTING = "INGESTING"
    POSE_ESTIMATION = "POSE_ESTIMATION"
    DENSE_MVS = "DENSE_MVS"
    MESH = "MESH"
    MATERIALS = "MATERIALS"
    TILING = "TILING"
    EXPORTING = "EXPORTING"
    QC = "QC"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RETRYING = "RETRYING"
    NEEDS_REFERENCE = "NEEDS_REFERENCE"


# Ordered pipeline stages for forward-only progression
PIPELINE_STAGES: list[JobStatus] = [
    JobStatus.CREATED,
    JobStatus.VALIDATING,
    JobStatus.INGESTING,
    JobStatus.POSE_ESTIMATION,
    JobStatus.DENSE_MVS,
    JobStatus.MESH,
    JobStatus.MATERIALS,
    JobStatus.TILING,
    JobStatus.EXPORTING,
    JobStatus.QC,
    JobStatus.COMPLETE,
]


class CoordinateSystem(StrEnum):
    ENU = "ENU"  # East-North-Up (local tangent plane)
    ECEF = "ECEF"  # Earth-Centered Earth-Fixed
    LOCAL = "LOCAL"  # Arbitrary local coordinate system
    WGS84 = "WGS84"  # Geographic


class PrivacyClassification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class Units(StrEnum):
    METRES = "METRES"
    MILLIMETRES = "MILLIMETRES"
    FEET = "FEET"
    UNITLESS = "UNITLESS"
