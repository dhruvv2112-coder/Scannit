# Shared Makefile. Each track adds its own targets; `make ci` runs everything CI runs.
PY ?= python3

.PHONY: help schemas-test ci
help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1

schemas-test:
	$(PY) -m pytest -q tests/schemas

# --- reconstruction (account1) ----------------------------------------------
RECON_PATH := packages/reconstruction-core/src:pipelines
RECON_WORK ?= work/recon
RECON_FIXTURE := $(RECON_WORK)/fixture
RECON_SMOKE := $(RECON_WORK)/smoke

.PHONY: recon-fixture recon-test recon-smoke recon-report recon-run
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

# make recon-run INPUT=path/to/images OUTPUT=work/recon/run [ARGS=--synthetic]
recon-run:
	PYTHONPATH=$(RECON_PATH) $(PY) -m photogrammetry.run --input $(INPUT) --output $(or $(OUTPUT),$(RECON_WORK)/run) $(ARGS)

ci: schemas-test recon-test recon-smoke recon-report
