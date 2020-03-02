SHELL = /bin/sh

ifndef FV3GFS_BUILD_DIR
	FV3GFS_BUILD_DIR=$(shell pwd)/lib/external/FV3/
endif
include $(FV3GFS_BUILD_DIR)/conf/configure.fv3

.PHONY: build clean clean-test clean-pyc clean-build docs help docs-docker
.DEFAULT_GOAL := help

define BROWSER_PYSCRIPT
import os, webbrowser, sys

try:
	from urllib import pathname2url
except:
	from urllib.request import pathname2url

webbrowser.open("file://" + pathname2url(os.path.abspath(sys.argv[1])))
endef
export BROWSER_PYSCRIPT

define PRINT_HELP_PYSCRIPT
import re, sys

for line in sys.stdin:
	match = re.match(r'^([a-zA-Z_-]+):.*?## (.*)$$', line)
	if match:
		target, help = match.groups()
		print("%-20s %s" % (target, help))
endef
export PRINT_HELP_PYSCRIPT

BROWSER := python3 -c "$$BROWSER_PYSCRIPT"

DOCKER_IMAGE?=us.gcr.io/vcm-ml/fv3gfs-python:latest

help:
	@python3 -c "$$PRINT_HELP_PYSCRIPT" < $(MAKEFILE_LIST)

clean: clean-build clean-pyc clean-test clean-lib ## remove all build, test, coverage and Python artifacts

clean-build: ## remove build artifacts
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

clean-pyc: ## remove Python file artifacts
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

clean-test: ## remove test and coverage artifacts
	rm -fr .tox/
	rm -f .coverage
	rm -fr htmlcov/
	rm -fr .pytest_cache

clean-lib:
	$(MAKE) -C lib clean

lint: ## check style with flake8
	flake8 fv3gfs tests

test: ## run tests quickly with the default Python
	bash tests/run_tests.sh

coverage: ## check code coverage quickly with the default Python
	coverage run --source fv3gfs setup.py test
	coverage report -m
	coverage html
	$(BROWSER) htmlcov/index.html

docs: ## generate Sphinx HTML documentation, including API docs
	$(MAKE) -C docs clean
	$(MAKE) -C docs html
	$(BROWSER) docs/_build/html/index.html

docs-docker:
	docker run --rm -v $(shell pwd)/docs:/fv3gfs-python/docs -w /fv3gfs-python $(DOCKER_IMAGE) make docs
	$(BROWSER) docs/_build/html/index.html

build-docker:
	./build_docker.sh

test-docker:
	./test_docker.sh

servedocs: docs ## compile the docs watching for changes
	watchmedo shell-command -p '*.rst' -c '$(MAKE) -C docs html' -R -D .

release: dist ## package and upload a release
	twine upload dist/*

build:
	$(MAKE) -C lib
	python3 setup.py build_ext --inplace

dist: clean ## builds source and wheel package
	$(MAKE) -C lib
	python3 setup.py build_ext --inplace
	python3 setup.py sdist
	python3 setup.py bdist_wheel
	ls -l dist

install: clean ## install the package to the active Python's site-packages
	python3 setup.py install
