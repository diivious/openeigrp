#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Exercise the real remote runner's 1R dispatch, artifact gates and reporting.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/state/runs/upload.pkg/netlabs/test/package/1R-lifecycle" \
         "$tmp/state/session" "$tmp/state/runs/upload.pkg/dist"
stage="$tmp/state/runs/upload.pkg"
cp "$root/netlabs/test/package/1R-lifecycle/"{topology.yml,scenario.yaml,driver.py} \
   "$stage/netlabs/test/package/1R-lifecycle/"
old=openeigrpd_0.5.9-diivious.dev_amd64.deb
new=openeigrpd_0.5.10-diivious.dev_amd64.deb
: >"$stage/dist/$old"
: >"$stage/dist/$new"
cat >"$tmp/bin/netlab" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "netlab $*" >>"$ACTION_LOG"
MOCK
cat >"$tmp/bin/docker" <<'MOCK'
#!/usr/bin/env bash
case "$1" in
    info) exit 0 ;;
    ps) exit 0 ;;
    image)
        [[ "$2" == inspect ]] || exit 2
        printf 'sha256:%064d\n' 0
        exit 0 ;;
esac
exit 2
MOCK
cat >"$tmp/bin/mock_python" <<'MOCK'
#!/usr/bin/env bash
[[ "$1" == */netlabs/test/package/1R-lifecycle/driver.py ]] || exit 99
seen_old=0
seen_new=0
for ((i=2;i<=$#;i++)); do
    arg="${!i}"
    case "$arg" in
        --package-old) j=$((i+1)); [[ "${!j}" == */dist/openeigrpd_0.5.9-diivious.dev_amd64.deb ]] && seen_old=1 ;;
        --package-new) j=$((i+1)); [[ "${!j}" == */dist/openeigrpd_0.5.10-diivious.dev_amd64.deb ]] && seen_new=1 ;;
        --artifacts) j=$((i+1)); artifacts="${!j}" ;;
    esac
done
if [[ "${EXPECT_NO_RELEASE:-0}" == 1 ]]; then
    (( ! seen_old && seen_new )) || exit 11
else
    (( seen_old && seen_new )) || exit 11
fi
if [[ "${SIM_BLOCKED:-0}" == 1 ]]; then
    printf '{"objectives":[{"name":"vm-prerequisites","status":"BLOCKED"}]}\n' >"$artifacts/report.json"
    exit 1
fi
if [[ "${SIM_CLEANUP_FAIL:-0}" == 1 ]]; then
    printf '{"objectives":[{"name":"lifecycle","status":"FAIL","cleanup_error":"netlab down failed"}]}\n' >"$artifacts/report.json"
    exit 1
fi
if [[ "${SIM_FAIL:-0}" == 1 ]]; then
    printf '{"objectives":[{"name":"upgrade","status":"FAIL"}]}\n' >"$artifacts/report.json"
else
    printf '{"objectives":[{"name":"lifecycle","status":"PASS"}]}\n' >"$artifacts/report.json"
fi
MOCK
chmod +x "$tmp/bin"/*
export PATH="$tmp/bin:$PATH" REMOTE_LAB_DATA="$tmp/state" \
    REMOTE_LAB_STATE="$tmp/state/session" NETLAB_PYTHON="$tmp/bin/mock_python" \
    ACTION_LOG="$tmp/actions"
runner="$root/netlabs/tools/remote/runner.sh"
image='openeigrp-package:test'
identity="sha256:$(printf '%064d' 0)"
printf '%s\n%s\n' "$image" "$identity" >"$stage/package-image.txt"
output="$(bash "$runner" uut-start "$stage" 1R-lifecycle package "$old" "$new")"
grep -q '^PASS: 1R-lifecycle' <<<"$output"
report="$(sed -n 's/^REPORT_ID=//p' <<<"$output")"
python3 - "$tmp/state/results/$report/result.json" <<'PY'
import json
import sys
result = json.load(open(sys.argv[1]))
assert result['router_count'] == 1, result
assert result['group'] == 'package', result
assert result['status'] == result['cleanup'] == 'PASS', result
assert result['images'] is None, result
assert result['required_objective_statuses'] == ['PASS'], result
PY
[[ ! -e "$tmp/state/session/session" ]]
# A single staged development package is valid when no release exists.
mkdir -p "$stage/netlabs/test/package/1R-lifecycle" "$stage/dist"
cp "$root/netlabs/test/package/1R-lifecycle/"{topology.yml,scenario.yaml,driver.py} \
   "$stage/netlabs/test/package/1R-lifecycle/"
: >"$stage/dist/$new"
printf '%s\n%s\n' "$image" "$identity" >"$stage/package-image.txt"
output="$(EXPECT_NO_RELEASE=1 bash "$runner" uut-start "$stage" 1R-lifecycle package '' "$new")"
grep -q '^PASS: 1R-lifecycle' <<<"$output"
report="$(sed -n 's/^REPORT_ID=//p' <<<"$output")"
python3 - "$tmp/state/results/$report/result.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1]))
assert result['status'] == 'PASS', result
assert result['cleanup'] == 'PASS', result
assert result['required_objective_statuses'] == ['PASS'], result
PY
[[ ! -e "$tmp/state/session/session" ]]
# Failure in the guest must not become PASS even if the remote driver exits 0.
mkdir -p "$stage/netlabs/test/package/1R-lifecycle" "$stage/dist"
cp "$root/netlabs/test/package/1R-lifecycle/"{topology.yml,scenario.yaml,driver.py} \
   "$stage/netlabs/test/package/1R-lifecycle/"
: >"$stage/dist/$old"; : >"$stage/dist/$new"
printf '%s\n%s\n' "$image" "$identity" >"$stage/package-image.txt"
if SIM_FAIL=1 bash "$runner" uut-start "$stage" 1R-lifecycle package "$old" "$new" >"$tmp/failed.log"; then
    echo 'FAIL: objective failure was accepted' >&2
    exit 1
fi
report="$(sed -n 's/^REPORT_ID=//p' "$tmp/failed.log")"
python3 - "$tmp/state/results/$report/result.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1]))
assert result['status'] == 'FAIL', result
assert result['cleanup'] == 'PASS', result
PY
# Missing container prerequisites are BLOCKED with a report and confirmed teardown.
mkdir -p "$stage/netlabs/test/package/1R-lifecycle" "$stage/dist"
cp "$root/netlabs/test/package/1R-lifecycle/"{topology.yml,scenario.yaml,driver.py} \
   "$stage/netlabs/test/package/1R-lifecycle/"
: >"$stage/dist/$new"
printf '%s\n%s\n' "$image" "$identity" >"$stage/package-image.txt"
if EXPECT_NO_RELEASE=1 SIM_BLOCKED=1 bash "$runner" uut-start "$stage" 1R-lifecycle package '' "$new" >"$tmp/blocked.log"; then
    echo 'FAIL: unavailable container runtime was accepted' >&2
    exit 1
fi
report="$(sed -n 's/^REPORT_ID=//p' "$tmp/blocked.log")"
[[ -n "$report" ]]
python3 - "$tmp/state/results/$report/result.json" <<'PYBLOCK'
import json
import sys
result = json.load(open(sys.argv[1]))
assert result['status'] == 'BLOCKED', result
assert result['cleanup'] == 'PASS', result
assert result['required_objective_statuses'] == ['BLOCKED'], result
PYBLOCK
[[ ! -e "$tmp/state/session/session" ]]
# Refuse missing second .deb before writing any remote session state.
if bash "$runner" uut-start "$stage" 1R-lifecycle package "$old" missing.deb >"$tmp/invalid.log" 2>&1; then
    echo 'FAIL: missing package was accepted' >&2
    exit 1
fi
[[ ! -e "$tmp/state/session/session" ]]
# A guest teardown error must keep the remote session for recovery.
mkdir -p "$stage/netlabs/test/package/1R-lifecycle" "$stage/dist"
cp "$root/netlabs/test/package/1R-lifecycle/"{topology.yml,scenario.yaml,driver.py} \
   "$stage/netlabs/test/package/1R-lifecycle/"
: >"$stage/dist/$new"
printf '%s\n%s\n' "$image" "$identity" >"$stage/package-image.txt"
if EXPECT_NO_RELEASE=1 SIM_CLEANUP_FAIL=1 bash "$runner" uut-start "$stage" \
        1R-lifecycle package '' "$new" >"$tmp/unsafe.log" 2>&1; then
    echo 'FAIL: package cleanup error was accepted' >&2
    exit 1
fi
report="$(sed -n 's/^REPORT_ID=//p' "$tmp/unsafe.log")"
python3 - "$tmp/state/results/$report/result.json" <<'PYCLEAN'
import json, sys
result = json.load(open(sys.argv[1]))
assert result['cleanup'] == 'FAIL', result
assert result['status'] == 'FAIL', result
PYCLEAN
[[ -f "$tmp/state/session/session" ]]
echo 'PASS: 1R package scenario dispatch, required-objective failure, cleanup, and artifact gate'
