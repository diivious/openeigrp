# Copyright (C) 2026 Donnie V. Savage
# Public developer entry points; implementation selectors remain with each suite.

.DEFAULT_GOAL := _default
PYTHON ?= $(CURDIR)/tools/python
export PYTHON
HOST_OS := $(shell uname -s)
TAG ?= $(or $(USER),$(shell id -un))
ARCH ?= all

SCALE_VOLUME := peers internal external line kite peer-routes
SCALE_STRESS := withdraw mcast linkdown mixed-tlv churn
SCALE_AUTH := auth-md5
SCALE_CASES := $(SCALE_VOLUME) $(SCALE_STRESS) $(SCALE_AUTH)
UUT_SUITES := ipv4-basic-core ipv6-basic-core rtp-core core-topology mesh-topology \
  basic-core md5-mixed-lan md5-security dual-diamond kite-topology \
  active-topology summary-topology

.PHONY: _default _base _portable _unit-tests _all _pre-commit all pre-commit \
  uut linux frr packages package-uut docker docker-uut clean distclean scale

# Each tier resolves one development identity and forwards it to all submakes.
# Neither caller-provided RELEASE/VERSION nor ARCH/GITHUB changes the gate.
define qualification
version="$$(./tools/version.sh --local '$(TAG)')" || exit $$?; \
  case "$$version" in *-dev) ;; *) echo 'error: local build needs a -dev version' >&2; exit 1 ;; esac; \
  printf 'OpenEIGRP qualification version: %s\n' "$$version"; \
  $(MAKE) $(1) EIGRP_BUILD_VERSION="$$version" TAG='$(TAG)' ARCH=all GITHUB= VERSION= RELEASE=0
endef

# make: host build and full-tree style validation; no Docker required.
_default:
	@$(call qualification,_base)

# make all: default build and fast tests, then build both Linux image/package
# architectures. Do not start runtime Docker or dpkg lifecycle UUTs here.
all:
	@$(call qualification,_all)

# make pre-commit: full local regression qualification, once per build.
pre-commit:
	@$(call qualification,_pre-commit)

_portable:
	$(MAKE) -C eigrp/code
	$(MAKE) -C libunix/code

_base:
ifeq ($(HOST_OS),Linux)
	$(MAKE) linux
else
	$(MAKE) _portable
endif
	$(PYTHON) tools/style-check.py --all

# Executable tooling and documentation tests belong to the quick UUT tier,
# not the compile/style-only default target.
_unit-tests:
	$(PYTHON) tools/test/test_style_check.py
	$(PYTHON) tools/test/test_style_fix.py
	$(PYTHON) -m unittest -q tools.test.test_make_targets \
	  tools.test.test_frr_release_selection tools.test.test_docker_source_selection \
	  tools.test.test_version tools.test.test_package tools.test.test_log_results \
	  tools.test.test_docker_preflight \
	  tools.test.test_tool_ownership tools.test.test_backup tools.test.test_uut_make \
	  linux.test.test_install_make linux.test.test_package_builds \
	  frr.test.setup.test_frr_setup_clone frr.test.setup.test_frr_setup_tag
	$(PYTHON) -m unittest -q linux.test.test_docs

# Keep the gate sequential even when make -j is used. Check Docker before
# running long UUT suites; missing Docker fails rather than silently skipping.
_all:
	@command -v docker >/dev/null 2>&1 || { echo 'error: make all requires Docker CLI' >&2; exit 1; }
	@docker info >/dev/null 2>&1 || { echo 'error: make all requires a running Docker Engine/Desktop' >&2; exit 1; }
	$(MAKE) _base
	$(MAKE) _unit-tests
	$(MAKE) uut
	$(MAKE) scale
ifeq ($(HOST_OS),Linux)
	$(PYTHON) tools/test-log.py linux -- $(PYTHON) -m pytest -q linux/test
else
	@printf '%s\n' 'Native Linux executable tests run inside the Debian package UUTs ($(HOST_OS))'
endif
	$(MAKE) docker ARCH=all GITHUB=
	$(MAKE) packages ARCH=all VERSION='$(EIGRP_BUILD_VERSION)' RELEASE=0

_pre-commit:
	$(MAKE) _all
	$(MAKE) scale SUITE=all
	$(MAKE) docker-uut ARCH=all
	$(MAKE) package-uut ARCH=all

# The platform check must precede ALL compilation, including portable archives.
linux:
	@test "$(HOST_OS)" = Linux || { echo 'error: make linux requires a Linux host' >&2; exit 1; }
	$(MAKE) -C linux

frr:
	EIGRP_BUILD_VERSION="$(EIGRP_BUILD_VERSION)" ./frr/tools/frr.sh --build

# VERSION overrides the resolved development version for explicit package builds.
# Without it, package metadata matches Docker's TAG release baseline.
packages:
	@case '$(ARCH)' in all|amd64|arm64) ;; *) echo 'error: ARCH must be all, amd64 or arm64' >&2; exit 2 ;; esac
	@version='$(VERSION)'; \
	if [ -z "$$version" ]; then \
	  version="$$(./tools/version.sh --local '$(TAG)')" || exit $$?; \
	fi; \
	linux/packaging/build-packages.sh --version "$$version" --arch '$(ARCH)' $(if $(filter 1,$(RELEASE)),--release)

package-uut:
	@case '$(ARCH)' in all|amd64|arm64) ;; *) echo 'error: ARCH must be all, amd64 or arm64' >&2; exit 2 ;; esac
	linux/packaging/build-packages.sh --version 0.0.0~uut --arch '$(ARCH)' --uut

uut: _portable
	@$(if $(and $(SUITE),$(SCENARIO)),echo 'error: select either SUITE or SCENARIO' >&2; exit 2;,true)
	$(PYTHON) tools/test-log.py uut/eigrp -- $(PYTHON) -m pytest -q -x libunix/test/test_archive_boundaries.py docker/test frr/test/version
ifeq ($(strip $(SCENARIO)),)
ifeq ($(strip $(SUITE)),)
	$(PYTHON) tools/test-log.py uut/eigrp -- $(PYTHON) -m pytest -x eigrp/test
	$(MAKE) -C eigrp/test/uut all-scenarios
else
	@$(if $(filter $(SUITE),$(UUT_SUITES)),true,echo 'error: unknown UUT SUITE=$(SUITE)' >&2; exit 2)
	$(MAKE) -C eigrp/test/uut $(SUITE)
endif
else
	$(MAKE) -C eigrp/test/uut eigrp-uut-node
	$(PYTHON) eigrp/test/uut/run_all.py --scenario '$(SCENARIO)'
endif

# Preserve specialized scale selectors without expanding the public entry points.
scale: _portable
	@$(if $(and $(SUITE),$(CASE)),echo 'error: select either SUITE or CASE' >&2; exit 2;,true)
ifeq ($(strip $(SUITE)$(CASE)),)
	@printf '\n%s\n' '=== Quick pacing/backpressure regression ==='
	$(MAKE) -C eigrp/test/uut eigrp-uut-node
	$(PYTHON) tools/test-log.py uut/scale -- $(PYTHON) -m pytest -q -x eigrp/test/packet/pacing.py
	$(PYTHON) tools/test-log.py uut/scale -- $(PYTHON) eigrp/test/uut/run.py eigrp/test/rtp/two-router.yaml eigrp/test/rtp/lost-ack.yaml
	EIGRP_SCALE_PEERS=32 EIGRP_SCALE_MCAST_SLOW_PEERS=8 EIGRP_SCALE_MCAST_ROUTES=64 \
	EIGRP_SCALE_WATCHDOG_SEC=120 $(PYTHON) tools/test-log.py uut/scale -- $(PYTHON) eigrp/test/scale/stress_extended.py mcast
else ifneq ($(strip $(CASE)),)
	@$(if $(filter $(CASE),$(SCALE_CASES)),true,echo 'error: unknown scale CASE=$(CASE)' >&2; exit 2)
ifeq ($(CASE),peers)
	$(MAKE) -C eigrp/test/uut eigrp-uut-node
	$(PYTHON) tools/test-log.py uut/scale -- $(PYTHON) eigrp/test/scale/neighbor_2048.py
else
	$(MAKE) -C eigrp/test/scale $(CASE)
endif
else
	@case '$(SUITE)' in 100k|stress|auth|all) ;; *) echo 'error: scale SUITE must be 100k, stress, auth or all' >&2; exit 2 ;; esac
	@case '$(SUITE)' in \
	  100k) cases='$(SCALE_VOLUME)' ;; \
	  stress) cases='$(SCALE_STRESS)' ;; \
	  auth) cases='$(SCALE_AUTH)' ;; \
	  all) cases='$(SCALE_CASES)' ;; \
	esac; \
	for item in $$cases; do \
	  printf '\n=== scale %s ===\n' "$$item"; \
	  if $(MAKE) scale SUITE= CASE="$$item"; then \
	    printf 'PASS %s\n' "$$item"; \
	  else \
	    printf 'FAIL %s\n' "$$item" >&2; exit 1; \
	  fi; \
	done
endif

# Build both architectures unless ARCH narrows the selection. Images are
# tagged separately so Docker never hides which architecture was produced.
# The unqualified local tag aliases the native host image for compatibility.
docker:
	@$(if $(and $(GITHUB),$(FRR)),echo 'error: GITHUB and FRR cannot be combined' >&2; exit 2;,true)
	@case '$(ARCH)' in all|amd64|arm64) ;; *) echo 'error: ARCH must be all, amd64 or arm64' >&2; exit 2 ;; esac
	@set -e; \
	if [ '$(ARCH)' = all ]; then archs='amd64 arm64'; else archs='$(ARCH)'; fi; \
	for arch in $$archs; do \
	  if [ -n '$(GITHUB)' ]; then \
	    ./frr/tools/docker.sh --build '$(GITHUB)' --platform "linux/$$arch" \
	      --image 'ghcr.io/diivious/openeigrp-frr:$(GITHUB)'-"$$arch"; \
	  else \
	    ./frr/tools/docker.sh --local '$(TAG)' --platform "linux/$$arch" \
	      --image 'openeigrp:$(TAG)'-"$$arch" $(if $(FRR),--frr-root '$(FRR)'); \
	  fi; \
	done; \
	if [ -z '$(GITHUB)' ]; then \
	  case "$$(uname -m)" in x86_64|amd64) native=amd64 ;; aarch64|arm64) native=arm64 ;; *) native= ;; esac; \
	  if [ -n "$$native" ]; then \
	    case " $$archs " in *" $$native "*) docker tag 'openeigrp:$(TAG)'-"$$native" 'openeigrp:$(TAG)' ;; esac; \
	  fi; \
	fi

# Exercise both built FRR images: CLI, persistent configuration, restarts,
# and real two-router protocol-88 adjacency (the --verify contract).
# Never accept a stale image from a different release baseline.
docker-uut:
	@case '$(ARCH)' in all|amd64|arm64) ;; *) echo 'error: ARCH must be all, amd64 or arm64' >&2; exit 2 ;; esac
	@set -e; \
	  expected='$(EIGRP_BUILD_VERSION)'; \
	  if [ -z "$$expected" ]; then \
	    expected="$$(./tools/version.sh --local '$(TAG)')" || exit $$?; \
	  fi; \
	  if [ '$(ARCH)' = all ]; then archs='amd64 arm64'; else archs='$(ARCH)'; fi; \
	  for arch in $$archs; do \
	    image='openeigrp:$(TAG)'-"$$arch"; \
	    actual="$$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$$image")" || exit $$?; \
	    if [ "$$actual" != "$$expected" ]; then \
	      printf 'error: %s reports %s, expected %s\n' "$$image" "$$actual" "$$expected" >&2; \
	      exit 1; \
	    fi; \
	    ./frr/tools/docker.sh --verify "$$image" || exit $$?; \
	  done

clean:
	$(MAKE) -C eigrp/code clean
	$(MAKE) -C libunix/code clean
	$(MAKE) -C linux/code clean
	$(MAKE) -C eigrp/test/uut clean
	$(MAKE) -C frr/test/build clean
	@# Remove generated Python caches, not test result evidence.
	@find eigrp libunix linux frr tools -type d \
	  \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf -- {} +

distclean: clean
	$(MAKE) -C eigrp/test/uut distclean
	$(MAKE) -C frr/test/build distclean
	$(MAKE) -C linux distclean
	$(MAKE) -C eigrp/test/scale distclean
	@# Personal Netlab evidence is owned by the separate regression environment.
	@rm -rf -- "$(CURDIR)/logs/uut" "$(CURDIR)/logs/frr" "$(CURDIR)/logs/linux"
