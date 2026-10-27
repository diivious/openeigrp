#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Discover and run local regressions and remote interoperability scenarios.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/remote/common.sh"

usage() {
    cat <<'HELP'
Usage: netlabs/tools/remote-uut.sh --list
       netlabs/tools/remote-uut.sh --update
       netlabs/tools/remote-uut.sh --all
       netlabs/tools/remote-uut.sh --rtp
       netlabs/tools/remote-uut.sh --dual
       netlabs/tools/remote-uut.sh --scenario 2R-neighbor
       netlabs/tools/remote-uut.sh --abort

--list       List discoverable complete 2R-6R live scenarios.
--update     Explicitly refresh the installed OpenEIGRP release image.
--all        Run all netlabs regressions on Linux, then all runnable live cases.
--rtp        Run infra/RTP regressions, then all runnable RTP live cases.
--dual       Run infra/DUAL regressions, then all runnable DUAL live cases.
--scenario   Run exactly one live scenario without the local regression suite.
--abort      Stop an interrupted run, restore faults, and tear down its lab.

New live scenarios: netlabs/test/{rtp,dual}/<case>/{driver.py,scenario.yaml,topology.yml}
Existing scenarios under netlabs/uut/ remain supported. Live tests execute
sequentially on the appliance; no further scenario starts after unverified
or failed teardown. Results are saved to netlabs/results/.
HELP
}

# Install the current recovery logic before contacting an older remote runner.
# An older in-flight run may not have a worker identity; in that case abort
# refuses to kill a process by guesswork and explains the manual recovery path.
run_abort() {
    local stage rc=0
    remote_transport_prepare
    stage="$(remote_new_stage)" || return 1
    if ! remote_stage "$stage" netlabs/tools/remote/runner.sh ||
       ! remote_install_runner "$stage"; then
        echo 'ERROR: Could not install the remote abort handler' >&2
        rc=1
    else
        remote_ssh 'bash /home/netlab/.remote-lab/runner.sh abort' || rc=$?
    fi
    # Only the newly staged abort handler is removed. The interrupted run's
    # workspace is owned by the remote recovery action.
    remote_ssh "rm -rf -- '$stage'" >/dev/null 2>&1 || rc=1
    return "$rc"
}

# Keep Bash 3.2 compatibility for macOS clients.
CASE_NAMES=()
CASE_GROUPS=()
CASE_DIRS=()
CASE_LAYOUTS=()

add_case() {
    local name="$1" group="$2" dir="$3" layout="$4" i
    [[ "$name" =~ ^[2-6]R-[a-z][a-z0-9-]*$ ]] || return 0
    for i in "${!CASE_NAMES[@]}"; do
        if [[ "${CASE_NAMES[i]}" == "$name" ]]; then
            printf 'ERROR: duplicate live scenario ID %s (%s and %s)\n' \
                "$name" "${CASE_DIRS[i]}" "$dir" >&2
            return 1
        fi
    done
    CASE_NAMES+=("$name")
    CASE_GROUPS+=("$group")
    CASE_DIRS+=("$dir")
    CASE_LAYOUTS+=("$layout")
}

discover() {
    local group dir name spec declared
    # Transitional live fixtures have a separately named Python driver.
    for spec in "$REMOTE_REPO_ROOT"/netlabs/uut/*/scenario.yaml; do
        [[ -f "$spec" ]] || continue
        dir="${spec%/scenario.yaml}"
        name="${dir##*/}"
        [[ -f "$dir/topology.yml" &&
           -f "$REMOTE_REPO_ROOT/netlabs/uut/$name.py" ]] || continue
        declared="$(sed -n 's/^[[:space:]]*group:[[:space:]]*\(rtp\|dual\)[[:space:]]*$/\1/p' "$spec" | head -n 1)"
        # Historical 2R-neighbor is a peer/RTP test; do not infer arbitrary
        # groups from names or claim the incomplete 2R-internal is runnable.
        if [[ -z "$declared" && "$name" == 2R-neighbor ]]; then
            declared=rtp
        fi
        add_case "$name" "$declared" "$dir" legacy || return 1
    done
    # A group's directory is the source of truth for new cases.
    for group in rtp dual; do
        for dir in "$REMOTE_REPO_ROOT/netlabs/test/$group"/*; do
            [[ -d "$dir" && -f "$dir/driver.py" &&
               -f "$dir/scenario.yaml" && -f "$dir/topology.yml" ]] || continue
            name="${dir##*/}"
            add_case "$name" "$group" "$dir" grouped || return 1
        done
    done
}

run_image_update() {
    remote_transport_prepare
    printf 'Checking published OpenEIGRP images on %s...\n' "$REMOTE_HOST"
    local stage rc
    stage="$(remote_new_stage)"
    remote_stage "$stage" netlabs/tools/remote/runner.sh netlabs/tools/remote/images.py
    remote_install_runner "$stage"
    if remote_run "$stage" image-update; then
        echo 'PASS: OpenEIGRP image update'
    else
        rc=$?
        remote_ssh "rm -rf -- '$stage'" >/dev/null 2>&1 || :
        printf 'FAIL: OpenEIGRP image update (remote exit %d)\n' "$rc" >&2
        return "$rc"
    fi
}

# LAST_SAFE=1 means a retrieved structured result attests clean teardown.
# A failed scenario can still be safe for the next independent test.
run_live() {
    local index="$1" name="${CASE_NAMES[$1]}" group="${CASE_GROUPS[$1]}"
    local layout="${CASE_LAYOUTS[$1]}" stage transcript report status
    local -a files steps
    LAST_SAFE=0
    LAST_RESULT=''
    printf '\nRunning live scenario: %s [%s]\n' "$name" "${group:-unclassified}"
    stage="$(remote_new_stage)" || return 1
    files=(
        netlabs/tools/remote/runner.sh
        netlabs/tools/remote/images.py
        netlabs/common
        netlabs/config
    )
    if [[ "$layout" == grouped ]]; then
        files+=("netlabs/test/$group/$name")
    else
        files+=("netlabs/uut/$name.py" "netlabs/uut/$name")
        # Historical route driver imports the neighbor CLI helpers.
        if [[ "$name" == 2R-internal ]]; then
            files+=(netlabs/uut/2R-neighbor.py)
        fi
    fi
    if ! remote_stage "$stage" "${files[@]}" || ! remote_install_runner "$stage"; then
        echo "ERROR: staging failed for $name" >&2
        return 1
    fi

    transcript="$(mktemp)"
    set +e
    if [[ "$layout" == grouped ]]; then
        remote_run "$stage" uut-start "$name" "$group" 2>&1 | tee "$transcript"
    else
        remote_run "$stage" uut-start "$name" 2>&1 | tee "$transcript"
    fi
    steps=("${PIPESTATUS[@]}")
    set -e
    status="${steps[0]}"
    if (( steps[1] != 0 )); then
        echo 'ERROR: Could not record remote UUT transcript' >&2
        status=1
    fi
    report="$(sed -n 's/^REPORT_ID=\(uut-[A-Za-z0-9_.-]*\)$/\1/p' "$transcript" | tail -n 1)"
    rm -f -- "$transcript"

    if [[ -n "$report" ]]; then
        local local_results="$REMOTE_REPO_ROOT/netlabs/results"
        if remote_fetch_report "$report" "$local_results"; then
            printf 'Report: %s/%s\n' "$local_results" "$report"
            LAST_RESULT="$report"
            # Never continue a batch when teardown cannot be verified.
            if [[ -f "$local_results/$report/result.json" ]] && \
               grep -Eq '"cleanup"[[:space:]]*:[[:space:]]*"PASS"' \
                   "$local_results/$report/result.json"; then
                LAST_SAFE=1
            fi
        else
            echo "ERROR: Could not download remote report $report" >&2
            status=1
        fi
    else
        if (( status == 0 )); then
            echo 'ERROR: Remote runner exited without providing a report ID' >&2
            status=1
        else
            printf 'ERROR: Remote runner exited %d before providing a report ID\n' "$status" >&2
        fi
    fi
    if (( status == 0 && ! LAST_SAFE )); then
        echo "ERROR: No verified remote cleanup result for $name" >&2
        status=1
    fi
    if (( status != 0 )); then
        printf 'FAIL: %s\n' "$name" >&2
    fi
    return "$status"
}

# Regression scripts use isolated/mocked inputs on the Linux appliance.
# Only the live scenarios provide actual Cisco/OpenEIGRP qualification evidence.
PASSED=0
FAILED=0
BLOCKED=0
RESULT_ROWS=''
record() {
    local kind="$1" name="$2" status="$3" report="${4:-}"
    RESULT_ROWS+="${kind}"$'\t'"${name}"$'\t'"${status}"$'\t'"${report}"$'\n'
    case "$status" in
        PASS) PASSED=$((PASSED + 1)) ;;
        BLOCKED|NOT_IMPLEMENTED) BLOCKED=$((BLOCKED + 1)) ;;
        *) FAILED=$((FAILED + 1)) ;;
    esac
}

# Stage all test inputs, excluding client credentials and generated results.
# Run the unit regressions on Linux instead of the macOS client: the provisioned
# interpreter supplies PyYAML/Jinja2 and the runtime exposes /proc and flock.
run_remote_regressions() {
    local selection="$1" stage transcript report local_results status
    local -a steps files
    printf '\nRunning %s regressions on %s...\n' "$selection" "$REMOTE_HOST"
    stage="$(remote_new_stage)" || { record unit regressions BLOCKED; return 1; }
    files=(
        netlabs/tools/remote
        netlabs/tools/remote-lab.sh
        netlabs/tools/remote-uut.sh
        netlabs/uut
        netlabs/test
        netlabs/config
        netlabs/common
    )
    if ! remote_stage "$stage" "${files[@]}" || ! remote_install_runner "$stage"; then
        echo 'ERROR: could not stage remote regressions' >&2
        record unit regressions BLOCKED
        return 1
    fi
    transcript="$(mktemp)"
    set +e
    remote_run "$stage" regressions "$selection" 2>&1 | tee "$transcript"
    steps=("${PIPESTATUS[@]}")
    set -e
    status="${steps[0]}"
    if (( steps[1] != 0 )); then
        echo 'ERROR: cannot save regression transcript' >&2
        status=1
    fi
    report="$(sed -n 's/^REPORT_ID=\(uut-[A-Za-z0-9_.-]*\)$/\1/p' "$transcript" | tail -n 1)"
    rm -f -- "$transcript"
    if [[ -z "$report" ]]; then
        printf 'BLOCKED: remote regression runner exited without a report (exit %s)\n' "$status" >&2
        record unit regressions BLOCKED
        return 1
    fi
    local_results="$REMOTE_REPO_ROOT/netlabs/results"
    if ! remote_fetch_report "$report" "$local_results"; then
        echo "BLOCKED: cannot retrieve remote regression report $report" >&2
        record unit regressions BLOCKED
        return 1
    fi
    printf 'Regression report: %s/%s\n' "$local_results" "$report"
    # Record each independent regression, including failures. Do not use
    # process exit status alone as evidence of which cases passed.
    local rows name result
    if ! rows="$(python3 - "$local_results/$report/regressions.json" <<'PYRESULT'
import json
from pathlib import Path
import sys
try:
    d = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    cases = d['cases']
    if not isinstance(cases, list) or not cases:
        raise ValueError('missing regression cases')
    for item in cases:
        if item['status'] not in ('PASS', 'FAIL', 'BLOCKED'):
            raise ValueError('invalid regression status')
        if not isinstance(item['name'], str) or not item['name']:
            raise ValueError('invalid case name')
        print(f"{item['name']}\t{item['status']}")
except (OSError, ValueError, KeyError, TypeError) as exc:
    print('Invalid remote regression report: ' + str(exc), file=sys.stderr)
    sys.exit(1)
PYRESULT
)"; then
        record unit regressions BLOCKED "$report"
        return 1
    fi
    while IFS=$'\t' read -r name result; do
        [[ -n "$name" ]] || continue
        record unit "$name" "$result" "$report"
    done <<<"$rows"
    # The remote exit code must agree with the structured test results.
    if (( status == 0 && ( FAILED != 0 || BLOCKED != 0 ) )) ||
       (( status != 0 && FAILED == 0 && BLOCKED == 0 )); then
        echo 'ERROR: remote regression status does not agree with report' >&2
        record unit regressions FAIL "$report"
        return 1
    fi
    (( status == 0 ))
}

write_summary() {
    local suite="$1" dir stamp out rows
    dir="$REMOTE_REPO_ROOT/netlabs/results"
    mkdir -p "$dir"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    out="$(mktemp "$dir/batch-${suite}-${stamp}-XXXXXXXX.json")"
    rows="$(mktemp "$dir/.batch-records-XXXXXXXX")"
    printf '%s' "$RESULT_ROWS" >"$rows"
    if ! python3 - "$suite" "$out" "$rows" "$PASSED" "$FAILED" "$BLOCKED" <<'PYJSON'
import datetime
import json
from pathlib import Path
import sys
suite, target, source = sys.argv[1:4]
passed, failed, blocked = map(int, sys.argv[4:7])
entries = []
for raw in Path(source).read_text(encoding='utf8').splitlines():
    kind, name, status, report = raw.split('\t')
    entries.append({'kind': kind, 'name': name, 'status': status,
                    'report_id': report or None})
result = {
    'schema': 'openeigrp-netlabs-batch/v1', 'selection': suite,
    'timestamp_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'status': 'PASS' if failed == blocked == 0 else 'FAIL',
    'summary': {'passed': passed, 'failed': failed, 'blocked': blocked},
    'cases': entries,
}
Path(target).write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
PYJSON
    then
        rm -f -- "$out" "$rows"
        return 1
    fi
    rm -f -- "$rows"
    printf 'Batch report: %s\n' "$out"
}

if (( $# < 1 )); then
    usage >&2
    exit 2
fi
case "$1" in
    --help|-h)
        [[ $# == 1 ]] || exit 2
        usage
        exit 0
        ;;
    --update)
        [[ $# == 1 ]] || { usage >&2; exit 2; }
        run_image_update
        exit $?
        ;;
    --abort)
        [[ $# == 1 ]] || { usage >&2; exit 2; }
        run_abort
        exit $?
        ;;
    --list|--all|--rtp|--dual)
        [[ $# == 1 ]] || { usage >&2; exit 2; }
        requested="${1#--}"
        ;;
    --scenario)
        if [[ $# != 2 || ! "$2" =~ ^[2-6]R-[a-z][a-z0-9-]*$ ]]; then
            usage >&2
            exit 2
        fi
        requested=scenario
        target="$2"
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

discover
if [[ "$requested" == list ]]; then
    for name in "${CASE_NAMES[@]}"; do
        printf '%s\n' "$name"
    done
    exit 0
fi

selected=()
for i in "${!CASE_NAMES[@]}"; do
    if [[ "$requested" == all || \
          ( "$requested" == scenario && "${CASE_NAMES[i]}" == "$target" ) || \
          ( "$requested" == "${CASE_GROUPS[i]}" ) ]]; then
        selected+=("$i")
    fi
done
if [[ "$requested" == scenario && ${#selected[@]} -ne 1 ]]; then
    printf 'Unknown or incomplete scenario: %s\n' "$target" >&2
    exit 2
fi

if [[ "$requested" == scenario ]]; then
    remote_transport_prepare
    run_live "${selected[0]}"
    exit $?
fi

# Never run live qualification with a failing or blocked infrastructure gate.
# The selected appliance may be busy, so do not start a second session while a
# previous live case is still operating.
regressions_ok=0
if remote_transport_prepare && run_remote_regressions "$requested"; then
    regressions_ok=1
fi
if (( ! regressions_ok )); then
    printf 'BLOCKED: live scenarios not started because regressions failed\n' >&2
    for i in "${selected[@]}"; do
        record live "${CASE_NAMES[i]}" BLOCKED
    done
    if (( ${#selected[@]} == 0 )); then
        record live "${requested}-live" NOT_IMPLEMENTED
    fi
    printf '\nRegression summary: %d PASS, %d FAIL, %d BLOCKED/NOT_IMPLEMENTED\n' \
        "$PASSED" "$FAILED" "$BLOCKED"
    write_summary "$requested" || exit 1
    exit 1
fi
if (( ${#selected[@]} == 0 )); then
    if [[ "$requested" != all ]]; then
        printf 'NOT_IMPLEMENTED: no runnable %s live scenarios found\n' "$requested" >&2
        record live "${requested}-live" NOT_IMPLEMENTED
    else
        echo 'BLOCKED: no complete live scenarios were discovered' >&2
        record live all-live BLOCKED
    fi
else
    stop_batch=0
    for i in "${selected[@]}"; do
        case_name="${CASE_NAMES[i]}"
        if (( stop_batch )); then
            record live "$case_name" BLOCKED
            continue
        fi

        if run_live "$i"; then
            if (( LAST_SAFE )); then
                record live "$case_name" PASS "$LAST_RESULT"
            else
                echo "FAIL: $case_name has no verified cleanup evidence" >&2
                record live "$case_name" FAIL "$LAST_RESULT"
            fi
        else
            record live "$case_name" FAIL "$LAST_RESULT"
        fi

        # Never start another scenario if cleanup did not complete.
        if (( ! LAST_SAFE )); then
            echo 'BLOCKED: stopping batch because remote teardown was not verified' >&2
            stop_batch=1
        fi
    done
fi
printf '\nRegression summary: %d PASS, %d FAIL, %d BLOCKED/NOT_IMPLEMENTED\n' \
    "$PASSED" "$FAILED" "$BLOCKED"
write_summary "$requested" || exit 1
(( FAILED == 0 && BLOCKED == 0 ))
