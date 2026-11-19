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
       netlabs/tools/remote-uut.sh [--local] [--tag VERSION] --all|--container|--package
       netlabs/tools/remote-uut.sh [--local] [--tag VERSION] --rtp|--dual
       netlabs/tools/remote-uut.sh [--local] [--tag VERSION] --scenario NAME
       netlabs/tools/remote-uut.sh --purge
       netlabs/tools/remote-uut.sh --abort

--list       List complete live scenarios.
--update     Pull the latest published FRR image and debian:latest on the appliance.
--all        Run the container and Debian package suites.
--container  Run container regressions and 2R-6R live scenarios.
--package    Run package regressions and the disposable Debian lifecycle.
--rtp/--dual Run the selected protocol regression and live scenarios.
--scenario   Run exactly one live scenario without regressions.
--package-old/--package-new  Explicit local .deb names for 1R-lifecycle debugging.
--local      Use the last completed local make all build (no image building).
--tag VERSION  Exact version selection: X.Y.Z with --local, X.Y for releases.
--purge      Remove Netlab reports and development images.
--abort      Recover an interrupted remote session.

Without --local, use published GHCR OpenEIGRP, as prepared by --update.
The published release identifier X.Y maps exactly to GHCR tag X.Y.0.
With --local, the build number X.Y.Z must match dist/netlabs-amd64.json,
whose full package version also includes the development label.

The package lifecycle uses the actual .deb for the selected build or published
release. Published package tests require an official GitHub Release .deb;
if none exists the run is blocked, not replaced with a local build.
Local tests may upgrade from the latest official published .deb when available.
The Debian systemd test image is always prebuilt by make all and transferred
from the client. No image/package compilation occurs during test execution.
Results are saved under logs/netlab/.

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
    if [[ "$group" == package ]]; then
        [[ "$name" == 1R-lifecycle ]] || return 0
    else
        [[ "$name" =~ ^[2-6]R-[a-z][a-z0-9-]*$ ]] || return 0
    fi
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
        # POSIX ERE alternation works with GNU and macOS/BSD sed.
        declared="$(sed -n -E 's/^[[:space:]]*group:[[:space:]]*(rtp|dual)[[:space:]]*$/\1/p' "$spec" | head -n 1)"
        # Historical 2R-neighbor is a peer/RTP test; do not infer arbitrary
        # groups from case names when scenario metadata is missing.
        if [[ -z "$declared" && "$name" == 2R-neighbor ]]; then
            declared=rtp
        fi
        add_case "$name" "$declared" "$dir" legacy || return 1
    done
    # A group's directory is the source of truth for new cases.
    for group in rtp dual package; do
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
    check_remote_docker || return $?
    printf 'Checking published OpenEIGRP images on %s...\n' "$REMOTE_HOST"
    local stage rc
    stage="$(remote_new_stage)"
    remote_stage "$stage" netlabs/tools/remote/runner.sh netlabs/tools/remote/images.py
    remote_install_runner "$stage"
    if remote_run "$stage" image-update; then
        echo 'PASS: OpenEIGRP and Debian image update'
    else
        rc=$?
        remote_ssh "rm -rf -- '$stage'" >/dev/null 2>&1 || :
        printf 'FAIL: OpenEIGRP/Debian image update (remote exit %d)\n' "$rc" >&2
        return "$rc"
    fi
}

# Check prerequisites before staging regressions, reporting blocked scenarios,
# or claiming that a development image is absent. Published-image tests need
# Published container tests require Docker on the appliance only.
check_local_docker() {
    command -v docker >/dev/null 2>&1 || {
        echo 'ERROR: local Docker CLI is required for --local and Debian package tests' >&2
        return 2
    }
    docker info >/dev/null 2>&1 || {
        echo 'ERROR: local Docker daemon unavailable. Start Docker Desktop or Docker Engine and verify docker info.' >&2
        return 2
    }
}

check_remote_docker() {
    if ! remote_ssh 'command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1'; then
        printf 'ERROR: Docker daemon unavailable on %s. Start Docker Engine and verify docker info on the appliance.\n' \
            "$REMOTE_HOST" >&2
        return 2
    fi
}

# Verify the build manifest and package checksums before transfer.
load_package_artifacts() {
    local manifest="$REMOTE_REPO_ROOT/dist/netlabs-amd64.json" data
    [[ -f "$manifest" ]] || {
        echo "ERROR: missing local Netlab artifact manifest; run make all" >&2
        return 2
    }
    local -a args=(validate --dist "$REMOTE_REPO_ROOT/dist" --manifest "$manifest")
    data="$(python3 "$REMOTE_REPO_ROOT/netlabs/tools/package-artifacts.py" "${args[@]}")" || return 2
    # JSON is controlled by the validator; produce tabular values rather than
    # executing a manifest as shell script. The filenames remain checked below.
    local names
    names="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["old"]["package"] if d["old"] else "-", d["new"]["package"], sep="\n")' "$data")" || return 2
    if [[ -z "$PACKAGE_NEW" ]]; then
        PACKAGE_OLD="${names%%$'\n'*}"
        PACKAGE_NEW="${names#*$'\n'}"
        [[ "$PACKAGE_OLD" != - ]] || PACKAGE_OLD=''
    fi
    # Use versioned AMD64 images. On macOS ARM64, openeigrp:TAG is the
    # native arm64 alias and must not be transferred to an AMD64 appliance.
    BUILD_IMAGE="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["image"])' "$data")" || return 2
    BUILD_IMAGE_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["image_id"])' "$data")" || return 2
    PACKAGE_IMAGE="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("package_image", ""))' "$data")" || return 2
    PACKAGE_IMAGE_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("package_image_id", ""))' "$data")" || return 2
    if [[ -z "$PACKAGE_IMAGE" || -z "$PACKAGE_IMAGE_ID" ]]; then
        echo "ERROR: missing locally built Debian systemd image in artifact manifest; run make all" >&2
        return 2
    fi
    if [[ -n "$PACKAGE_OLD" ]]; then
        printf 'Debian upgrade: %s -> %s\n' "$PACKAGE_OLD" "$PACKAGE_NEW"
    else
        printf 'Debian package: %s (no published baseline; upgrade SKIPPED)\n' "$PACKAGE_NEW"
    fi
}

# Only inspected locally and transferred to the appliance; never built here.
DEV_IMAGE=''
DEV_ID=''
BUILD_IMAGE=''
BUILD_IMAGE_ID=''
PACKAGE_IMAGE=''
PACKAGE_IMAGE_ID=''
sync_image() {
    local name="$1" expected_id="${2:-}" local_id platform stage status=0 remote_id
    check_local_docker || return $?
    local_id="$(docker image inspect --format '{{.Id}}' "$name" 2>/dev/null)" || {
        echo "ERROR: missing local Docker image $name; run make all" >&2
        return 1
    }
    [[ "$local_id" =~ ^sha256:[a-f0-9]{64}$ ]] || return 1
    [[ -z "$expected_id" || "$local_id" == "$expected_id" ]] || {
        echo "ERROR: $name differs from the build manifest; run make all" >&2
        return 1
    }
    platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$name")" || return 1
    [[ "$platform" == linux/amd64 ]] || {
        echo "ERROR: $name is $platform; StormTrooper requires linux/amd64" >&2
        return 1
    }
    printf 'Transferred image candidate: %s (%s)\n' "$name" "$local_id"
    stage="$(remote_new_stage)" || return 1
    if ! remote_stage "$stage" netlabs/tools/remote/dev-image.sh; then
        status=1
    else
        remote_id="$(remote_ssh "bash '$stage/netlabs/tools/remote/dev-image.sh' inspect '$name'")" || status=1
        if (( status == 0 )); then
            if [[ "$remote_id" == "$local_id" ]]; then
                echo 'PASS: development image unchanged; transfer skipped'
            else
                echo "Transferring $name to the Netlab appliance..."
                set +e
                docker save "$name" | gzip -1 | \
                    remote_ssh "bash '$stage/netlabs/tools/remote/dev-image.sh' import '$name' '$local_id'"
                local -a steps=("${PIPESTATUS[@]}")
                set -e
                if (( steps[0] != 0 || steps[1] != 0 || steps[2] != 0 )); then
                    echo 'ERROR: Docker image transfer/import failed' >&2
                    status=1
                fi
            fi
        fi
    fi
    remote_ssh "rm -rf -- '$stage'" >/dev/null 2>&1 || status=1
    return "$status"
}
run_image_sync() {
    [[ -n "$BUILD_IMAGE" && -n "$BUILD_IMAGE_ID" ]] || {
        echo 'ERROR: missing local FRR build identity; run make all' >&2
        return 2
    }
    DEV_IMAGE="$BUILD_IMAGE"
    sync_image "$DEV_IMAGE" "$BUILD_IMAGE_ID" || return $?
    DEV_ID="$BUILD_IMAGE_ID"
}
# Local selection uses the single manifest written last by make all.
# A container-only selection must not depend on package files/checksums.
load_local_build() {
    local manifest="$REMOTE_REPO_ROOT/dist/netlabs-amd64.json" details
    [[ -f "$manifest" ]] || {
        echo 'ERROR: no completed local build manifest; run make all' >&2
        return 2
    }
    details="$(python3 - "$manifest" "$version_tag" <<'PYLOCAL'
import json
from pathlib import Path
import re
import sys
try:
    obj = json.loads(Path(sys.argv[1]).read_text())
    if obj.get('schema') != 'openeigrp-netlabs-amd64/v1':
        raise ValueError('invalid local build manifest')
    version = obj['new']['version']
    match = re.fullmatch(r'([0-9]+\.[0-9]+\.[0-9]+)-[A-Za-z0-9.-]+\.dev', version)
    if not match:
        raise ValueError('invalid local development version')
    if sys.argv[2] and match[1] != sys.argv[2]:
        raise ValueError(f'local build is {match[1]}, not {sys.argv[2]}')
    image = obj['image']
    identity = obj['image_id']
    if image != f'openeigrp:{version}-amd64' or not re.fullmatch(r'sha256:[a-f0-9]{64}', identity):
        raise ValueError('invalid AMD64 FRR identity in build manifest')
    print(image, identity, version, sep='\n')
except (OSError, ValueError, KeyError, TypeError) as exc:
    raise SystemExit(f'ERROR: {exc}')
PYLOCAL
)" || return 2
    BUILD_IMAGE="${details%%$'\n'*}"
    details="${details#*$'\n'}"
    BUILD_IMAGE_ID="${details%%$'\n'*}"
    BUILD_VERSION="${details#*$'\n'}"
    printf 'Selected local build: %s (%s)\n' "$BUILD_VERSION" "$BUILD_IMAGE"
}

# The Debian systemd environment is a separate, prebuilt, non-EIGRP image.
# Do not mistake it for the OpenEIGRP package or compile it on the appliance.
load_package_environment() {
    local desired_id="$PACKAGE_IMAGE_ID"
    PACKAGE_IMAGE='openeigrp-package:netlab'
    PACKAGE_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$PACKAGE_IMAGE" 2>/dev/null)" || {
        echo 'ERROR: missing Debian systemd test image; run make all' >&2
        return 2
    }
    [[ "$PACKAGE_IMAGE_ID" =~ ^sha256:[a-f0-9]{64}$ ]] || return 2
    if [[ -n "$desired_id" && "$PACKAGE_IMAGE_ID" != "$desired_id" ]]; then
        echo 'ERROR: Debian test image changed since make all; rebuild artifacts' >&2
        return 2
    fi
}


# Select only published, installed images. An explicit version never falls
# back to another release and neither path pulls images during tests.
load_published_image() {
    local available version
    available="$(remote_ssh "docker image ls --format '{{.Repository}}:{{.Tag}}'")" || return 2
    version="$(printf '%s\n' "$available" | python3 -c '
import re, sys
prefix = "ghcr.io/diivious/openeigrp-frr:"
versions = []
for row in sys.stdin:
    if row.startswith(prefix):
        tag = row.strip()[len(prefix):]
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", tag):
            versions.append(tag)
if not versions:
    raise SystemExit("ERROR: no published GHCR image on appliance; run --update")
print(max(versions, key=lambda s: tuple(map(int, s.split(".")))))
')" || return 2
    if [[ -n "$version_tag" ]]; then
        version="${version_tag}.0"
        if ! grep -Fxq "ghcr.io/diivious/openeigrp-frr:$version" <<<"$available"; then
            echo "ERROR: published release $version_tag is not installed on appliance (expected GHCR $version); use --update or pull that exact image" >&2
            return 2
        fi
    fi
    PUBLISHED_VERSION="$version"
    PUBLISHED_RELEASE="${version%.*}"
    PUBLISHED_IMAGE="ghcr.io/diivious/openeigrp-frr:$version"
    local platform
    platform="$(remote_ssh "docker image inspect --format '{{.Os}}/{{.Architecture}}' '$PUBLISHED_IMAGE'")" || return 2
    [[ "$platform" == linux/amd64 ]] || {
        echo "ERROR: published image $PUBLISHED_IMAGE is $platform, expected linux/amd64" >&2
        return 2
    }
    printf 'Selected published image: %s\n' "$PUBLISHED_IMAGE"
}

load_published_package() {
    local data
    data="$(python3 "$REMOTE_REPO_ROOT/netlabs/tools/package-artifacts.py" \
        download --dist "$REMOTE_REPO_ROOT/dist" --release "$PUBLISHED_RELEASE")" || return 2
    [[ "$data" != null ]] || {
        echo "BLOCKED: no published Debian package for rel-$PUBLISHED_RELEASE (use --local for a development build)" >&2
        return 4
    }
    PACKAGE_NEW="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["package"])' "$data")" || return 2
    PACKAGE_OLD=''
    printf 'Selected published Debian package: %s\n' "$PACKAGE_NEW"
}

run_package_image_sync() {
    sync_image "$PACKAGE_IMAGE" "$PACKAGE_IMAGE_ID" || return $?
    printf 'Debian Containerlab image: %s (%s)\n' "$PACKAGE_IMAGE" "$PACKAGE_IMAGE_ID"
}
check_package_image() {
    local actual
    actual="$(docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' "$PACKAGE_IMAGE" 2>/dev/null)" || return 2
    [[ "$actual" == "$PACKAGE_IMAGE_ID linux/amd64" ]] || {
        echo "ERROR: Debian systemd image $PACKAGE_IMAGE must be linux/amd64" >&2
        return 2
    }
}

run_purge() {
    local stage rc=0 local_images image
    remote_transport_prepare || return 1
    check_remote_docker || return $?
    stage="$(remote_new_stage)" || return 1

    if ! remote_stage "$stage" netlabs/tools/remote/dev-image.sh; then
        remote_ssh "rm -rf -- '$stage'" >/dev/null 2>&1 || :
        return 1
    fi

    # The remote helper holds the lab lock and refuses active/orphaned labs.
    if ! remote_ssh "bash '$stage/netlabs/tools/remote/dev-image.sh' purge"; then
        rc=1
    fi

    remote_ssh "rm -rf -- '$stage'" >/dev/null 2>&1 || rc=1
    (( rc == 0 )) || return "$rc"

    # Client image removal is scoped to development tags, not GHCR releases.
    if command -v docker >/dev/null 2>&1; then
        local_images="$(docker image ls --format '{{.Repository}}:{{.Tag}}')" || return 1
        while IFS= read -r image; do
            [[ "$image" == openeigrp:* || "$image" == openeigrp-package:* ]] || continue
            docker image rm "$image" || rc=1
        done <<<"$local_images"
    fi

    # Delete known report artifacts only. Preserve unrelated files and config.
    local f results="$REMOTE_REPO_ROOT/logs/netlab"
    if [[ -d "$results" ]]; then
        for f in "$results"/uut-* "$results"/batch-*.json; do
            [[ -e "$f" ]] || continue
            rm -rf -- "$f" || rc=1
        done
    fi

    (( rc == 0 )) && echo 'PASS: netlabs development images and historical reports purged'
    return "$rc"
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
        if [[ "$group" == package ]]; then
            files+=(linux/test/test_systemd_package.py linux/packaging/verify-deb.sh
                    "dist/$PACKAGE_NEW")
            [[ -z "$PACKAGE_OLD" ]] || files+=("dist/$PACKAGE_OLD")
        fi
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

    # The image choice lives only in this disposable stage. Default release
    # selection therefore cannot accidentally inherit an earlier dev run.
    if [[ -n "$DEV_IMAGE" ]]; then
        remote_ssh "printf '%s\\n%s\\n' '$DEV_IMAGE' '$DEV_ID' > '$stage/dev-image.txt'" || return 1
    fi

    if [[ -n "${RELEASE_IMAGE:-}" && "$group" != package ]]; then
        remote_ssh "printf '%s\n' '$RELEASE_IMAGE' > '$stage/release-image.txt'" || return 1
    fi

    if [[ "$group" == package ]]; then
        remote_ssh "printf '%s\n%s\n' '$PACKAGE_IMAGE' '$PACKAGE_IMAGE_ID' > '$stage/package-image.txt'" || return 1
    fi

    transcript="$(mktemp)"
    set +e
    if [[ "$group" == package ]]; then
        remote_run "$stage" uut-start "$name" package "$PACKAGE_OLD" "$PACKAGE_NEW" 2>&1 | tee "$transcript"
    elif [[ "$layout" == grouped ]]; then
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
        local local_results="$REMOTE_REPO_ROOT/logs/netlab"
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
        netlabs/tools/package-artifacts.py
        netlabs/tools/package-image.sh
        netlabs/tools/docker-amd.sh
        netlabs/uut
        netlabs/test
        netlabs/config
        netlabs/common
    )
    if [[ "$selection" == package || "$selection" == all ]]; then
        files+=(linux/test/test_systemd_package.py)
    fi
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
    local_results="$REMOTE_REPO_ROOT/logs/netlab"
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
    dir="$REMOTE_REPO_ROOT/logs/netlab"
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

requested=''
target=''
local_mode=0
version_tag=''
PACKAGE_OLD=''
PACKAGE_NEW=''
package_unavailable=0
manual_packages=0
BUILD_VERSION=''
PUBLISHED_RELEASE=''
PUBLISHED_VERSION=''
PUBLISHED_IMAGE=''

while (( $# )); do
    case "$1" in
        --help|-h)
            [[ $# == 1 && -z "$requested" ]] || exit 2
            usage; exit 0 ;;
        --update|--abort|--list|--all|--container|--package|--rtp|--dual|--purge)
            [[ -z "$requested" ]] || { usage >&2; exit 2; }
            requested="${1#--}"; shift ;;
        --scenario)
            [[ -z "$requested" && $# -ge 2 && "$2" =~ ^[1-6]R-[a-z][a-z0-9-]*$ ]] || {
                usage >&2; exit 2;
            }
            requested=scenario; target="$2"; shift 2 ;;
        --local)
            (( local_mode == 0 )) || { usage >&2; exit 2; }
            local_mode=1; shift ;;
        --tag)
            [[ -z "$version_tag" && $# -ge 2 ]] || { usage >&2; exit 2; }
            version_tag="$2"; shift 2 ;;
        --package-old|--package-new)
            [[ $# -ge 2 && "$2" =~ ^openeigrpd_[A-Za-z0-9.+~_-]+_amd64\.deb$ ]] || {
                usage >&2; exit 2;
            }
            if [[ "$1" == --package-old ]]; then
                [[ -z "$PACKAGE_OLD" ]] || exit 2
                PACKAGE_OLD="$2"
            else
                [[ -z "$PACKAGE_NEW" ]] || exit 2
                PACKAGE_NEW="$2"
            fi
            manual_packages=1; shift 2 ;;
        *) usage >&2; exit 2 ;;
    esac
done

[[ -n "$requested" ]] || { usage >&2; exit 2; }

if (( manual_packages )); then
    [[ "$requested" == scenario && "$target" == 1R-lifecycle &&
       -n "$PACKAGE_OLD" && -n "$PACKAGE_NEW" && "$PACKAGE_OLD" != "$PACKAGE_NEW" &&
       $local_mode -eq 0 && -z "$version_tag" ]] || {
        echo 'ERROR: --package-old/--package-new require an untagged 1R-lifecycle scenario' >&2
        exit 2
    }
    for package in "$PACKAGE_OLD" "$PACKAGE_NEW"; do
        [[ -f "$REMOTE_REPO_ROOT/dist/$package" ]] || {
            echo "ERROR: missing local package dist/$package" >&2; exit 2;
        }
    done
fi

if (( local_mode )); then
    [[ -z "$version_tag" || "$version_tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
        echo 'ERROR: --local --tag requires exact X.Y.Z local build version' >&2; exit 2;
    }
else
    [[ -z "$version_tag" || "$version_tag" =~ ^[0-9]+\.[0-9]+$ ]] || {
        echo 'ERROR: published --tag requires exact X.Y release version' >&2; exit 2;
    }
fi

case "$requested" in
    update|abort|purge|list)
        (( local_mode == 0 )) && [[ -z "$version_tag" ]] || {
            echo "ERROR: --local/--tag cannot be combined with --$requested" >&2; exit 2;
        } ;;
esac
case "$requested" in
    update) run_image_update; exit $? ;;
    abort) run_abort; exit $? ;;
    purge) run_purge; exit $? ;;
esac

discover
if [[ "$requested" == list ]]; then
    for name in "${CASE_NAMES[@]}"; do printf '%s\n' "$name"; done
    exit 0
fi

selected=()
for i in "${!CASE_NAMES[@]}"; do
    if [[ ( "$requested" == all ) || \
          ( "$requested" == container && "${CASE_GROUPS[i]}" != package ) || \
          ( "$requested" == scenario && "${CASE_NAMES[i]}" == "$target" ) || \
          ( "$requested" == "${CASE_GROUPS[i]}" ) ]]; then
        selected+=("$i")
    fi
done

if [[ "$requested" == scenario && ${#selected[@]} -ne 1 ]]; then
    printf 'Unknown or incomplete scenario: %s\n' "$target" >&2
    exit 2
fi

package_selected=0
container_selected=0
for i in "${selected[@]}"; do
    if [[ "${CASE_GROUPS[i]}" == package ]]; then
        package_selected=1
    else
        container_selected=1
    fi
done

# Validate local artifacts before contacting the appliance. No build or
# Docker pull is performed by this path.
if (( local_mode )); then
    check_local_docker || exit $?
    load_local_build || exit $?

    if (( package_selected )); then
        load_package_artifacts || exit $?
        load_package_environment || exit $?
        check_package_image || exit $?
    fi

    if (( container_selected )); then
        local_id="$(docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' "$BUILD_IMAGE" 2>/dev/null)" || {
            echo "ERROR: missing local image $BUILD_IMAGE; run make all" >&2; exit 2;
        }
        [[ "$local_id" == "$BUILD_IMAGE_ID linux/amd64" ]] || {
            echo "ERROR: local image $BUILD_IMAGE differs from build manifest or is not AMD64" >&2; exit 2;
        }
    fi

elif (( package_selected && manual_packages )); then
    check_local_docker || exit $?
    load_package_environment || exit $?
    check_package_image || exit $?
fi

# Published selection happens against the appliance where the container runs.
# In local mode this is not used for selecting the FRR development image.
remote_transport_prepare || exit $?
check_remote_docker || exit $?
if (( ! local_mode && ! manual_packages )); then
    load_published_image || exit $?
    if (( package_selected )); then
        if load_published_package; then
            check_local_docker || exit $?
            load_package_environment || exit $?
            check_package_image || exit $?
        else
            rc=$?
            if (( rc == 4 )); then package_unavailable=1; else exit "$rc"; fi
        fi
    fi
fi

if (( local_mode && container_selected )); then
    run_image_sync || exit $?
fi

if (( package_selected && ! package_unavailable )); then
    run_package_image_sync || exit $?
fi

# Pin an exact published release in the disposable topology. A default run
# also pins its selection so another --update cannot change it mid-batch.
if (( ! local_mode && container_selected )); then
    RELEASE_IMAGE="$PUBLISHED_IMAGE"
fi

if [[ "$requested" == scenario ]]; then
    if (( package_unavailable )); then
        echo 'BLOCKED: no published Debian artifact for 1R-lifecycle' >&2
        exit 2
    fi
    run_live "${selected[0]}"
    exit $?
fi

regressions_ok=0

if run_remote_regressions "$requested"; then
    regressions_ok=1;
fi

if (( ! regressions_ok )); then
    printf 'BLOCKED: live scenarios not started because regressions failed\n' >&2
    for i in "${selected[@]}"; do record live "${CASE_NAMES[i]}" BLOCKED; done
    if (( ${#selected[@]} == 0 )); then record live "${requested}-live" NOT_IMPLEMENTED; fi
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
        if (( stop_batch )) || { (( package_unavailable )) && [[ "${CASE_GROUPS[i]}" == package ]]; }; then
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
        if (( ! LAST_SAFE )); then
            echo 'BLOCKED: stopping batch because remote teardown was not verified' >&2
            stop_batch=1
        fi
    done
fi

printf '\n***\n***\n***\n'
printf 'Regression summary:\n    %d PASS\n    %d FAIL\n    %d BLOCKED/NOT_IMPLEMENTED\n' \
    "$PASSED" "$FAILED" "$BLOCKED"
printf '\n***\n***\n***\n\n'

write_summary "$requested" || exit 1
(( FAILED == 0 && BLOCKED == 0 ))
