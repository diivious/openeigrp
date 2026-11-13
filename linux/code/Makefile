# Native Linux daemon, interface inventory, dual-stack packets, and RIB.
# Copyright (C) 2026 Donnie V. Savage
CC ?= cc
AR ?= ar
CPPFLAGS += -I../../eigrp/code -I../../libunix/code -I. -include ../../eigrp/code/eigrp_build_version.h
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -D_GNU_SOURCE -Wall -Wextra -Werror -pthread

.PHONY: all clean
all: libeigrp_linux_interface.a libeigrp_linux_packet.a libeigrp_linux_rib.a libeigrp_linux_policy.a libeigrp_linux_mgnt.a openeigrpd-config oectl openeigrpd

eigrp_linux_interface.o: eigrp_linux_interface.c eigrp_linux_interface.h ../../eigrp/code/eigrp_sys.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

libeigrp_linux_interface.a: eigrp_linux_interface.o
	$(AR) rcs $@ $^

eigrp_linux_packet.o: eigrp_linux_packet.c eigrp_linux_packet.h ../../eigrp/code/eigrp_sys.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

libeigrp_linux_packet.a: eigrp_linux_packet.o
	$(AR) rcs $@ $^

eigrp_linux_rib.o: eigrp_linux_rib.c eigrp_linux_rib.h eigrp_linux_redist.h ../../eigrp/code/eigrp_rib.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

eigrp_linux_redist.o: eigrp_linux_redist.c eigrp_linux_redist.h ../../eigrp/code/eigrp_rib.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

libeigrp_linux_rib.a: eigrp_linux_rib.o eigrp_linux_redist.o
	$(AR) rcs $@ $^

eigrp_linux_policy.o: eigrp_linux_policy.c eigrp_linux_policy.h ../../eigrp/code/eigrp_sys.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

eigrp_linux_config.o: eigrp_linux_config.c eigrp_linux_config.h eigrp_linux_policy.h ../../libunix/code/eigrp_unix_config_private.h
	$(CC) $(CPPFLAGS) -I../../libunix/code $(CFLAGS) -c -o $@ $<

libeigrp_linux_policy.a: eigrp_linux_policy.o eigrp_linux_config.o
	$(AR) rcs $@ $^

eigrp_linux_version.o: eigrp_linux_version.c eigrp_linux_version.h ../../eigrp/code/eigrp_integration_version.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

eigrp_linux_mgnt.o: eigrp_linux_mgnt.c eigrp_linux_mgnt.h ../../eigrp/code/eigrp_mgnt.h ../../eigrp/code/eigrp_cli.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

libeigrp_linux_mgnt.a: eigrp_linux_mgnt.o eigrp_linux_version.o
	$(AR) rcs $@ $^

openeigrpd-config: openeigrpd-config.c ../../libunix/code/eigrp_unix_config.c ../../libunix/code/eigrp_unix_config.h
	$(CC) $(CPPFLAGS) -I../../libunix/code $(CFLAGS) -o $@ openeigrpd-config.c ../../libunix/code/eigrp_unix_config.c

oectl: oectl.c eigrp_linux_version.c eigrp_linux_version.h eigrp_linux_mgnt.h ../../eigrp/code/eigrp_integration_version.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ oectl.c eigrp_linux_version.c

# Static archives are grouped because the EIGRP/host callbacks are circular.
# This executable must never link the UUT simulation adapter or FRR/BIRD.
openeigrpd: ../../eigrp/code/eigrp_build_version.h openeigrpd.c libeigrp_linux_interface.a libeigrp_linux_packet.a libeigrp_linux_rib.a libeigrp_linux_policy.a libeigrp_linux_mgnt.a ../../libunix/code/libeigrp_unix.a ../../eigrp/code/libeigrp-core.a ../../libunix/code/eigrp_unix_config.o ../../libunix/code/eigrp_unix_config_apply.o
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ openeigrpd.c \
		../../libunix/code/eigrp_unix_config.o \
		../../libunix/code/eigrp_unix_config_apply.o \
		-Wl,--start-group libeigrp_linux_interface.a libeigrp_linux_packet.a \
		libeigrp_linux_rib.a libeigrp_linux_policy.a libeigrp_linux_mgnt.a \
		../../libunix/code/libeigrp_unix.a ../../eigrp/code/libeigrp-core.a \
		-Wl,--end-group

clean:
	rm -f eigrp_linux_interface.o libeigrp_linux_interface.a \
		eigrp_linux_packet.o libeigrp_linux_packet.a \
		eigrp_linux_rib.o eigrp_linux_redist.o libeigrp_linux_rib.a \
		eigrp_linux_policy.o eigrp_linux_config.o libeigrp_linux_policy.a \
		eigrp_linux_mgnt.o eigrp_linux_version.o libeigrp_linux_mgnt.a openeigrpd-config oectl openeigrpd

# Rebuild version-aware binary targets whenever the generated release changes.
eigrp_linux_mgnt.o eigrp_linux_version.o oectl openeigrpd: ../../eigrp/code/eigrp_build_version.h

# Direct Linux builds cannot silently reuse a header from an earlier build.
.PHONY: version-header
version-header:
	$(MAKE) -C ../../eigrp/code
	$(MAKE) -C ../../libunix/code

../../eigrp/code/eigrp_build_version.h: | version-header
	@test -f "$@"

openeigrpd-config oectl openeigrpd eigrp_linux_mgnt.o eigrp_linux_version.o: | version-header
