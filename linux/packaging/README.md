# Native Debian/Ubuntu package

This is a **local build** for Debian and Ubuntu. No APT repository, Docker
image, FRR/BIRD component or UUT broker is required at runtime. The package
name is `openeigrpd`; it installs `openeigrpd`, `oectl`, and the offline
`openeigrpd-config` validator.

## Native daemon prerequisite

`make linux` builds the real `linux/code/openeigrpd` foreground daemon,
connecting the portable EIGRP runtime to Linux raw sockets, Netlink and
local IPC. The package builder still fails closed if this executable is
missing or is not a native Linux ELF; UUT fixtures must never be shipped
as a routing daemon.

The daemon accepts `-f PATH`, `-s SOCKET`, `-t` for offline syntax checks,
`-V` and `-h`. Systemd uses `LoadCredential=` to read the root-only
configuration, creates `/run/openeigrpd` with safe ownership, and launches
the daemon with the EIGRP routing capabilities. The management socket is
`/run/openeigrpd/control.sock` (0660, group `openeigrpctl`). SIGTERM
stops management and AF workers and releases kernel routes. SIGHUP is
rejected without mutation until a failure-atomic live reload exists.

## Build

Install local build prerequisites (example on Debian/Ubuntu):

```sh
sudo apt-get install build-essential dpkg-dev binutils systemd
```

Build the real daemon and create a native package from the repository root:

```sh
make linux
```

Then run:

```sh
linux/packaging/build-deb.sh --version 0.1.0
# output: dist/openeigrpd_0.1.0_amd64.deb (or arm64 on native ARM64)
```

A known daemon path and output directory can also be supplied:

```sh
linux/packaging/build-deb.sh --version 0.1.0 \
    --daemon /path/to/real/openeigrpd --output ./dist
```

Build on the target architecture (native `amd64` or `arm64`). The script
rejects non-native ELF executables and unsupported architectures; it does
not cross-compile. It runs the existing Linux CLI/config-validator build,
derives actual shared-library dependencies using `dpkg-shlibdeps`, and
assembles the package unprivileged with `dpkg-deb --root-owner-group`.
The final archive is independently checked for package identity, architecture,
configuration ownership/mode, required executables, service files, and routing-suite
dependencies before it is reported as built or published. The builder
normalizes directory modes so an administrator's restrictive shell `umask`
cannot produce an uninstallable package or inaccessible installed paths.
Build on the oldest distribution you intend to support, or use a clean
per-distribution builder, because binary glibc requirements are build-host
specific. Only native amd64 package construction has been exercised here;
ARM64 and Ubuntu are not yet independently qualified.

Inspect before installing:

```sh
dpkg-deb --info dist/openeigrpd_*.deb
dpkg-deb --contents dist/openeigrpd_*.deb
dpkg-deb --ctrl-tarfile dist/openeigrpd_*.deb | tar -tvf -
linux/packaging/verify-deb.sh dist/openeigrpd_0.1.0_amd64.deb 0.1.0 amd64
```

## Dual-architecture builds on an ARM64 Mac

Docker Desktop can build **Linux** Debian binaries for both `arm64` (native
container) and `amd64` (emulated container). No development toolchain is
required on Stormtroopers, and macOS binaries are never included. From the
repository root, with Docker Desktop running:

```sh
make packages VERSION=0.1.0
# or: linux/packaging/build-packages.sh --version 0.1.0
```

Both packages appear in `dist/` **only after both builds succeed**:

```text
dist/openeigrpd_0.1.0_amd64.deb
dist/openeigrpd_0.1.0_arm64.deb
```

For a single architecture:

```sh
linux/packaging/build-packages.sh --version 0.1.0 --arch amd64
linux/packaging/build-packages.sh --version 0.1.0 --arch arm64
```

The wrapper uses `debian:12` for each platform, copies source into a fresh
container workspace, deletes stale object files and host-built binaries,
runs `make linux` and the executable packaging tests, then builds the package.
It deliberately requires a **newly built** `linux/code/openeigrpd`. Until
that daemon exists in the Linux Makefile, production packages fail closed.
The source mount is read-only and an unsuccessful dual-build does not
publish half a pair. Emulated AMD64 builds on Apple silicon are slower and
should be independently tested on actual AMD64 Linux.

Docker image tags and Debian APT packages can change. The fixed Debian suite
keeps the glibc baseline consistent but does **not** make this a bit-for-bit
reproducible build; pin image digests and package dependencies if that becomes
a release requirement. For Ubuntu-targeted packages needing other runtime
libraries, build and qualify separately for the oldest supported Ubuntu
release rather than assuming cross-distribution compatibility.

## GitHub release packaging

`.github/workflows/debian.yml` runs two separate native runner jobs:

- `ubuntu-24.04` produces Linux `amd64`
- `ubuntu-24.04-arm` produces Linux `arm64`

Both invoke **the same container wrapper** and Debian 12 build. Pull requests
to `master` touching packaging or Linux sources run both jobs. `workflow_dispatch`
builds both test artifacts but does not publish. A matching `rel-X.Y.Z` tag
triggers both builds, checks package architecture and version, then runs the full portable `make uut` release gate before attaching
both `.deb` files and a `SHA256SUMS` file to that tag's GitHub Release.
Missing/failing architectures block publication. The publishing job does not
overwrite release assets, and does not start a hosted APT repository.

This is separate from the existing FRR Docker release workflow; neither is a
replacement for the full portable UUT/Netlab release regression gates.
Release qualification still requires live Linux routing, real-systemd
lifecycle and Cisco interoperability tests on privileged infrastructure;
the executable and local package builder are implemented.

## Installation and service lifecycle

```sh
sudo apt install ./dist/openeigrpd_0.1.0_amd64.deb
sudoedit /etc/openeigrpd.conf
sudo openeigrpd-config -t -f /etc/openeigrpd.conf
sudo systemctl enable --now openeigrpd.service
sudo systemctl status openeigrpd.service
sudo oectl show protocols
sudo systemctl restart openeigrpd.service
sudo systemctl stop openeigrpd.service
sudo systemctl disable openeigrpd.service
```

**Installation does not enable or start routing.** To activate it, explicitly
configure and enable/start the service. The default configuration contains
no routing instance. Validate your router configuration and privileges before
enabling the service. `systemctl restart` is intentionally
used instead of reload: a failure-atomic daemon reload has not landed.

The service runs under system user `openeigrp`, using capabilities
`CAP_NET_RAW` and `CAP_NET_ADMIN` (no unrestricted root process). Systemd
creates `/run/openeigrpd` at start and removes it at stop. The separate
`openeigrpctl` group may be assigned to trusted users for read-only
management socket access; for example:

```sh
sudo usermod -aG openeigrpctl USERNAME
```

Group membership does **not** grant the `clear events` operation: the IPC
server checks `SO_PEERCRED` and only allows root or daemon UID to modify
state. The control directory is not group-writable. Re-login for group
membership to take effect. `oectl` does not require privileged packet
capabilities.

The configuration is a dpkg-managed `conffile` at `/etc/openeigrpd.conf`
owned by root with mode `0600`. Authentication keys require this mode.
`LoadCredential=` makes a private read-only copy accessible to the service
user without making the root file group-readable or daemon-writable. The
configuration is preserved on upgrade and ordinary package removal.

On upgrade, a previously running service is **try-restarted** through
`deb-systemd-invoke` (respecting host service policy); a stopped or disabled
service is not started. Existing enablement is not changed. The package
scripts do not create persistent state directories or install external
routing software. During removal the service is stopped and the unit is
uninstalled. Configuration remains until purge:

```sh
sudo apt remove openeigrpd     # preserves /etc/openeigrpd.conf
sudo apt purge openeigrpd      # removes the conffile
```

The system UID and management group are deliberately retained on purge;
removing/reassigning a daemon UID can inadvertently grant access to files
still owned by that numeric UID. The transient runtime directory is owned
and cleaned by systemd, not maintained by package scripts.

## Test and qualification

```sh
python3 linux/test/test_packaging.py
# DANGEROUS: installs test packages into the running OS. Use a disposable VM.
sudo OPENEIGRP_PACKAGE_SYSTEM_TEST=1 python3 linux/test/test_packaging.py
python3 linux/test/test_oectl.py
python3 linux/test/test_config.py
```

The root-gated test performs actual `dpkg` install, upgrade, modified
conffile preservation, remove and purge using a clearly identified **test
fixture daemon**, never a production binary. Running that test confirms
package-manager behavior only. For release qualification, repeat installation
and the `systemctl` commands above inside fresh Debian/Ubuntu VMs, with a
real daemon, actual IP proto-88 peers and Netlab scenarios; verify the
control socket group and capability restrictions, graceful withdrawal,
shutdown and restart. `linux/test/test_daemon.py` exercises the actual executable and IPC server,
but this restricted test environment cannot exercise CAP_NET_RAW,
CAP_NET_ADMIN or privileged Cisco/Netlab adjacency and kernel RIB paths.
A live systemd PID 1 VM remains required for release qualification.
