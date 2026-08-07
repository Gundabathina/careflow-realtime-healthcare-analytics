\
# CareFlow Analytics -- convenience wrappers around the project's own
# scripts/. No logic lives here; every target just calls an existing
# script or docker compose command documented in the README.

.PHONY: help test postgres-up postgres-down warehouse-load warehouse-validate \
        bronze silver gold pipeline dbt-deps dbt-build dbt-test dbt-docs \
        dashboard airflow-up airflow-down airflow-trigger

help:
	@echo "CareFlow Analytics -- available targets:"
	@echo "  make test                Run the full pytest suite"
	@echo "  make postgres-up         Start the CareFlow PostgreSQL container"
	@echo "  make postgres-down       Stop the CareFlow PostgreSQL container (non-destructive)"
	@echo "  make bronze              Run Bronze ingestion"
	@echo "  make silver              Run Silver transformation"
	@echo "  make gold                Run Gold modeling"
	@echo "  make pipeline            Run Bronze -> Silver -> Gold in sequence"
	@echo "  make warehouse-load      Load Gold into PostgreSQL"
	@echo "  make warehouse-validate  Validate the PostgreSQL warehouse against Gold"
	@echo "  make dbt-deps            Install dbt packages (.venv-dbt)"
	@echo "  make dbt-build           Run dbt build (.venv-dbt)"
	@echo "  make dbt-test            Run dbt tests only (.venv-dbt)"
	@echo "  make dbt-docs            Generate dbt docs (.venv-dbt)"
	@echo "  make dashboard           Start the Streamlit dashboard (.venv-dashboard)"
	@echo "  make airflow-up          Start Airflow (Docker Compose, airflow profile)"
	@echo "  make airflow-down        Stop Airflow services (non-destructive)"
	@echo "  make airflow-trigger     Trigger the careflow_end_to_end DAG"

test:
	PYTHONPATH=src python3 -m pytest -q tests/

postgres-up:
	PYTHONPATH=src python3 scripts/start_postgres.py

postgres-down:
	docker compose stop postgres

bronze:
	PYTHONPATH=src python3 scripts/ingest_bronze.py

silver:
	PYTHONPATH=src python3 scripts/build_silver_layer.py

gold:
	PYTHONPATH=src python3 scripts/build_gold_layer.py

pipeline: bronze silver gold

warehouse-load:
	PYTHONPATH=src python3 scripts/load_postgres_warehouse.py

warehouse-validate:
	PYTHONPATH=src python3 scripts/validate_postgres_warehouse.py

dbt-deps:
	bash -c 'set -a && source .env && set +a && PYTHONPATH=src python3 scripts/run_dbt.py deps'

dbt-build:
	bash -c 'set -a && source .env && set +a && PYTHONPATH=src python3 scripts/run_dbt.py build'

dbt-test:
	bash -c 'set -a && source .env && set +a && PYTHONPATH=src python3 scripts/run_dbt.py test'

dbt-docs:
	bash -c 'set -a && source .env && set +a && PYTHONPATH=src python3 scripts/run_dbt.py docs-generate'

dashboard:
	PYTHONPATH=src python3 scripts/start_dashboard.py

airflow-up:
	PYTHONPATH=src python3 scripts/start_airflow.py

airflow-down:
	docker compose --profile airflow stop

airflow-trigger:
	PYTHONPATH=src python3 scripts/trigger_careflow_dag.py --dag-id careflow_end_to_end
