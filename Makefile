# Copyright (C) 2026 Donnie V. Savage
# Public developer entry points; implementation selectors remain with each suite.

.DEFAULT_GOAL := all
PYTHON ?= $(CURDIR)/tools/python
export PYTHON
HOST_OS := $(shell uname -s)
TAG ?= $(or $(USER),$(shell id -un))-test
ARCH ?= all

SCALE_VOLUME := peers internal external line kite peer-routes
SCALE_STRESS := withdraw mcast linkdown mixed-tlv churn
SCALE_AUTH := auth-md5
SCALE_CASES := $(SCALE_VOLUME) $(SCALE_STRESS) $(SCALE_AUTH)
UUT_SUITES := ipv4-basic-core ipv6-basic-core rtp-core core-topology mesh-topology \
  basic-core md5-mixed-lan md5-security dual-diamond kite-topology \
  active-topology summary-topology

.PHONY: all pre-commit uut linux frr packages package-uut docker clean distclean portable-build check scale

# Keep the developer gate ordered, even under make -j. No Docker daemon,
# FRR checkout, or Netlab environment is required for make all.
all:
ifeq ($(HOST_OS),Linux)
	$(MAKE) linux
else
	$(MAKE) portable-build
endif
	$(MAKE) check SOURCE=all
	$(MAKE) uut
	$(MAKE) scale
	$(MAKE) scale SUITE=all
ifeq ($(HOST_OS),Linux)
	$(PYTHON) tools/test-log.py linux -- $(PYTHON) -m pytest -q linux/test
else
	@printf '%s\n' 'SKIP: host-native Linux tests ($(HOST_OS))'
endif

# Full pre-commit qualification adds both architectures of the FRR Docker
# image and Debian package, then exercises the package lifecycle in Docker.
# Explicit ARCH=all ensures this gate cannot silently test just one target.
pre-commit:
	$(MAKE) all
	$(MAKE) docker ARCH=all GITHUB=
	$(MAKE) packages ARCH=all VERSION=
	$(MAKE) package-uut ARCH=all

portable-build:
	$(MAKE) -C eigrp/code
	$(MAKE) -C libunix/code

# The platform check must precede ALL compilation, including portable archives.
linux:
	@test "$(HOST_OS)" = Linux || { echo 'error: make linux requires a Linux host' >&2; exit 1; }
	$(MAKE) -C linux

frr:
	./frr/tools/frr.sh --build

# VERSION overrides the resolved development version for explicit package builds.
# Without it, package metadata matches Docker's TAG release baseline.
packages:
	@case '$(ARCH)' in all|amd64|arm64) ;; *) echo 'error: ARCH must be all, amd64 or arm64' >&2; exit 2 ;; esac
	@version='$(VERSION)'; \
	if [ -z "$$version" ]; then \
	  version="$$(./tools/version.sh --local '$(TAG)')" || exit $$?; \
	fi; \
	linux/packaging/build-packages.sh --version "$$version" --arch '$(ARCH)'

package-uut:
	@case '$(ARCH)' in all|amd64|arm64) ;; *) echo 'error: ARCH must be all, amd64 or arm64' >&2; exit 2 ;; esac
	linux/packaging/build-packages.sh --version 0.0.0~uut --arch '$(ARCH)' --uut

# Preserve existing internal regression selector used by FRR integration tools.
check:
	@case '$(SOURCE)' in ''|changed|all) ;; *) echo 'error: check SOURCE must be changed or all' >&2; exit 2 ;; esac
	$(PYTHON) tools/style-check.py $(if $(filter all,$(SOURCE)),--all,$(if $(STYLE_CHECK_BASE),--base $(STYLE_CHECK_BASE)))
	$(PYTHON) tools/test/test_style_check.py
	$(PYTHON) tools/test/test_style_fix.py
	$(PYTHON) -m unittest -q tools.test.test_make_targets \
	  tools.test.test_frr_release_selection tools.test.test_docker_source_selection \
	  tools.test.test_version tools.test.test_package tools.test.test_log_results \
	  tools.test.test_docker_preflight \
	  tools.test.test_tool_ownership tools.test.test_backup tools.test.test_uut_make \
	  linux.test.test_install_make linux.test.test_package_builds \
	  frr.test.setup.test_frr_setup_clone frr.test.setup.test_frr_setup_tag

uut: portable-build
	@$(if $(and $(SUITE),$(SCENARIO)),echo 'error: select either SUITE or SCENARIO' >&2; exit 2;,true)
	$(PYTHON) tools/test-log.py uut/eigrp -- $(PYTHON) -m pytest -q libunix/test/test_archive_boundaries.py docker/test
ifeq ($(strip $(SCENARIO)),)
ifeq ($(strip $(SUITE)),)
	$(PYTHON) tools/test-log.py uut/eigrp -- $(PYTHON) -m pytest eigrp/test
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
scale: portable-build
	@$(if $(and $(SUITE),$(CASE)),echo 'error: select either SUITE or CASE' >&2; exit 2;,true)
ifeq ($(strip $(SUITE)$(CASE)),)
	@printf '\n%s\n' '=== Quick pacing/backpressure regression ==='
	$(MAKE) -C eigrp/test/uut eigrp-uut-node
	$(PYTHON) tools/test-log.py uut/scale -- $(PYTHON) -m pytest -q eigrp/test/packet/pacing.py
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
	@failed=0; \
	case '$(SUITE)' in \
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
	    printf 'FAIL %s\n' "$$item"; failed=1; \
	  fi; \
	done; exit $$failed
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
