# Common commands for the study. Run these inside WSL2 (or any Linux shell).
# See docs/environment.md for what the Moodle targets expect, and
# docs/machine-profile.md for the host they were written against.

PY := .venv/bin/python
PORT ?= 8090
DURATION ?= 30

# Moodle-facing targets source .env in the recipe shell rather than with
# make's include. Some values are quoted JSON, and make would keep the
# quotes as part of the value while a shell strips them correctly.
ENV := set -a; . ./.env; set +a;

.PHONY: help venv smoke validate mock analyse test-analyse env \
        sync-plugin serve serve-stop serve-status bench-setup bench-status \
        bench-teardown ceiling clean

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

# ---------------------------------------------------------------------------
# Moodle environment (phase 4). All of these read .env.
# ---------------------------------------------------------------------------

env: ## create .env from the example
	@test -f .env && echo ".env already exists, not overwriting" || \
		(cp .env.example .env && echo "created .env from .env.example")

sync-plugin: ## copy plugin/edgellm into the Moodle checkout
	$(ENV) ./scripts/sync-plugin.sh sync
	$(ENV) cd "$$MOODLE_ROOT" && php admin/cli/upgrade.php --non-interactive

serve: ## start Moodle's dev server with the benchmark worker count
	./scripts/serve-moodle.sh start

serve-stop: ## stop it
	./scripts/serve-moodle.sh stop

serve-status: ## is it running, and with how many workers
	./scripts/serve-moodle.sh status

bench-setup: ## point Moodle at the backend and open the bench endpoint
	$(ENV) cd "$$MOODLE_ROOT" && php "$(CURDIR)/scripts/bench_config.php" --setup

bench-status: ## show what is currently configured
	$(ENV) cd "$$MOODLE_ROOT" && php "$(CURDIR)/scripts/bench_config.php" --status

bench-teardown: ## restore everything bench-setup changed
	$(ENV) cd "$$MOODLE_ROOT" && php "$(CURDIR)/scripts/bench_config.php" --teardown

ceiling: ## measure the web server concurrency ceiling with real Moodle in the path
	$(PY) scripts/measure_ceiling.py
