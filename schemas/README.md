# Interchange schemas

JSON Schema 2020-12, versioned by directory (`v0/` = pre-freeze, may change by PR; frozen at Night 22 as `v1/`). Every instance carries `schema_version`.

| Schema | Producer | Consumers |
|---|---|---|
| `reconstruction_job` | gateway (account2) creates, reconstruction (account1) updates stages | gateway, UI |
| `camera_model`, `transform` | account1 | account3 viewer |
| `ingest_manifest` | account1 ingest (REC-001) | account1 later stages |
| `mesh_artifact`, `texture_artifact`, `spatial_tile` | account1 | account3 export/viewer |
| `quality_report` | account1 | gateway, UI |

Conventions: meters, right-handed Z-up scene frame, OpenCV camera frame, `p_to = s·R·p_from + t`, explicit `scale_status`.

The `$id` host `schemas.scannit.invalid` is a deliberately unresolvable placeholder; schemas are resolved locally by `$id`.

Validated by `tests/schemas` (account2, PR #3).
