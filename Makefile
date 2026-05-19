.PHONY: install lint test eval demo demo-static clean

PY := .venv/bin/python
PIP := .venv/bin/pip
STREAMLIT := .venv/bin/streamlit

install:
	python3.12 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

lint:
	$(PY) -m ruff check src/ tests/

test:
	$(PY) -m pytest -m "not llm"

eval:
	$(PY) -m pytest -m llm

demo:
	$(PY) -m pharma_ad_compliance.cli check --text "Этот препарат полностью безопасен и не имеет побочных эффектов. Рекомендуется детям."

demo-static:
	$(PY) -c "import json, pathlib; p = pathlib.Path('demo_output/sample_report.json'); print(p.read_text() if p.exists() else 'Run make demo first to generate a snapshot.')"

streamlit:
	$(STREAMLIT) run src/pharma_ad_compliance/app.py

clean:
	rm -rf .venv .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
