#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Validate exact local/published selection, checksums, and no test-time builds.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
repo="$tmp/repo"
mkdir -p "$repo/netlabs/tools/remote" "$repo/netlabs/test/package/1R-lifecycle" \
    "$repo/netlabs/test/rtp/2R-case" "$repo/dist" "$tmp/bin"
cp "$root/netlabs/tools/remote-uut.sh" "$repo/netlabs/tools/"
cp "$root/netlabs/tools/package-artifacts.py" "$repo/netlabs/tools/"
cp "$root/netlabs/tools/remote/common.sh" "$repo/netlabs/tools/remote/"
for dir in "$repo/netlabs/test/package/1R-lifecycle" "$repo/netlabs/test/rtp/2R-case"; do
    echo 'name: mock' >"$dir/scenario.yaml"
    echo 'name: mock' >"$dir/topology.yml"
    echo '# dummy scenario' >"$dir/driver.py"
done
cat >"$tmp/config" <<'CFG'
REMOTE_HOST=mock-appliance
REMOTE_USER=netlab
REMOTE_SSH_PORT=22
CFG
cat >"$tmp/bin/ssh" <<'MOCK_SSH'
#!/bin/sh
echo 'ssh: connect to host mock-appliance port 22: Connection refused' >&2
exit 255
MOCK_SSH
cat >"$tmp/bin/docker" <<'MOCK_DOCKER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$MOCK_DOCKER_LOG"
case "$1" in
    info) exit 0 ;;
    image)
        [[ "$2" == inspect && "$3" == --format ]] || exit 2
        name="${*: -1}"
        if [[ "${FAIL_PACKAGE_IMAGE:-0}" == 1 && "$name" == openeigrp-package:netlab ]]; then
            exit 1
        fi
        case "$name" in
            openeigrp-package:netlab) id="$(printf 'b%.0s' {1..64})" ;;
            openeigrp:0.5.9-test.dev-amd64) id="$(printf 'a%.0s' {1..64})" ;;
            *) exit 1 ;;
        esac
        arch=amd64
        if [[ "${BAD_PACKAGE_ARCH:-0}" == 1 && "$name" == openeigrp-package:netlab ]]; then
            arch=arm64
        fi
        format="$4"
        if [[ "$format" == *'{{.Id}}'* && "$format" == *'{{.Architecture}}'* ]]; then
            printf 'sha256:%s linux/%s\n' "$id" "$arch"
        elif [[ "$format" == *'{{.Architecture}}'* ]]; then
            printf 'linux/%s\n' "$arch"
        else
            printf 'sha256:%s\n' "$id"
        fi ;;
    *) exit 2 ;;
esac
MOCK_DOCKER
chmod +x "$tmp/bin/docker" "$tmp/bin/ssh"
export REMOTE_LAB_CONFIG="$tmp/config" PATH="$tmp/bin:$PATH" \
    MOCK_DOCKER_LOG="$tmp/docker.log"
run() { bash "$repo/netlabs/tools/remote-uut.sh" "$@" >"$tmp/output" 2>&1; }
listing="$(bash "$repo/netlabs/tools/remote-uut.sh" --list)"
grep -qx '1R-lifecycle' <<<"$listing"
grep -qx '2R-case' <<<"$listing"
# Exact selectors reject invalid versions without contacting SSH/Docker.
for invalid in 0.5 0.5.1-test ../0.5 0.5.1.0; do
    if run --local --tag "$invalid" --container; then exit 1; fi
    grep -q 'exact X.Y.Z' "$tmp/output"
done
for invalid in 0.5.1 0.5-test ../0.5; do
    if run --tag "$invalid" --container; then exit 1; fi
    grep -q 'exact X.Y' "$tmp/output"
done
if run --local --tag 0.5.1 --purge; then exit 1; fi
grep -q 'cannot be combined' "$tmp/output"
# Published mode never silently substitutes a local development package.
if run --container; then exit 1; fi
grep -q 'Remote transport: SSH connection refused' "$tmp/output"
if run --tag 0.5 --container; then exit 1; fi
grep -q 'Remote transport: SSH connection refused' "$tmp/output"

python3 - "$repo/dist" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
folder = Path(sys.argv[1])
name = 'openeigrpd_0.5.9-test.dev_amd64.deb'
(folder / name).write_bytes(b'local package fixture')
manifest = {
    'schema': 'openeigrp-netlabs-amd64/v1',
    'image': 'openeigrp:0.5.9-test.dev-amd64',
    'image_id': 'sha256:' + 'a'*64,
    'package_image': 'openeigrp-package:netlab',
    'package_image_id': 'sha256:' + 'b'*64,
    'old': None,
    'new': {'version': '0.5.9-test.dev', 'package': name,
            'sha256': hashlib.sha256((folder / name).read_bytes()).hexdigest()},
}
(folder / 'netlabs-amd64.json').write_text(json.dumps(manifest))
PY
if run --local --tag 0.5.8 --package; then exit 1; fi
grep -q 'local build is 0.5.9, not 0.5.8' "$tmp/output"
! grep -q 'Remote transport' "$tmp/output"
if FAIL_PACKAGE_IMAGE=1 run --local --package; then exit 1; fi
grep -q 'missing Debian systemd test image' "$tmp/output"
if BAD_PACKAGE_ARCH=1 run --local --package; then exit 1; fi
grep -q 'must be linux/amd64' "$tmp/output"
if run --local --tag 0.5.9 --package; then exit 1; fi
grep -q 'Selected local build: 0.5.9-test.dev' "$tmp/output"
grep -q 'upgrade SKIPPED' "$tmp/output"
grep -q 'Remote transport: SSH connection refused' "$tmp/output"
if run --local --container; then exit 1; fi
grep -q 'Remote transport: SSH connection refused' "$tmp/output"
if run --local --all; then exit 1; fi
grep -q 'Remote transport: SSH connection refused' "$tmp/output"
printf 'corrupt\n' >>"$repo/dist/openeigrpd_0.5.9-test.dev_amd64.deb"
if run --local --package; then exit 1; fi
grep -q 'missing or modified new artifact' "$tmp/output"
! grep -Eq '^(pull|build|run) ' "$MOCK_DOCKER_LOG"
echo 'PASS: exact published/local selectors, package checksums, no test-time builds'
