#!/usr/bin/env bash
# Copyright (C) 2026 Donnie V. Savage
# Resolve a shared OpenEIGRP version for native packages and Docker images.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() { printf 'version: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'USAGE'
Usage: tools/version.sh --latest [--repo PATH]
       tools/version.sh --local [LABEL] [--repo PATH]

--latest obtains the highest numeric rel-X.Y.Z tag reachable from origin/master.
--local adds -LABEL to that release (default: $USER-test).
An explicit X.Y.Z local version is retained unchanged for release builds.
Remote release metadata must be available; no guessed fallback is used.
USAGE
}

[[ $# -gt 0 ]] || { usage >&2; exit 2; }
mode="$1"; shift
case "$mode" in
    --latest|--local) ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown mode: $mode" ;;
esac

label="${USER:-$(id -un)}-test"
if [[ "$mode" == --local && $# -gt 0 && "$1" != --* ]]; then
    label="$1"; shift
fi
repo="$root"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            [[ $# -ge 2 && -n "$2" ]] || die 'missing --repo path'
            repo="$2"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

if [[ "$mode" == --local ]]; then
    if [[ "$label" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        printf '%s\n' "$label"
        exit 0
    fi
    [[ "$label" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] ||
        die 'invalid development label (letters, digits, dot or hyphen only)'
    [[ ! "$label" =~ ^[0-9]+\.[0-9]+\.[0-9]+[-~+] ]] ||
        die 'pass a development suffix, not a full version (example: diivious-test)'
fi

git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
    die "not a Git checkout: $repo"
git -C "$repo" remote get-url origin >/dev/null 2>&1 ||
    die "Git remote origin is required: $repo"
# Fetch release metadata only. This updates local Git refs, not the checked-out
# files, and rejects stale/disconnected release information instead of guessing.
git -C "$repo" fetch --quiet --tags --no-recurse-submodules origin \
    '+refs/heads/master:refs/remotes/origin/master' ||
    die 'unable to refresh origin/master release tags'

# Only use tags still advertised by origin. A deleted or branch-only local
# release tag must never be used to label either integration artifact.
remote_tags="$(git -C "$repo" ls-remote --refs --tags origin 'refs/tags/rel-*')" ||
    die 'unable to read origin release tags'
version="$(
    while IFS=$'\t' read -r sha ref; do
        tag="${ref#refs/tags/rel-}"
        [[ "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
        local_sha="$(git -C "$repo" rev-parse "refs/tags/rel-$tag")" || exit 1
        [[ "$sha" == "$local_sha" ]] || {
            printf 'version: local release tag rel-%s differs from origin\n' "$tag" >&2
            exit 1
        }
        if git -C "$repo" merge-base --is-ancestor \
            "refs/tags/rel-$tag^{commit}" refs/remotes/origin/master; then
            printf '%s\n' "$tag"
        fi
    done <<< "$remote_tags" | sort -t . -k1,1n -k2,2n -k3,3n | tail -1
)" || die 'unable to verify release tags on origin/master'
[[ -n "$version" ]] || die 'no rel-X.Y.Z release tag reachable from origin/master'
if [[ "$mode" == --latest ]]; then
    printf '%s\n' "$version"
else
    printf '%s-%s\n' "$version" "$label"
fi
