SHELL := /bin/bash

# A pre-push hook runs with GIT_DIR naming this repository; nothing here runs
# git on the host, and this keeps it that way should something start to.
unexport GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR \
         GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX

.PHONY: help ci-local ci-local-deep ci-remote test lint check image e2e e2e-recorded \
        preview suite-same fmt install-hooks

PLUGIN      := kraken
PAIR        := ../plugin-coinbase
DOCKER      := DOCKER_BUILDKIT=1 docker
PY_VERSION  := 3.12
CHECK       := meridian-$(PLUGIN)-check
IMAGE       ?= $(PLUGIN):local
# The SDK pyproject.toml pins, and its base image. Where the sibling
# meridian-python checkout carries exactly that version -- one not yet
# published, being tried -- it is built from there (SDK_REPO): the tests' and
# the check's containers install it from source, and the plugin's image is
# built on a base made from it. Otherwise both come from PyPI and ghcr.io.
SDK_VERSION := $(shell sed -n 's/.*"open-meridian==\([0-9][0-9.]*\)".*/\1/p' pyproject.toml)
SDK_REPO    ?= $(if $(shell grep -s '^version = "$(SDK_VERSION)"$$' ../meridian-python/pyproject.toml),../meridian-python,)
SDK_CONTEXT := $(if $(SDK_REPO),--build-context sdk=$(SDK_REPO),)
BASE        ?= $(if $(SDK_REPO),plugin-python:$(SDK_VERSION)-local,ghcr.io/open-meridian/plugin-python:$(SDK_VERSION))
PLUGIN_CHECK := meridian-$(PLUGIN)-plugin-check
# The meridian check.yaml holds the plugin to, read from there so the two
# cannot drift.
MERIDIAN_VERSION := $(shell sed -n 's/^ *MERIDIAN_VERSION: *\([0-9][0-9.]*\).*/\1/p' .github/workflows/check.yaml)
# A meridian built locally (a directory holding its binary), for a release
# not yet published: `make check MERIDIAN_BIN=../cli-build`. Empty, the
# release check.yaml pins is installed.
MERIDIAN_BIN ?=
CLI_CONTEXT := $(if $(MERIDIAN_BIN),--build-context cli=$(MERIDIAN_BIN),)

# The released runtime `make e2e` proves the plugin against, by tag and digest,
# and core's plugin harness published with it, by the same commit's tag and
# its digest: core main 6202b4a, contract v21. Moved together by a deliberate commit, and with the SDK when a
# contract version changes.
# `make e2e RUNTIME_IMAGE=...:latest HARNESS_IMAGE=...:latest` tries a newer
# core; e2e-latest.yaml does that weekly.
RUNTIME_IMAGE ?= ghcr.io/open-meridian/meridian-runtime:6202b4a@sha256:854d866396de5f2f0c39b0ad3b1bd8b283f1549bb86d9684db71283988821012
HARNESS_IMAGE ?= ghcr.io/open-meridian/meridian-harness:6202b4a@sha256:f8346eadc65a5194331af2ee277dddbbdc6bcbeeca7ac196cb7ac7990bed8090
# Its roles as pyproject.toml declares them (a JSON list's items), so the
# harness launches it as `meridian plugin upload` would; and the e2e's reader
# (e2e/reader.py) beside it, holding `custody` beside `reporting` to resolve
# a custodian's records, and `signal` to read and hear trades and quotes
# (contract v19), run from the same image.
ROLES := $(shell sed -n 's/^roles *= *\[\(.*\)\]/\1/p' pyproject.toml | tr -d ' ')
PLUGINS := [{"instance": "$(PLUGIN)", "image": "$(IMAGE)", "roles": [$(ROLES)]}, \
            {"instance": "reader", "image": "$(IMAGE)", "roles": ["custody", "reporting", "signal"]}]
# What the compose files read: the runtime, and this repository's e2e/.
export MERIDIAN_RUNTIME_IMAGE := $(RUNTIME_IMAGE)
export E2E_DIR := $(CURDIR)/e2e
E2E := docker compose -p $(PLUGIN)-e2e -f .e2e/harness/compose.yaml \
       -f .e2e/harness/plugins.yaml -f e2e/plugin.yaml
# The files every pair keeps byte for byte: the suite, the data plugin's work
# and its tests, and the data role's pages.
SAME := src/$(PLUGIN)/market.py src/$(PLUGIN)/dgm.py src/$(PLUGIN)/products.py src/$(PLUGIN)/raw.py \
        src/$(PLUGIN)/stream.py src/$(PLUGIN)/page.py src/$(PLUGIN)/templates/connection.html \
        src/$(PLUGIN)/templates/datasets.html tests/test_suite.py tests/test_dgm.py \
        tests/test_page.py tests/test_raw.py tests/test_stream.py tests/conftest.py

help:
	@echo "  make ci-local       every gate: lint, tests, plugin check, the image, e2e, and the pair held the same"
	@echo "  make ci-remote      what ci.yaml runs: lint and tests"
	@echo "  make test           the tests, in a container: the SDK's data suite among them"
	@echo "  make lint           ruff and mypy, strict"
	@echo "  make check          what check.yaml runs: meridian plugin check --verified --run-tests"
	@echo "  make image          build the plugin's image, as upload would, and check it"
	@echo "  make e2e            the plugin, synthetic, on the plugin harness of the runtime it pins"
	@echo "  make e2e-recorded   the same, replaying RECORDED=<dir>: a developer's recorded exchanges"
	@echo "  make preview        write preview/: each page on synthetic data, linking the kit"
	@echo "  make suite-same     the files the pair shares, the same in $(PAIR)"
	@echo "  make fmt            apply the formatter"
	@echo "  make install-hooks  point git at hooks/ so push fires ci-local"

# Local green is the completion signal; CI is confirmation. Every CI job is a
# target reachable from here.
ci-local: ci-remote check image e2e suite-same
	@echo
	@echo "ci-local: GREEN"

ci-local-deep: ci-local

ci-remote: lint test
	@echo
	@echo "ci-remote: GREEN"

lint:
	@$(DOCKER) build $(SDK_CONTEXT) -f Dockerfile.check --target lint . >/dev/null 2>&1 \
		|| { echo "lint FAILED; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build $(SDK_CONTEXT) -f Dockerfile.check --target lint --progress=plain ." >&2; exit 1; }
	@echo "lint OK: ruff and mypy (strict) clean"

test:
	@$(DOCKER) build $(SDK_CONTEXT) -f Dockerfile.check --target test -t $(CHECK) . >/dev/null 2>&1 \
		|| { echo "test FAILED to build; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build $(SDK_CONTEXT) -f Dockerfile.check --target test --progress=plain ." >&2; exit 1; }
	@docker run --rm $(CHECK) python -m pytest -q -rs >.test.log 2>&1 \
		|| { echo "test FAILED. The last 40 lines, and the whole of it in .test.log:" >&2; \
		     tail -40 .test.log >&2; exit 1; }
	@echo "test OK: $$(tail -1 .test.log)"

# check.yaml's job, in a container: the meridian it pins, installed as it is
# there, holds the plugin to the framework's rules and runs its tests. The
# repository is mounted read-only, and the check writes nothing to it.
check:
	@[ -n "$(MERIDIAN_VERSION)" ] \
		|| { echo "check FAILED: .github/workflows/check.yaml pins no MERIDIAN_VERSION" >&2; exit 1; }
	@$(DOCKER) build $(SDK_CONTEXT) -f Dockerfile.check --target check $(CLI_CONTEXT) --build-arg MERIDIAN_VERSION=$(MERIDIAN_VERSION) \
		-t $(PLUGIN_CHECK) . >/dev/null 2>&1 \
		|| { echo "check FAILED to build; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build $(SDK_CONTEXT) -f Dockerfile.check --target check $(CLI_CONTEXT) --build-arg MERIDIAN_VERSION=$(MERIDIAN_VERSION) --progress=plain ." >&2; exit 1; }
	@out="$$(docker run --rm -v "$(CURDIR)":/w:ro $(PLUGIN_CHECK) meridian plugin check --verified --run-tests 2>&1)" \
		|| { echo "check FAILED:" >&2; echo "$$out" >&2; exit 1; }
	@echo "check OK: meridian $(MERIDIAN_VERSION) plugin check --verified --run-tests, every rule holds"

# The image `meridian plugin upload` builds from this Dockerfile, checked for
# what a plugin's image must be: it starts as 65532, imports the SDK and
# itself, and carries nothing of this repository but the package.
image:
	@if [ -n "$(SDK_REPO)" ]; then \
		$(MAKE) --no-print-directory -C "$(SDK_REPO)" base-image BASE_IMAGE=$(BASE) >/dev/null \
			|| { echo "image FAILED: the base for open-meridian $(SDK_VERSION) was not built from $(SDK_REPO)" >&2; exit 1; }; \
	fi
	@$(DOCKER) build --build-arg BASE=$(BASE) -t $(IMAGE) . >/dev/null 2>&1 \
		|| { echo "image FAILED; see it with:" >&2; \
		     echo "  DOCKER_BUILDKIT=1 docker build --build-arg BASE=$(BASE) --progress=plain ." >&2; exit 1; }
	@[ "$$(docker inspect -f '{{.Config.User}}' $(IMAGE))" = "65532" ] \
		|| { echo "image FAILED: it does not run as 65532" >&2; exit 1; }
	@docker run --rm --entrypoint python $(IMAGE) -c \
		"import meridian, $(PLUGIN).__main__, $(PLUGIN).page; from importlib import resources; \
		 assert any(f.name.endswith('.json') for f in (resources.files('$(PLUGIN)') / 'synthetic').iterdir())" \
		|| { echo "image FAILED: it does not import the SDK and itself, with its synthetic exchanges" >&2; exit 1; }
	@[ -z "$$(docker run --rm --entrypoint sh $(IMAGE) -c 'ls -A /plugin')" ] \
		|| { echo "image FAILED: /plugin holds files; only the installed package belongs" >&2; exit 1; }
	@echo "image OK: $(IMAGE) on $(BASE), $$(docker image inspect -f '{{.Size}}' $(IMAGE) | awk '{printf "%.0f MB", $$1/1e6}')"

# The plugin, with sample data, on the plugin harness published with the
# runtime it pins, beside the e2e's reader: e2e/run.py says each step. Its own
# compose project and no published port; nothing outlives it.
e2e: image
	@$(MAKE) --no-print-directory e2e-run E2E_FILES= E2E_ARGS=

# The same run replaying a developer's recorded exchanges (the plan's Q11,
# `make e2e-recorded`): RECORDED names a directory of this plugin's raw
# records of Kraken's real answers, recorded under personal use and never
# committed, mounted where sample data replays them from. Not in ci-local:
# what is recorded is a developer's, never CI's.
# DAY names a day the recordings hold daily candles for, which the run reads.
RECORDED ?=
DAY ?=
e2e-recorded: image
	@[ -n "$(RECORDED)" ] && [ -d "$(RECORDED)" ] && [ -n "$(DAY)" ] \
		|| { echo "e2e-recorded FAILED: RECORDED=<dir> names no directory of recorded exchanges, or DAY=YYYY-MM-DD no day they hold" >&2; exit 1; }
	@RECORDED_DIR="$(abspath $(RECORDED))" $(MAKE) --no-print-directory e2e-run E2E_FILES="-f e2e/recorded.yaml" E2E_ARGS="--day=$(DAY)"

E2E_FILES ?=
E2E_ARGS ?=
.PHONY: e2e-run
e2e-run:
	@[ -n '$(ROLES)' ] || { echo "e2e FAILED: pyproject.toml declares no roles" >&2; exit 1; }
	@rm -rf .e2e && mkdir -p .e2e
	@for image in $(RUNTIME_IMAGE) $(HARNESS_IMAGE); do \
		case "$$image" in *@sha256:*) ;; *) docker pull -q "$$image" >/dev/null 2>&1 || true;; esac; \
	done
	@# The harness is taken down, and its copy removed, however the run ends:
	@# `meridian plugin check` reads every source file under this directory.
	@trap '$(E2E) $(E2E_FILES) logs --no-color >.e2e/components.log 2>&1; \
		$(E2E) $(E2E_FILES) down -v --remove-orphans >>.e2e/components.log 2>&1; rm -rf .e2e/harness' EXIT; \
	id="$$(docker create $(HARNESS_IMAGE) none 2>>.e2e/components.log)" \
		&& docker cp "$$id:/harness" .e2e/harness >/dev/null \
		&& docker rm "$$id" >/dev/null \
		|| { echo "e2e FAILED: no plugin harness at /harness in $(HARNESS_IMAGE)" >&2; trap - EXIT; exit 1; }; \
	printf '%s\n' '$(PLUGINS)' >.e2e/plugins.json; \
	docker run --rm -i -v "$(CURDIR)/.e2e/harness":/harness:ro python:3.12-alpine \
		python /harness/harness.py compose <.e2e/plugins.json >.e2e/harness/plugins.yaml 2>>.e2e/components.log \
		|| { echo "e2e FAILED: the harness did not write its plugins from .e2e/plugins.json" >&2; exit 1; }; \
	python3 e2e/run.py $(E2E_FILES) $(E2E_ARGS)

preview:
	@$(DOCKER) build $(SDK_CONTEXT) -f Dockerfile.check --target test -t $(CHECK) . >/dev/null 2>&1
	@mkdir -p preview
	@for page in connection datasets datasets-empty; do \
		docker run --rm $(CHECK) python -m $(PLUGIN).preview $$page >preview/$$page.html || exit 1; \
	done
	@echo "preview: preview/connection.html, preview/datasets.html, preview/datasets-empty.html"

# The pair passes one suite unchanged (plans/the-lake-prices-the-book, row 6):
# the files it shares are held byte for byte to the sibling's, the package's
# name aside. Where the sibling is not checked out (CI), there is nothing to
# hold them to, and it says so.
suite-same:
	@if [ ! -d "$(PAIR)" ]; then echo "suite-same: $(PAIR) is not checked out beside this one; nothing to compare"; exit 0; fi; \
	other=$$(sed -n 's/^PLUGIN *:= *//p' $(PAIR)/Makefile); differ=""; \
	for file in $(SAME); do \
		theirs=$$(echo "$$file" | sed "s#src/$(PLUGIN)/#src/$$other/#"); \
		cmp -s "$$file" "$(PAIR)/$$theirs" || differ="$$differ $$file"; \
	done; \
	[ -z "$$differ" ] || { echo "suite-same FAILED: differs from $(PAIR):$$differ" >&2; exit 1; }; \
	echo "suite-same OK: $(words $(SAME)) files the same as $(PAIR)'s"

# Applied in a container and written back, because the host has no toolchain.
fmt:
	@docker run --rm -v "$(CURDIR)":/w -w /w python:$(PY_VERSION)-slim \
		sh -c 'pip install -q ruff >/dev/null 2>&1; python -m ruff check --fix src tests e2e >/dev/null; python -m ruff format src tests'
	@echo "fmt: applied"

install-hooks:
	@git config core.hooksPath hooks
	@echo "hooks installed: git push now runs 'make ci-local' first"
