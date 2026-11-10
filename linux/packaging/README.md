# Native Debian/Ubuntu package

This is a **local build** for Debian and Ubuntu. No APT repository, Docker
image, FRR/BIRD component or UUT broker is required at runtime. The package
name is `openeigrpd`; it installs `openeigrpd`, `oectl`, and the offline
`openeigrpd-config` validator.

## Prerequisite: a real native daemon

**The current `linux-support` branch (Task 12) has no runnable
`linux/code/openeigrpd`.** It builds native libraries and `oectl`, but the
scheduler/process entry point has not landed. Consequently the package
builder intentionally fails without a daemon executable. It must not package
the configuration validator, a test fixture, or the UUT broker as the daemon.

The executable supplied to the builder must be a complete native Linux ELF
implementing `openeigrpd -f PATH` and reading the specified configuration.
The systemd unit uses this interface to pass a private `LoadCredential=` copy
of the root-owned configuration. It must also create the IPC socket at
`/run/openeigrpd/control.sock` with mode `0660` and group `openeigrpctl`,
handle SIGTERM, and use the routing capabilities supplied by systemd. These
are integration requirements, **not capabilities currently verified in
`linux-support`**.

## Build

Install local build prerequisites (example on Debian/Ubuntu):

```sh
sudo apt-get install build-essential dpkg-dev binutils systemd
```

With a **real** daemon executable already compiled as `linux/code/openeigrpd`,
run from the repository root:

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
Release publishing remains blocked until the standalone daemon is available
and live Linux routing/systemd lifecycle tests pass.

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
no routing instance. Do not enable before the real daemon's process and
configuration contract is verified. `systemctl restart` is intentionally
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
shutdown and restart. No native daemon or live systemd PID 1 is present in
the provided baseline, so those release gates cannot yet pass.

## Developer entrypoint: `tools/package.sh`

The repository's `tools/package.sh` is the Debian packaging counterpart to
`tools/docker.sh`. It uses the local working tree (including uncommitted edits),
and executes Linux build operations inside Docker rather than requiring a Linux
compiler on macOS.

Both integrations resolve the development version from the highest numeric
`rel-X.Y.Z` tag reachable from the freshly fetched `origin/master`. A release
must be merged into `master` before it advances development versioning. If
Git metadata is unavailable or the fetch fails, the scripts stop rather than
manufacture a fallback version.

For example, with `rel-0.5.0` on `master`:

```sh
./tools/docker.sh --local diivious-test --platform linux/amd64 \
    --image openeigrp:diivious-test
./tools/package.sh --local diivious-test --platform linux/amd64 \
    --image openeigrp:diivious-test
# Both embed 0.5.0-diivious-test.
# Docker image reference: openeigrp:diivious-test
# Debian output: dist/openeigrpd_0.5.0-diivious-test_amd64.deb
```

Omitting a local label uses `$USER-test`; `--local X-Y` uses `0.5.0-X-Y`.
After `rel-0.6.0` is merged to `master`, the same commands embed
`0.6.0-diivious-test`. An explicitly requested release version (e.g.
`--local 0.6.0`) stays `0.6.0`, subject to the Docker release-source checks.
Run `tools/version.sh --latest` to inspect the fetched release baseline.

`--image` in `tools/package.sh` identifies the corresponding Docker image;
its tag must match the `--local` label. The image is **not** executed or
required for packaging. Use `--builder-image debian:12` (default) or
`--builder-image ubuntu:24.04` to select the real Debian package builder.
An Alpine OpenEIGRP runtime image is not a suitable package builder.

```sh
# Compile and test fixture-based dpkg install/upgrade/remove/purge in Docker.
tools/package.sh --uut
tools/package.sh --uut --platform linux/arm64 --builder-image ubuntu:24.04

# Build the production package from the working tree (real daemon required).
tools/package.sh --local diivious-test --platform linux/amd64 \
    --image openeigrp:diivious-test
tools/package.sh --local X-Y --platform linux/arm64 \
    --image openeigrp:X-Y --builder-image ubuntu:24.04

# Optional output override.
tools/package.sh --local X-Y --platform linux/amd64 --output /tmp/packages
```

The local working tree is mounted read-only and copied into the builder.
`--uut` does not publish test fixtures, and `--local` refuses to publish a
production package without a real native `openeigrpd`. Neither mode enables
routing. A separate production release build uses the exact release version
without a development suffix.

Wrapper tests that do not require a Docker daemon:

```sh
python3 tools/test/test_version.py
python3 tools/test/test_package.py
python3 tools/test/test_docker_source_selection.py
python3 linux/test/test_package_builds.py
```

`--uut` covers the Debian package lifecycle, not portable EIGRP YAML UUT
scenarios or live systemd routing, which must be qualified separately.
