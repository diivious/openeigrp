# Native Linux interface inventory, dual-stack packets, and kernel RIB.
# Copyright (C) 2026 Donnie V. Savage
CC ?= cc
AR ?= ar
CPPFLAGS += -I../../eigrp/code -I.
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -D_GNU_SOURCE -Wall -Wextra -Werror -pthread

.PHONY: all clean
all: libeigrp_linux_interface.a libeigrp_linux_packet.a libeigrp_linux_rib.a

eigrp_linux_interface.o: eigrp_linux_interface.c eigrp_linux_interface.h ../../eigrp/code/eigrp_sys.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

libeigrp_linux_interface.a: eigrp_linux_interface.o
	$(AR) rcs $@ $^

eigrp_linux_packet.o: eigrp_linux_packet.c eigrp_linux_packet.h ../../eigrp/code/eigrp_sys.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

libeigrp_linux_packet.a: eigrp_linux_packet.o
	$(AR) rcs $@ $^

eigrp_linux_rib.o: eigrp_linux_rib.c eigrp_linux_rib.h ../../eigrp/code/eigrp_rib.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

libeigrp_linux_rib.a: eigrp_linux_rib.o
	$(AR) rcs $@ $^

clean:
	rm -f eigrp_linux_interface.o libeigrp_linux_interface.a \
		eigrp_linux_packet.o libeigrp_linux_packet.a \
		eigrp_linux_rib.o libeigrp_linux_rib.a
