# Reconstruction (account1) targets. Shared CI lives in account2's .github/workflows/ci.yml.
PY ?= python3
RECON_PATH := packages/reconstruction-core/src:pipelines
RECON_WORK ?= work/recon
RECON_FIXTURE := $(RECON_WORK)/fixture
RECON_SMOKE := $(RECON_WORK)/smoke
# Same rules as account2's [tool.ruff] (pyproject.toml, PR #3), scoped to account1 paths.
RECON_LINT_PATHS := pipelines packages/reconstruction-core tests/reconstruction
RUFF_ARGS := --line-length 120 --target-version py311

.PHONY: help recon-fixture recon-test recon-smoke recon-report recon-lint recon-ci
help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1

recon-lint:
	ruff check $(RUFF_ARGS) --select E,F,I,W,UP,B,SIM $(RECON_LINT_PATHS)
	ruff format --check $(RUFF_ARGS) $(RECON_LINT_PATHS)

recon-fixture:
	rm -rf $(RECON_FIXTURE)
	$(PY) tests/reconstruction/make_fixture.py $(RECON_FIXTURE)

recon-test:
	$(PY) -m pytest -q tests/reconstruction

recon-smoke: recon-fixture
	PYTHONPATH=$(RECON_PATH) $(PY) -m ingest $(RECON_FIXTURE) --out $(RECON_SMOKE) --interval 0.25
	@test -s $(RECON_SMOKE)/manifest.json && echo "recon-smoke: OK -> $(RECON_SMOKE)/manifest.json"

recon-report:
	@test -s $(RECON_SMOKE)/manifest.json || (echo "run 'make recon-smoke' first" && exit 1)
	PYTHONPATH=$(RECON_PATH) $(PY) -m recon_core.report $(RECON_SMOKE)/manifest.json --job-id smoke --out $(RECON_SMOKE)/quality_report.json

recon-ci: recon-lint recon-test recon-smoke recon-report
