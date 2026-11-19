#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Build the disposable Debian systemd environment as an ordinary build artifact.
# Never invoked by remote-uut.sh; that script only validates/transfers images.
set -euo pipefail

[[ $# == 2 && "$1" == build ]] || {
    echo 'Usage: package-image.sh build Dockerfile' >&2
    exit 2
}
dockerfile="$2"
[[ -f "$dockerfile" && "${dockerfile##*/}" == Dockerfile ]] || {
    echo 'ERROR: Debian test Dockerfile is missing' >&2
    exit 2
}
command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 || {
    echo 'ERROR: local Docker daemon unavailable' >&2
    exit 2
}

# --platform applies to FROM resolution even when the host is ARM64.
# Inspect the output image, not the host-native debian:latest manifest.
image='openeigrp-package:netlab'
docker build --platform linux/amd64 --pull --tag "$image" "${dockerfile%/*}" >&2 || {
    echo 'ERROR: Debian systemd test image build failed' >&2
    exit 1
}
identity="$(docker image inspect --format '{{.Id}}' "$image")" || exit 1
platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" || exit 1
[[ "$platform" == linux/amd64 && "$identity" =~ ^sha256:[a-f0-9]{64}$ ]] || {
    echo "ERROR: invalid Debian systemd image: $platform ($identity)" >&2
    exit 1
}
printf '%s\n' "$identity"
