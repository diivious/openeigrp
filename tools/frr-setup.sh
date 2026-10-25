#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
#
# Copyright (C) 2026 Donnie V. Savage
#
# Debian development-machine setup helper for EIGRP + FRR.

set -euo pipefail

script_name="$(basename "$0")"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
invocation_dir="$(pwd -P)"
devel_root="$(cd "$invocation_dir/.." && pwd -P)"
frr_root=""
frr_url=https://github.com/FRRouting/frr.git
frr_tag=frr-10.7.1
frr_sha=f8c0b08dcb0c78f9e42b9b86ae70b049e4e617c1
skip_clone=0
skip_build=0

usage() {
	cat <<USAGE
usage: $script_name [options]

options:
  --devel-root PATH    Development root. Default: parent of the current
                       working directory. Relative paths are resolved from CWD.
  --skip-clone         Require an existing FRR checkout; do not clone.
  --skip-build         Do not configure/build/install FRR.
  --help               Show this help.

This script is intentionally Debian-focused. Run it from an existing EIGRP
checkout. It installs FRR build packages, clones the official FRR 10.7.1
release if needed, stages this EIGRP tree into FRR, and creates config files.
An existing checkout must already be at the pinned release commit.
USAGE
}

fail() {
	echo "error: $*" >&2
	exit 1
}

while [[ "$#" -gt 0 ]]; do
	case "$1" in
		--devel-root)
			[[ "$#" -ge 2 ]] || fail "--devel-root requires a path"
			devel_root="$2"
			shift 2
			;;
		--skip-clone)
			skip_clone=1
			shift
			;;
		--skip-build)
			skip_build=1
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

if [[ "${EUID}" -eq 0 ]]; then
	fail "do not run this script as root"
fi

if ! groups | grep -qw sudo; then
	cat >&2 <<MSG
error: user must be in the sudo group. As root, run:
    /sbin/usermod -aG sudo $USER
    /sbin/usermod -aG adm $USER
Then log out and back in.
MSG
	exit 1
fi

case "$devel_root" in
	/*) ;;
	*) devel_root="$invocation_dir/$devel_root" ;;
esac

mkdir -p "$devel_root"
devel_root="$(cd "$devel_root" && pwd -P)"
frr_root="$devel_root/frr"

if ! grep -q '^/usr/local/lib$' /etc/ld.so.conf 2>/dev/null; then
	echo "Adding /usr/local/lib to /etc/ld.so.conf"
	echo '/usr/local/lib' | sudo tee -a /etc/ld.so.conf >/dev/null
	sudo ldconfig
fi

echo "Installing FRR build dependencies"
sudo apt-get update
sudo apt-get install -y \
	build-essential wget git autoconf automake libtool make \
	libreadline-dev texinfo libjson-c-dev pkg-config bison flex \
	libc-ares-dev python3-dev python3-pytest python3-sphinx \
	libsnmp-dev libcap-dev libelf-dev libunwind-dev \
	libprotobuf-c-dev protobuf-c-compiler rsync patch

# Debian 12 often needs newer libyang2 than the base repo provides for FRR.
echo "Checking libyang2"
if ! apt list -a libyang2 2>/dev/null | grep -q '2\.1\.128'; then
	work_dir="$(mktemp -d)"
	trap 'rm -rf "$work_dir"' EXIT
	(
		cd "$work_dir"
		case "$(uname -m)" in
			aarch64|arm64)
				base='https://ci1.netdef.org/artifact/LIBYANG-LIBYANG2/shared/build-00184/Debian-12-arm8-Packages'
				arch='arm64'
				;;
			*)
				base='https://ci1.netdef.org/artifact/LIBYANG-LIBYANG2/shared/build-00184/Debian-12-x86_64-Packages'
				arch='amd64'
				;;
		esac
		for pkg in libyang2 libyang2-dev libyang2-tools; do
			wget "$base/${pkg}_2.1.128.83.gfc4dbd92-1~deb12_${arch}.deb"
		done
		sudo apt-get install -y ./*.deb
	)
fi

if ! getent group frr >/dev/null; then
	echo "Creating FRR groups/users"
	sudo addgroup --system --gid 92 frr
	sudo addgroup --system --gid 85 frrvty
	sudo adduser --system --ingroup frr --home /var/opt/frr/ --gecos "FRR suite" --shell /bin/false frr
	sudo usermod -a -G frrvty frr
fi

sudo usermod -a -G frr "$USER"
sudo usermod -a -G frrvty "$USER"

if [[ "$skip_clone" -eq 0 && ! -e "$frr_root" ]]; then
	echo "Cloning official FRR release $frr_tag"
	git clone --branch "$frr_tag" --depth 1 --single-branch "$frr_url" "$frr_root"
fi

[[ -d "$frr_root" ]] || fail "FRR checkout not found: $frr_root"
git -C "$frr_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 || \
	fail "FRR path is not a Git checkout: $frr_root"
actual_sha="$(git -C "$frr_root" rev-parse HEAD)"
[[ "$actual_sha" == "$frr_sha" ]] || \
	fail "FRR checkout is at $actual_sha, expected $frr_tag ($frr_sha); checkout is unchanged"

"$script_dir/frr-install.sh" --frr-root "$frr_root"

if [[ "$skip_build" -eq 0 ]]; then
	"$script_dir/frr.sh" --all --frr-root "$frr_root"
	sudo make -C "$frr_root" install
fi

echo "Creating FRR config directories"
sudo install -m 775 -o frr -g frrvty -d /etc/frr
sudo install -m 755 -o frr -g frrvty /dev/null /etc/frr/vtysh.conf
sudo install -m 755 -o frr -g frr -d /var/log/frr
sudo install -m 755 -o frr -g frr -d /var/opt/frr

echo 'service integrated-vtysh-config' | sudo tee /etc/frr/vtysh.conf >/dev/null
sudo chmod 640 /etc/frr/vtysh.conf
sudo cp "$script_dir/etc.frr.frr.conf" /etc/frr/frr.conf

sudo install -m 640 -o frr -g frr \
	"$frr_root/tools/etc/frr/daemons" /etc/frr/daemons
if [[ -f "$frr_root/tools/frr.service" ]]; then
	sudo cp "$frr_root/tools/frr.service" /etc/systemd/system/frr.service
fi

sudo systemctl daemon-reload
sudo systemctl restart frr || sudo systemctl start frr

echo "Setup complete. Log out/back in if FRR group membership was just added."
