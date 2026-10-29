#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Import a local development image or purge owned artifacts on StormTrooper.
# Intentionally independent of the active UUT runner; uses the same lock.
set -euo pipefail

BASE="${REMOTE_LAB_DATA:-/home/netlab-data}"
STATE="${REMOTE_LAB_STATE:-/home/netlab/.remote-lab}"

valid_image() {
    [[ "$1" =~ ^openeigrp:[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]
}

valid_id() {
    [[ "$1" =~ ^sha256:[a-f0-9]{64}$ ]]
}

assert_idle() {
    mkdir -p "$STATE"
    exec 9>"$STATE/lock"
    flock -n 9 || { echo 'BUSY: remote lab or UUT operation in progress' >&2; return 1; }
    if [[ -e "$STATE/session" || -e "$STATE/owner" ]]; then
        echo 'BUSY: remote session or worker requires recovery' >&2
        return 1
    fi
    local names
    names="$(docker ps -a --format '{{.Names}}')" || return 1
    if grep -Eq '^clab-(eigrp-|oeig-lab-)' <<<"$names"; then
        echo 'BUSY: OpenEIGRP containerlab resources remain' >&2
        return 1
    fi
}

case "${1:-}" in
    inspect)
        [[ $# == 2 ]] && valid_image "$2" || exit 2
        # A missing image has an empty ID, allowing the client to transfer.
        docker image inspect --format '{{.Id}}' "$2" 2>/dev/null || :
        ;;
    import)
        [[ $# == 3 ]] && valid_image "$2" && valid_id "$3" || exit 2
        assert_idle
        # No intermediate tarball. Both sides of the decompression pipeline
        # must succeed. A truncated upload must never authorize qualification.
        gzip -dc | docker load
        actual="$(docker image inspect --format '{{.Id}}' "$2")"
        platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$2")"
        if [[ "$actual" != "$3" || "$platform" != linux/amd64 ]]; then
            echo "ERROR: imported image mismatch: $2 ($actual, $platform)" >&2
            exit 1
        fi
        printf 'PASS: imported %s (%s)\n' "$2" "$actual"
        ;;
    purge)
        [[ $# == 1 ]] || exit 2
        assert_idle
        rc=0
        # Do not touch published GHCR releases, Cisco images, or unrelated
        # repositories. Only the local development repository is eligible.
        images="$(docker image ls --format '{{.Repository}}:{{.Tag}}')" || exit 1
        while IFS= read -r image; do
            valid_image "$image" || continue
            docker image rm "$image" || rc=1
        done <<<"$images"
        # Reports contain the captured evidence for completed UUT runs.
        # A failed/unrecovered session is rejected above, never purged.
        if [[ -d "$BASE/results" ]]; then
            for item in "$BASE"/results/uut-* "$BASE"/results/batch-*.json; do
                [[ -e "$item" ]] || continue
                rm -rf -- "$item" || rc=1
            done
        fi
        (( rc == 0 )) && echo 'PASS: remote netlabs development images and reports purged'
        exit "$rc"
        ;;
    *) echo 'usage: dev-image.sh inspect IMAGE | import IMAGE ID | purge' >&2; exit 2 ;;
esac
