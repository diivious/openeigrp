#!/usr/bin/env bash
# Copyright (C) 2026 Donnie V. Savage
set -euo pipefail
root="$(cd "$(dirname "$0")/../../../.." && pwd)"
version="${EIGRP_BUILD_VERSION:-show-0.0.0-dev}"
make -s -C "$root/eigrp/code" EIGRP_BUILD_VERSION="$version"
make -s -C "$root/libunix/code" libeigrp_unix.a libeigrp_uut.a EIGRP_BUILD_VERSION="$version"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cc -std=gnu11 -DEIGRP_DISABLE_SNMP \
  -I"$root/frr/test/build/include" -I"$root/frr/test/build/include/lib" \
  -I"$root/frr/code" -I"$root/eigrp/code" -I"$root/libunix/code" \
  -I"$root" -Wno-unused-function -Wno-unused-variable \
  -ffunction-sections -fdata-sections -pthread \
  -o "$tmp/check" "$root/frr/test/uut/management/test_named_render.c" \
  -Wl,--gc-sections -Wl,--start-group "$root/eigrp/code/libeigrp-core.a" \
  "$root/libunix/code/libeigrp_uut.a" "$root/libunix/code/libeigrp_unix.a" \
  -Wl,--end-group
"$tmp/check"
