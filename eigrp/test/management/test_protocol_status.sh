#!/usr/bin/env bash
# Copyright (C) 2026 Donnie V. Savage
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
version="${EIGRP_BUILD_VERSION:-show-0.0.0-dev}"
make -s -C "$root/eigrp/code" EIGRP_BUILD_VERSION="$version"
make -s -C "$root/libunix/code" libeigrp_unix.a libeigrp_uut.a eigrp-wire EIGRP_BUILD_VERSION="$version"
tmp="$(mktemp -d)"
trap 'kill "$broker" 2>/dev/null || true; wait "$broker" 2>/dev/null || true; rm -rf "$tmp"' EXIT
export EIGRP_UNIX_WIRE_SOCKET="$tmp/wire.sock"
"$root/libunix/code/eigrp-wire" "$EIGRP_UNIX_WIRE_SOCKET" >"$tmp/broker.log" 2>&1 &
broker=$!
for i in $(seq 1 80); do
  test -S "$EIGRP_UNIX_WIRE_SOCKET" && break
  sleep 0.05
done
test -S "$EIGRP_UNIX_WIRE_SOCKET"
cc -std=c11 -D_POSIX_C_SOURCE=200809L -DEIGRP_DISABLE_SNMP \
  -I"$root/eigrp/code" -I"$root/libunix/code" -pthread \
  -o "$tmp/check" "$root/eigrp/test/management/test_protocol_status.c" \
  -Wl,--start-group "$root/eigrp/code/libeigrp-core.a" \
  "$root/libunix/code/libeigrp_uut.a" "$root/libunix/code/libeigrp_unix.a" \
  -Wl,--end-group
"$tmp/check"
