#!/bin/sh
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Map a developer-qualified OpenEIGRP binary version to Debian metadata.
set -eu

fail() { printf 'build-version: %s\n' "$*" >&2; exit 1; }
[ "$#" -eq 2 ] || fail 'usage: build-version.sh --package|--binary VERSION'
mode=$1
version=$2
case "$version" in
    *[!A-Za-z0-9.+:~_-]*|'') fail 'invalid version characters' ;;
esac
numeric='^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$'
case "$mode" in
    --package)
        if printf '%s\n' "$version" | grep -Eq "$numeric"; then
            printf '%s\n' "$version"
            exit 0
        fi
        case "$version" in
            *-dev) ;;
            *) fail 'development identity must end in -dev' ;;
        esac
        stem=${version%-dev}
        base=$(printf '%s\n' "$stem" | sed -n 's/^.*-\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)$/\1/p')
        [ -n "$base" ] || fail 'expected developer-X.Y.Z-dev'
        user=${stem%-$base}
        [ -n "$user" ] || fail 'missing developer name'
        printf '%s~%s.dev\n' "$base" "$user" ;;
    --binary)
        base=${version%%~*}
        printf '%s\n' "$base" | grep -Eq "$numeric" || fail 'expected X.Y.Z package version'
        if [ "$version" = "$base" ]; then
            printf '%s\n' "$base"
            exit 0
        fi
        user=${version#*~}
        user=${user%.dev}
        [ -n "$user" ] || fail 'missing development qualifier'
        printf '%s-%s-dev\n' "$user" "$base" ;;
    *) fail 'unknown mode' ;;
esac
