.DEFAULT_GOAL := help
.PHONY: help install lint format types test cov check dist run

help:  ## List the targets
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

install:  ## Install the dev tools and the git hooks
	uv sync && uv run pre-commit install

lint:  ## Ruff lint and format check
	uv run ruff check . && uv run ruff format --check .

format:  ## Format the code and apply safe lint fixes
	uv run ruff format . && uv run ruff check --fix .

types:  ## Type-check with mypy
	uv run mypy

test:  ## Run the tests
	uv run pytest

cov:  ## Run the tests with a coverage report
	uv run pytest --cov --cov-report=term-missing

check: lint types test  ## Lint, format check, types and tests

dist:  ## Build the wheel and sdist, and check they hold no private files
	rm -rf dist && uv build && uv run python scripts/check_dist.py

run:  ## Start the bot
	python3 -m deal_scout
