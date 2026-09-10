PYTHON ?= python3

.PHONY: install-dev lint typecheck test check

install-dev:
	$(PYTHON) -m pip install -e .[dev]

lint:
	ruff check .

typecheck:
	mypy

test:
	pytest

check: lint typecheck test
