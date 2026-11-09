# Standalone Unix host adapter

`libunix/` is the native macOS/Linux host for developing and qualifying the
portable OpenEIGRP engine without embedding it in FRR or BIRD. It implements
host services and test-oriented facilities, **not an alternative EIGRP
protocol implementation**. Protocol decisions remain in `../eigrp/code/`.

## Runtime and host services

The shared Unix library provides process initialization and teardown, monotonic time,
immediate events and timers, cancellation, file-descriptor readiness, basic
work queues, default-VRF resolution, and standalone logging. Its scheduler
uses POSIX threads, `poll(2)`, and a private wake pipe; Unix descriptors stay
inside the adapter.

The UUT library maintains a synthetic interface inventory with IPv4/IPv6 addresses
and lifecycle notifications. A UUT-only **unprivileged in-memory RIB** holds
connected/source routes and learned EIGRP routes; it does not change the
macOS or Linux kernel routing table. Interface, route, and management walkers
make the resulting state observable to tests and host front ends.

| Area | Implementation / entry point |
|:--|:--|
| Shared Unix library (`libeigrp_unix.a`) | `eigrp_unix_sys.c`, `eigrp_unix_format.c`, `eigrp_unix_keychain.c` |
| UUT-only library (`libeigrp_uut.a`) | `eigrp_uut_interface.c`, `eigrp_uut_rib.c`, `eigrp_uut_segment.c`, `eigrp_uut_wire.c`, `eigrp_uut_policy.c` |
| Scheduling and host services | `code/eigrp_unix_sys.c`, `code/eigrp_unix.h` |
| Interfaces and lifecycle | `code/eigrp_uut_interface.c`, `code/eigrp_uut_interface.h` |
| In-memory routing table | `code/eigrp_uut_rib.c`, `code/eigrp_uut_rib.h` |
| Standalone configuration | `code/eigrp_unix_config.c` |
| Logical segments and packet transport | `code/eigrp_uut_segment.c`, `code/eigrp_uut_wire.c` |
| Wire broker | `code/eigrp_uut_wire_broker.c`, `code/eigrp-wire` |

## Logical segments and packet transport

A shared segment model attaches multiple units under test (UUTs) to a logical
control-plane network. **It is not an Ethernet simulator:** it does not model
VLANs, switching, MAC learning, ARP, or IPv6 neighbor discovery.

The `eigrp-wire` broker uses local Unix-domain `SOCK_STREAM` connections, one
per UUT. A versioned, length-delimited envelope transports segment,
interface, address-family, and endpoint metadata together with the unchanged
EIGRP packet. Multicast fans out to members of the same segment; unicast is
delivered only to the registered destination interface. The forwarding
boundary is intended to support controlled packet faults. Full UUT
orchestration and policy integration remain separate work.

## Build and test

From the repository root:

```sh
make build       # Portable engine and Unix host support
make uut         # Archive-boundary tests, portable pytest and YAML UUT scenarios
make all         # Required full pre-merge gate
make help        # Available commands and selectors
```

`make build` creates two **separate** static archives under `libunix/code/`.
The first holds only functionality shared with native Linux; it has no virtual
packet transport, synthetic interfaces, simulated RIB, or UUT policy symbols.
The second holds all those UUT-only host callbacks. The portable fallback
logger remains in `eigrp/code/libeigrp-core.a`; it is not duplicated here.
The standalone config parser (`eigrp_unix_config.o`) remains independently
buildable for future native-daemon integration, but is not in either archive
because the current UUT frontend uses its own scenario-driven configuration.

The UUT executable links the portable core archive and both host archives,
while the `eigrp-wire` broker remains an independent executable with the same
wire format. `make uut` executes symbol/archive boundary tests and actual
runtime/RIB harnesses before running YAML scenarios.

To run the broker directly, use:

```sh
libunix/code/eigrp-wire [socket-path]
```

Set `EIGRP_UNIX_WIRE_SOCKET` for Unix EIGRP processes when using a non-default
broker socket path. Unix-specific tests live in [`test/`](test/); portable
tests live in `../eigrp/test/`.

## Architecture references

The authoritative Unix integration details and call flows are in
[`specs/integration.md`](specs/integration.md). The host-neutral API is in
[`../specs/public-api.md`](../specs/public-api.md); the cross-platform
integration contract is in
[`../specs/platform-integration.md`](../specs/platform-integration.md).

Unix-specific configuration, timer, socket, interface, and routing-table
objects must not appear in the portable EIGRP API. The FRR and BIRD adapters
have independent host implementations.

## Native configuration staging

The native parser now provides `eigrp_unix_config_parse_file()` and
`eigrp_unix_config_parse_text()` for all-or-nothing syntactic validation into
an owned plan. `eigrp_unix_config_commit()` forwards that plan to the portable
semantic `eigrp_cli.h` targets once, for initial startup only. A failed
semantic dispatch is reported as failure, not silently accepted. Runtime
mutation is not transactionally rolled back: do not use `apply_file()` or
`apply_text()` on a running daemon as a SIGHUP reload implementation.
The standalone Linux checker is `linux/code/openeigrpd-config -t`.
