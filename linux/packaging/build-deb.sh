#!/bin/sh
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Build a native OpenEIGRP Debian package from local, already built sources.
set -eu
# Debian control and installed directories must stay traversable even when
# the invoking administrator uses a restrictive umask (e.g. 077).
umask 022

usage()
{
    cat <<'USAGE'
usage: linux/packaging/build-deb.sh --version VERSION [--daemon ELF] [--output DIR]

Build a native-architecture .deb. The real openeigrpd executable is required;
this tool does not synthesize a daemon from libraries or UUT fixtures.
USAGE
}

fail()
{
    printf 'build-deb: %s\n' "$*" >&2
    exit 1
}

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
daemon="$root/linux/code/openeigrpd"
output="$root/dist"
version=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --version|--daemon|--output)
            [ "$#" -ge 2 ] || fail "missing value for $1"
            case "$1" in
                --version) version=$2 ;;
                --daemon) daemon=$2 ;;
                --output) output=$2 ;;
            esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "unknown argument: $1" ;;
    esac
done
[ -n "$version" ] || fail 'a --version is required'
case "$version" in
    [0-9]*) ;;
    *) fail 'version must begin with a digit' ;;
esac
# Versions are package metadata, never shell syntax or paths.
case "$version" in
    *[!A-Za-z0-9.+:~_-]*|'') fail 'invalid version' ;;
esac
for tool in dpkg dpkg-deb dpkg-shlibdeps dpkg-architecture make readelf install mktemp; do
    command -v "$tool" >/dev/null 2>&1 || fail "missing build tool: $tool"
done
if ! dpkg --compare-versions "$version" eq "$version" >/dev/null 2>&1; then
    fail 'invalid Debian package version'
fi
[ -f "$daemon" ] && [ -x "$daemon" ] || fail "real daemon executable missing: $daemon"

arch=$(dpkg-architecture -qDEB_HOST_ARCH)
case "$arch" in
    amd64) elf_machine='Advanced Micro Devices X86-64' ;;
    arm64) elf_machine='AArch64' ;;
    *) fail "unsupported native architecture: $arch (only amd64 and arm64)" ;;
esac
check_binary()
{
    file=$1
    machine=$(LC_ALL=C readelf -h "$file" 2>/dev/null | sed -n 's/^[[:space:]]*Machine:[[:space:]]*//p')
    [ "$machine" = "$elf_machine" ] || fail "non-native or non-ELF executable: $file ($machine)"
    [ -x "$file" ] || fail "not executable: $file"
    # Reject dynamic linkage to external routing suites and the UUT broker.
    if LC_ALL=C readelf -d "$file" | grep -Ei 'NEEDED.*(libfrr|libbird|libeigrp_uut|eigrp-wire)' >/dev/null; then
        fail "external routing framework or UUT dependency: $file"
    fi
}
check_binary "$daemon"
make -C "$root/linux/code" oectl openeigrpd-config >/dev/null
check_binary "$root/linux/code/oectl"
check_binary "$root/linux/code/openeigrpd-config"

output=$(mkdir -p -- "$output" && CDPATH= cd -- "$output" && pwd)
# Keep the verified artifact on the output filesystem so publication is an
# atomic rename; failed validation must never leave a new public .deb behind.
work=$(mktemp -d "$output/.openeigrpd-stage.XXXXXX")
trap 'rm -rf -- "$work"' EXIT HUP INT TERM
pkg="$work/package"
mkdir -p "$pkg/DEBIAN" "$pkg/etc" "$pkg/usr/sbin" "$pkg/usr/bin" \
    "$pkg/usr/lib/systemd/system" "$pkg/usr/lib/sysusers.d" \
    "$pkg/usr/share/man/man5" "$pkg/usr/share/man/man8" \
    "$work/debian"
install -m 0755 "$daemon" "$pkg/usr/sbin/openeigrpd"
install -m 0755 "$root/linux/code/oectl" "$pkg/usr/bin/oectl"
install -m 0755 "$root/linux/code/openeigrpd-config" "$pkg/usr/bin/openeigrpd-config"
install -m 0600 "$root/linux/packaging/openeigrpd.conf" "$pkg/etc/openeigrpd.conf"
install -m 0644 "$root/linux/packaging/openeigrpd.service" "$pkg/usr/lib/systemd/system/openeigrpd.service"
install -m 0644 "$root/linux/packaging/openeigrpd.sysusers" "$pkg/usr/lib/sysusers.d/openeigrpd.conf"
install -m 0644 "$root/linux/packaging/man/oectl.8" "$pkg/usr/share/man/man8/oectl.8"
install -m 0644 "$root/linux/packaging/man/openeigrpd.conf.5" "$pkg/usr/share/man/man5/openeigrpd.conf.5"
install -m 0644 "$root/linux/packaging/man/openeigrpd.8" "$pkg/usr/share/man/man8/openeigrpd.8"
for script in postinst prerm postrm; do
    install -m 0755 "$root/linux/packaging/$script" "$pkg/DEBIAN/$script"
done
printf '/etc/openeigrpd.conf\n' > "$pkg/DEBIAN/conffiles"
# dpkg-shlibdeps derives actual shared-library dependencies for this host.
# Do not guess libc versions or unnecessarily depend on routing frameworks.
cat > "$work/debian/control" <<'CTRL'
Source: openeigrpd
Section: net
Priority: optional
Maintainer: Donnie V. Savage <diivious@hotmail.com>
Standards-Version: 4.7.0

Package: openeigrpd
Architecture: any
Description: Native Linux EIGRP routing daemon
CTRL
multiarch=$(dpkg-architecture -qDEB_HOST_MULTIARCH)
shlibs=$(cd "$work" && dpkg-shlibdeps -O -l"/usr/lib/$multiarch" \
    -e"$pkg/usr/sbin/openeigrpd" \
    -e"$pkg/usr/bin/oectl" \
    -e"$pkg/usr/bin/openeigrpd-config")
case "$shlibs" in
    shlibs:Depends=*) deps=${shlibs#shlibs:Depends=} ;;
    *) fail "invalid dpkg-shlibdeps output: $shlibs" ;;
esac
case "$deps" in
    *frr*|*bird*|*uut*) fail "external routing dependency in package: $deps" ;;
esac
cat > "$pkg/DEBIAN/control" <<CTRL
Package: openeigrpd
Version: $version
Section: net
Priority: optional
Architecture: $arch
Maintainer: Donnie V. Savage <diivious@hotmail.com>
Depends: $deps, systemd, init-system-helpers
Description: Native Linux EIGRP routing daemon and management client
 Standalone EIGRP integration for Linux using the portable OpenEIGRP core.
 Installs the openeigrpd daemon, oectl client, configuration validator and
 an intentionally disabled systemd unit. No FRR, BIRD or UUT broker.
CTRL
# A setgid parent directory (e.g. shared build checkout) propagates mode
# 2xxx into staging. dpkg rejects a setgid DEBIAN directory even when umask
# is 022; canonicalize directories for reproducible, safe package metadata.
find "$pkg" -type d -exec chmod g-s,u-s {} +
find "$pkg" -type d -exec chmod 0755 {} +
filename="openeigrpd_${version}_${arch}.deb"
# The archive is built by an unprivileged user without fakeroot.
dpkg-deb --build --root-owner-group "$pkg" "$work/$filename" >/dev/null
"$root/linux/packaging/verify-deb.sh" "$work/$filename" "$version" "$arch"
mv -f -- "$work/$filename" "$output/$filename"
printf '%s\n' "$output/$filename"
