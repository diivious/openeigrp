#!/bin/sh
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Verify executable Docker platform support before multiarch builds.
set -eu

for arch in amd64 arm64; do
    case "$arch" in
        amd64) expected=x86_64 ;;
        arm64) expected=aarch64 ;;
    esac
    if actual=$(docker run --rm --platform "linux/$arch" alpine:3.22 uname -m); then
        if [ "$actual" = "$expected" ]; then
            printf 'Docker linux/%s: %s\n' "$arch" "$actual"
            continue
        fi
        printf 'Docker linux/%s returned %s, expected %s\n' \
            "$arch" "$actual" "$expected" >&2
    else
        printf 'Docker cannot execute linux/%s; check binfmt/QEMU setup\n' \
            "$arch" >&2
    fi
    exit 1
done
