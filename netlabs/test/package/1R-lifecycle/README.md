# 1R-lifecycle: Debian package qualification in Containerlab

Copyright (C) 2026 Donnie V. Savage

The single Netlab `1R-lifecycle` scenario exercises the native Debian package
inside a disposable Debian systemd container with **systemd as PID 1**. It uses the
same Containerlab provider as the existing FRR tests. No Vagrant/libvirt, Cisco
router, or extra virtual machine manager is needed.

## Build and run

```sh
make all
netlabs/tools/remote-uut.sh --local --package
netlabs/tools/remote-uut.sh --local --all
netlabs/tools/remote-uut.sh --local --tag 0.5.1 --package
netlabs/tools/remote-uut.sh --package
netlabs/tools/remote-uut.sh --tag 0.5 --package
```

`make all` builds the native and AMD64 FRR images, the AMD64 and ARM64
Debian packages, and `openeigrp-package:netlab`, an AMD64 Debian systemd
image without OpenEIGRP preinstalled. It also creates
`dist/netlabs-amd64.json` with the exact package checksums and image IDs.
On an ARM64 Mac, the native `openeigrp:diivious` alias remains ARM64;
`openeigrp:diivious-test` is the AMD64 test alias. The remote runner uses
the versioned AMD64 FRR image, never the native alias.

`remote-uut.sh --local` verifies the images and packages against this build
manifest, then copies them to StormTrooper. An optional local tag must match
the manifest's exact numeric build version (for example, `0.5.1`). Without
`--local`, the package test selects only an official GitHub release `.deb`
for the installed published image (or exact `--tag X.Y`). If the `.deb` is
not published, the lifecycle is BLOCKED instead of silently using development artifacts. It never pulls or
builds local artifacts. The package image is built from the Dockerfile in
this directory using `--platform linux/amd64`. The test installs the actual
transferred `.deb` using APT and verifies it using
`linux/packaging/verify-deb.sh`.

For local upgrade tests, a published release `.deb` from GitHub Releases is
version A, and the locally built development package is B. Published-only
package tests use the released package and do not claim an upgrade. `make all` downloads the official release asset if published, checks
`SHA256SUMS`, and validates Debian version ordering. If
no release package is published, stopped/running upgrade phases are `SKIPPED`
while install, service, remove, reinstall and purge still run. Run `make all` again after publishing a release to select the new baseline. No public
APT package repository is required.

The disposable image launches a short init wrapper which makes its private
mount tree recursively shared before replacing itself with systemd as PID 1.
Docker can otherwise hide systemd `LoadCredential=` mounts from a service's
mount namespace, leaving its credential directory empty even though the unit
starts executing. The wrapper runs on both first boot and `docker restart`.
If mount propagation cannot be established, the container fails immediately;
production service hardening and configuration permissions are not relaxed.

`provider: clab`, `device: linux`, and topology name `eigrp_pkg` are fixed.
The name meets Netlab's 16-character identifier rule. The exact owned Docker
container name is `clab-eigrp_pkg-uut`. The driver rejects an existing
container, checks for preexisting OpenEIGRP state in each fresh container,
then transfers the two packages (or B alone) and the installed-package test
script. Three independent containers are created when A exists; otherwise
two are created. `netlab down` must remove each one before the next phase.
The remote runner also checks for leftover container ownership before
reporting successful cleanup.

## Verified behavior

1. **Fresh service:** APT dependency resolution, metadata, installed files,
   ownership and permissions, systemd/sysusers/man pages, required identities,
   start/stop/restart/enable/disable, installed management client and validator,
   capabilities, hardening, runtime socket, error diagnostics and journal
   secret checks. Restart the **same container** to check systemd startup and
   persistent enablement, then purge and verify cleanup.
2. **Stopped upgrade:** Install A, change configuration, upgrade to B and
   verify version, retained configuration and no unwanted service start.
3. **Running lifecycle:** Install A, start and enable the daemon, upgrade B,
   verify that a new process is running and the management client works,
   then remove, reinstall and purge. Verify configuration retention and that
   unrelated files are preserved.

The no-published-release path uses `local-lifecycle` instead of phases 2/3.
The guest checks are reused from `linux/test/test_systemd_package.py`. They
run only with an explicit disposable-container marker and Docker systemd PID 1;
never run this acceptance script on a workstation or an existing installation.

**Coverage limit:** `docker restart` replaces container userspace and PID 1,
not the Linux kernel. A true machine reboot is therefore marked `NOT_TESTED`.
The test also does not claim Cisco EIGRP interoperability, other CPU architectures,
or independent distro support. Containerlab's default Linux-node privilege
and Docker service/cgroup support are required for systemd. If systemd does not
start with its expected Linux capabilities, report a real failure rather than
silently disabling unit hardening or running a standalone daemon.

The driver saves per-phase logs, Netlab operations, and `report.json`; the
remote runner adds structured cleanup evidence. Inspect `logs/netlab/` after
a run. On a partial deployment, use the existing Netlab abort/recovery path;
do not delete an unverified lab or install packages on the appliance itself.
