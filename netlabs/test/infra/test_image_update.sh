#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Exercise the actual --update client dispatch without touching a real host.

set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/netlabs/tools/remote"
cp "$root/netlabs/tools/remote-uut.sh" "$tmp/netlabs/tools/remote-uut.sh"
cat >"$tmp/netlabs/tools/remote/common.sh" <<'MOCK'
#!/usr/bin/env bash
REMOTE_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
REMOTE_HOST=mock-appliance
remote_transport_prepare() { :; }
remote_new_stage() { printf '/home/netlab-data/runs/upload.mock\n'; }
remote_stage() { printf '%s\n' "$*" >>"$MOCK_DISPATCH"; }
remote_install_runner() { :; }
remote_ssh() { printf '%s\n' "$*" >>"$MOCK_CLEANUP"; }
remote_run() {
    [[ "$1" == /home/netlab-data/runs/upload.mock && "$2" == image-update ]] || return 75
    echo 'Latest published OpenEIGRP release: 0.4.0'
    return "${MOCK_UPDATE_RC:-0}"
}
MOCK
export MOCK_DISPATCH="$tmp/dispatch.log"
export MOCK_CLEANUP="$tmp/cleanup.log"
bash "$tmp/netlabs/tools/remote-uut.sh" --update >"$tmp/output.log"
grep -Fq 'PASS: OpenEIGRP image update' "$tmp/output.log"
grep -Fq 'netlabs/tools/remote/images.py' "$tmp/dispatch.log"
grep -Fq 'netlabs/tools/remote/runner.sh' "$tmp/dispatch.log"
if MOCK_UPDATE_RC=47 bash "$tmp/netlabs/tools/remote-uut.sh" --update >"$tmp/error.log" 2>&1; then
    echo 'FAIL: update returned success on remote failure' >&2
    exit 1
else
    rc=$?
fi
[[ "$rc" == 47 ]]
grep -Fq 'FAIL: OpenEIGRP image update (remote exit 47)' "$tmp/error.log"
grep -Fq "rm -rf -- '/home/netlab-data/runs/upload.mock'" "$tmp/cleanup.log"
echo 'PASS: --update stages the helper and preserves remote update failures'
