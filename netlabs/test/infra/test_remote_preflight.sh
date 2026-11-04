#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Verify actionable SSH preflight errors without contacting a real appliance.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/client/netlabs/tools/remote" \
         "$tmp/client/netlabs/uut/2R-neighbor"
cp "$root/netlabs/tools/remote/common.sh" "$tmp/client/netlabs/tools/remote/common.sh"
cp "$root/netlabs/tools/remote-uut.sh" "$tmp/client/netlabs/tools/remote-uut.sh"
printf 'group: rtp\n' >"$tmp/client/netlabs/uut/2R-neighbor/scenario.yaml"
printf 'name: eigrp-neighbor\n' >"$tmp/client/netlabs/uut/2R-neighbor/topology.yml"
: >"$tmp/client/netlabs/uut/2R-neighbor.py"
cat >"$tmp/remote.cfg" <<'CONFIG'
REMOTE_HOST=badhost.invalid
REMOTE_USER=netlab
REMOTE_PASSWORD=dummy-secret
REMOTE_SSH_PORT=2222
CONFIG

cat >"$tmp/bin/ssh" <<'MOCKSSH'
#!/usr/bin/env bash
case "${SSH_FIXTURE:-}" in
    key-ok) exit 0 ;;
    dns) echo 'ssh: Could not resolve hostname badhost.invalid: Name or service not known' >&2 ;;
    refused) echo 'ssh: connect to host badhost.invalid port 2222: Connection refused' >&2 ;;
    timeout) echo 'ssh: connect to host badhost.invalid port 2222: Connection timed out' >&2 ;;
    host-key) echo 'Host key verification failed.' >&2 ;;
    auth) echo 'netlab@badhost.invalid: Permission denied (publickey,password).' >&2 ;;
    *) echo 'Unexpected SSH fixture' >&2 ;;
esac
exit 255
MOCKSSH
cat >"$tmp/bin/sshpass" <<'MOCKSSHPASS'
#!/usr/bin/env bash
[[ "$1" == -e && "${SSHPASS:-}" == dummy-secret ]] || exit 90
case "${SSHPASS_FIXTURE:-}" in
    good) exit 0 ;;
    bad) echo 'netlab@badhost.invalid: Permission denied, please try again.' >&2; exit 5 ;;
    *) echo 'unexpected sshpass call' >&2; exit 99 ;;
esac
MOCKSSHPASS
chmod +x "$tmp/bin/ssh" "$tmp/bin/sshpass"
export PATH="$tmp/bin:$PATH" REMOTE_LAB_CONFIG="$tmp/remote.cfg"

run_prepare() {
    /bin/bash -c 'source "$1"; remote_transport_prepare; printf "MODE=%s\n" "$REMOTE_MODE"' \
        bash "$tmp/client/netlabs/tools/remote/common.sh"
}
check_error() {
    local fixture="$1" expected="$2" output status=0
    output="$(SSH_FIXTURE="$fixture" SSHPASS_FIXTURE=bad run_prepare 2>&1)" || status=$?
    [[ "$status" == 2 ]] || { echo "Unexpected rc=$status for $fixture: $output" >&2; exit 1; }
    [[ "$output" == *"$expected"* ]] || { echo "$output" >&2; exit 1; }
    [[ "$output" == *'netlab@badhost.invalid'* && "$output" == *'2222'* ]] || exit 1
    [[ "$output" != *dummy-secret* ]] || { echo 'Password leaked in diagnostic' >&2; exit 1; }
}
check_error dns 'hostname cannot be resolved'
check_error refused 'SSH connection refused'
check_error timeout 'SSH connection timed out'
check_error host-key 'SSH host-key verification failed'
check_error auth 'SSH authentication failed'
[[ "$(SSH_FIXTURE=auth SSHPASS_FIXTURE=good run_prepare)" == MODE=password ]]
[[ "$(SSH_FIXTURE=key-ok run_prepare)" == MODE=key ]]

# A bad hostname must stop the batch before regressions or misleading BLOCKED
# summaries. It must also stop a single-case run before staging anything.
for selection in --all '--scenario 2R-neighbor'; do
    status=0
    # shellcheck disable=SC2086
    output="$(SSH_FIXTURE=dns bash "$tmp/client/netlabs/tools/remote-uut.sh" $selection 2>&1)" || status=$?
    [[ "$status" == 2 ]] || { echo "Unexpected exit $status: $output" >&2; exit 1; }
    [[ "$output" == *'hostname cannot be resolved'* ]] || exit 1
    [[ "$output" != *'BLOCKED: live scenarios'* ]] || exit 1
    [[ "$output" != *'Running all regressions'* ]] || exit 1
done
[[ ! -d "$tmp/client/netlabs/results" ]]
echo 'PASS: remote preflight diagnoses SSH failures and stops before tests'
