# Copyright (C) 2026 Donnie V. Savage
PYTHON ?= ../../../tools/python
CC ?= cc
CFLAGS ?= -O2 -g
CPPFLAGS += -DEIGRP_UUT_TEST -DEIGRP_DISABLE_SNMP -I../../../eigrp/code -I../../../libunix/code
CFLAGS += -std=c11 -D_POSIX_C_SOURCE=200809L -Wall -Wextra -Wno-unused-parameter -Wno-unused-const-variable -pthread
LOG_TEST := ../../../tools/test-log.py

CORE_LIB := ../../../eigrp/code/libeigrp-core.a
UNIX_LIB := ../../../libunix/code/libeigrp_unix.a
UUT_LIB := ../../../libunix/code/libeigrp_uut.a

ifeq ($(shell uname -s),Darwin)
LIBS := $(CORE_LIB) $(UUT_LIB) $(UNIX_LIB) $(CORE_LIB) $(UUT_LIB) $(UNIX_LIB)
else
LIBS := -Wl,--start-group $(CORE_LIB) $(UUT_LIB) $(UNIX_LIB) -Wl,--end-group
endif

.PHONY: all clean distclean ipv4-basic-core ipv6-basic-core basic-core \
	md5-mixed-lan md5-security rtp-core dual-diamond kite-topology \
	core-topology mesh-topology active-topology summary-topology all-scenarios
all: eigrp-uut-node

$(CORE_LIB): $(wildcard ../../../eigrp/code/*.c)
	$(MAKE) -C ../../../eigrp/code

$(UNIX_LIB): ../../../libunix/code/eigrp_unix_sys.c \
             ../../../libunix/code/eigrp_unix_format.c \
             ../../../libunix/code/eigrp_unix_keychain.c
	$(MAKE) -C ../../../libunix/code libeigrp_unix.a

$(UUT_LIB): ../../../libunix/code/eigrp_uut_interface.c \
            ../../../libunix/code/eigrp_uut_rib.c \
            ../../../libunix/code/eigrp_uut_segment.c \
            ../../../libunix/code/eigrp_uut_wire.c \
            ../../../libunix/code/eigrp_uut_policy.c
	$(MAKE) -C ../../../libunix/code libeigrp_uut.a

eigrp-uut-node: node.c $(CORE_LIB) $(UNIX_LIB) $(UUT_LIB)
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ node.c $(LIBS)

clean:
	rm -f eigrp-uut-node
	rm -rf eigrp-uut-node.dSYM

distclean: clean
	rm -rf ../../../logs/uut/eigrp

ipv4-basic-core: eigrp-uut-node
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py examples/ipv4-basic-core.yaml examples/ipv4-basic-core-gate.yaml

ipv6-basic-core: eigrp-uut-node
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py examples/ipv6-basic-core.yaml examples/ipv6-basic-core-gate.yaml

md5-mixed-lan: eigrp-uut-node
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py auth-topology/ipv4-md5-mixed-lan.yaml auth-topology/md5-mixed-lan.yaml

md5-security: eigrp-uut-node
	@set -e; \
	for scenario in auth-topology/md5-*.yaml; do \
		$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py auth-topology/ipv4-md5-mixed-lan.yaml $$scenario; \
	done

basic-core: eigrp-uut-node
	@set -e; \
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py examples/ipv4-basic-core.yaml examples/ipv4-basic-core-gate.yaml; \
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py examples/ipv6-basic-core.yaml examples/ipv6-basic-core-gate.yaml

dual-diamond: eigrp-uut-node
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py dual-diamond/ipv4-diamond-fs.yaml dual-diamond/feasible-successor.yaml

kite-topology: eigrp-uut-node
	@set -e; \
	for scenario in dual-kite/[a-d]-*.yaml dual-kite/query-*.yaml; do \
		$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py dual-kite/ipv4-kite.yaml $$scenario; \
	done

core-topology: eigrp-uut-node
	@set -e; \
	for scenario in core-topology/square*.yaml; do \
		$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py core-topology/ipv4-core-square.yaml $$scenario; \
	done; \
	for scenario in core-topology/cross*.yaml; do \
		$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py core-topology/ipv4-core-cross.yaml $$scenario; \
	done

mesh-topology: eigrp-uut-node
	@set -e; \
	for scenario in mesh-topology/k6-*.yaml; do \
		$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py mesh-topology/ipv4-dense-mesh-k6.yaml $$scenario; \
	done

active-topology: eigrp-uut-node
	@set -e; \
	for scenario in active-topology/sia-*.yaml; do \
		$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py active-topology/ipv4-sia-chain.yaml $$scenario; \
	done

rtp-core: eigrp-uut-node
	@set -e; \
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py ../rtp/two-router.yaml ../rtp/srtt-rto.yaml; \
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py ../rtp/two-router.yaml ../rtp/lost-reliable-packet.yaml; \
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py ../rtp/two-router.yaml ../rtp/lost-ack.yaml; \
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py ../rtp/two-router.yaml ../rtp/peer-restart-before-eot.yaml; \
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py ../rtp/two-router.yaml ../rtp/peer-restart.yaml; \
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py ../rtp/two-router.yaml ../rtp/peer-restart-hold.yaml; \
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py ../rtp/three-router-shared.yaml ../rtp/backpressure-recovery.yaml; \
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py ../rtp/three-router-shared.yaml ../rtp/reliable-multicast-peer-retry.yaml

all-scenarios: eigrp-uut-node
	$(PYTHON) run_all.py

summary-topology: eigrp-uut-node
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py summary-topology/ipv4-summary-boundary.yaml summary-topology/query-boundary.yaml
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py summary-topology/ipv4-summary-metric.yaml summary-topology/metric-selection.yaml
	$(PYTHON) $(LOG_TEST) uut/eigrp -- $(PYTHON) run.py summary-topology/ipv4-summary-static-metric.yaml summary-topology/static-metric.yaml
