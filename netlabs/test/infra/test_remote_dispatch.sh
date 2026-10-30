#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Verify actual client SSH dispatch, stage forwarding, and early failure reports.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/stage/netlabs/tools/remote"

cat >"$tmp/remote.cfg" <<'CFG'
REMOTE_HOST=127.0.0.1
REMOTE_USER=netlab
REMOTE_SSH_PORT=22
CFG

# Run the assembled SSH command locally, preserving its actual argument vector.
# A missing -n in the authentication probe deliberately consumes caller input.
cat >"$tmp/bin/ssh" <<'SH'
#!/usr/bin/env bash
for arg in "$@"; do
    if [[ "$arg" == -n ]]; then
        no_stdin=1
    fi
done
if [[ "${no_stdin:-0}" -ne 1 && "${!#}" == true ]]; then
    read -r -t 0.2 discarded || :
fi
exec bash -c "${!#}"
SH
chmod +x "$tmp/bin/ssh"

cat >"$tmp/stage/netlabs/tools/remote/runner.sh" <<'SH'
#!/usr/bin/env bash
[[ $# -ge 1 ]] || exit 2
case "$1" in
    uut-start|lab-start|regressions)
        [[ $# == 3 && "$2" == "$EXPECTED_STAGE" ]] || exit 2
        printf '%s %s\n' "$1" "$3" >>"$MOCK_LOG"
        ;;
    status)
        [[ $# == 1 ]] || exit 2
        echo 'No active lab or UUT session'
        ;;
    *) exit 2 ;;
esac
exit "${MOCK_RUNNER_RC:-0}"
SH
chmod +x "$tmp/stage/netlabs/tools/remote/runner.sh"

export PATH="$tmp/bin:$PATH"
export REMOTE_LAB_CONFIG="$tmp/remote.cfg"
export EXPECTED_STAGE="$tmp/stage"
export MOCK_LOG="$tmp/runner.log"
# shellcheck disable=SC1091
source "$root/netlabs/tools/remote/common.sh"

remote_transport_prepare
[[ "$REMOTE_MODE" == key ]]

remote_run "$EXPECTED_STAGE" uut-start 2R-neighbor
remote_run "$EXPECTED_STAGE" lab-start 101
remote_run "$EXPECTED_STAGE" regressions all
[[ $(sed -n '1p' "$MOCK_LOG") == 'uut-start 2R-neighbor' ]]
[[ $(sed -n '2p' "$MOCK_LOG") == 'lab-start 101' ]]
[[ $(sed -n '3p' "$MOCK_LOG") == 'regressions all' ]]
[[ "$(remote_run "$EXPECTED_STAGE" status)" == 'No active lab or UUT session' ]]

# Preserve the remote process exit code across the SSH command boundary.
export MOCK_RUNNER_RC=42
if remote_run "$EXPECTED_STAGE" uut-start 2R-neighbor; then
    echo 'FAIL: remote runner error was lost' >&2
    exit 1
else
    rc=$?
fi
[[ "$rc" == 42 ]]
unset MOCK_RUNNER_RC
printf 'PASS: SSH dispatch forwards workspace and preserves remote exit codes\n'

# An auth probe may not drain script input (including Bash heredocs).
if ! printf 'sentinel\n' | (
    remote_transport_prepare
    IFS= read -r line
    [[ "$line" == sentinel ]]
); then
    echo 'FAIL: SSH authentication probe consumed stdin' >&2
    exit 1
fi
printf 'PASS: SSH authentication probe preserves stdin\n'

# A UUT that exits before REPORT_ID must produce a definitive diagnostic.
mkdir -p "$tmp/client/netlabs/tools/remote" "$tmp/client/netlabs/uut/2R-neighbor"
cp "$root/netlabs/tools/remote-uut.sh" "$tmp/client/netlabs/tools/remote-uut.sh"
printf 'schema: openeigrp-live-neighbor/v1\nname: 2R-neighbor\n' >"$tmp/client/netlabs/uut/2R-neighbor/scenario.yaml"
printf 'name: eigrp-neighbor\n' >"$tmp/client/netlabs/uut/2R-neighbor/topology.yml"
printf '# UUT driver\n' >"$tmp/client/netlabs/uut/2R-neighbor.py"
cat >"$tmp/client/netlabs/tools/remote/common.sh" <<'SH'
#!/usr/bin/env bash
REMOTE_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
REMOTE_HOST=mock-appliance
remote_transport_prepare() { :; }
remote_new_stage() { printf '/home/netlab-data/runs/upload.mock\n'; }
remote_stage() { :; }
remote_install_runner() { :; }
remote_run() { return 2; }
remote_fetch_report() { return 1; }
SH
if bash "$tmp/client/netlabs/tools/remote-uut.sh" --scenario 2R-neighbor >"$tmp/client.log" 2>&1; then
    echo 'FAIL: missing report ID was reported as success' >&2
    exit 1
else
    rc=$?
fi
[[ "$rc" == 2 ]]
grep -Fq 'ERROR: Remote runner exited 2 before providing a report ID' "$tmp/client.log"
grep -Fq 'FAIL: 2R-neighbor' "$tmp/client.log"
printf 'PASS: early UUT failure reports exit status and FAIL\n'
