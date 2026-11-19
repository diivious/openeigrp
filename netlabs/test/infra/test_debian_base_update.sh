#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Exercise remote --update: refresh Debian base, do not build development images.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/data/runs/upload.mock" "$tmp/state"
cat >"$tmp/bin/docker" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$MOCK_LOG"
case "$1" in
    info) exit 0 ;;
    ps) exit 0 ;;
    pull)
        [[ "$*" == 'pull --platform linux/amd64 debian:latest' ]] || exit 2
        [[ "${FAIL_PULL:-0}" != 1 ]] || exit 15 ;;
    *) echo 'unexpected Docker operation' >&2; exit 99 ;;
esac
MOCK
cat >"$tmp/bin/mock_python" <<'MOCK'
#!/usr/bin/env bash
[[ "$1" == */netlabs/tools/remote/images.py && "$2" == update ]] || exit 99
printf 'release image update invoked\n' >>"$MOCK_LOG"
MOCK
chmod +x "$tmp/bin/"*
export PATH="$tmp/bin:$PATH" MOCK_LOG="$tmp/calls" \
       REMOTE_LAB_DATA="$tmp/data" REMOTE_LAB_STATE="$tmp/state" \
       NETLAB_PYTHON="$tmp/bin/mock_python"
runner="$root/netlabs/tools/remote/runner.sh"
if ! bash "$runner" image-update "$tmp/data/runs/upload.mock" >"$tmp/pass" 2>&1; then
    cat "$tmp/pass" >&2
    exit 1
fi
grep -Fqx 'pull --platform linux/amd64 debian:latest' "$tmp/calls"
grep -Fqx 'release image update invoked' "$tmp/calls"
! grep -q '^build ' "$tmp/calls"
mkdir -p "$tmp/data/runs/upload.mock"
if FAIL_PULL=1 bash "$runner" image-update "$tmp/data/runs/upload.mock" >"$tmp/fail" 2>&1; then
    echo 'FAIL: failed Debian image pull became a successful update' >&2
    exit 1
fi
! grep -q '^build ' "$tmp/calls"
echo 'PASS: --update pulls Debian latest without building on the appliance'
