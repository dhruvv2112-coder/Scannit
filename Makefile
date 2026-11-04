# Reconstruction (account1) targets. Shared CI lives in account2's .github/workflows/ci.yml.
PY ?= python3

.PHONY: help recon-ci
help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1

# Grows as the REC stack lands; each REC PR adds its targets here.
recon-ci:
	@echo "recon-ci: no reconstruction code on this branch yet"
