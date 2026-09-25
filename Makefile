PYTHON ?= python3
ARCH ?= amd64
ISO_ARGS ?=
SHELL_SCRIPTS := $(wildcard *.sh scripts/*.sh image/*.sh)

.PHONY: help test lint lint-python lint-shell check iso clean

help:
	@echo "make test        - run the web panel test suite"
	@echo "make lint        - pyflakes + shellcheck"
	@echo "make check       - lint + test"
	@echo "make iso         - build the installer ISO (ARCH=amd64|arm64, ISO_ARGS='--unattended ...')"
	@echo "make clean       - remove build output"

test:
	$(PYTHON) -m pytest -q

lint-python:
	$(PYTHON) -m pyflakes web tests

lint-shell:
	@for f in $(SHELL_SCRIPTS); do bash -n "$$f" || exit 1; done
	shellcheck -S error $(SHELL_SCRIPTS)

lint: lint-python lint-shell

check: lint test

iso:
	./image/build-iso.sh --arch $(ARCH) $(ISO_ARGS)

clean:
	rm -rf dist .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
