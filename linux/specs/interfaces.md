# Native Linux interface integration

Copyright (C) 2026 Donnie V. Savage

## Ownership and scope

`linux/code/eigrp_linux_interface.c` owns a Linux RTNETLINK interface inventory.
The host calls `eigrp_linux_intf_start()` after process initialization,
registers `eigrp_linux_intf_fd()` with its event loop, calls
`eigrp_linux_intf_process()` on readability, and calls `eigrp_linux_intf_stop()`
before destroying the portable core. A resync may replace the descriptor; the
host must re-register the returned descriptor and discard the old registration.
This task does not provide the daemon event loop, packet transport or RIB.

The provider implements `eigrp_sys_interface_walk()`, using the public
`eigrp_intf_runtime_state_t` contract only. It sends host lifecycle events via
`eigrp_sys_intf_update/down/remove/addr_update()` rather than calling any
private topology, DUAL or neighbor entry points. It neither links to nor
reuses the simulated interface inventory in `libunix/libeigrp_uut.a`.

## Kernel snapshot and events

The Netlink socket subscribes to link and IPv4/IPv6 address groups *before*
requesting `RTM_GETLINK` and `RTM_GETADDR` dumps. Notifications interleaved
with the dumps are buffered and replayed in their original arrival order after
both snapshots. Notification sequence numbers are not assumed to be zero.
Dump interruption, malformed framing, socket errors and lost messages trigger
a full resynchronization; retirement of previous interface identities precedes
republication after a successful resync. A failed resync does not advertise a
partial inventory. The next `process()` retries.

Per-batch updates are transactional: the old inventory remains available to
walkers until the new one is complete. Interface names and addresses are owned
by the provider; no kernel frame or pointer into a mutable inventory is kept
as an asynchronous callback argument. An ifindex reused after deletion is
retired and recreated. Multiple changes within a read batch are reconciled
against the final state. Unchanged duplicate notifications are suppressed.

A mutex protects inventory walks against concurrent mutation. The host must
serialize its start/process/stop lifecycle; the provider also serializes these
entry points. Callbacks into EIGRP are issued without the inventory mutex.

## Normalization and limits

- Index: kernel `ifi_index`, preserved as the public 32-bit identity.
- Name: `IFLA_IFNAME`, owned NUL-terminated copy.
- State: operative only when both `IFF_UP` and `IFF_RUNNING` are set.
- MTU: `IFLA_MTU` in bytes. Interface bandwidth is reported in Kbit/s using
  `/sys/class/net/<interface>/speed` when valid; otherwise a documented
  1 Gbit/s fallback. This is a host estimate, **not** an EIGRP metric policy.
- Addresses: `IFA_LOCAL` preferred to `IFA_ADDRESS` for IPv4 point-to-point;
  IPv4 and IPv6 are normalized into `eigrp_prefix_t`. IPv4 secondary address
  flags are propagated. Tentative/DAD-failed IPv6 addresses are withheld.
- VRF: only the default Linux VRF is published. VRF masters and interfaces
  enslaved through a master chain to a VRF are not published. Ordinary bridges,
  bonds, VLANs, veths, tunnels, loopback, and physical links remain eligible.
- Link type: public EIGRP `type` remains zero/default. Kernel ARPHRD is not
  an EIGRP-owned type and must not cross the interface contract.

`eigrp_sys_interface_walk()` filters on the requested EIGRP AF and rejects
nondefault VRF runtimes. The provider does not install kernel routes, open
EIGRP packet sockets, or fabricate UUT interfaces.

## Verification boundaries

`linux/test/test_interface.py` runs production parser fixtures, a live kernel
snapshot, socket-error resynchronization, archive symbol checks, and a real
network namespace test when user namespaces and iproute2 are available.
`eigrp/test/uut/interface-lifecycle/` exercises the *portable* EIGRP
link-down/up withdrawal/recovery contract via the established YAML UUT
framework. It does **not** drive Linux Netlink. Integration with a future
native daemon, FRR/BIRD interop, and full Netlab suites require their
respective later stages and environments.
