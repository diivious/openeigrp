# Live systemd package qualification

This is the final, privileged qualification step for the native `openeigrpd`
Debian package. The ordinary `linux/test/test_packaging.py` exercise verifies
real `dpkg` operations in a non-systemd container; it **cannot** verify a
systemd service running under PID 1 or live routing capabilities.

Use a **disposable, clean Debian or Ubuntu VM** with systemd as PID 1,
networking isolated from production, and `CAP_NET_RAW` and `CAP_NET_ADMIN`
available. Do not use a production router, development workstation, or
container that does not boot systemd. The test changes system accounts, the
dpkg database, `/etc`, `/usr`, and systemd enablement, and purges the package.
System account names may remain after purge by design.

Obtain the source checkout and build **two versions** of the same native ELF
on the VM (or use two appropriately versioned packages built for that VM's
architecture and libc). For example, from the repository root:

```sh
make linux
linux/packaging/build-deb.sh --version 0.1.0~qa1
linux/packaging/build-deb.sh --version 0.1.0~qa2
```

Do not run the systemd lifecycle test if any OpenEIGRP package or configuration
already exists. Verify the VM is disposable and run:

```sh
sudo env \
  OPENEIGRP_PACKAGE_SYSTEMD_TEST=1 \
  OPENEIGRP_PACKAGE_OLD="$PWD/dist/openeigrpd_0.1.0~qa1_$(dpkg --print-architecture).deb" \
  OPENEIGRP_PACKAGE_NEW="$PWD/dist/openeigrpd_0.1.0~qa2_$(dpkg --print-architecture).deb" \
  python3 linux/test/test_systemd_package.py
```

The test checks: installation is inactive and disabled; the root-only
configuration is accepted; systemd creates a group-traversable runtime
directory and a protected control socket; the real `oectl` talks to the real
daemon; daemon UID, GID, effective capabilities, and `NoNewPrivs` are correct;
restart works; stopped upgrades stay stopped, while running upgrades preserve administrator configuration and
service enablement; stop cleans the socket; package removal stops the service
and preserves configuration; purge deletes the configuration. All package
inputs are checked with the native `.deb` manifest verifier before installation.

A passing result is **not** a Cisco interoperability or DUAL convergence test.
Additionally qualify real protocol-88 neighbor formation, route install/
withdrawal, and teardown on an isolated Linux/Netlab topology, using the
existing portable YAML UUT and Cisco interoperability gates as appropriate.
Repeat on both amd64 and arm64 VMs, and on each Debian/Ubuntu release claimed
to be supported. Docker cross-platform orchestration tests alone do not
establish arm64 runtime compatibility.
