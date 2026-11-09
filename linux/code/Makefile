# Native Linux interface inventory, dual-stack packets, and kernel RIB.
# Copyright (C) 2026 Donnie V. Savage
CC ?= cc
AR ?= ar
CPPFLAGS += -I../../eigrp/code -I.
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -D_GNU_SOURCE -Wall -Wextra -Werror -pthread

.PHONY: all clean
all: libeigrp_linux_interface.a libeigrp_linux_packet.a libeigrp_linux_rib.a openeigrpd-config

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

openeigrpd-config: openeigrpd-config.c ../../libunix/code/eigrp_unix_config.c ../../libunix/code/eigrp_unix_config.h
	$(CC) $(CPPFLAGS) -I../../libunix/code $(CFLAGS) -o $@ openeigrpd-config.c ../../libunix/code/eigrp_unix_config.c

clean:
	rm -f eigrp_linux_interface.o libeigrp_linux_interface.a \
		eigrp_linux_packet.o libeigrp_linux_packet.a \
		eigrp_linux_rib.o eigrp_linux_redist.o libeigrp_linux_rib.a openeigrpd-config
