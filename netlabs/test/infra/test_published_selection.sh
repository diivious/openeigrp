#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Verify default published selection and exact X.Y pin without local artifacts.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
repo="$tmp/repo"
mkdir -p "$repo/netlabs/tools/remote" "$repo/netlabs/test/rtp/2R-case"
cp "$root/netlabs/tools/remote-uut.sh" "$repo/netlabs/tools/"
cat >"$repo/netlabs/tools/remote/common.sh" <<'MOCK_COMMON'
REMOTE_REPO_ROOT="${MOCK_REPO}"
REMOTE_HOST=mock-appliance
remote_transport_prepare() { return 0; }
remote_ssh() {
    case "$1" in
        *'docker info'*) return 0 ;;
        *'docker image ls'*) printf '%s\n' \
            'ghcr.io/diivious/openeigrp-frr:0.5.0' \
            'ghcr.io/diivious/openeigrp-frr:0.6.0' \
            'ghcr.io/diivious/openeigrp-frr:0.5.0-amd64' ;;
        *'docker image inspect'*) printf 'linux/amd64\n' ;;
        *) echo "unexpected remote_ssh: $1" >&2; return 1 ;;
    esac
}
remote_new_stage() { echo 'STAGE_REACHED'; return 1; }
MOCK_COMMON
for file in driver.py topology.yml scenario.yaml; do
    printf 'name: fixture\n' >"$repo/netlabs/test/rtp/2R-case/$file"
done
export MOCK_REPO="$repo"
run() { bash "$repo/netlabs/tools/remote-uut.sh" "$@" >"$tmp/out" 2>&1; }
if run --scenario 2R-case; then exit 1; fi
grep -q 'Selected published image: ghcr.io/diivious/openeigrp-frr:0.6.0' "$tmp/out"
if run --tag 0.5 --scenario 2R-case; then exit 1; fi
grep -q 'Selected published image: ghcr.io/diivious/openeigrp-frr:0.5.0' "$tmp/out"
if grep -q 'Selected published image: ghcr.io/diivious/openeigrp-frr:0.6.0' "$tmp/out"; then exit 1; fi
if run --tag 0.7 --scenario 2R-case; then exit 1; fi
grep -q 'published release 0.7 is not installed' "$tmp/out"
! grep -q 'STAGE_REACHED' "$tmp/out"
# A published FRR release is not a substitute for a missing published .deb.
mkdir -p "$repo/netlabs/test/package/1R-lifecycle"
for file in driver.py topology.yml scenario.yaml; do
    printf 'name: fixture\n' >"$repo/netlabs/test/package/1R-lifecycle/$file"
done
cat >"$repo/netlabs/tools/package-artifacts.py" <<'PYMOCK'
#!/usr/bin/env python3
print('null')
PYMOCK
if run --tag 0.5 --scenario 1R-lifecycle; then exit 1; fi
grep -q 'BLOCKED: no published Debian package for rel-0.5' "$tmp/out"
grep -q 'BLOCKED: no published Debian artifact for 1R-lifecycle' "$tmp/out"
! grep -q 'Selected local build' "$tmp/out"
# Even without a published .deb, --all still runs independent container cases.
cat >>"$repo/netlabs/tools/remote/common.sh" <<'MOCK_RUN'
remote_new_stage() { printf '/home/netlab-data/runs/upload.mock\n'; }
remote_stage() { :; }
remote_install_runner() { :; }
remote_ssh() {
    case "$1" in
        *"docker info"*) return 0 ;;
        *"docker image ls"*) printf '%s\n' \
            'ghcr.io/diivious/openeigrp-frr:0.5.0' \
            'ghcr.io/diivious/openeigrp-frr:0.6.0' ;;
        *"docker image inspect"*) printf 'linux/amd64\n' ;;
        *"release-image.txt"*) return 0 ;;
        *) return 1 ;;
    esac
}
remote_run() {
    case "$2" in
        regressions) printf 'REPORT_ID=uut-regression\n' ;;
        uut-start)
            [[ "$3" == 2R-case ]] || exit 80
            printf 'REPORT_ID=uut-container\n' ;;
        *) return 1 ;;
    esac
}
remote_fetch_report() {
    mkdir -p "$2/$1"
    case "$1" in
        uut-regression)
            printf '{"cases":[{"name":"infra","status":"PASS"}]}\n' >"$2/$1/regressions.json" ;;
        uut-container)
            printf '{"cleanup":"PASS"}\n' >"$2/$1/result.json" ;;
        *) return 1 ;;
    esac
}
MOCK_RUN
if run --all; then echo 'FAIL: missing published package qualified as PASS' >&2; exit 1; fi
grep -q 'Running live scenario: 2R-case' "$tmp/out"
! grep -q 'Running live scenario: 1R-lifecycle' "$tmp/out"
grep -Fxq '    2 PASS' "$tmp/out"
grep -Fxq '    0 FAIL' "$tmp/out"
grep -Fxq '    1 BLOCKED/NOT_IMPLEMENTED' "$tmp/out"
summary="$(sed -n 's/^Batch report: //p' "$tmp/out")"
python3 - "$summary" <<'PYCHECK'
import json
from pathlib import Path
import sys
batch = json.loads(Path(sys.argv[1]).read_text())
assert batch['summary'] == {'passed': 2, 'failed': 0, 'blocked': 1}, batch
assert {(c['name'], c['status']) for c in batch['cases'] if c['kind'] == 'live'} == {
    ('2R-case', 'PASS'), ('1R-lifecycle', 'BLOCKED')}
PYCHECK
echo 'PASS: exact published selection; missing .deb blocks package, not containers'
