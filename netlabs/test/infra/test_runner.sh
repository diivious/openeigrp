#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Mocked runner integration: dispatch 2R-6R, orphan detection, report, recovery.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
runner="$root/netlabs/tools/remote/runner.sh"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/state/runs" "$tmp/state/session"

cat >"$tmp/bin/docker" <<'MOCK'
#!/usr/bin/env bash
if [[ "${DOCKER_FAIL:-0}" == 1 ]]; then exit 9; fi
if [[ "${1:-}" == ps ]]; then
    if [[ -f "$DOCKER_NODES" ]]; then cat "$DOCKER_NODES"; fi
fi
MOCK
cat >"$tmp/bin/netlab" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$1" >>"$LAB_ACTIONS"
if [[ "${1:-}" == down && "${FAIL_DOWN:-0}" == 1 ]]; then
    printf '%s\n' 'clab-eigrp-five-router-r5' >"$DOCKER_NODES"
    exit 1
fi
MOCK
cat >"$tmp/bin/mock_python" <<'MOCK'
#!/usr/bin/env bash
if [[ "${1:-}" == */netlabs/tools/remote/images.py ]]; then
    [[ "${2:-}" == prepare ]] || exit 2
    [[ "${SELECT_FAIL:-0}" == 0 ]] || exit 55
    printf '{"image":"ghcr.io/diivious/openeigrp-frr:0.4.0","digests":["sha256:test"]}\n' >"$4"
    exit 0
fi
for ((i=1;i<=$#;i++)); do
    if [[ "${!i}" == --artifacts ]]; then
        j=$((i+1))
        if [[ "${STATUS_FAIL:-0}" == 1 ]]; then
            printf '{"ipv4":{"status":"FAIL"},"ipv6":{"status":"PASS"}}\n' >"${!j}/report.json"
        else
            printf '{"ipv4":{"status":"PASS"},"ipv6":{"status":"PASS"}}\n' >"${!j}/report.json"
        fi
    fi
done
if [[ "${DRIVER_FAIL:-0}" == 1 ]]; then exit 42; fi
MOCK
chmod +x "$tmp/bin/"*
export PATH="$tmp/bin:$PATH"
export REMOTE_LAB_DATA="$tmp/state"
export REMOTE_LAB_STATE="$tmp/state/session"
export NETLAB_PYTHON="$tmp/bin/mock_python"
export LAB_ACTIONS="$tmp/actions"
export DOCKER_NODES="$tmp/nodes"

create_scenario() {
    local count="$1" dir
    dir="$tmp/state/runs/upload.${count}/netlabs/uut/${count}R-case"
    mkdir -p "$dir" "$tmp/state/runs/upload.${count}/netlabs/tools/remote"
    printf 'name: eigrp-%s-router\nnodes:\n' "$count" >"$dir/topology.yml"
    printf 'name: %sR-case\n' "$count" >"$dir/scenario.yaml"
    printf '# mocked driver\n' >"${dir%/*}/${count}R-case.py"
    printf '# mocked image selector\n' >"$tmp/state/runs/upload.${count}/netlabs/tools/remote/images.py"
}

for count in 2 3 4 5 6; do
    create_scenario "$count"
    out="$(bash "$runner" uut-start "$tmp/state/runs/upload.${count}" "${count}R-case")"
    grep -Fq "PASS: ${count}R-case" <<<"$out"
    report="$(sed -n 's/^REPORT_ID=//p' <<<"$out")"
    python3 - "$tmp/state/results/$report/result.json" "$count" <<'PY'
import json
from pathlib import Path
import sys

result = json.loads(Path(sys.argv[1]).read_text())
assert result['status'] == 'PASS', result
assert result['cleanup'] == 'PASS', result
assert result['router_count'] == int(sys.argv[2]), result
assert result['images']['digests'] == ['sha256:test'], result
assert result['driver_report']['ipv4']['status'] == 'PASS', result
PY
    [[ ! -f "$tmp/state/session/session" ]]
done
# If the runner selects the appliance's real netlab rather than the test
# wrapper, its teardown calls can silently pass without testing isolation.
if [[ $(grep -c '^down$' "$LAB_ACTIONS" 2>/dev/null || :) -ne 5 ]]; then
    echo 'FAIL: runner bypassed mocked netlab teardown' >&2
    exit 1
fi
echo 'PASS: 2R-6R dispatch, structured results, image evidence, and teardown'

# Grouped suites dispatch the selected driver from netlabs/test/<group>/.
groupstage="$tmp/state/runs/upload.grouped"
groupdir="$groupstage/netlabs/test/dual/4R-feasibility"
mkdir -p "$groupdir" "$groupstage/netlabs/tools/remote"
printf 'name: eigrp-dual-feasibility\n' >"$groupdir/topology.yml"
printf 'name: 4R-feasibility\n' >"$groupdir/scenario.yaml"
printf '# grouped driver\n' >"$groupdir/driver.py"
printf '# mock image selector\n' >"$groupstage/netlabs/tools/remote/images.py"
out="$(bash "$runner" uut-start "$groupstage" 4R-feasibility dual)"
grep -Fq 'PASS: 4R-feasibility' <<<"$out"
report="$(sed -n 's/^REPORT_ID=//p' <<<"$out")"
python3 - "$tmp/state/results/$report/result.json" <<'PYGROUP'
import json
from pathlib import Path
import sys

result = json.loads(Path(sys.argv[1]).read_text())
assert result['group'] == 'dual', result
assert result['router_count'] == 4, result
assert result['cleanup'] == 'PASS', result
PYGROUP
[[ ! -f "$tmp/state/session/session" ]]
echo 'PASS: grouped DUAL UUT dispatch preserves structured result and cleanup'

# A no-state orphan of an arbitrary 6R topology must block new work.
printf '%s\n' clab-eigrp-six-router-r6 >"$DOCKER_NODES"
create_scenario 2
if bash "$runner" uut-start "$tmp/state/runs/upload.2" 2R-case >"$tmp/orphan.log" 2>&1; then
    echo 'FAIL: orphan allowed' >&2
    exit 1
fi
grep -Fq 'Orphaned OpenEIGRP containerlab nodes detected' "$tmp/orphan.log"
# Legacy instructional lab names must remain protected, including bare oeig-lab.
printf '%s\n' clab-oeig-lab-r1 >"$DOCKER_NODES"
if bash "$runner" uut-start "$tmp/state/runs/upload.2" 2R-case >"$tmp/legacy-orphan.log" 2>&1; then
    echo 'FAIL: legacy instructional orphan allowed' >&2
    exit 1
fi
grep -Fq 'Orphaned OpenEIGRP containerlab nodes detected' "$tmp/legacy-orphan.log"
: >"$DOCKER_NODES"
if DOCKER_FAIL=1 bash "$runner" status >"$tmp/status.log" 2>&1; then
    : # status does not inspect resources until asked to create a new session
fi
if DOCKER_FAIL=1 bash "$runner" uut-start "$tmp/state/runs/upload.2" 2R-case >"$tmp/docker-fail.log" 2>&1; then
    echo 'FAIL: Docker inspection failure accepted' >&2
    exit 1
fi
grep -Fq 'Cannot verify orphaned' "$tmp/docker-fail.log"
echo 'PASS: arbitrary topology orphan and docker inspection error block new runs'

# Image selection failure is BLOCKED, not an interoperability PASS.
if SELECT_FAIL=1 bash "$runner" uut-start "$tmp/state/runs/upload.2" 2R-case >"$tmp/image-fail.log" 2>&1; then
    echo 'FAIL: image selection reported success' >&2
    exit 1
fi
report="$(sed -n 's/^REPORT_ID=//p' "$tmp/image-fail.log")"
python3 - "$tmp/state/results/$report/result.json" <<'PY'
import json
from pathlib import Path
import sys

result = json.loads(Path(sys.argv[1]).read_text())
assert result['status'] == 'BLOCKED', result
assert result['cleanup'] == 'PASS', result
PY
[[ ! -f "$tmp/state/session/session" ]]
echo 'PASS: missing image is BLOCKED and still leaves an artifact bundle'

# A failed teardown retains the session; explicit recovery can clear it.
create_scenario 5
if FAIL_DOWN=1 bash "$runner" uut-start "$tmp/state/runs/upload.5" 5R-case >"$tmp/teardown.log" 2>&1; then
    echo 'FAIL: cleanup failure reported success' >&2
    exit 1
fi
[[ -f "$tmp/state/session/session" ]]
report="$(sed -n 's/^REPORT_ID=//p' "$tmp/teardown.log")"
python3 - "$tmp/state/results/$report/result.json" <<'PY'
import json
from pathlib import Path
import sys

result = json.loads(Path(sys.argv[1]).read_text())
assert result['status'] == 'FAIL', result
assert result['cleanup'] == 'FAIL', result
PY
: >"$DOCKER_NODES"
bash "$runner" stop | grep -F 'Stale UUT session cleared'
[[ ! -f "$tmp/state/session/session" ]]
echo 'PASS: failed cleanup retains session until recovery'

# An exit-zero driver cannot override its own required failing assertion.
create_scenario 3
if STATUS_FAIL=1 bash "$runner" uut-start "$tmp/state/runs/upload.3" 3R-case >"$tmp/objective.log" 2>&1; then
    echo 'FAIL: required failing objective became PASS' >&2
    exit 1
fi
report="$(sed -n 's/^REPORT_ID=//p' "$tmp/objective.log")"
python3 - "$tmp/state/results/$report/result.json" <<'PYTEST'
import json
from pathlib import Path
import sys

result = json.loads(Path(sys.argv[1]).read_text())
assert result['status'] == 'FAIL', result
assert result['driver_exit_code'] == 0, result
assert result['required_objective_statuses'] == ['FAIL', 'PASS'], result
PYTEST
echo 'PASS: required driver assertions cannot be masked by a zero exit code'

# If fault restoration fails, keep the namespace for operator recovery.
create_scenario 4
stage="$tmp/state/runs/upload.4"
mkdir -p "$stage/netlabs/common" "$stage/artifacts"
printf 'import sys; sys.exit(1)\n' >"$stage/netlabs/common/network_fault.py"
printf '{}' >"$stage/artifacts/network.fault.json"
before="$(grep -c '^down$' "$tmp/actions" || :)"
if bash "$runner" uut-start "$stage" 4R-case >"$tmp/fault.log" 2>&1; then
    echo 'FAIL: restoration failure became PASS' >&2
    exit 1
fi
[[ -f "$tmp/state/session/session" && -d "$stage" ]]
[[ "$(grep -c '^down$' "$tmp/actions" || :)" -eq "$before" ]]
report="$(sed -n 's/^REPORT_ID=//p' "$tmp/fault.log")"
python3 - "$tmp/state/results/$report/result.json" <<'PYTEST'
import json, sys
x=json.load(open(sys.argv[1])); assert x['status']=='FAIL'; assert x['cleanup']=='FAIL'
PYTEST
printf 'import sys; sys.exit(0)\n' >"$stage/netlabs/common/network_fault.py"
bash "$runner" stop | grep -F 'Stale UUT session cleared'
[[ ! -f "$tmp/state/session/session" ]]
[[ "$(grep -c '^down$' "$tmp/actions" || :)" -gt "$before" ]]
echo 'PASS: failed fault cleanup preserves namespaces until recoverable'

# Client discovery only includes complete and supported 2R-6R fixtures.
client="$tmp/client"
mkdir -p "$client/netlabs/tools/remote" "$client/netlabs/uut"
cp "$root/netlabs/tools/remote-uut.sh" "$client/netlabs/tools/remote-uut.sh"
cat >"$client/netlabs/tools/remote/common.sh" <<'MOCK'
#!/usr/bin/env bash
REMOTE_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MOCK
for count in 2 3 4 5 6 7; do
    mkdir -p "$client/netlabs/uut/${count}R-case"
    touch "$client/netlabs/uut/${count}R-case/scenario.yaml"
    touch "$client/netlabs/uut/${count}R-case/topology.yml"
    touch "$client/netlabs/uut/${count}R-case.py"
done
rm "$client/netlabs/uut/4R-case/topology.yml"
actual="$(bash "$client/netlabs/tools/remote-uut.sh" --list)"
expected=$'2R-case\n3R-case\n5R-case\n6R-case'
[[ "$actual" == "$expected" ]] || {
    printf 'FAIL: discovery expected <%s> got <%s>\n' "$expected" "$actual" >&2
    exit 1
}
if bash "$client/netlabs/tools/remote-uut.sh" --scenario 7R-case >/dev/null 2>&1; then
    echo 'FAIL: 7R scenario was accepted' >&2
    exit 1
fi
echo 'PASS: client discovery filters incomplete fixtures and invalid router counts'

# Server-side regression execution runs on Linux and returns a durable report.
reg_stage="$tmp/state/runs/upload.regressions"
reg_dir="$reg_stage/netlabs/test/infra"
mkdir -p "$reg_dir" "$reg_stage/netlabs/tools/remote"
cp "$root/netlabs/tools/remote/regressions.py" "$reg_stage/netlabs/tools/remote/regressions.py"
printf '#!/bin/sh\nexit 0\n' >"$reg_dir/test_pass.sh"
remote_python="$(command -v python3)"
out="$(NETLAB_PYTHON="$remote_python" bash "$runner" regressions "$reg_stage" dual)"
report="$(sed -n 's/^REPORT_ID=//p' <<<"$out")"
python3 - "$tmp/state/results/$report/result.json" <<'PYREPORT'
import json
from pathlib import Path
import sys

result = json.loads(Path(sys.argv[1]).read_text())
assert result['status'] == 'PASS', result
assert result['cleanup'] == 'PASS', result
assert [case['status'] for case in result['cases']] == ['PASS'], result
PYREPORT
[[ ! -d "$reg_stage" && ! -f "$tmp/state/session/session" ]]
reg_stage="$tmp/state/runs/upload.regressionsfail"
reg_dir="$reg_stage/netlabs/test/infra"
mkdir -p "$reg_dir" "$reg_stage/netlabs/tools/remote"
cp "$root/netlabs/tools/remote/regressions.py" "$reg_stage/netlabs/tools/remote/regressions.py"
printf '#!/bin/sh\nexit 19\n' >"$reg_dir/test_fail.sh"
if NETLAB_PYTHON="$remote_python" bash "$runner" regressions "$reg_stage" dual >"$tmp/regressionsfail.log"; then
    echo 'FAIL: regression failure was incorrectly accepted' >&2
    exit 1
fi
report="$(sed -n 's/^REPORT_ID=//p' "$tmp/regressionsfail.log")"
python3 - "$tmp/state/results/$report/result.json" <<'PYREPORT'
import json
from pathlib import Path
import sys

result = json.loads(Path(sys.argv[1]).read_text())
assert result['status'] == 'FAIL', result
assert result['cleanup'] == 'PASS', result
assert [case['status'] for case in result['cases']] == ['FAIL'], result
PYREPORT
[[ ! -d "$reg_stage" && ! -f "$tmp/state/session/session" ]]
echo 'PASS: remote regression action reports failures and cleans staged fixtures'
