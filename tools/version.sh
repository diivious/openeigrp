#!/usr/bin/env bash
# Copyright (C) 2026 Donnie V. Savage
# Resolve a shared OpenEIGRP release/build identity from origin/master.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() { printf 'version: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'USAGE'
Usage: tools/version.sh --latest [--repo PATH]
       tools/version.sh --ci [--repo PATH]
       tools/version.sh --local [LABEL] [--repo PATH]
       tools/version.sh --current [LABEL] [--repo PATH]

--latest returns the highest rel-MAJOR.MINOR release on origin/master.
--ci returns MAJOR.MINOR.0-merge.dev at the checked-out commit without allocating.
--local allocates the next local build: MAJOR.MINOR.BUILD-LABEL.dev.
--current returns the last allocated local build without incrementing it.
An existing rel-MAJOR.MINOR.0 tag can seed the first two-component release.
Development labels default to $USER and must be valid in Docker and Debian.
USAGE
}

[[ $# -gt 0 ]] || { usage >&2; exit 2; }
mode="$1"; shift
case "$mode" in
    --latest|--ci|--local|--current) ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown mode: $mode" ;;
esac

label="${USER:-$(id -un)}"
if [[ "$mode" != --latest && $# -gt 0 && "$1" != --* ]]; then
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
if [[ "$mode" == --local || "$mode" == --current ]]; then
    [[ "$label" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] ||
        die 'invalid development label (letters, digits, dot or hyphen only)'
fi

git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
    die "not a Git checkout: $repo"
git -C "$repo" remote get-url origin >/dev/null 2>&1 ||
    die "Git remote origin is required: $repo"
git -C "$repo" fetch --quiet --tags --no-recurse-submodules origin \
    '+refs/heads/master:refs/remotes/origin/master' ||
    die 'unable to refresh origin/master release tags'

remote_tags="$(git -C "$repo" ls-remote --refs --tags origin 'refs/tags/rel-*')" ||
    die 'unable to read origin release tags'
# CI resolves the release baseline against its immutable checkout, not a
# moving origin/master, so parallel jobs and architectures use one identity.
release_ref=refs/remotes/origin/master
if [[ "$mode" == --ci ]]; then
    release_ref=HEAD
    # Pull-request checkouts point at a synthetic merge commit. Its first
    # parent is the immutable target-branch baseline, not PR-added tags.
    if [[ "${GITHUB_EVENT_NAME:-}" == pull_request ]]; then
        release_ref=HEAD^1
    fi
fi
version="$(
    while IFS=$'\t' read -r sha ref; do
        tag="${ref#refs/tags/rel-}"
        # Existing rel-X.Y.0 tags are historical release metadata only.
        if [[ "$tag" =~ ^([0-9]+)\.([0-9]+)\.0$ ]]; then
            major="${BASH_REMATCH[1]}"; minor="${BASH_REMATCH[2]}"
        elif [[ "$tag" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
            major="${BASH_REMATCH[1]}"; minor="${BASH_REMATCH[2]}"
        else
            continue
        fi
        (( 10#$major <= 255 && 10#$minor <= 255 )) || continue
        local_sha="$(git -C "$repo" rev-parse "refs/tags/rel-$tag")" || exit 1
        [[ "$sha" == "$local_sha" ]] || {
            printf 'version: local release tag rel-%s differs from origin\n' "$tag" >&2
            exit 1
        }
        if git -C "$repo" merge-base --is-ancestor \
            "refs/tags/rel-$tag^{commit}" "$release_ref"; then
            printf '%s.%s\n' "$major" "$minor"
        fi
    done <<< "$remote_tags" | sort -u -t . -k1,1n -k2,2n | tail -1
)" || die 'unable to verify release tags on origin/master'
[[ -n "$version" ]] || die "no rel-MAJOR.MINOR release tag reachable from $release_ref"
if [[ "$mode" == --ci ]]; then
    printf '%s.0-merge.dev\n' "$version"
elif [[ "$mode" == --latest ]]; then
    printf '%s\n' "$version"
else
    if [[ "$mode" == --local ]]; then
        action=next
    else
        action=current
    fi
    build="$(python3 "$root/tools/build-count.py" "$action" --base "$version" --repo "$repo")" || exit $?
    printf '%s.%s-%s.dev\n' "$version" "$build" "$label"
fi
