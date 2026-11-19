#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Build the FRR container and native Debian test artifact for AMD64 Netlab.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
usage() {
    cat <<'USAGE'
Usage: netlabs/tools/docker-amd.sh [LABEL]

Build openeigrp:LABEL (linux/amd64) and the native Debian development .deb
from the same source and version. Use the latest official GitHub Release
AMD64 .deb if published, validating SHA256SUMS. Without a published Debian
package, upgrade tests are explicitly SKIPPED but local package tests run.
Write dist/netlabs-amd64.json for remote-uut.sh. Default LABEL is $USER.
No images or packages are published. The old package always comes from GitHub.
USAGE
}

if [[ $# -gt 1 ]]; then usage >&2; exit 2; fi
if [[ ${1:-} == -h || ${1:-} == --help ]]; then usage; exit 0; fi
label="${1:-${USER:-$(id -un)}}"
[[ "$label" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || {
    echo 'ERROR: invalid Docker image label' >&2; exit 2;
}
dist="$root/dist"
mkdir -p "$dist"
# A failed attempt cannot leave an earlier manifest eligible for --all.
rm -f -- "$dist/netlabs-amd64.json"
version="$("$root/tools/version.sh" --local "$label" --repo "$root")"
"$root/tools/build-version.sh" --package "$version" >/dev/null
image="openeigrp:$label"

# Official release packages are optional until native Debian releases exist.
# A missing release skips upgrade qualification, but never skips the local build.
# Network, checksum, and incomplete published release errors are still fatal.
old_json="$(python3 "$root/netlabs/tools/package-artifacts.py" download --dist "$dist")"
old_version="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["version"] if d else "")' "$old_json")"
old_package="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["package"] if d else "")' "$old_json")"

if [[ -n "$old_version" ]]; then
    # Debian version ordering must make this a genuine upgrade.
    docker run --rm --platform linux/amd64 debian:latest \
        dpkg --compare-versions "$version" gt "$old_version" || {
        echo "ERROR: local $version is not newer than published $old_version" >&2
        exit 1
    }
else
    echo 'NOTE: No published native Debian release; upgrade tests will be SKIPPED.'
fi

EIGRP_BUILD_VERSION="$version" \
    "$root/frr/tools/docker.sh" --local "$label" --platform linux/amd64 \
    --image "$image"
"$root/linux/packaging/build-packages.sh" --version "$version" --arch amd64
new_package="openeigrpd_${version}_amd64.deb"

# Check both archives with the real package verifier inside Debian. macOS
# clients do not necessarily have dpkg-deb installed.
docker run --rm --platform linux/amd64 \
    --mount "type=bind,source=$dist,target=/dist,readonly" \
    --mount "type=bind,source=$root/linux/packaging/verify-deb.sh,target=/verify-deb.sh,readonly" \
    debian:latest /bin/sh -ec '
        /verify-deb.sh "/dist/$1" "$2" amd64
        if [ -n "$3" ]; then
            /verify-deb.sh "/dist/$3" "$4" amd64
            dpkg --compare-versions "$2" gt "$4"
        fi
    ' -- "$new_package" "$version" "$old_package" "$old_version"
# The optional one-architecture builder must produce the same test environment
# as make all. remote-uut.sh never calls either build path.
package_image='openeigrp-package:netlab'
package_image_id="$(bash "$root/netlabs/tools/package-image.sh" build \
    "$root/netlabs/test/package/1R-lifecycle/Dockerfile")" || exit $?
image_id="$(docker image inspect --format '{{.Id}}' "$image")"
[[ "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]] || {
    echo 'ERROR: could not identify the locally built Docker image' >&2; exit 1;
}

# Atomic manifest update: a partial build must never become a runnable test.
python3 - "$dist" "$old_json" "$version" "$new_package" "$image" "$image_id" \
    "$label" "$package_image" "$package_image_id" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
folder = Path(sys.argv[1])
old = json.loads(sys.argv[2])
version, new_name, image, image_id, label, package_image, package_image_id = sys.argv[3:]
new = {'version': version, 'package': new_name,
       'sha256': hashlib.sha256((folder / new_name).read_bytes()).hexdigest()}
manifest = {'schema': 'openeigrp-netlabs-amd64/v1', 'image': image,
            'image_id': image_id, 'build_tag': label,
            'package_image': package_image, 'package_image_id': package_image_id,
            'old': old, 'new': new}
with tempfile.NamedTemporaryFile('w', dir=folder, prefix='.netlabs-amd64.',
                                 delete=False, encoding='utf-8') as tmp:
    json.dump(manifest, tmp, indent=2, sort_keys=True)
    tmp.write('\n')
    staged = tmp.name
os.replace(staged, folder / 'netlabs-amd64.json')
print('Netlab image:    ' + image)
print('Published A:     ' + (old['package'] + ' (' + old['release'] + ')' if old else 'none (upgrade SKIPPED)'))
print('Development B:   ' + new_name)
print('Artifact index:  ' + str(folder / 'netlabs-amd64.json'))
PY
