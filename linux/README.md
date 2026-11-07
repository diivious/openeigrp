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
