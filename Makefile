# Shared Makefile. Each track adds its own targets; `make ci` runs everything CI runs.
PY ?= python3

.PHONY: help schemas-test ci
help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1

schemas-test:
	$(PY) -m pytest -q tests/schemas

ci: schemas-test
