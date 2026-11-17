#!/bin/sh
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Validate the shared binary/Debian version without changing its spelling.
set -eu

fail() { printf 'build-version: %s\n' "$*" >&2; exit 1; }
[ "$#" -eq 2 ] || fail 'usage: build-version.sh --package|--binary VERSION'
case "$1" in --package|--binary) ;; *) fail 'unknown mode' ;; esac
version=$2
# Numeric release identity or numbered development build. Build numbers are
# decimal strings, not limited by the 8-bit EIGRP vendor fields.
if ! printf '%s\n' "$version" | grep -Eq \
    '^([0-9]+\.[0-9]+|[0-9]+\.[0-9]+\.[1-9][0-9]*-[A-Za-z0-9][A-Za-z0-9.-]*\.dev)$'; then
    fail 'expected MAJOR.MINOR or MAJOR.MINOR.BUILD-LABEL.dev'
fi
# The 8-bit on-wire vendor fields cannot encode values greater than 255.
major=${version%%.*}
minor=${version#*.}
minor=${minor%%.*}
[ "${#major}" -le 3 ] && [ "${#minor}" -le 3 ] &&
    [ "$major" -le 255 ] && [ "$minor" -le 255 ] ||
    fail 'EIGRP vendor major/minor must be in 0..255'
# The binary and Debian package must carry one exactly identical identity.
printf '%s\n' "$version"
