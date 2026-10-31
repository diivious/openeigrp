#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Exercise actual remote_stage() command construction without SSH/netlab.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
printf 'REMOTE_HOST=localhost\nREMOTE_USER=netlab\nREMOTE_SSH_PORT=22\n' >"$tmp/cfg"
export REMOTE_LAB_CONFIG="$tmp/cfg"
source "$root/netlabs/tools/remote/common.sh"

# The stub records the tar argument vector. No real file transfer is needed.
tar() {
    printf '%s\n' "$@" >"$tmp/staged"
}
remote_ssh() { cat >/dev/null; }

for family in ipv4 ipv6; do
    remote_stage /home/netlab-data/runs/upload.TEST \
        "netlabs/test/rtp/2R-transport-$family"
    grep -Fxq "netlabs/test/rtp/2R-transport-$family" "$tmp/staged"
    grep -Fxq 'netlabs/test/rtp/2R-transport/driver.py' "$tmp/staged"
done
remote_stage /home/netlab-data/runs/upload.TEST netlabs/test/dual/4R-addpath-compat
if grep -Fxq 'netlabs/test/rtp/2R-transport/driver.py' "$tmp/staged"; then
    echo 'FAIL: unrelated scenario staged transport engine' >&2
    exit 1
fi
echo 'PASS: single-AF RTP staging shares original driver only when needed'
