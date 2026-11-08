# Native Linux interface discovery; no UUT, FRR or BIRD dependencies.
# Copyright (C) 2026 Donnie V. Savage
CC ?= cc
AR ?= ar
CPPFLAGS += -I../../eigrp/code -I.
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -D_GNU_SOURCE -Wall -Wextra -Werror -pthread

.PHONY: all clean
all: libeigrp_linux_interface.a

eigrp_linux_interface.o: eigrp_linux_interface.c eigrp_linux_interface.h ../../eigrp/code/eigrp_sys.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

libeigrp_linux_interface.a: eigrp_linux_interface.o
	$(AR) rcs $@ $^

clean:
	rm -f eigrp_linux_interface.o libeigrp_linux_interface.a
