#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Server-side session owner for interactive labs and automated UUT runs.

set -Eeuo pipefail

# SSH commands do not reliably load the netlab user's login profile.
# Preserve caller-provided command paths before appliance fallbacks. Mocked
# regressions must never pick the real netlab from the provisioned virtualenv.
# NETLAB_PYTHON still explicitly selects the provisioned interpreter.
export PATH="$PATH:/home/netlab/netlab-venv/bin:/home/netlab/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"

BASE="${REMOTE_LAB_DATA:-/home/netlab-data}"
RUNS="$BASE/runs"
RESULTS="$BASE/results"
STATE_DIR="${REMOTE_LAB_STATE:-/home/netlab/.remote-lab}"
SESSION="$STATE_DIR/session"
LOCK="$STATE_DIR/lock"
OWNER="$STATE_DIR/owner"
ABORT_LOCK="$STATE_DIR/abort.lock"

mkdir -p "$RUNS" "$RESULTS" "$STATE_DIR"
chmod 700 "$STATE_DIR"

# Hold the same lock through deployment, execution, reporting, and teardown.
exec 9>"$LOCK"
# Abort must be able to signal the lock owner. It obtains the ordinary lock
# only after the worker releases it, and never tears down resources concurrently.
if [[ "${1:-}" != abort ]]; then
    flock -n 9 || {
        echo 'BUSY: another lab/UUT operation is in progress; use --abort to recover' >&2
        exit 3
    }
fi

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

state_get() {
    [[ -f "$SESSION" ]] && sed -n "s/^$1=//p" "$SESSION" | head -n 1 || :
}

state_write() {
    local file
    file="$(mktemp "$STATE_DIR/session.XXXXXX")"
    printf 'mode=%s\nname=%s\nworkspace=%s\nphase=%s\ngroup=%s\n' \
        "$1" "$2" "$3" "$4" "${5:-}" >"$file"
    chmod 600 "$file"
    mv -f "$file" "$SESSION"
}

state_clear() {
    rm -f "$SESSION"
}

# /proc start time protects against PID reuse. The control record contains
# only processes launched by this runner, not arbitrary host/container PIDs.
proc_identity() {
    local line pid="$1"
    local -a fields
    [[ -r "/proc/$pid/stat" ]] || return 1
    IFS= read -r line <"/proc/$pid/stat" || return 1
    line="${line##*) }"
    read -r -a fields <<<"$line"
    ((${#fields[@]} > 19)) || return 1
    printf '%s %s %s\n' "${fields[19]}" "${fields[2]}" "${fields[3]}"
}

owner_get() {
    [[ -f "$OWNER" ]] && sed -n "s/^$1=//p" "$OWNER" | head -n 1 || :
}

owner_write() {
    local mode="$1" workspace="$2" identity start group session file
    identity="$(proc_identity "$$")" || fail 'Cannot identify regression worker'
    read -r start group session <<<"$identity"
    file="$(mktemp "$STATE_DIR/owner.XXXXXX")"
    printf 'pid=%s\nstart=%s\ngroup=%s\nsession=%s\nmode=%s\nworkspace=%s\n' \
        "$$" "$start" "$group" "$session" "$mode" "$workspace" >"$file"
    chmod 600 "$file"
    mv -f "$file" "$OWNER"
}

owner_clear() {
    if [[ "$(owner_get pid)" == "$$" ]]; then
        rm -f -- "$OWNER"
    fi
}

state_validate() {
    [[ -f "$SESSION" ]] || fail 'No active lab or UUT session'

    local work
    work="$(state_get workspace)"
    [[ "$work" == "$RUNS"/* && "$work" != *'..'* ]] || \
        fail "Refusing invalid session workspace: $work"
}

# Restrict to lab-owned topology names, but do not restrict router count or
# concrete scenario suffix. Capture all containers, including stopped nodes.
# This deliberately does not match unrelated containerlab deployments.
clab_nodes_exist() {
    local nodes
    nodes="$(docker ps -a --format '{{.Names}}')" || {
        echo 'ERROR: docker ps failed; cannot verify orphan ownership' >&2
        return 2
    }
    grep -Eq '^clab-(eigrp-|oeig-lab-)' <<<"$nodes"
}

assert_free() {
    if [[ -f "$SESSION" ]]; then
        echo "BUSY: $(state_get mode) '$(state_get name)' ($(state_get phase)); use --status/--stop first" >&2
        exit 3
    fi

    # A missing state file must not hide orphaned Containerlab resources.
    local found=0
    clab_nodes_exist || found=$?
    if (( found == 2 )); then
        fail 'Cannot verify orphaned containerlab resources'
    elif (( found == 0 )); then
        fail 'Orphaned OpenEIGRP containerlab nodes detected; investigate before starting a new session'
    fi
}

validate_stage() {
    [[ "$1" =~ ^${RUNS}/upload\.[A-Za-z0-9]+$ && -d "$1" ]] || \
        fail 'Invalid or missing staged workspace'
}

# A successful netlab down is not sufficient when a container remains.
netlab_destroy() {
    local topology="$1"
    if ! command -v netlab >/dev/null 2>&1; then
        echo 'ERROR: netlab not installed' >&2
        return 1
    fi

    if ! (cd "${topology%/*}" && netlab down); then
        echo 'WARNING: netlab down failed; checking for surviving nodes' >&2
    fi
    local found=0
    clab_nodes_exist || found=$?
    if (( found == 2 )); then
        return 1
    elif (( found == 0 )); then
        echo 'ERROR: OpenEIGRP containerlab nodes remain after teardown' >&2
        return 1
    fi
    return 0
}

# Fault and capture recovery precedes netlab down: namespaces must still exist
# when restoring tc settings. Do not discard state markers on partial failure.
recover_evidence() {
    local stage="$1" rc=0 helper
    # Network faults must be restored before terminating namespaces.
    # Capture stops after the fault so both remain available for diagnosis.
    for helper in network_fault capture; do
        if [[ -f "$stage/netlabs/common/$helper.py" ]]; then
            if ! python3 "$stage/netlabs/common/$helper.py" cleanup \
                    --directory "$stage/artifacts"; then
                echo "ERROR: $helper cleanup failed" >&2
                rc=1
            fi
        elif [[ "$helper" == network_fault ]] &&
             compgen -G "$stage/artifacts/*.fault.json" >/dev/null; then
            echo "ERROR: $helper cleanup helper missing" >&2
            rc=1
        elif [[ "$helper" == capture ]] &&
             compgen -G "$stage/artifacts/*.capture.json" >/dev/null; then
            echo "ERROR: $helper cleanup helper missing" >&2
            rc=1
        fi
    done
    return "$rc"
}

run_lab() {
    local stage="$1" id="$2" work
    assert_free
    validate_stage "$stage"
    [[ "$id" =~ ^[0-9]{3}$ ]] || fail 'Invalid lab number'

    work="$stage/netlabs/labs/$id"
    [[ -f "$work/topology.yml" ]] || fail 'Missing lab topology.yml'
    state_write lab "$id" "$work" deploying

    if (cd "$work" && netlab up); then
        state_write lab "$id" "$work" running
        echo "ACTIVE LAB $id (workspace: $work)"
    else
        echo 'Lab deployment failed; attempting teardown' >&2
        if netlab_destroy "$work/topology.yml"; then
            state_clear
            rm -rf -- "$stage"
        else
            echo 'Cleanup failed; session retained for recovery' >&2
        fi
        return 1
    fi
}

run_uut() {
    local stage="$1" name="$2" group="${3:-}" work driver report rc clean_rc image_rc capture_rc
    local -a steps
    assert_free
    validate_stage "$stage"
    [[ "$name" =~ ^[2-6]R-[a-z][a-z0-9-]*$ ]] || fail 'Invalid scenario name (requires 2R-6R)'

    # New cases live under test/{rtp,dual}/<case>/ with one driver.py.
    # Existing netlabs/uut cases retain their original path and driver name.
    case "$group" in
        rtp|dual)
            work="$stage/netlabs/test/$group/$name"
            driver="$work/driver.py"
            ;;
        '')
            work="$stage/netlabs/uut/$name"
            driver="$stage/netlabs/uut/$name.py"
            ;;
        *) fail 'Invalid UUT test group' ;;
    esac
    [[ -f "$work/scenario.yaml" && -f "$work/topology.yml" && -f "$driver" ]] || \
        fail 'Missing UUT scenario, topology, or driver'
    local topology_name
    topology_name="$(sed -n 's/^name:[[:space:]]*\([a-zA-Z0-9_-]*\)[[:space:]]*$/\1/p' "$work/topology.yml" | head -n 1)"
    [[ "$topology_name" =~ ^(eigrp-[a-zA-Z0-9_-]+|oeig-lab-[a-zA-Z0-9_-]+)$ ]] || \
        fail 'Topology name must start with eigrp- or oeig-lab- for orphan detection'
    command -v docker >/dev/null || fail 'Docker unavailable'
    command -v netlab >/dev/null || fail 'netlab unavailable'

    report="$(mktemp -d "$RESULTS/uut-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX")"
    state_write uut "$name" "$stage" running "$group"
    owner_write uut "$stage"
    mkdir -p "$stage/artifacts"
    date -u +'%Y-%m-%dT%H:%M:%SZ' >"$stage/artifacts/started-at.txt"

    # Modify only the uploaded topology; repository fixtures remain unchanged.
    # A failed image selection is a setup failure and still gets a report.
    image_rc=0
    "${NETLAB_PYTHON:-/home/netlab/netlab-venv/bin/python}" \
        "$stage/netlabs/tools/remote/images.py" prepare \
        "$work/topology.yml" "$stage/artifacts/image.json" || image_rc=$?

    # Stream Python output while keeping an identical transcript in runner.log.
    # PIPESTATUS[0] is the test result; tee must never mask a UUT failure.
    set +e
    if (( image_rc == 0 )); then
        PYTHONUNBUFFERED=1 timeout --foreground --signal=TERM --kill-after=20s 1500s \
            "${NETLAB_PYTHON:-/home/netlab/netlab-venv/bin/python}" \
            "$driver" \
            --scenario "$work/scenario.yaml" \
            --topology "$work/topology.yml" \
            --artifacts "$stage/artifacts" 9>&- 2>&1 | tee "$stage/artifacts/runner.log" 9>&-
        steps=("${PIPESTATUS[@]}")
        rc="${steps[0]}"
        if (( steps[1] != 0 )); then
            echo 'ERROR: Unable to save UUT output to runner.log' >&2
            rc=1
        fi
        if (( rc == 0 )) && [[ ! -f "$stage/artifacts/report.json" ]]; then
            echo 'ERROR: UUT succeeded without report.json' | tee -a "$stage/artifacts/runner.log" >&2
            rc=1
        fi
    else
        rc="$image_rc"
        printf 'BLOCKED: OpenEIGRP image selection (exit %s)\n' "$rc" | tee "$stage/artifacts/runner.log"
        # Preserve the existing diagnostic for clients that grep the old line.
        printf 'FAIL: OpenEIGRP image selection (exit %s)\n' "$rc" | tee -a "$stage/artifacts/runner.log"
    fi
    set -e

    echo 'Cleaning up remote topology...'
    clean_rc=0
    if ! recover_evidence "$stage" >>"$stage/artifacts/cleanup.log" 2>&1; then
        clean_rc=1
        # Namespace recovery requires the container to remain available.
        # Never destroy it while an fault/capture marker is unresolved.
        echo 'ERROR: evidence restoration failed; preserving router namespaces' \
            >>"$stage/artifacts/cleanup.log"
    elif ! netlab_destroy "$work/topology.yml" >>"$stage/artifacts/cleanup.log" 2>&1; then
        clean_rc=1
    fi
    if (( clean_rc != 0 )); then
        echo 'FAIL: netlab/evidence cleanup failed; session retained' >&2
    fi

    # Copy prior to writing the durable structured summary. Never let a
    # driver-supplied JSON report mask an infrastructure or teardown failure.
    cp -a "$stage/artifacts/." "$report/"
    printf 'scenario=%s\nstatus=%s\ncleanup=%s\n' \
        "$name" "$rc" "$clean_rc" >"$report/run-status.txt"
    local result_rc=0
    python3 - "$report" "$name" "$rc" "$clean_rc" "$image_rc" "$group" <<'PY' || result_rc=$?
import datetime
import json
from pathlib import Path
import sys
folder, name, run_rc, clean_rc, image_rc, group = sys.argv[1:]
folder = Path(folder)
run_rc, clean_rc, image_rc = map(int, (run_rc, clean_rc, image_rc))
def read_json(name):
    try:
        return json.loads((folder / name).read_text(encoding='utf8'))
    except (ValueError, OSError):
        return None
driver_report = read_json('report.json')
status = 'FAIL' if clean_rc else 'BLOCKED' if image_rc else 'PASS' if not run_rc else 'FAIL'
required = []
if isinstance(driver_report, dict):
    objectives = driver_report.get('objectives', [])
    if isinstance(objectives, dict):
        objectives = list(objectives.values())
    if isinstance(objectives, list):
        required.extend(x.get('status') for x in objectives
                        if isinstance(x, dict) and x.get('required', True))
    for family in ('ipv4', 'ipv6'):
        item = driver_report.get(family)
        if isinstance(item, dict):
            required.append(item.get('status'))
if not image_rc and not clean_rc:
    if not required or any(s not in ('PASS','FAIL','NOT_RUN','BLOCKED','NOT_IMPLEMENTED') for s in required):
        status = 'FAIL'
    elif 'FAIL' in required:
        status = 'FAIL'
    elif 'BLOCKED' in required or 'NOT_RUN' in required:
        status = 'BLOCKED'
    elif 'NOT_IMPLEMENTED' in required:
        status = 'NOT_IMPLEMENTED'
    elif not run_rc:
        status = 'PASS'
    else:
        status = 'FAIL'
result = {
    'schema': 'openeigrp-netlabs-result/v1', 'scenario': name, 'group': group or 'legacy',
    'router_count': int(name.split('R-', 1)[0]),
    'timestamp_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'started_at_utc': (folder / 'started-at.txt').read_text().strip(),
    'status': status, 'driver_exit_code': run_rc,
    'image_selection_exit_code': image_rc,
    'cleanup': 'PASS' if clean_rc == 0 else 'FAIL',
    'images': read_json('image.json'), 'driver_report': driver_report,
    'required_objective_statuses': required,
    'artifacts': sorted(f.name for f in folder.iterdir() if f.is_file()),
}
(folder / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
if status != 'PASS':
    sys.exit(1)
PY
    if (( result_rc != 0 && rc == 0 )); then
        rc=1
        printf 'scenario=%s\nstatus=%s\ncleanup=%s\n' \
            "$name" "$rc" "$clean_rc" >"$report/run-status.txt"
    fi

    if [[ "$clean_rc" -eq 0 ]]; then
        state_clear
        rm -rf -- "$stage"
    else
        rc=1
    fi
    owner_clear

    echo "REPORT_ID=${report##*/}"
    if [[ "$rc" -eq 0 ]]; then
        echo "PASS: $name"
    else
        echo "FAIL: $name"
    fi
    return "$rc"
}

# Regression suites execute on Linux, where the provisioned netlab Python
# environment provides PyYAML/Jinja2 and /proc and flock exist. They use
# isolated test mocks, not live Docker/netlab resources.
run_regressions() {
    local stage="$1" selection="$2" report rc=0
    assert_free
    validate_stage "$stage"
    [[ "$selection" == all || "$selection" == rtp || "$selection" == dual ]] || \
        fail 'Invalid regression selection'
    [[ -f "$stage/netlabs/tools/remote/regressions.py" ]] || \
        fail 'Missing regression runner'
    state_write regressions "$selection" "$stage" running
    owner_write regressions "$stage"
    report="$(mktemp -d "$RESULTS/uut-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX")"
    mkdir -p "$stage/artifacts"
    "${NETLAB_PYTHON:-/home/netlab/netlab-venv/bin/python}" \
        "$stage/netlabs/tools/remote/regressions.py" \
        --selection "$selection" --artifacts "$stage/artifacts" 9>&- || rc=$?
    cp -a "$stage/artifacts/." "$report/"
    if [[ ! -f "$report/regressions.json" ]]; then
        printf '{"status":"BLOCKED","cases":[],"error":"regression runner did not produce results"}\n' \
            >"$report/regressions.json"
        rc=1
    fi
    python3 - "$report/regressions.json" "$report/result.json" <<'PYRESULT'
import json
from pathlib import Path
import sys
try:
    result = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
except (OSError, ValueError):
    result = {'status': 'BLOCKED', 'cases': [], 'error': 'invalid regression report'}
Path(sys.argv[2]).write_text(json.dumps({
    'schema': 'openeigrp-netlabs-regression-result/v1',
    'status': result.get('status', 'BLOCKED'), 'cleanup': 'PASS',
    'cases': result.get('cases', []),
}, indent=2) + '\n', encoding='utf8')
PYRESULT
    printf 'regressions=%s\nstatus=%s\ncleanup=0\n' "$selection" "$rc" >"$report/run-status.txt"
    rm -rf -- "$stage"
    state_clear
    owner_clear
    echo "REPORT_ID=${report##*/}"
    if (( rc == 0 )); then
        echo "PASS: $selection regressions"
    else
        echo "FAIL: $selection regressions"
    fi
    return "$rc"
}

run_image_update() {
    local stage="$1" rc=0
    assert_free
    validate_stage "$stage"
    command -v docker >/dev/null || fail 'Docker unavailable'
    "${NETLAB_PYTHON:-/home/netlab/netlab-venv/bin/python}" \
        "$stage/netlabs/tools/remote/images.py" update || rc=$?
    rm -rf -- "$stage"
    return "$rc"
}

show_status() {
    if [[ ! -f "$SESSION" ]]; then
        echo 'No active lab or UUT session'
        return 0
    fi

    state_validate
    local mode work
    mode="$(state_get mode)"
    work="$(state_get workspace)"
    echo "Active: $mode $(state_get name) ($(state_get phase))"
    echo "Workspace: $work"

    if [[ "$mode" == lab && -d "$work" ]]; then
        (cd "$work" && netlab status) || true
    else
        docker ps --filter name=clab-eigrp-
    fi
}

connect_lab() {
    state_validate
    [[ "$(state_get mode)" == lab && "$(state_get phase)" == running ]] || \
        fail 'Only running instructional labs allow connections'

    local work node choice nodes=()
    work="$(state_get workspace)"
    # Bash 3.2-compatible array loading also supports local macOS mock tests.
    while IFS= read -r node; do
        nodes+=("$node")
    done < <(
        "${NETLAB_PYTHON:-/home/netlab/netlab-venv/bin/python}" - "$work/topology.yml" <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding='utf8') as file:
    topology = yaml.safe_load(file)
for name in (topology.get('nodes') or {}):
    print(name)
PY
    )

    ((${#nodes[@]})) || fail 'No nodes in topology'
    echo 'Routers:'
    local i
    for i in "${!nodes[@]}"; do
        printf '%d) %s\n' "$((i + 1))" "${nodes[i]}"
    done

    read -r -p 'Select router number: ' choice
    [[ "$choice" =~ ^[0-9]+$ && "$choice" -ge 1 &&
       "$choice" -le "${#nodes[@]}" ]] || fail 'Invalid selection'
    node="${nodes[choice-1]}"

    cd "$work"
    flock -u 9  # A connected operator must not block a later --stop operation.
    exec netlab connect "$node"
}

stop_session() {
    state_validate
    local mode work
    mode="$(state_get mode)"
    work="$(state_get workspace)"

    if [[ "$mode" == lab ]]; then
        if netlab_destroy "$work/topology.yml"; then
            state_clear
            echo 'Lab stopped'
        else
            fail 'Lab teardown failed; session retained'
        fi
    elif [[ "$mode" == uut ]]; then
        # A running UUT holds the lock; this can only recover an abandoned one.
        local group topology
        group="$(state_get group)"
        case "$group" in
            rtp|dual) topology="$work/netlabs/test/$group/$(state_get name)/topology.yml" ;;
            '') topology="$work/netlabs/uut/$(state_get name)/topology.yml" ;;
            *) fail 'Invalid stored UUT group; session retained' ;;
        esac
        if recover_evidence "$work" && netlab_destroy "$topology"; then
            state_clear
            echo 'Stale UUT session cleared'
        else
            fail 'UUT recovery teardown failed; session retained'
        fi
    else
        fail "Unknown session mode: $mode"
    fi
}

# An interrupted SSH session may leave the worker running with the flock FD
# inherited by its children. Signal only our recorded process group, wait for
# the normal lock, then restore faults and destroy the topology. Never remove
# the lock file or kill a PID selected solely by its process name.
abort_session() {
    local pid start group session identity mode workspace current owner_pid rc=0
    local i report clean_rc=0

    exec 8>"$ABORT_LOCK"
    flock -n 8 || fail 'Another abort is in progress'

    pid="$(owner_get pid)"
    start="$(owner_get start)"
    group="$(owner_get group)"
    session="$(owner_get session)"
    mode="$(owner_get mode)"
    workspace="$(owner_get workspace)"

    if [[ -n "$pid" ]]; then
        [[ "$pid" =~ ^[1-9][0-9]*$ && "$group" == "$pid" &&
           "$session" == "$pid" && "$start" =~ ^[0-9]+$ &&
           ( "$mode" == uut || "$mode" == regressions ) ]] || \
            fail 'Worker is not an isolated UUT/regression process; refusing to signal it'

        identity="$(proc_identity "$pid" || :)"
        if [[ -n "$identity" ]]; then
            [[ "$identity" == "$start $group $session" ]] || \
                fail 'Worker PID identity changed; refusing to signal it'
            # This confirms that PID is still the server runner, not an
            # unrelated process that happened to reuse the process number.
            if ! tr '\0' ' ' <"/proc/$pid/cmdline" | grep -Fq 'netlabs/tools/remote/runner.sh'; then
                fail 'Recorded PID is not the Netlabs runner'
            fi
            echo "Stopping $mode worker (PID $pid)..."
            kill -TERM -- "-$group" || fail 'Could not stop recorded worker'
            for ((i = 0; i < 40; i++)); do
                if ! kill -0 -- "-$group" 2>/dev/null; then
                    break
                fi
                sleep 0.1
            done
            # Escalation is permitted only if the original leader still
            # exists with the same kernel start time and process group.
            if kill -0 -- "-$group" 2>/dev/null &&
               [[ "$(proc_identity "$pid" || :)" == "$identity" ]]; then
                kill -KILL -- "-$group" || :
            fi
        fi
    fi

    # A previous-generation runner may hold the lock without an owner record.
    # It is unsafe to infer that its PID or children belong to this UUT.
    if [[ -z "$pid" ]]; then
        # No registered worker means there is nobody we can safely signal.
        # Check once rather than waiting for an unknown lock owner.
        flock -n 9 || fail 'Runner lock held by an unregistered worker; manual inspection required'
    else
        flock -w 12 9 || fail 'Runner lock remains held; inspect unresponsive worker'
    fi

    if [[ ! -f "$SESSION" ]]; then
        if [[ -n "$pid" && "$(owner_get pid)" == "$pid" ]]; then
            rm -f -- "$OWNER"
        fi
        echo 'No active UUT/regression session remains'
        return 0
    fi
    state_validate
    current="$(state_get mode)"
    [[ "$current" == uut || "$current" == regressions ]] || \
        fail "Active session is $current; use remote-lab.sh --stop instead"
    [[ -z "$mode" || ( "$mode" == "$current" && "$workspace" == "$(state_get workspace)" ) ]] || \
        fail 'Session changed while aborting; refusing cleanup'
    [[ -z "$(owner_get pid)" || "$(owner_get pid)" == "$pid" ]] || \
        fail 'A different worker now owns the session; refusing cleanup'

    workspace="$(state_get workspace)"
    report="$(mktemp -d "$RESULTS/uut-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX")"
    mkdir -p "$workspace/artifacts"
    echo "Recovering interrupted $current session..."

    if [[ "$current" == uut ]]; then
        local topology group_name
        group_name="$(state_get group)"
        case "$group_name" in
            rtp|dual)
                topology="$workspace/netlabs/test/$group_name/$(state_get name)/topology.yml"
                ;;
            '')
                topology="$workspace/netlabs/uut/$(state_get name)/topology.yml"
                ;;
            *)
                echo 'ERROR: invalid stored UUT group' >>"$workspace/artifacts/cleanup.log"
                clean_rc=1
                ;;
        esac
        if (( clean_rc == 0 )); then
            if recover_evidence "$workspace" >>"$workspace/artifacts/cleanup.log" 2>&1 &&
               netlab_destroy "$topology" >>"$workspace/artifacts/cleanup.log" 2>&1; then
                state_clear
            else
                clean_rc=1
            fi
        fi
    else
        state_clear
    fi

    # Preserve partial diagnostics even when fault restoration is unsuccessful.
    cp -a "$workspace/artifacts/." "$report/" || rc=1
    printf 'session=%s\nstatus=aborted\ncleanup=%s\n' \
        "$current" "$clean_rc" >"$report/run-status.txt"
    python3 - "$report/result.json" "$current" "$clean_rc" <<'PYABORT'
import datetime
import json
from pathlib import Path
import sys
path, mode, clean = sys.argv[1:]
result = {
    'schema': 'openeigrp-netlabs-abort/v1',
    'timestamp_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'mode': mode, 'status': 'BLOCKED',
    'cleanup': 'PASS' if clean == '0' else 'FAIL',
}
Path(path).write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
PYABORT
    if (( clean_rc == 0 )); then
        rm -rf -- "$workspace"
        rm -f -- "$OWNER"
        echo 'PASS: interrupted session cleaned up'
    else
        echo 'FAIL: cleanup incomplete; session retained for recovery' >&2
        rc=1
    fi
    echo "REPORT_ID=${report##*/}"
    return "$rc"
}

[[ $# -ge 1 ]] || fail 'Missing runner action'
action="$1"
shift
case "$action" in
    lab-start)
        [[ $# == 2 ]] || exit 2
        run_lab "$1" "$2"
        ;;
    uut-start)
        [[ $# == 2 || $# == 3 ]] || exit 2
        run_uut "$1" "$2" "${3:-}"
        ;;
    regressions)
        [[ $# == 2 ]] || exit 2
        run_regressions "$1" "$2"
        ;;
    image-update)
        [[ $# == 1 ]] || exit 2
        run_image_update "$1"
        ;;
    status)
        [[ $# == 0 ]] || exit 2
        show_status
        ;;
    connect)
        [[ $# == 0 ]] || exit 2
        connect_lab
        ;;
    stop)
        [[ $# == 0 ]] || exit 2
        stop_session
        ;;
    abort)
        [[ $# == 0 ]] || exit 2
        abort_session
        ;;
    *)
        fail "Unknown action: $action"
        ;;
esac
