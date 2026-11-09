# Standalone Unix and UUT host libraries. No FRR/BIRD dependencies.
# Copyright (C) 2026 Donnie V. Savage
CC ?= cc
AR ?= ar
CFLAGS ?= -O2 -g
CPPFLAGS += -I../../eigrp/code -I.
CFLAGS += -std=c11 -D_POSIX_C_SOURCE=200809L -Wall -Wextra -Werror -pthread

# Host scheduling, formatting and explicit keychain registration are usable by
# both the UUT and a future native Linux daemon. Do not add UUT callbacks here.
UNIX_OBJS := eigrp_unix_sys.o eigrp_unix_format.o eigrp_unix_keychain.o

# The virtual host is deliberately confined to this archive. In particular,
# policy.c must be compiled with EIGRP_UUT_TEST for the deterministic fixture.
UUT_OBJS := eigrp_uut_interface.o eigrp_uut_rib.o eigrp_uut_segment.o \
            eigrp_uut_wire.o eigrp_uut_policy.o

.PHONY: all clean
all: libeigrp_unix.a libeigrp_uut.a eigrp_unix_config.o eigrp_unix_config_apply.o eigrp-wire

libeigrp_unix.a: $(UNIX_OBJS)
	$(AR) rcs $@ $^

libeigrp_uut.a: $(UUT_OBJS)
	$(AR) rcs $@ $^

# This parser is native-daemon-facing and not part of either shared archive.
# Continue building it to detect integration errors before the daemon lands.
eigrp_unix_config.o: eigrp_unix_config.c eigrp_unix_config.h eigrp_unix_config_private.h eigrp_unix.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

eigrp_unix_config_apply.o: eigrp_unix_config_apply.c eigrp_unix_config_private.h eigrp_unix_keychain.h ../../eigrp/code/eigrp_cli.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

eigrp_unix_sys.o: eigrp_unix_sys.c eigrp_unix.h ../../eigrp/code/eigrp_sys.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

eigrp_unix_format.o: eigrp_unix_format.c ../../eigrp/code/eigrp_sys.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

eigrp_unix_keychain.o: eigrp_unix_keychain.c eigrp_unix_keychain.h ../../eigrp/code/eigrp_sys.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

eigrp_uut_interface.o: eigrp_uut_interface.c eigrp_uut_interface.h eigrp_uut_rib.h eigrp_uut_segment.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -DEIGRP_UUT_TEST -c -o $@ $<

eigrp_uut_rib.o: eigrp_uut_rib.c eigrp_uut_rib.h ../../eigrp/code/eigrp_rib.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -DEIGRP_UUT_TEST -c -o $@ $<

eigrp_uut_segment.o: eigrp_uut_segment.c eigrp_uut_segment.h eigrp_uut_interface.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -DEIGRP_UUT_TEST -c -o $@ $<

eigrp_uut_wire.o: eigrp_uut_wire.c eigrp_uut_wire.h eigrp_unix.h eigrp_uut_interface.h eigrp_uut_segment.h ../../eigrp/code/eigrp_sys.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -DEIGRP_UUT_TEST -c -o $@ $<

eigrp_uut_policy.o: eigrp_uut_policy.c ../../eigrp/code/eigrp_sys.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -DEIGRP_UUT_TEST -c -o $@ $<

eigrp-wire: eigrp_uut_wire_broker.c eigrp_uut_wire.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -o $@ eigrp_uut_wire_broker.c

clean:
	rm -f $(UNIX_OBJS) $(UUT_OBJS) eigrp_unix_config.o eigrp_unix_config_apply.o \
	      libeigrp_unix.a libeigrp_uut.a libeigrp-unix-runtime.a \
	      eigrp_unix_rib.o eigrp_unix_interface.o eigrp_unix_segment.o \
	      eigrp_unix_wire.o eigrp_unix_policy.o eigrp_log.o eigrp-wire
