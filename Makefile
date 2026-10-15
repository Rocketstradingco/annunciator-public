# Common tasks. Everything runs from this checkout with the system python3;
# `make dev` installs the optional lint tools into your user environment.
PYTHON ?= python3
PORT ?= 18160

.PHONY: help dev test lint format audit docs docs-check check run setup doctor preview package sync apk clean

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

dev: ## Install development tools (ruff, pytest)
	$(PYTHON) -m pip install --user -e '.[dev]'

test: ## Run the test suite (standard library unittest)
	$(PYTHON) -m unittest discover -s tests

lint: ## Lint and check formatting with ruff
	ruff check .
	ruff format --check .

format: ## Format and auto-fix with ruff
	ruff format .
	ruff check --fix .

audit: ## Check for private addresses, paths, emails and keys
	$(PYTHON) tools/audit_public.py

docs: ## Regenerate config/schema.json, examples, reference tables and web/guide.html
	$(PYTHON) tools/build_docs.py

docs-check: ## Fail if generated files are stale
	$(PYTHON) tools/build_docs.py --check

check: test lint audit docs-check ## Everything CI runs for Python
	$(PYTHON) -m annunciator config validate config/minimal.example.jsonc config/full.example.jsonc

run: ## Start the dashboard in the foreground
	$(PYTHON) -m annunciator serve --port $(PORT)

setup: ## Run the setup wizard
	$(PYTHON) -m annunciator setup

doctor: ## Check this installation without changing anything
	$(PYTHON) -m annunciator doctor

preview: ## Serve the UI with synthetic data on :18162
	$(PYTHON) tools/ui_preview.py

package: ## Build the source-only ZIP under dist/
	$(PYTHON) tools/package_public.py

sync: ## Copy web/ into the Android project
	npm run sync

apk: ## Build a debug APK (needs Node 22, JDK 21 and the Android SDK)
	npm run apk

clean: ## Remove caches and build output (never runtime/ or config/local.json)
	rm -rf dist build *.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -not -path './node_modules/*' -exec rm -rf {} +
