.PHONY: install install-locked lock lint typecheck test coverage eval audit demo demo-static streamlit clean

PY := .venv/bin/python
PIP := .venv/bin/pip
STREAMLIT := .venv/bin/streamlit

install:
	python3.12 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

# Reproducible install: exact pinned versions from requirements.lock.
install-locked:
	python3.12 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -r requirements.lock
	$(PIP) install -e . --no-deps

# Regenerate the lockfile from the current (known-good) venv.
lock:
	$(PIP) freeze --exclude-editable > requirements.lock

lint:
	$(PY) -m ruff check src/ tests/

typecheck:
	$(PY) -m mypy src/

test:
	$(PY) -m pytest -m "not llm"

# Coverage gates currently informational only — baseline is ~25% and we ratchet
# in a separate PR. HTML report lands in htmlcov/index.html.
coverage:
	$(PY) -m pytest -m "not llm" --cov=src --cov-report=term-missing --cov-report=html

# Regression eval over case_law/regression_dataset/ (real LLM calls, subscription
# auth). `pytest -m llm` collected nothing (no llm-marked tests), so run the CLI
# evaluator that the README describes. NOT a CI target — the GitHub runner has
# no authenticated `claude` CLI; the regression-eval.yml workflow was removed.
eval:
	$(PY) -m pharma_ad_compliance.cli eval

# Audit the locked dependency set against the PyPI advisory database.
# Local-only — too noisy for default CI on a portfolio repo. Run before release.
audit:
	$(PY) -m pip install --quiet pip-audit && $(PY) -m pip_audit -r requirements.lock --strict

demo:
	$(PY) -m pharma_ad_compliance.cli check --text "Этот препарат полностью безопасен и не имеет побочных эффектов. Рекомендуется детям."

demo-static:
	$(PY) -c "import json, pathlib; p = pathlib.Path('demo_output/sample_report.json'); print(p.read_text() if p.exists() else 'Run make demo first to generate a snapshot.')"

streamlit:
	$(STREAMLIT) run src/pharma_ad_compliance/app.py

clean:
	rm -rf .venv .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
