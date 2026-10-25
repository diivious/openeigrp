# Copyright (C) 2026 Donnie V. Savage
# OpenEIGRP development entry points. Detailed selectors live with their suites.

.DEFAULT_GOAL := build
PYTHON ?= $(CURDIR)/tools/python
export PYTHON

SCALE_VOLUME := peers internal external line kite peer-routes
SCALE_STRESS := withdraw mcast linkdown mixed-tlv churn
SCALE_AUTH := auth-md5
SCALE_CASES := $(SCALE_VOLUME) $(SCALE_STRESS) $(SCALE_AUTH)
UUT_SUITES := ipv4-basic-core ipv6-basic-core rtp-core core-topology mesh-topology \
  basic-core md5-mixed-lan md5-security dual-diamond kite-topology \
  active-topology summary-topology

.PHONY: all build check uut scale docker clean help portable-build frr-smoke

help: ## Show developer commands and selectors.
	@printf '%s\n' 'Usage: make [target] [options]' '' 'Targets:' \
	  '  build          Build portable EIGRP and Unix support (default: make)' \
	  '  all            Build, full check, UUT and quick scale in sequence' \
	  '  check          Style validation and style-checker unit tests' \
	  '  uut            Portable unit tests and YAML scenarios' \
	  '  scale          Quick pacing and backpressure qualification' \
	  '  docker         Build a local or GitHub-sourced Docker image' \
	  '  clean          Remove generated build and UUT files' \
	  '  help           Show this help' '' 'Options:' \
	  '  check SOURCE=all                Check entire tree (default: changed)' \
	  '  uut SUITE=ipv4-basic-core      Run a named UUT suite' \
	  '  uut SCENARIO=path/to/file.yaml Run one YAML scenario (topology discovered)' \
	  '  scale SUITE=100k|stress|auth|all' \
	  '  scale CASE=peers|internal|external|line|kite|peer-routes|withdraw|mcast|linkdown|mixed-tlv|churn|auth-md5' \
	  '  docker FRR=../frr              Local build with explicit FRR checkout' \
	  '  docker GITHUB=latest           Latest OpenEIGRP release' \
	  '  docker GITHUB=1.2.3           Specific OpenEIGRP release (rel-1.2.3)' '' \
	  'Before opening a pull request to merge, run: make all'

# Separate recursive invocations enforce ordering even under make -j.
all: ## Complete pre-merge gate (build, full style, UUT, quick scale).
	$(MAKE) build
	$(MAKE) check SOURCE=all
	$(MAKE) uut
	$(MAKE) scale

portable-build:
	$(MAKE) -C eigrp/code

build: portable-build ## Build portable EIGRP and Unix support.
	$(MAKE) -C unix/code

check: ## Run style validation and its executable unit tests (SOURCE=changed|all).
	@case '$(SOURCE)' in ''|changed|all) ;; *) echo 'error: check SOURCE must be changed or all' >&2; exit 2 ;; esac
	$(PYTHON) tools/style-check.py $(if $(filter all,$(SOURCE)),--all,$(if $(STYLE_CHECK_BASE),--base $(STYLE_CHECK_BASE)))
	$(PYTHON) tools/test/test_style_check.py
	$(PYTHON) -m unittest -q tools.test.test_make_targets \
	  tools.test.test_frr_release_selection tools.test.test_docker_source_selection

uut: build ## Run pytest + YAML scenarios; SUITE or SCENARIO selects a subset.
	@$(if $(and $(SUITE),$(SCENARIO)),echo 'error: select either SUITE or SCENARIO' >&2; exit 2;,true)
ifeq ($(strip $(SCENARIO)),)
ifeq ($(strip $(SUITE)),)
	$(PYTHON) -m pytest eigrp/test
	$(MAKE) -C eigrp/test/uut all-scenarios
else
	@$(if $(filter $(SUITE),$(UUT_SUITES)),true,echo 'error: unknown UUT SUITE=$(SUITE)' >&2; exit 2)
	$(MAKE) -C eigrp/test/uut $(SUITE)
endif
else
	$(MAKE) -C eigrp/test/uut eigrp-uut-node
	$(PYTHON) eigrp/test/uut/run_all.py --scenario '$(SCENARIO)'
endif

# Quick-scale is the existing packet pacing, lost-ACK, and 32-peer multicast
# RTP qualification. Heavy tests remain delegated to eigrp/test/scale.
scale: build ## Run quick scale, a named SUITE, or a single CASE.
	@$(if $(and $(SUITE),$(CASE)),echo 'error: select either SUITE or CASE' >&2; exit 2;,true)
ifeq ($(strip $(SUITE)$(CASE)),)
	@printf '\n%s\n' '=== Quick pacing/backpressure scale gate ==='
	$(MAKE) -C eigrp/test/uut eigrp-uut-node
	$(PYTHON) -m pytest -q eigrp/test/packet/pacing.py
	$(PYTHON) eigrp/test/uut/run.py eigrp/test/rtp/two-router.yaml eigrp/test/rtp/lost-ack.yaml
	EIGRP_SCALE_PEERS=32 EIGRP_SCALE_MCAST_SLOW_PEERS=8 EIGRP_SCALE_MCAST_ROUTES=64 \
	EIGRP_SCALE_WATCHDOG_SEC=120 $(PYTHON) eigrp/test/scale/stress_extended.py mcast
else ifneq ($(strip $(CASE)),)
	@$(if $(filter $(CASE),$(SCALE_CASES)),true,echo 'error: unknown scale CASE=$(CASE)' >&2; exit 2)
ifeq ($(CASE),peers)
	$(MAKE) -C eigrp/test/uut eigrp-uut-node
	$(PYTHON) eigrp/test/scale/neighbor_2048.py
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

# Keep Buildx, image/platform overrides, deploy and verify in tools/docker.sh.
docker: ## Local sources by default; GITHUB=latest|X.Y.Z for tagged releases.
	@$(if $(and $(GITHUB),$(FRR)),echo 'error: GITHUB and FRR cannot be combined' >&2; exit 2;,true)
ifeq ($(strip $(GITHUB)),)
	./tools/docker.sh --local '$(or $(USER),$(shell id -un))-test' $(if $(FRR),--frr-root '$(FRR)')
else
	./tools/docker.sh --build '$(GITHUB)'
endif

frr-smoke:
	$(MAKE) -C frr/test/build

clean: ## Remove generated build and UUT artifacts.
	$(MAKE) -C eigrp/code clean
	$(MAKE) -C unix/code clean
	$(MAKE) -C eigrp/test/uut clean
