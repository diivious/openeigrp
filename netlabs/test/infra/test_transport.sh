#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Verify portable tar staging and controlled client --abort dispatch.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/source/netlabs" "$tmp/extracted" "$tmp/client/netlabs/tools/remote"
printf 'netlabs archive\n' >"$tmp/source/netlabs/example.txt"
cat >"$tmp/remote.cfg" <<'CONFIG'
REMOTE_HOST=127.0.0.1
REMOTE_USER=netlab
REMOTE_SSH_PORT=22
CONFIG

export REMOTE_LAB_CONFIG="$tmp/remote.cfg"
# shellcheck disable=SC1091
source "$root/netlabs/tools/remote/common.sh"
REMOTE_REPO_ROOT="$tmp/source"

# Capture the exact tar stream produced by remote_stage, without SSH.
remote_ssh() {
    cat >"$tmp/staged.tar"
}
remote_stage /home/netlab-data/runs/upload.archive netlabs/example.txt
python3 - "$tmp/staged.tar" <<'PY'
from pathlib import Path
import sys
archive = Path(sys.argv[1]).read_bytes()
assert archive[257:262] == b'ustar', 'staging requires the portable ustar format'
assert b'SCHILY.fflags' not in archive
PY
tar -xf "$tmp/staged.tar" -C "$tmp/extracted"
[[ "$(cat "$tmp/extracted/netlabs/example.txt")" == 'netlabs archive' ]]
echo 'PASS: staging uses USTAR without unsupported extended metadata'

# The client installs the current recovery handler before sending abort. A
# failing handler must cause --abort to exit nonzero and still remove its stage.
cp "$root/netlabs/tools/remote-uut.sh" "$tmp/client/netlabs/tools/remote-uut.sh"
cat >"$tmp/client/netlabs/tools/remote/common.sh" <<'MOCK'
#!/usr/bin/env bash
REMOTE_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
REMOTE_HOST=mock-appliance
remote_transport_prepare() { :; }
remote_new_stage() { printf '/home/netlab-data/runs/upload.mock\n'; }
remote_stage() { printf '%s\n' "$*" >>"$MOCK_LOG"; }
remote_install_runner() { printf 'installed\n' >>"$MOCK_LOG"; }
remote_ssh() {
    printf '%s\n' "$*" >>"$MOCK_LOG"
    if [[ "$*" == *'runner.sh abort'* ]]; then
        return "${MOCK_ABORT_RC:-0}"
    fi
}
MOCK
export MOCK_LOG="$tmp/abort.log"
bash "$tmp/client/netlabs/tools/remote-uut.sh" --abort
[[ $(grep -c 'netlabs/tools/remote/runner.sh' "$MOCK_LOG") == 1 ]]
[[ $(grep -c '^installed$' "$MOCK_LOG") == 1 ]]
grep -Fq 'runner.sh abort' "$MOCK_LOG"
grep -Fq "rm -rf -- '/home/netlab-data/runs/upload.mock'" "$MOCK_LOG"

: >"$MOCK_LOG"
if MOCK_ABORT_RC=47 bash "$tmp/client/netlabs/tools/remote-uut.sh" --abort; then
    echo 'FAIL: client accepted failed remote abort' >&2
    exit 1
else
    rc=$?
fi
[[ "$rc" == 47 ]]
grep -Fq "rm -rf -- '/home/netlab-data/runs/upload.mock'" "$MOCK_LOG"
echo 'PASS: client abort installs handler, propagates failure, and removes temporary stage'
