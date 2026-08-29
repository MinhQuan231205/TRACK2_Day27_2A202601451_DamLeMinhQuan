PYTHON ?= python

.PHONY: reset baseline tests gx dbt dashboard generate lineage

reset:
	$(PYTHON) scripts/reset_lab.py

baseline:
	$(PYTHON) scripts/run_baseline.py

tests:
	pytest tests_public tests_student -q

gx:
	$(PYTHON) gx/validate_orders.py

dbt:
	$(PYTHON) scripts/sync_dbt_seeds.py
	dbt build --project-dir dbt_project --profiles-dir dbt_project

dashboard:
	streamlit run dashboard/app.py

lineage:
	$(PYTHON) observability/openlineage_emit.py

generate:
	$(PYTHON) scripts/generate_data.py --rows 600 --days 42 --seed 27
