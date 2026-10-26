#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Execute an automated interoperability scenario and retrieve its report.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/remote/common.sh"

usage() {
    cat <<'HELP'
Usage: netlabs/tools/remote-uut.sh --scenario 2R-neighbor
       netlabs/tools/remote-uut.sh --list
       netlabs/tools/remote-uut.sh --update

Runs a live mixed OpenEIGRP-FRR/CSR1000v qualification scenario,
retrieves its report, and destroys the topology even when assertions fail.
--update checks GHCR for the latest published versioned OpenEIGRP image and
pulls it onto the netlab appliance. Each scenario then uses the highest locally
installed version. No registry access is needed for normal test runs.
A scenario is NOT a replacement for the deterministic 'make uut' portable gate.
HELP
}

[[ $# == 1 || $# == 2 ]] || {
    usage >&2
    exit 2
}

case "$1" in
    --list)
        [[ $# == 1 ]] || exit 2
        for spec in "$REMOTE_REPO_ROOT"/netlabs/uut/*/scenario.yaml; do
            [[ -f "$spec" ]] || continue
            id="$(basename "$(dirname "$spec")")"
            [[ -f "$REMOTE_REPO_ROOT/netlabs/uut/$id.py" ]] && printf '%s\n' "$id"
        done
        exit 0
        ;;
    --update)
        [[ $# == 1 ]] || exit 2
        remote_transport_prepare
        printf 'Checking published OpenEIGRP images on %s...\n' "$REMOTE_HOST"
        stage="$(remote_new_stage)"
        remote_stage "$stage" netlabs/tools/remote/runner.sh netlabs/tools/remote/images.py
        remote_install_runner "$stage"
        if remote_run "$stage" image-update; then
            echo 'PASS: OpenEIGRP image update'
        else
            rc=$?
            # The runner may have exited before cleanup (for example: BUSY).
            # The stage path has already passed remote_stage's strict validation.
            remote_ssh "rm -rf -- '$stage'" >/dev/null 2>&1 || :
            printf 'FAIL: OpenEIGRP image update (remote exit %d)\n' "$rc" >&2
            exit "$rc"
        fi
        exit 0
        ;;
    --scenario)
        [[ $# == 2 && "$2" =~ ^[0-9]+R-[a-z][a-z0-9-]*$ ]] || {
            usage >&2
            exit 2
        }
        scenario="$2"
        ;;
    --help|-h)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

[[ -f "$REMOTE_REPO_ROOT/netlabs/uut/$scenario/scenario.yaml" ]] || {
    echo "Unknown scenario: $scenario" >&2
    exit 2
}
[[ -f "$REMOTE_REPO_ROOT/netlabs/uut/$scenario.py" ]] || {
    echo "Missing UUT test driver: $scenario.py" >&2
    exit 2
}

remote_transport_prepare
printf 'Staging %s on %s...\n' "$scenario" "$REMOTE_HOST"
stage="$(remote_new_stage)"
# The internal-route UUT reuses the proven Cisco/FRR CLI helpers from
# 2R-neighbor.py; stage that driver alongside the selected scenario.
files=(netlabs/tools/remote/runner.sh netlabs/tools/remote/images.py
    "netlabs/uut/$scenario.py"
    "netlabs/uut/$scenario" netlabs/config)
if [[ "$scenario" == 2R-internal ]]; then
    files+=(netlabs/uut/2R-neighbor.py)
fi
remote_stage "$stage" "${files[@]}"
remote_install_runner "$stage"

printf 'Starting automated UUT: %s...\n' "$scenario"

# Stream stdout now, while retaining the transcript for REPORT_ID extraction.
# PIPESTATUS preserves the remote test exit code rather than tee's success code.
transcript="$(mktemp)"
trap 'rm -f -- "$transcript"' EXIT
set +e
remote_run "$stage" uut-start "$scenario" 2>&1 | tee "$transcript"
steps=("${PIPESTATUS[@]}")
set -e

status="${steps[0]}"
if (( steps[1] != 0 )); then
    echo 'ERROR: Could not record the remote UUT transcript' >&2
    status=1
fi

report="$(sed -n 's/^REPORT_ID=\(uut-[A-Za-z0-9_.-]*\)$/\1/p' "$transcript" | tail -n 1)"
if [[ -n "$report" ]]; then
    local_results="$REMOTE_REPO_ROOT/netlabs/results"
    if remote_fetch_report "$report" "$local_results"; then
        echo "Report: $local_results/$report"
    else
        echo "WARNING: Could not download remote report $report" >&2
        status=1
    fi
else
    if (( status == 0 )); then
        echo 'ERROR: Remote runner exited without providing a report ID' >&2
        status=1
    else
        printf 'ERROR: Remote runner exited %d before providing a report ID\n' "$status" >&2
    fi
    printf 'FAIL: %s\n' "$scenario" >&2
fi

exit "$status"
