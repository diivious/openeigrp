# Copyright (C) 2026 Donnie V. Savage
# Native Linux integration: compilation remains in code/, not logs/.

.DEFAULT_GOAL := all
HOST_OS := $(shell uname -s)
PREFIX ?= /usr
BINDIR ?= $(PREFIX)/bin
SBINDIR ?= $(PREFIX)/sbin
SYSCONFDIR ?= /etc
SYSTEMDDIR ?= $(PREFIX)/lib/systemd/system
SYSUSERSDIR ?= $(PREFIX)/lib/sysusers.d
MANDIR ?= $(PREFIX)/share/man
DESTDIR ?=

.PHONY: all clean distclean install

all:
	@test "$(HOST_OS)" = Linux || { echo 'error: native Linux compilation requires Linux' >&2; exit 1; }
	$(MAKE) -C ../eigrp/code
	$(MAKE) -C ../libunix/code
	$(MAKE) -C code

install: all
	install -d -m 0755 "$(DESTDIR)$(SBINDIR)" "$(DESTDIR)$(BINDIR)" \
	  "$(DESTDIR)$(SYSCONFDIR)" "$(DESTDIR)$(SYSTEMDDIR)" \
	  "$(DESTDIR)$(SYSUSERSDIR)" "$(DESTDIR)$(MANDIR)/man5" "$(DESTDIR)$(MANDIR)/man8"
	install -m 0755 code/openeigrpd "$(DESTDIR)$(SBINDIR)/openeigrpd"
	install -m 0755 code/oectl "$(DESTDIR)$(BINDIR)/oectl"
	install -m 0755 code/openeigrpd-config "$(DESTDIR)$(BINDIR)/openeigrpd-config"
	@if [ ! -e "$(DESTDIR)$(SYSCONFDIR)/openeigrpd.conf" ] && \
	   [ ! -L "$(DESTDIR)$(SYSCONFDIR)/openeigrpd.conf" ]; then \
	  install -m 0600 packaging/openeigrpd.conf "$(DESTDIR)$(SYSCONFDIR)/openeigrpd.conf"; \
	else \
	  printf '%s\n' 'Preserving existing openeigrpd.conf'; \
	fi
	install -m 0644 packaging/openeigrpd.service "$(DESTDIR)$(SYSTEMDDIR)/openeigrpd.service"
	install -m 0644 packaging/openeigrpd.sysusers "$(DESTDIR)$(SYSUSERSDIR)/openeigrpd.conf"
	install -m 0644 man/oectl.8 "$(DESTDIR)$(MANDIR)/man8/oectl.8"
	install -m 0644 man/openeigrpd.conf.5 "$(DESTDIR)$(MANDIR)/man5/openeigrpd.conf.5"
	install -m 0644 packaging/openeigrpd.8 "$(DESTDIR)$(MANDIR)/man8/openeigrpd.8"
	@# No daemon enable/start or systemd invocation in an install recipe.

clean:
	$(MAKE) -C code clean

distclean: clean
	@rm -rf -- ../logs/linux
