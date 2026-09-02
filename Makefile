# Phase 1 targets only. Docker, Moodle and Arm B targets arrive with their
# phases; this file grows rather than being written speculatively.
#
# Run these inside WSL2 (or any Linux shell). See docs/machine-profile.md.

PY := .venv/bin/python
PORT ?= 8090
DURATION ?= 30

.PHONY: help venv smoke validate mock analyse test-analyse clean

help:
	@grep -E "^[a-zA-Z_-]+:.*?## .*$$" $(MAKEFILE_LIST) | \
		awk "BEGIN {FS = \":.*?## \"}; {printf \"  %-12s %s\n\", \$$1, \$$2}"

venv: ## create .venv and install pinned dependencies
	./scripts/bootstrap-venv.sh

mock: ## run the deterministic mock endpoint in the foreground
	$(PY) bench/mock_server.py --profile mid --port $(PORT)

smoke: ## quick end-to-end check of mock + harness
	./scripts/smoke.sh

validate: ## prove the harness against the mock at every concurrency level
	$(PY) bench/validate_harness.py --duration $(DURATION)

clean: ## remove generated smoke output and caches
	rm -rf results/raw/smoke bench/__pycache__

analyse: ## summarise raw CSVs and regenerate every chart
	$(PY) bench/analyse.py results/raw/validation --out-dir results

test-analyse: ## self-checks for the analysis code
	$(PY) bench/test_analyse.py
