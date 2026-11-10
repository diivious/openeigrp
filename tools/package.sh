#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Local Debian/Ubuntu package build and lifecycle test entrypoint.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    cat <<'USAGE'
Usage:
  tools/package.sh --local [LABEL|X.Y.Z] [--platform linux/amd64|linux/arm64]
                           [--image NAME:TAG] [--builder-image DEBIAN_IMAGE]
                           [--output DIR]
  tools/package.sh --uut [--platform linux/amd64|linux/arm64]
                         [--builder-image DEBIAN_IMAGE]

  --local     Build a production .deb from local working sources. Versions
              are latest rel-X.Y.Z on origin/master plus -LABEL; default
              LABEL is $USER-test. Exact release X.Y.Z is preserved.
              A real daemon is required.
  --uut       In a disposable container, compile native Linux components,
              build test-fixture packages and run the real dpkg install,
              upgrade, remove and purge lifecycle regression tests. Never
              publishes test-fixture packages.
  --platform  Target Linux architecture (default: host architecture).
  --image     Corresponding Docker output image (e.g. openeigrp:diivious-test).
              In --local, the tag must match LABEL; it is not the builder.
  --builder-image  Debian/Ubuntu build environment (default: debian:12).
  --output    Output directory for --local (default: dist/).

Requires Docker Desktop/Engine. The source is mounted read-only and copied
into the container before compilation. Neither mode pushes a Docker image,
changes working-tree files or starts routing on the host. Development release
metadata is refreshed from origin/master through Git; no images are pushed.
USAGE
}

die() { printf 'package: %s\n' "$*" >&2; exit 1; }

host_platform() {
    case "$(uname -m)" in
        x86_64|amd64) printf 'linux/amd64\n' ;;
        aarch64|arm64) printf 'linux/arm64\n' ;;
        *) die 'cannot infer platform; specify --platform linux/amd64|linux/arm64' ;;
    esac
}

[[ $# -gt 0 ]] || { usage >&2; exit 2; }
operation="$1"
shift
case "$operation" in
    --help|-h) [[ $# -eq 0 ]] || die 'unexpected arguments after --help'; usage; exit 0 ;;
    --local|--uut) ;;
    *) usage >&2; die "unknown operation: $operation" ;;
esac

version="${USER:-$(id -un)}-test"
if [[ "$operation" == --local && $# -gt 0 && "$1" != --* ]]; then
    version="$1"
    shift
fi
platform="$(host_platform)"
builder_image=debian:12
related_image=""
output="$root/dist"
output_set=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform|--image|--builder-image|--output)
            [[ $# -ge 2 && -n "$2" ]] || die "missing value for $1"
            case "$1" in
                --platform) platform="$2" ;;
                --image) related_image="$2" ;;
                --builder-image) builder_image="$2" ;;
                --output) output="$2"; output_set=1 ;;
            esac
            shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done
[[ "$platform" == linux/amd64 || "$platform" == linux/arm64 ]] ||
    die 'only linux/amd64 and linux/arm64 are supported'
[[ "$builder_image" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/@:-]*$ ]] ||
    die 'invalid --builder-image; expected a Docker image reference'
if [[ -n "$related_image" ]]; then
    [[ "$related_image" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/:@-]*:[a-zA-Z0-9._-]+$ ]] ||
        die 'invalid --image; expected NAME:TAG'
fi
if [[ "$operation" == --uut ]]; then
    [[ "$output_set" == 0 ]] || die '--output applies only to --local'
    [[ -z "$related_image" ]] ||
        die '--image applies only to --local; use --builder-image for UUT'
    exec "$root/linux/packaging/build-packages.sh" --uut \
        --version 0.0.0~uut --arch "${platform#linux/}" --image "$builder_image"
fi

# Keep this in lockstep with tools/docker.sh: the builder and the OCI version
# label receive exactly the same resolved version, while the image reference
# retains the caller's original (short) tag.
if [[ -n "$related_image" && "${related_image##*:}" != "$version" ]]; then
    die "--image tag must match --local label/version: $version"
fi
label="$version"
version="$("$root/tools/version.sh" --local "$label" --repo "$root")" ||
    die 'unable to resolve release baseline from origin/master'
printf 'package: OpenEIGRP version %s\n' "$version" >&2

exec "$root/linux/packaging/build-packages.sh" \
    --version "$version" --arch "${platform#linux/}" \
    --image "$builder_image" --output "$output"
