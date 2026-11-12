#!/bin/sh
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Validate a completed native Debian package before it is published.
set -eu

fail()
{
    printf 'verify-deb: %s\n' "$*" >&2
    exit 1
}

[ "$#" -eq 3 ] || fail 'usage: verify-deb.sh PACKAGE.deb VERSION amd64|arm64'
package=$1
version=$2
arch=$3
[ -f "$package" ] || fail "missing package: $package"
case "$arch" in
    amd64|arm64) ;;
    *) fail "unsupported architecture: $arch" ;;
esac
for tool in dpkg-deb mktemp awk; do
    command -v "$tool" >/dev/null 2>&1 || fail "missing build tool: $tool"
done

field()
{
    dpkg-deb -f "$package" "$1" 2>/dev/null || fail "invalid package metadata: $package"
}
[ "$(field Package)" = openeigrpd ] || fail 'wrong package name'
[ "$(field Version)" = "$version" ] || fail 'wrong package version'
[ "$(field Architecture)" = "$arch" ] || fail 'wrong package architecture'
case "$(field Depends)" in
    *frr*|*bird*|*uut*|*eigrp-wire*)
        fail 'external routing framework or UUT dependency' ;;
esac

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT HUP INT TERM
# Reading the full data archive also rejects truncated or corrupt packages.
dpkg-deb --contents "$package" > "$work/manifest" || fail 'invalid data archive'
dpkg-deb -e "$package" "$work/control" || fail 'invalid control archive'
[ "$(cat "$work/control/conffiles" 2>/dev/null)" = /etc/openeigrpd.conf ] ||
    fail 'configuration is not a dpkg conffile'

# Verify mode, root ownership, file type, and exact location for privileged
# executables, configuration and systemd integration files.
check_file()
{
    file=$1
    mode=$2
    awk -v name="./$file" -v mode="$mode" '
        $NF == name && $1 == mode && $2 == "root/root" { found = 1 }
        END { exit !found }
    ' "$work/manifest" || fail "missing or unsafe package entry: $file"
}
check_file usr/sbin/openeigrpd -rwxr-xr-x
check_file usr/bin/oectl -rwxr-xr-x
check_file usr/bin/openeigrpd-config -rwxr-xr-x
check_file etc/openeigrpd.conf -rw-------
check_file usr/lib/systemd/system/openeigrpd.service -rw-r--r--
check_file usr/lib/sysusers.d/openeigrpd.conf -rw-r--r--

# No unexpected payloads, symlinks, privileged mode bits, or inaccessible
# directories may be published. Keep this allowlist in sync with the package.
awk '
    BEGIN {
        allowed["./usr/sbin/openeigrpd"] = 1
        allowed["./usr/bin/oectl"] = 1
        allowed["./usr/bin/openeigrpd-config"] = 1
        allowed["./etc/openeigrpd.conf"] = 1
        allowed["./usr/lib/systemd/system/openeigrpd.service"] = 1
        allowed["./usr/lib/sysusers.d/openeigrpd.conf"] = 1
        allowed["./usr/share/man/man5/openeigrpd.conf.5"] = 1
        allowed["./usr/share/man/man8/oectl.8"] = 1
        allowed["./usr/share/man/man8/openeigrpd.8"] = 1
    }
    $1 ~ /^d/ {
        if ($1 != "drwxr-xr-x" || $2 != "root/root") exit 1
        next
    }
    $1 ~ /^-/ {
        if (!allowed[$NF] || $2 != "root/root" || $1 ~ /[sS]/) exit 1
        next
    }
    { exit 1 }
' "$work/manifest" || fail 'unexpected or unsafe package contents'
