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
interface, packet, and Netlink RIB libraries. On Linux, `make all` also runs
this build before the existing style, UUT, and scale checks. On non-Linux
hosts, `make all` retains the portable build and test flow; `make linux`
explicitly reports that a Linux host is required. `make clean` removes the
Linux build outputs as well.

These targets currently produce libraries, **not** a runnable `openeigrpd`
daemon. The native daemon integration is separate work.
