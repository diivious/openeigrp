# OpenEIGRP Native Linux Integration

This directory contains the standalone OpenEIGRP Linux integration.

- `code/` - openeigrpd, oectl, and Linux-specific services
- `specs/` - Linux integration specifications
- `test/` - Native Linux integration tests
- `man/` - openeigrpd(8), openeigrpd.conf(5), oectl(8)
- `packaging/` - Debian packaging and systemd integration

The Linux integration uses the portable EIGRP engine under
`eigrp/` and shared POSIX services from `libunix/`.

Linux-specific networking, interfaces, RIB operations, and
process management must not be implemented in the portable core.

The UUT simulation backend is not a production dependency.

## Building the native Linux libraries

From the repository root on Linux:

```sh
make linux
```

This builds the portable EIGRP core, shared Unix support, and the native
interface, packet, Netlink RIB and read-only route-observation libraries. On Linux, `make all` also runs
this build before the existing style, UUT, and scale checks. On non-Linux
hosts, `make all` retains the portable build and test flow; `make linux`
explicitly reports that a Linux host is required. `make clean` removes the
Linux build outputs as well.

These targets currently produce libraries, **not** a runnable `openeigrpd`
daemon. The native daemon integration is separate work.


## Route redistribution observer

The Linux RIB archive now also includes `eigrp_linux_redist.c`. It accepts
`connected` and `static` redistribution subscriptions and normalizes Linux
Netlink route snapshots/notifications into the existing portable RIB interface.
The host must pump `eigrp_linux_redist_fd()` with
`eigrp_linux_redist_process()` and re-register the fd after reconnection.
An explicit `eigrp_linux_redist_resync()` refreshes interface-derived metrics.
Only default-VRF, main-table unicast IPv4/IPv6 sources are supported; other
sources and nonzero route instances are rejected. EIGRP protocol-192 routes
are never redistributed by this observer.

Run `python3 linux/test/test_redist.py` for crafted Netlink route tests and,
where user/network namespaces are available, real route mutation tests.
Run the portable YAML scenarios under `eigrp/test/uut/linux-redist/` to verify
the unaffected core redistribution/DUAL contract. Netlab daemon
interoperability remains blocked until the native daemon task connects the
scheduler, configuration and startup paths.

## Native configuration staging (Task 9 partial)

`make linux` also builds `linux/code/openeigrpd-config`, a syntax-only
validator of the future `/etc/openeigrpd.conf` configuration:

```sh
linux/code/openeigrpd-config -t -f linux/config/openeigrpd.conf.example
python3 linux/test/test_config.py
```

The staged Unix parser preserves its previous parsed plan after invalid input
and dispatches initial-load configuration through the portable public semantic
APIs. It rejects unsupported directives, including unfinished policy features.
**There is no runnable `openeigrpd` here, so neither startup configuration nor
SIGHUP reload is yet wired to an operational daemon.** Do not use the initial
semantic commit API as a substitute for a failure-atomic live reload. The
transaction design and remaining integration requirements are documented in
`linux/specs/configuration.md`.
