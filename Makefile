# Common tasks. Run `make help` for the list.
# PROFILE selects the run profile for pipeline targets (sample | full | fixture). Make passes it
# explicitly, so `RESGAP_PROFILE` in .env only affects direct `uv run resgap ...` calls.
SHELL := /bin/bash
UV ?= uv
RUN := $(UV) run --extra dev
PROFILE ?= sample
TEST_DB ?= postgresql://resgap:resgap@localhost:5433/resgap_test
LINT_PATHS := src tests scripts dashboard

.PHONY: help setup env up down db-wait test-db init-db ingest transform dq report run status \
        offline-demo test test-unit lint format fixtures dashboard psql clean clean-lake \
        source-check

help:  ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n",$$1,$$2}'

setup:  ## create .venv (Python 3.12) and install the package with dev tools
	$(UV) sync --extra dev

env:  ## create .env from the template (never overwrites)
	@test -f .env || (cp .env.example .env && echo "created .env")

up: env  ## start PostgreSQL in Docker and wait until it is ready
	docker compose up -d db
	@$(MAKE) --no-print-directory db-wait

down:  ## stop containers (the database volume is kept)
	docker compose down

db-wait:
	@for i in $$(seq 1 30); do docker compose exec -T db pg_isready -q && exit 0; sleep 2; done; \
	 echo "PostgreSQL did not become ready"; exit 1

test-db:  ## create the separate resgap_test database in the Docker container (once)
	@if docker compose exec -T db pg_isready -q >/dev/null 2>&1; then \
	  docker compose exec -T db psql -U resgap -d resgap -tAc \
	    "select 1 from pg_database where datname='resgap_test'" | grep -q 1 || \
	  docker compose exec -T db createdb -U resgap resgap_test; \
	else echo "Docker db not running: make sure TEST_DB ($(TEST_DB)) exists"; fi

init-db:  ## create schemas, ops tables and SQL helper functions
	$(RUN) resgap --profile $(PROFILE) init-db

ingest:  ## download sources -> object store -> raw tables (idempotent)
	$(RUN) resgap --profile $(PROFILE) ingest

transform:  ## build staging, curated and serving tables in SQL
	$(RUN) resgap --profile $(PROFILE) transform

dq:  ## run data-quality checks on the last transform (exit 2 on a critical failure)
	$(RUN) resgap dq

report:  ## write reports/resilience_gap_report.html from the last transform
	$(RUN) resgap report

run:  ## whole pipeline on live data: ingest, dq, transform, dq, report
	$(RUN) resgap --profile $(PROFILE) run

source-check:  ## probe every source: availability, redirects, schema, freshness (no DB needed)
	$(RUN) resgap source-check --json reports/source_check.json

status:  ## recent pipeline runs and table sizes
	$(RUN) resgap status

offline-demo:  ## whole pipeline on the committed real-data fixtures (no network)
	$(RUN) resgap --profile fixture run --offline-dir tests/fixtures

test-unit:  ## unit tests (no database needed)
	$(RUN) pytest tests/unit -q

test: test-db  ## all tests; integration tests DROP the pipeline schemas in TEST_DB only
	RESGAP_TEST_DATABASE_URL=$(TEST_DB) $(RUN) pytest -q

lint:  ## ruff lint
	$(RUN) ruff check $(LINT_PATHS)

format:  ## ruff format
	$(RUN) ruff format $(LINT_PATHS)

fixtures:  ## rebuild tests/fixtures from live sources (real excerpts; network)
	$(RUN) python scripts/build_fixtures.py

dashboard:  ## interactive dashboard on http://localhost:8501 (installs the dashboard extra)
	$(UV) run --extra dashboard streamlit run dashboard/app.py

psql:  ## open psql inside the database container
	docker compose exec db psql -U resgap -d resgap

clean:  ## remove caches and the generated report (keeps data and database)
	rm -f reports/resilience_gap_report.html
	rm -rf .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -not -path './.venv/*' -prune -exec rm -rf {} +

clean-lake:  ## DELETE downloaded raw files in data/lake (re-downloaded on the next run)
	rm -rf data/lake
