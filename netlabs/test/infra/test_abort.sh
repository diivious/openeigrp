#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Regression coverage for controlled worker cancellation and recovery.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
runner="$root/netlabs/tools/remote/runner.sh"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT

mkdir -p "$tmp/bin" "$tmp/state/runs" "$tmp/state/session"
export REMOTE_LAB_DATA="$tmp/state"
export REMOTE_LAB_STATE="$tmp/state/session"
export LAB_ACTIONS="$tmp/actions.log"
export DOCKER_NODES="$tmp/nodes"
export NETLAB_PYTHON="$tmp/bin/mock_python"

cat >"$tmp/bin/netlab" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$1" >>"$LAB_ACTIONS"
if [[ "$1" == down && "${FAIL_DOWN:-0}" == 1 ]]; then
    printf '%s\n' 'clab-eigrp-abort-oe' >"$DOCKER_NODES"
    exit 1
fi
MOCK

cat >"$tmp/bin/docker" <<'MOCK'
#!/usr/bin/env bash
if [[ "$1" == ps && -f "$DOCKER_NODES" ]]; then
    cat "$DOCKER_NODES"
fi
MOCK

cat >"$tmp/bin/mock_python" <<'MOCK'
#!/usr/bin/env bash
case "$1" in
    */netlabs/tools/remote/images.py)
        printf '{"image":"test"}\n' >"$4"
        ;;
    */netlabs/tools/remote/regressions.py)
        printf 'regression started\n'
        sleep 120
        ;;
    *)
        printf 'driver started\n'
        sleep 120
        ;;
esac
MOCK
chmod +x "$tmp/bin/"*
export PATH="$tmp/bin:$PATH"

make_uut() {
    local stage="$1" dir="$1/netlabs/uut/2R-abort"
    mkdir -p "$dir" "$stage/netlabs/tools/remote"
    printf 'name: eigrp-abort\n' >"$dir/topology.yml"
    printf 'name: 2R-abort\n' >"$dir/scenario.yaml"
    printf '# driver\n' >"$stage/netlabs/uut/2R-abort.py"
    printf '# image helper\n' >"$stage/netlabs/tools/remote/images.py"
}

wait_for_owner() {
    local i
    for ((i = 0; i < 100; i++)); do
        if [[ -s "$REMOTE_LAB_STATE/owner" && -f "$REMOTE_LAB_STATE/session" ]]; then
            return 0
        fi
        sleep 0.05
    done
    echo 'FAIL: worker did not register' >&2
    return 1
}

check_abort_result() {
    local log="$1" expected="$2" report
    report="$(sed -n 's/^REPORT_ID=//p' "$log" | tail -n 1)"
    [[ "$report" == uut-* && -f "$REMOTE_LAB_DATA/results/$report/result.json" ]]
    python3 - "$REMOTE_LAB_DATA/results/$report/result.json" "$expected" <<'PY'
import json
from pathlib import Path
import sys
result = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
assert result['status'] == 'BLOCKED', result
assert result['cleanup'] == sys.argv[2], result
PY
}

# Cancel a live UUT while its driver is still running. The runner was started
# in a distinct process group, exactly as the SSH transport starts real runs.
stage="$REMOTE_LAB_DATA/runs/upload.live"
make_uut "$stage"
setsid --wait bash "$runner" uut-start "$stage" 2R-abort >"$tmp/live.log" 2>&1 &
worker=$!
wait_for_owner
if bash "$runner" status >"$tmp/busy.log" 2>&1; then
    echo 'FAIL: concurrent session was not locked' >&2
    exit 1
fi
grep -Fq 'BUSY:' "$tmp/busy.log"
bash "$runner" abort >"$tmp/abort-live.log"
wait "$worker" || :
check_abort_result "$tmp/abort-live.log" PASS
[[ ! -f "$REMOTE_LAB_STATE/session" && ! -e "$stage" ]]
grep -Fqx down "$LAB_ACTIONS"
echo 'PASS: active live run can be aborted and cleaned up'

# The regression runner has no topology to destroy, but it must still be
# cancellable when an SSH client disconnects.
stage="$REMOTE_LAB_DATA/runs/upload.regression"
mkdir -p "$stage/netlabs/tools/remote"
printf '# regression runner\n' >"$stage/netlabs/tools/remote/regressions.py"
setsid --wait bash "$runner" regressions "$stage" all >"$tmp/regression.log" 2>&1 &
worker=$!
wait_for_owner
bash "$runner" abort >"$tmp/abort-regression.log"
wait "$worker" || :
check_abort_result "$tmp/abort-regression.log" PASS
[[ ! -f "$REMOTE_LAB_STATE/session" && ! -e "$stage" ]]
[[ $(grep -c '^down$' "$LAB_ACTIONS") == 1 ]]
echo 'PASS: active regression run can be aborted without touching routers'

# An unverified PID or a non-session-leader must never be signaled.
stage="$REMOTE_LAB_DATA/runs/upload.unknown"
make_uut "$stage"
printf 'mode=uut\nname=2R-abort\nworkspace=%s\nphase=running\ngroup=\n' \
    "$stage" >"$REMOTE_LAB_STATE/session"
printf 'pid=123\nstart=1\ngroup=12\nsession=12\nmode=uut\nworkspace=%s\n' \
    "$stage" >"$REMOTE_LAB_STATE/owner"
if bash "$runner" abort >"$tmp/refused.log" 2>&1; then
    echo 'FAIL: arbitrary PID accepted for cancellation' >&2
    exit 1
fi
grep -Fq 'refusing to signal' "$tmp/refused.log"
[[ -f "$REMOTE_LAB_STATE/session" && -d "$stage" ]]
rm -f "$REMOTE_LAB_STATE/owner"
bash "$runner" abort >"$tmp/stale.log"
check_abort_result "$tmp/stale.log" PASS
[[ ! -f "$REMOTE_LAB_STATE/session" && ! -d "$stage" ]]
echo 'PASS: arbitrary PID refused; verified unlocked stale session recovered'

# A previous-generation worker may hold the normal lock but have no owner
# record. Abort must not assume that its PID or process group is ours.
python3 - "$REMOTE_LAB_STATE/lock" "$tmp/old-lock-ready" "$tmp/old-lock-release" <<'PYLOCK' &
import fcntl
from pathlib import Path
import sys
import time
lock, ready, release = map(Path, sys.argv[1:])
with lock.open('a') as stream:
    fcntl.flock(stream, fcntl.LOCK_EX)
    ready.touch()
    for _ in range(200):
        if release.exists():
            break
        time.sleep(.05)
PYLOCK
holder=$!
for ((i = 0; i < 100; i++)); do
    [[ ! -f "$tmp/old-lock-ready" ]] || break
    sleep .05
done
[[ -f "$tmp/old-lock-ready" ]]
if bash "$runner" abort >"$tmp/old-lock.log" 2>&1; then
    echo 'FAIL: abort accepted an unregistered lock owner' >&2
    exit 1
fi
grep -Fq 'unregistered worker' "$tmp/old-lock.log"
touch "$tmp/old-lock-release"
wait "$holder"
echo 'PASS: unregistered running worker is not force-killed'

# Failed evidence restoration must preserve the namespace and cleanup markers.
stage="$REMOTE_LAB_DATA/runs/upload.fault"
make_uut "$stage"
mkdir -p "$stage/netlabs/common" "$stage/artifacts"
printf 'import sys; sys.exit(1)\n' >"$stage/netlabs/common/network_fault.py"
printf '{}\n' >"$stage/artifacts/network.fault.json"
if bash "$runner" abort >"$tmp/empty-abort.log" 2>&1; then
    : # Empty session is not an error; no state was created yet.
fi
printf 'mode=uut\nname=2R-abort\nworkspace=%s\nphase=running\ngroup=\n' \
    "$stage" >"$REMOTE_LAB_STATE/session"
if bash "$runner" abort >"$tmp/fault-fail.log" 2>&1; then
    echo 'FAIL: failing evidence restoration returned PASS' >&2
    exit 1
fi
check_abort_result "$tmp/fault-fail.log" FAIL
[[ -f "$REMOTE_LAB_STATE/session" && -e "$stage/artifacts/network.fault.json" ]]
printf 'import sys; sys.exit(0)\n' >"$stage/netlabs/common/network_fault.py"
bash "$runner" abort >"$tmp/fault-fixed.log"
check_abort_result "$tmp/fault-fixed.log" PASS
[[ ! -f "$REMOTE_LAB_STATE/session" ]]
echo 'PASS: failed restoration retains session and permits recovery'
