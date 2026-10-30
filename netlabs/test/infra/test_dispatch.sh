#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Exercise real remote-uut selection, grouped staging, and batch safety.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
client="$tmp/client"
mkdir -p "$client/netlabs/tools/remote" "$client/netlabs/uut/2R-neighbor" \
    "$client/netlabs/test/rtp/3R-ack-loss" "$client/netlabs/test/dual/4R-feasibility" \
    "$client/netlabs/test/infra"
cp "$root/netlabs/tools/remote-uut.sh" "$client/netlabs/tools/remote-uut.sh"
printf 'name: 2R-neighbor\ngroup: rtp\n' >"$client/netlabs/uut/2R-neighbor/scenario.yaml"
printf 'name: eigrp-neighbor\n' >"$client/netlabs/uut/2R-neighbor/topology.yml"
printf '# legacy driver\n' >"$client/netlabs/uut/2R-neighbor.py"
for group_case in rtp/3R-ack-loss dual/4R-feasibility; do
    path="$client/netlabs/test/$group_case"
    printf 'name: %s\n' "${group_case##*/}" >"$path/scenario.yaml"
    printf 'name: eigrp-test\n' >"$path/topology.yml"
    printf '# grouped driver\n' >"$path/driver.py"
done
# Incomplete scenarios must not appear in --list or any suite.
mkdir -p "$client/netlabs/test/dual/5R-incomplete"
printf 'name: 5R-incomplete\n' >"$client/netlabs/test/dual/5R-incomplete/scenario.yaml"
for dir in infra rtp dual; do
    mkdir -p "$client/netlabs/test/$dir"
    cat >"$client/netlabs/test/$dir/test_smoke.sh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "${BASH_SOURCE[0]}" >>"$MOCK_LOCAL_LOG"
MOCK
done
cat >"$client/netlabs/tools/remote/common.sh" <<'MOCK'
#!/usr/bin/env bash
REMOTE_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
REMOTE_HOST=mock-appliance
remote_transport_prepare() { :; }
remote_new_stage() { printf '/home/netlab-data/runs/upload.mock\n'; }
remote_stage() { printf 'stage %s\n' "$*" >>"$MOCK_REMOTE_LOG"; }
remote_install_runner() { :; }
remote_run() {
    printf 'run %s\n' "$*" >>"$MOCK_REMOTE_LOG"
    case "$2" in
        regressions)
            printf '%s\n' "$3" >"$MOCK_SUITE"
            printf 'REPORT_ID=uut-regressions\n'
            [[ -z "${MOCK_REGRESSION_FAIL:-}" ]] || return 1
            ;;
        uut-start)
            case "$3" in
                2R-neighbor|3R-ack-loss|4R-feasibility) ;;
                *) return 2 ;;
            esac
            printf 'REPORT_ID=uut-%s\n' "$3"
            if [[ "$3" == "${MOCK_FAILED_CASE:-}" ]]; then
                echo "FAIL: $3"
                return 42
            fi
            printf 'PASS: %s\n' "$3"
            ;;
        *) return 2 ;;
    esac
}
remote_fetch_report() {
    mkdir -p "$2/$1"
    if [[ "$1" == uut-regressions ]]; then
        case "$(cat "$MOCK_SUITE")" in
            all) groups='infra rtp dual' ;;
            rtp) groups='infra rtp' ;;
            dual) groups='infra dual' ;;
            *) return 2 ;;
        esac
        python3 - "$2/$1/regressions.json" "$groups" "${MOCK_REGRESSION_FAIL:-}" <<'PYREG'
import json
from pathlib import Path
import sys

out, groups, failed = sys.argv[1:]
cases = [
    {'name': group, 'status': 'FAIL' if group == failed else 'PASS'}
    for group in groups.split()
]
Path(out).write_text(json.dumps({
    'status': 'FAIL' if failed else 'PASS',
    'cases': cases,
}))
PYREG
        return 0
    fi
    if [[ "$1" == "uut-${MOCK_DIRTY_CASE:-}" ]]; then
        printf '{"cleanup":"FAIL"}\n' >"$2/$1/result.json"
    else
        printf '{"cleanup":"PASS"}\n' >"$2/$1/result.json"
    fi
}
MOCK

export MOCK_LOCAL_LOG="$tmp/local.log"
export MOCK_REMOTE_LOG="$tmp/remote.log"
export MOCK_SUITE="$tmp/suite.txt"
: >"$MOCK_REMOTE_LOG"
: >"$MOCK_LOCAL_LOG"
list="$(bash "$client/netlabs/tools/remote-uut.sh" --list)"
[[ "$list" == $'2R-neighbor\n3R-ack-loss\n4R-feasibility' ]] || {
    echo "FAIL: discovery returned: $list" >&2
    exit 1
}
echo 'PASS: legacy and grouped case discovery with incomplete-case filtering'

bash "$client/netlabs/tools/remote-uut.sh" --scenario 4R-feasibility >"$tmp/single.log"
[[ ! -s "$MOCK_LOCAL_LOG" ]]
grep -Fq 'uut-start 4R-feasibility dual' "$MOCK_REMOTE_LOG"
grep -Fq 'netlabs/test/dual/4R-feasibility' "$MOCK_REMOTE_LOG"
echo 'PASS: single grouped case staging and dispatch without local regressions'

: >"$MOCK_REMOTE_LOG"; : >"$MOCK_LOCAL_LOG"
bash "$client/netlabs/tools/remote-uut.sh" --rtp >"$tmp/rtp.log"
[[ $(grep -c 'run .*uut-start' "$MOCK_REMOTE_LOG") == 2 ]]
grep -Fq 'uut-start 2R-neighbor' "$MOCK_REMOTE_LOG"
grep -Fq 'uut-start 3R-ack-loss rtp' "$MOCK_REMOTE_LOG"
! grep -Fq 'uut-start 4R-feasibility' "$MOCK_REMOTE_LOG"
grep -Fq 'regressions rtp' "$MOCK_REMOTE_LOG"
[[ ! -s "$MOCK_LOCAL_LOG" ]]
echo 'PASS: --rtp runs only RTP unit and live cases'

: >"$MOCK_REMOTE_LOG"; : >"$MOCK_LOCAL_LOG"
bash "$client/netlabs/tools/remote-uut.sh" --dual >"$tmp/dual.log"
[[ $(grep -c 'run .*uut-start' "$MOCK_REMOTE_LOG") == 1 ]]
grep -Fq 'uut-start 4R-feasibility dual' "$MOCK_REMOTE_LOG"
grep -Fq 'regressions dual' "$MOCK_REMOTE_LOG"
[[ ! -s "$MOCK_LOCAL_LOG" ]]
echo 'PASS: --dual runs only DUAL unit and live cases'

: >"$MOCK_REMOTE_LOG"; : >"$MOCK_LOCAL_LOG"
MOCK_FAILED_CASE=2R-neighbor bash "$client/netlabs/tools/remote-uut.sh" --all >"$tmp/all.log" 2>&1 || rc=$?
[[ "${rc:-0}" != 0 ]]
[[ $(grep -c 'run .*uut-start' "$MOCK_REMOTE_LOG") == 3 ]]
[[ ! -s "$MOCK_LOCAL_LOG" ]]
grep -Fq 'regressions all' "$MOCK_REMOTE_LOG"
# Report names have random suffixes; sorting them is not execution order.
# Read the exact result emitted by this invocation.
first_report="$(sed -n 's/^Batch report: //p' "$tmp/all.log")"
if [[ ! -f "$first_report" ]]; then
    echo 'FAIL: no batch result for first run' >&2
    exit 1
fi
python3 - "$first_report" <<'PY'
import json
from pathlib import Path
import sys

result = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
assert result['summary'] == {'passed': 5, 'failed': 1, 'blocked': 0}, result
assert [case['name'] for case in result['cases'] if case['kind'] == 'live'] == [
    '2R-neighbor', '3R-ack-loss', '4R-feasibility',
], result
PY
echo 'PASS: --all preserves failure and continues independent live cases'

# Even an older result with a lexicographically larger filename must not
# override the report emitted by the current invocation.
cp "$first_report" "$client/netlabs/results/batch-all-20991231T235959Z-zzzzzzzz.json"

# Failed required infrastructure regressions block every live case.
: >"$MOCK_REMOTE_LOG"; : >"$MOCK_LOCAL_LOG"
if MOCK_REGRESSION_FAIL=infra \
        bash "$client/netlabs/tools/remote-uut.sh" --all >"$tmp/unit-fail.log" 2>&1; then
    echo 'FAIL: failed infrastructure gate launched live scenarios' >&2; exit 1
fi
[[ $(grep -c 'run .*regressions all' "$MOCK_REMOTE_LOG") -eq 1 ]]
! grep -Fq 'uut-start' "$MOCK_REMOTE_LOG"
grep -Fq 'BLOCKED: live scenarios not started because regressions failed' "$tmp/unit-fail.log"
gate_report="$(sed -n 's/^Batch report: //p' "$tmp/unit-fail.log")"
if [[ ! -f "$gate_report" ]]; then
    echo 'FAIL: no batch result for gated run' >&2
    exit 1
fi
python3 - "$gate_report" <<'PYGATE'
import json, sys
from pathlib import Path
result = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
assert result['summary'] == {'passed': 2, 'failed': 1, 'blocked': 3}, result
PYGATE
echo 'PASS: failing infrastructure gate prevents untrustworthy live qualification'

: >"$MOCK_REMOTE_LOG"; : >"$MOCK_LOCAL_LOG"
if MOCK_DIRTY_CASE=3R-ack-loss bash "$client/netlabs/tools/remote-uut.sh" --rtp >"$tmp/dirty.log" 2>&1; then
    echo 'FAIL: unclean remote report accepted' >&2; exit 1
fi
grep -Fq 'BLOCKED: stopping batch' "$tmp/dirty.log"
[[ $(grep -c 'run .*uut-start' "$MOCK_REMOTE_LOG") == 2 ]]
echo 'PASS: batch stops when cleanup was not verified'

# Empty DUAL is NOT_IMPLEMENTED, not a successful qualification run.
rm -rf -- "$client/netlabs/test/dual/4R-feasibility"
if bash "$client/netlabs/tools/remote-uut.sh" --dual >"$tmp/empty.log" 2>&1; then
    echo 'FAIL: empty DUAL run was successful' >&2
    exit 1
fi
grep -Fq 'NOT_IMPLEMENTED: no runnable dual live scenarios' "$tmp/empty.log"
echo 'PASS: absent DUAL cases are not reported as PASS'
