# Native Linux OpenEIGRP

`openeigrpd` is the standalone Linux routing daemon. It uses the portable
EIGRP core and shared Unix scheduling, with native IPv4/IPv6 sockets,
Netlink interface/RIB services, and an `oectl` management socket.
It does **not** need FRR, BIRD, a virtual-wire UUT broker, or Docker at runtime.

## Where to go

| Goal | Documentation |
|---|---|
| Build, configure, and operate OpenEIGRP on Linux | This README and the manual pages below |
| Change Linux integration code or run its tests | [Developer guide](specs/developer.md) |
| Build, verify, and publish Debian packages | [Maintainer guide](specs/maintainer.md) |
| Understand Linux interfaces and implementation contracts | [Linux specifications](specs/) |

Directory responsibilities:

```text
linux/
  README.md         Linux overview, quick start, and navigation
  code/             Daemon, oectl, config validator, and host services
  config/           Example configuration
  packaging/        Debian/systemd scripts, metadata, and man/ sources
  specs/            Developer and maintainer guides, host specifications
  test/             Native executable tests and Netlab scenarios
```

The portable engine lives in `eigrp/`, shared Unix services in `libunix/`,
and Linux-specific system integration in `linux/`. Protocol logic should not
be reimplemented in this integration.

## Build and first run

On Linux, from the repository root:

```sh
make linux
linux/code/openeigrpd-config -t -f linux/config/openeigrpd.conf.example
linux/code/openeigrpd -t -f "$PWD/linux/config/openeigrpd.conf.example"
```

Both validators check configuration syntax without enabling routing.
A real daemon run requires `CAP_NET_RAW` for raw IP protocol-88 sockets and
`CAP_NET_ADMIN` for kernel route changes. Use a dedicated test environment
until live network behavior is qualified.

For a package install, follow the [maintainer guide](specs/maintainer.md)
for package creation or obtain a compatible release `.deb`. Installation
alone does not enable routing. Configure and explicitly start the service:

```sh
sudo apt install ./dist/openeigrpd_0.1.0_amd64.deb   # adjust version/arch
sudoedit /etc/openeigrpd.conf
sudo openeigrpd-config -t -f /etc/openeigrpd.conf
sudo systemctl enable --now openeigrpd
sudo oectl show protocols
```

The sample configuration includes IPv4 and IPv6 address-family examples.
Select the correct physical interface, AS, and addresses for your network
before activating the daemon.

## Operations

```sh
oectl version
oectl show protocols
oectl show neighbors --asn 4453 --afi 4
oectl show interfaces --asn 6473 --afi 6
oectl show routes --asn 4453 --afi 4 --json
oectl show tech-support
```

`oectl` is read-only except `clear events`, which requires root or the daemon
startup UID. The default management socket is `/run/openeigrpd/control.sock`
(mode 0660, group `openeigrpctl`). Membership in that group allows
observational access, not mutation. Scoped queries can autodetect one running
default-VRF AF; multiple AFs need explicit selection.

Use `systemctl restart openeigrpd` to apply a new validated config. `SIGHUP`
is **not** a reload: it is rejected while preserving active state.

## Command and technical references

| Need | Reference |
|---|---|
| Daemon flags, signals, lifecycle, permissions | [openeigrpd(8)](packaging/man/openeigrpd.8) |
| Named-mode directives and IPv4/IPv6 examples | [openeigrpd.conf(5)](packaging/man/openeigrpd.conf.5) |
| `oectl` commands, filters and JSON output | [oectl(8)](packaging/man/oectl.8) |
| Packaging, installation, upgrades and removal | [Maintainer guide](specs/maintainer.md) |
| Live Debian/systemd VM qualification | [Qualification guide](specs/systemd-qualification.md) |
| Architecture, host APIs and native test commands | [Developer guide](specs/developer.md) |
| Native config/parser and transaction design | [Configuration](specs/configuration.md) |
| Management framing, pagination, concurrency | [Management IPC](specs/management-ipc.md) |
| Native executable tests and lab topology | [Linux tests](test/), [Netlab](test/netlab/README.md) |

## Current limitations

- Native parser supports default-VRF IPv4 and IPv6 unicast AFs; source
  redistribution is limited to connected/static routes in the default VRF.
- MD5 key-chain auth is supported. HMAC-SHA-256, encoded type-7 secrets,
  route-map `set`, complex ACL expressions and EIGRP Stub runtime are not.
- `oectl show routes` exposes the portable topology view, not the Linux RIB.
  IPC pagination is bounded; concurrent routing changes are not snapshotted.
- Linux native packet/daemon tests exist, but a real systemd VM and
  privileged Cisco/Netlab adjacency qualification remain separate gates.

Executable documentation validation: `python3 linux/test/test_docs.py`.
On Linux with `make linux` outputs present this checks actual CLI options
and the sample configurations. Install `mandoc` or `groff` to verify man
rendering as well.
