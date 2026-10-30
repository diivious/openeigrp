#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# One-time administrator setup for dedicated StormTrooper interoperability UUT.
# No root action is performed automatically by a live test.
set -euo pipefail

if (( EUID != 0 )); then
    echo 'Run this script as root on the dedicated netlab host.' >&2
    exit 1
fi
if ! id netlab >/dev/null 2>&1; then
    echo 'Missing netlab system account.' >&2
    exit 1
fi
for tool in sudo visudo nsenter tcpdump tc; do
    command -v "$tool" >/dev/null || { echo "Missing host tool: $tool" >&2; exit 1; }
done
nsenter="$(readlink -f "$(command -v nsenter)")"
case "$nsenter" in
    /usr/bin/nsenter|/bin/nsenter) ;;
    *) echo "Unexpected nsenter location: $nsenter" >&2; exit 1 ;;
esac
rule='/etc/sudoers.d/90-openeigrp-netlabs-packet'
if [[ -e "$rule" ]]; then
    expected="netlab ALL=(root) NOPASSWD: $nsenter"
    [[ "$(cat "$rule")" == "$expected" ]] || {
        echo "Refusing to replace an existing local sudo policy: $rule" >&2
        exit 1
    }
else
    umask 077
    printf 'netlab ALL=(root) NOPASSWD: %s\n' "$nsenter" >"$rule"
fi
chmod 0440 "$rule"
if ! visudo -cf "$rule"; then
    echo 'Invalid sudo rule; administrator must review/remove it.' >&2
    exit 1
fi
if ! runuser -u netlab -- sudo -n "$nsenter" -t 1 -n -- true; then
    echo 'Privilege verification failed; inspect sudo configuration.' >&2
    exit 1
fi
echo 'PASS: netlab noninteractive network namespace access enabled'
echo 'Security: sudo nsenter grants elevated namespace access to the netlab account.'
echo 'Use only on a dedicated lab host and remove the sudoers rule to revoke.'
