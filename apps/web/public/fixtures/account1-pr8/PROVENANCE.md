# Account 1 PR #8 output (synthetic)

Produced locally on 2026-11-04 by running Account 1's own pipeline, unmodified, from
`feat/recon/REC-003-first-glb` @ 7a55bfd:

    uv venv -p 3.11 && uv pip install -r requirements-dev.txt -r packages/reconstruction-core/requirements.txt  # pycolmap 3.11.1
    RECON_DEMO_OUT=<out> PYTHONPATH=packages/reconstruction-core/src:pipelines pytest -q tests/reconstruction/test_run_glb.py

Input: Account 1's synthetic 12-image fixture (`DATASET_MISSING`). Output: 12,497 sparse points,
`scale_status: arbitrary`, `mesh_acceptance: not_met`. **Not camera-derived, not a mesh, not metric.**
Used only to prove the viewer accepts the producer's real manifest and GLB format.
