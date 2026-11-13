#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
#
# Copyright (C) 2026 Donnie V. Savage
#
# Assemble and stage this EIGRP project into an FRR checkout.
#
# The project keeps common EIGRP source in eigrp/ and FRR-specific source in
# frr/.  FRR itself still expects one flattened eigrpd/ source directory, so
# this installer owns that projection. FRR-wide YANG and vtysh support must
# already be supplied by the selected upstream revision.

set -euo pipefail

script_name="$(basename "$0")"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
eigrp_root="$(cd "$script_dir/../.." && pwd)"

frr_root=""
install_eigrpd=1
install_tests=1
schema_only=0
dry_run=0

common_src="$eigrp_root/eigrp/code"
frr_src="$eigrp_root/frr/code"
frr_test_src="$eigrp_root/frr/test/eigrpd"
frr_topotest_src="$eigrp_root/frr/test/topotests"

usage() {
	cat <<USAGE
usage: $script_name [options]

options:
  --frr-root PATH       FRR checkout root. Default inference order:
                        ../frr, ~/devel/frr, or parent when run from an FRR tree.
  --no-eigrpd           Do not assemble/install FRR/eigrpd/.
  --no-tests            Do not copy frr/test/ into FRR tests/eigrpd/.
  --schema-only         Validate upstream EIGRP YANG/vtysh and refresh embedded YANG;
                        do not stage daemon/tests.
  --dry-run             Validate and print actions without changing FRR.
  --help                Show this help.

installs:
  eigrp/code/ + frr/code/* -> FRR/eigrpd/
  frr/test/eigrpd/      -> FRR/tests/eigrpd/
  frr/test/topotests/    -> FRR/tests/topotests/

Only native FRR schema and VTY support are accepted; no FRR-wide source edits.
USAGE
}

fail() {
	echo "error: $*" >&2
	exit 1
}

require_command() {
	command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

is_abs_path() {
	case "$1" in
		/*) return 0 ;;
		*) return 1 ;;
	esac
}

resolve_existing_path() {
	local path="$1"
	if is_abs_path "$path"; then
		cd "$path" && pwd
	else
		cd "$PWD" && cd "$path" && pwd
	fi
}

infer_frr_root() {
	local candidate

	for candidate in \
		"$eigrp_root/../frr" \
		"$HOME/devel/frr" \
		"$eigrp_root/.."; do
		if [[ -d "$candidate/tests" && -f "$candidate/bootstrap.sh" ]]; then
			cd "$candidate" && pwd
			return 0
		fi
	done

	return 1
}

inject_eigrp_release() {
	local stage="$1"
	local release=""
	local tag=""

	# Release builds must correspond to the tagged source at HEAD.
	if [[ -n "${EIGRP_BUILD_VERSION:-}" ]]; then
		release="$EIGRP_BUILD_VERSION"
		if [[ "$release" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
			tag="rel-$release"
			git -C "$eigrp_root" tag --points-at HEAD --list "$tag" \
				| grep -Fxq "$tag" || \
				fail "release build requires EIGRP tag $tag at HEAD"
		fi
	else
		release="$("$eigrp_root/tools/version.sh" --local)" || \
			fail "unable to resolve OpenEIGRP development version"
	fi

	# Always regenerate the staged header through the same generator as the
	# portable Makefile.  Never mutate the feature contract or copy a stale
	# local header into an independently staged FRR build.
	python3 "$eigrp_root/tools/gen-version.py" \
		--version "$release" --output "$stage/eigrp_build_version.h" || \
		fail "unable to generate OpenEIGRP build identity"
	echo "release: $release"
}

rsync_project_tree() {
	local src="$1"
	local dst="$2"
	shift 2

	[[ -d "$src" ]] || fail "source directory not found: $src"
	mkdir -p "$dst"
	rsync -a "$@" \
		--exclude '.git/' \
		--exclude '.pytest_cache/' \
		--exclude '__pycache__/' \
		--exclude '__MACOSX/' \
		--exclude '.DS_Store' \
		--exclude '*.o' \
		--exclude '*.lo' \
		--exclude '*.la' \
		--exclude '*_clippy.c' \
		--exclude '*~' \
		--exclude 'obj/' \
		--exclude 'logs/' \
		"$src"/ "$dst"/
}

assemble_eigrpd_tree() {
	local stage="$1"

	[[ -d "$common_src" ]] || fail "missing common EIGRP source: $common_src"
	[[ -d "$frr_src" ]] || fail "missing FRR adapter source: $frr_src"

	mkdir -p "$stage"

	# Portable source establishes the base daemon tree.
	rsync_project_tree "$common_src" "$stage"
	inject_eigrp_release "$stage"

	# FRR-specific daemon files overlay the portable tree.
	rsync_project_tree "$frr_src" "$stage"
}

install_eigrpd_tree() {
	local dst="$frr_root/eigrpd"
	local stage

	stage="$(mktemp -d)"
	assemble_eigrpd_tree "$stage"

	if [[ -L "$dst" ]]; then
		fail "FRR eigrpd destination is a symlink; split common/FRR source requires a real staging directory: $dst"
	fi

	if [[ "$dry_run" -eq 1 ]]; then
		echo "would assemble: eigrp/code/ + frr/code/ -> $dst/"
		rm -rf "$stage"
		return 0
	fi

	mkdir -p "$dst"
	rsync -a --delete "$stage"/ "$dst"/
	rm -rf "$stage"
	echo "installed: eigrp/code/ + frr/code/ -> $dst/"
}

install_test_tree() {
	local dst="$frr_root/tests/eigrpd"

	if [[ ! -d "$frr_test_src" ]]; then
		echo "warning: no FRR test payload exists at $frr_test_src" >&2
		return 0
	fi

	if [[ "$dry_run" -eq 1 ]]; then
		echo "would sync: $frr_test_src/ -> $dst/"
		return 0
	fi

	mkdir -p "$dst"
	rsync_project_tree "$frr_test_src" "$dst" --delete
	echo "installed: frr/test/eigrpd/ -> $dst/"
}


install_topotest_tree() {
	local dst="$frr_root/tests/topotests"

	if [[ ! -d "$frr_topotest_src" ]]; then
		return 0
	fi
	if ! find "$frr_topotest_src" -mindepth 1 -type f | grep -q .; then
		return 0
	fi

	if [[ "$dry_run" -eq 1 ]]; then
		echo "would sync: $frr_topotest_src/ -> $dst/"
		return 0
	fi

	mkdir -p "$dst"
	rsync_project_tree "$frr_topotest_src" "$dst"
	echo "installed: frr/test/topotests/ -> $dst/"
}


eigrp_grammar_schema_current() {
	local yang="$frr_root/yang/frr-eigrpd.yang"
	local neighbor_policy
	local redistribution_protocol
	local redistribution_route_instance
	local protocol
	local topology

	[[ -f "$yang" ]] || return 1
	grep -Fq 'list named {' "$yang" || return 1
	grep -Fq 'key "afi vrf asn";' "$yang" || return 1
	grep -Fq 'list af-interface {' "$yang" || return 1
	grep -Fq 'EIGRP_STEP1_TOPOLOGY_COMPOUND_MANDATORY' "$yang" || return 1
	grep -Fq 'EIGRP_STEP1_CONFIG_COMPLETE' "$yang" || return 1
	grep -Fq 'typedef eigrp-redistribution-protocol {' "$yang" || return 1
	grep -Fq 'typedef eigrp-redistribution-route-instance {' "$yang" || return 1
	grep -Fq 'key "protocol route-instance";' "$yang" || return 1
	grep -Fq 'type eigrp-redistribution-protocol;' "$yang" || return 1
	grep -Fq 'type eigrp-redistribution-route-instance;' "$yang" || return 1
	grep -Fq "must \"../protocol != 'eigrp' or . != 0\" {" "$yang" || return 1
	grep -Fq "must \"../protocol = 'eigrp' or ../protocol = 'ospf' or . = 0\" {" "$yang" || return 1
	grep -Fq 'A BGP ASN or IS-IS area tag is a protocol' "$yang" || return 1

	redistribution_protocol="$(sed -n '/^[[:space:]]*typedef eigrp-redistribution-protocol {/,/^[[:space:]]*typedef eigrp-redistribution-route-instance {/p' "$yang")"
	[[ -n "$redistribution_protocol" ]] || return 1
	for protocol in connected static rip ospf isis bgp eigrp; do
		grep -Fq "enum $protocol;" <<<"$redistribution_protocol" || return 1
	done
	[[ "$(grep -Ec '^[[:space:]]*enum[[:space:]]+[a-z0-9-]+;' <<<"$redistribution_protocol")" -eq 7 ]] || return 1

	redistribution_route_instance="$(sed -n '/^[[:space:]]*typedef eigrp-redistribution-route-instance {/,/^[[:space:]]*typedef authentication-type {/p' "$yang")"
	[[ -n "$redistribution_route_instance" ]] || return 1
	grep -Fq 'type uint16 {' <<<"$redistribution_route_instance" || return 1
	grep -Fq 'range "0..65535";' <<<"$redistribution_route_instance" || return 1

	grep -Fq "must \". != 'hmac-sha-256' or (../authentication-encryption-type and ../authentication-password)\";" "$yang" || return 1
	grep -Fq 'when "../administrative-distance";' "$yang" || return 1
	grep -Fq 'description "Address-family metric weights: TOS, K1 through K5, and optional RFC 7868 K6";' "$yang" || return 1
	grep -Fq 'description "IPv4 or IPv6 summary prefix";' "$yang" || return 1
	grep -Fq 'description "Fixed metric for an IPv4 or IPv6 summary aggregate";' "$yang" || return 1
	if grep -Fq 'must "count(../instance[vrf =current()/vrf]) = 1";' "$yang"; then
		return 1
	fi

	neighbor_policy="$(sed -n '/^[[:space:]]*list neighbor-policy {/,/^[[:space:]]*container neighbor-maximum-prefix {/p' "$yang")"
	[[ -n "$neighbor_policy" ]] || return 1
	if grep -Fq 'leaf dampened' <<<"$neighbor_policy"; then
		return 1
	fi

	topology="$(sed -n '/^[[:space:]]*container topology {/,/^[[:space:]]*uses frr-filter:distribute-list-group;/p' "$yang")"
	[[ -n "$topology" ]] || return 1
	if grep -Fq 'container metric-weights {' <<<"$topology"; then
		return 1
	fi

	return 0
}

invalidate_eigrp_yang_embed() {
	local count
	local yang_source="$frr_root/yang/frr-eigrpd.yang"
	local yang_embed="$frr_root/yang/frr-eigrpd.yang.c"

	[[ -f "$yang_source" ]] || fail "FRR EIGRP YANG source not found: $yang_source"

	# The .yang source is authoritative. FRR generates frr-eigrpd.yang.c as a
	# build artifact. Incompatible or duplicate upstream schema is rejected,
	# not rewritten or repaired by the installer.
	if [[ "$dry_run" -eq 1 ]]; then
		echo "would validate: exactly one EIGRP named-mode schema node"
		echo "would remove generated: yang/frr-eigrpd.yang.c"
		return 0
	fi

	count="$(grep -Ec '^[[:space:]]*list[[:space:]]+named[[:space:]]*\{' "$yang_source" || true)"
	[[ "$count" -eq 1 ]] || \
		fail "expected exactly one upstream EIGRP named-mode schema node; found $count"

	rm -f "$yang_embed"
	echo "invalidated generated: yang/frr-eigrpd.yang.c (FRR build will regenerate it)"
}

while [[ "$#" -gt 0 ]]; do
	case "$1" in
		--frr-root)
			[[ "$#" -ge 2 ]] || fail "--frr-root requires a path"
			frr_root="$(resolve_existing_path "$2")"
			shift 2
			;;
		--no-eigrpd)
			install_eigrpd=0
			shift
			;;
		--no-tests)
			install_tests=0
			shift
			;;
		--schema-only)
			schema_only=1
			install_eigrpd=0
			install_tests=0
			shift
			;;
		--dry-run)
			dry_run=1
			shift
			;;
		--help)
			usage
			exit 0
			;;
		*)
			fail "unknown option: $1"
			;;
	esac
done

require_command rsync
require_command git

if [[ -z "$frr_root" ]]; then
	if ! frr_root="$(infer_frr_root)"; then
		fail "FRR root could not be inferred; use --frr-root /path/to/frr"
	fi
fi

[[ -d "$frr_root" ]] || fail "FRR root does not exist: $frr_root"
[[ -f "$frr_root/bootstrap.sh" ]] || fail "not an FRR checkout root: $frr_root"
git -C "$frr_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 || \
	fail "FRR root is not a Git working tree: $frr_root"
[[ -d "$common_src" ]] || fail "missing project eigrpd directory: $common_src"
[[ -d "$frr_src" ]] || fail "missing project FRR directory: $frr_src"

# Native FRR integration is a prerequisite for every staging/build action.
# A source without required named-mode support is not an installable target.
if ! eigrp_grammar_schema_current; then
	fail "FRR YANG schema lacks required upstream EIGRP named-mode support"
fi
if ! grep -Fq 'router eigrp <(1-65535)|WORD>' "$frr_root/vtysh/vtysh.c" ||
   ! grep -Fq 'install_element(EIGRP_NODE, &router_eigrp_cmd);' "$frr_root/vtysh/vtysh.c"; then
	fail "FRR vtysh lacks required upstream EIGRP named-mode support"
fi
if [[ "$schema_only" -eq 1 ]]; then
	invalidate_eigrp_yang_embed
	exit 0
fi

if [[ "$install_eigrpd" -eq 1 ]]; then
	install_eigrpd_tree
fi

if [[ "$install_tests" -eq 1 ]]; then
	install_test_tree
	install_topotest_tree
fi
