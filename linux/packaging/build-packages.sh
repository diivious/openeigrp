#!/bin/sh
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Build isolated Debian/Ubuntu-compatible packages for both Linux architectures.
set -eu

usage()
{
    cat <<'USAGE'
usage: linux/packaging/build-packages.sh --version VERSION [--arch amd64|arm64|all] [--output DIR]

Requires Docker with linux/amd64 and linux/arm64 platform support. Builds in
Debian 12 containers using the checked-out source, not host-compiled binaries.
USAGE
}

fail()
{
    printf 'build-packages: %s\n' "$*" >&2
    exit 1
}

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
version=
archs='amd64 arm64'
output="$root/dist"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --version|--arch|--output)
            [ "$#" -ge 2 ] || fail "missing value for $1"
            case "$1" in
                --version) version=$2 ;;
                --arch)
                    case "$2" in
                        all) archs='amd64 arm64' ;;
                        amd64|arm64) archs=$2 ;;
                        *) fail "unsupported architecture: $2" ;;
                    esac ;;
                --output) output=$2 ;;
            esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "unknown argument: $1" ;;
    esac
done
case "$version" in
    [0-9]*) ;;
    *) fail 'a Debian --version beginning with a digit is required' ;;
esac
case "$version" in
    *[!A-Za-z0-9.+:~_-]*|'') fail 'invalid Debian package version' ;;
esac
command -v docker >/dev/null 2>&1 || fail 'Docker is required'
docker info >/dev/null 2>&1 || fail 'Docker daemon is not running or accessible'
mkdir -p -- "$output"
output=$(CDPATH= cd -- "$output" && pwd)
staging=$(mktemp -d "$output/.openeigrpd-build.XXXXXX")
trap 'rm -rf -- "$staging"' EXIT
trap 'exit 1' HUP INT TERM

# The same Debian 12 image is used for both architectures and in GitHub CI.
# A fixed suite limits glibc requirements; it is not an immutable dependency
# lockfile. Pin an image digest for bit-for-bit reproducibility if needed.
image=debian:12
for arch in $archs; do
    printf 'Building openeigrpd %s for linux/%s (%s)\n' "$version" "$arch" "$image"
    docker run --rm --platform "linux/$arch" \
        --mount "type=bind,source=$root,target=/src,readonly" \
        --mount "type=bind,source=$staging,target=/out" \
        -e "PACKAGE_VERSION=$version" -e "PACKAGE_ARCH=$arch" \
        -e "OUTPUT_UID=$(id -u)" -e "OUTPUT_GID=$(id -g)" \
        "$image" /bin/sh -euc '
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -qq
            apt-get install -y -qq --no-install-recommends \
                build-essential dpkg-dev binutils make python3 ca-certificates
            native=$(dpkg-architecture -qDEB_HOST_ARCH)
            if [ "$native" != "$PACKAGE_ARCH" ]; then
                echo "wrong container architecture: $native, expected $PACKAGE_ARCH" >&2
                exit 1
            fi
            mkdir -p /work/eigrp /work/libunix /work/linux
            cp -a /src/Makefile /work/Makefile
            cp -a /src/eigrp/code /work/eigrp/code
            cp -a /src/libunix/code /work/libunix/code
            cp -a /src/linux/code /work/linux/code
            cp -a /src/linux/man /work/linux/man
            cp -a /src/linux/packaging /work/linux/packaging
            cp -a /src/linux/test /work/linux/test
            cd /work
            # Never accept a stale host-architecture or prebuilt daemon.
            make -C eigrp/code clean >/dev/null
            make -C libunix/code clean >/dev/null
            make -C linux/code clean >/dev/null
            rm -f linux/code/openeigrpd
            make linux
            python3 linux/test/test_packaging.py
            if [ ! -x linux/code/openeigrpd ]; then
                echo "native openeigrpd was not built; no release package created" >&2
                exit 1
            fi
            mkdir -p /work/output
            linux/packaging/build-deb.sh --version "$PACKAGE_VERSION" \
                --output /work/output
            package="openeigrpd_${PACKAGE_VERSION}_${PACKAGE_ARCH}.deb"
            test "$(dpkg-deb -f "/work/output/$package" Architecture)" = "$PACKAGE_ARCH"
            cp "/work/output/$package" /out/
            chown "$OUTPUT_UID:$OUTPUT_GID" "/out/$package"
        '
    package="$staging/openeigrpd_${version}_${arch}.deb"
    [ -s "$package" ] || fail "container did not produce $package"
done
# Publish only after every selected architecture finishes successfully.
for arch in $archs; do
    package="openeigrpd_${version}_${arch}.deb"
    mv -f "$staging/$package" "$output/$package"
    printf '%s\n' "$output/$package"
done
