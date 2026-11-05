# Scannit Project Plan — Source of Truth

## Overview

This directory is the **single source of truth** for the Scannit project plan.
All feature tracking, milestones, risks, and nightly handoffs live here in version-controlled files.

## Files

| File | Purpose |
|------|---------|
| `features.csv` | Every feature ticket across all three workstreams (PLT, REC, VWR) |
| `milestones.csv` | Sprint milestones with target nights and acceptance criteria |
| `risks.csv` | Identified risks with likelihood, impact, and mitigation |
| `handoffs/account{1,2,3}/` | Nightly handoff documents per worker account |
| `nightly/YYYY-MM-DD.json` | Machine-readable nightly audit output |

## Feature ID Conventions

- `PLT-NNN` — Platform / orchestration / backend (Account 2)
- `REC-NNN` — Reconstruction pipeline (Account 1)
- `VWR-NNN` — Interactive viewer / experience (Account 3)

## Status Values

`BACKLOG` → `READY` → `IN_PROGRESS` → `IN_REVIEW` → `DONE`

Side statuses: `BLOCKED`, `DEFERRED`

**DONE** requires: merged code + passing CI + acceptance evidence (test log, screenshot, or demo URL).

## Google Sheet Sync

**Status: NOT CONNECTED**

The Google Sheet URL has not been verified. Once the user supplies or authorises
the target sheet ID, it will be recorded here and sync will be enabled using
stable feature IDs (not row indices). No destructive reconciliation — the CSV
remains authoritative.

Sheet URL: _(awaiting user authorisation)_

## Schedule

24-night sprint: 2026-11-03 to 2026-11-26 (00:00–08:00 IST nightly windows).

## Nightly Audit

Each night produces:
1. `handoffs/account2/YYYY-MM-DD.md` — human-readable summary
2. `nightly/YYYY-MM-DD.json` — machine-readable audit with fields:
   `window_ist`, `merged_prs`, `unmerged_prs`, `ci_failures`, `features_done`,
   `features_in_review`, `blocked_features`, `evidence_gaps`, `schedule_risk`,
   `next_critical_path`, `sheet_sync_status`
