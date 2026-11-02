# Project plan

**Tracker:** [`features.csv`](features.csv) is the source of truth. No Google Sheet is linked yet; if one is created, add its URL here by PR and keep the CSV in sync.

## Columns

`ID,Feature,Owner,Priority,Dependencies,Status,Acceptance,Tests,PR,Commit,Demo,Blocker,Updated_IST`

- **ID** prefix by track: `PLAN-` shared, `REC-` reconstruction (account1). Accounts 2 and 3 add their own prefixes in their first PR.
- **Status:** `TODO`, `IN_PROGRESS`, `REVIEW`, `BLOCKED`, `DONE`. `DONE` only after CI passes and the PR is merged.
- **Dependencies:** `;`-separated IDs.
- **Updated_IST:** `YYYY-MM-DD HH:MM` Asia/Kolkata.

## Workers

| Account | Track | Owned paths |
|---|---|---|
| account1 | Metric reconstruction / photogrammetry | `services/reconstruction/`, `packages/reconstruction-core/`, `pipelines/ingest/`, `pipelines/photogrammetry/`, `pipelines/tiling/`, `tests/reconstruction/`, `infra/workers/` |
| account2 | API gateway (+ to be filled in by account2) | _account2 to fill_ |
| account3 | Viewer / format export (+ to be filled in by account3) | _account3 to fill_ |

Shared (changes need review from every affected owner): `schemas/`, `docs/project_plan/`, `.github/`, root `Makefile`.

## Handoffs

Each session ends with `docs/handoffs/<account>/YYYY-MM-DD.md`.
