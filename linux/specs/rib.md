# Native Linux kernel RIB

Copyright (C) 2026 Donnie V. Savage

## Scope and ownership

`linux/code/eigrp_linux_rib.c` provides the existing `eigrp_rib.h` route
installation, withdrawal, instance teardown, and initialization/finish
contract. The portable engine determines the successors, weighted sharing,
route type and administrative distance. The Linux backend never invokes DUAL,
processes EIGRP packets, redistributes routes, or links to the virtual-wire
RIB. The `eigrp_rib_redistribute_add/remove` entry points are implemented by
`eigrp_linux_redist.c`, a separate read-only Netlink observer. It does not
share or adopt the selected-route ownership records in this file.

Default-VRF IPv4/IPv6 unicast routes are installed in `RT_TABLE_MAIN` with
`RTPROT_EIGRP` (192). `RTA_PRIORITY` is the portable `install.admin_dist`
(0–255): **Linux route priority, not the 64-bit EIGRP composite metric**.
The EIGRP scalar metric remains in the host's desired-route snapshot; Linux
has no directly equivalent 64-bit route metric. Route tags are similarly not
assigned to an unrelated kernel forwarding field. The kernel sets route scope
according to the constructed `RTM_NEWROUTE` request. Routes are limited to
32 next hops, the portable `EIGRP_MAX_PATHS_MAX` selection ceiling. For ECMP,
`RTA_MULTIPATH` contains copied interface indices, optional same-family
next-hop gateways and weight hints; Linux supports weights 1–256. An absent
or zero hint means weight 1. Single-path weight hints do not affect the
kernel and are normalized to 1. IPv6 link-local gateways require an ifindex.

Calls are serialized by a private mutex because the portable AF can call
from multiple runtime threads. Netlink transactions have bounded receives,
sequence checks, sender validation, ACK/error processing, timeout and socket
reopening. New routes use exclusive create. Replacements validate that the
previously installed route still matches the kernel (prefix, protocol,
priority, interface/next-hop set), delete it, then install the new route;
failed replacements attempt to restore the old route. Withdrawal likewise
verifies the old snapshot and never intentionally deletes a route with a
foreign protocol, unexpected priority or modified next-hop set.

## Reconciliation and failure behavior

The host calls `eigrp_linux_rib_reconcile()` after a Netlink socket fault,
on interface recovery and periodically after route-selection changes. It
retries failed installation/withdrawal and re-installs entries removed from
the kernel. Per-call error returns are not silently converted to success.
Failed requests are retained as desired state for a future reconciliation.
The synchronous backend does not create threads or timers of its own.

Graceful `eigrp_rib_instance_delete` and `eigrp_rib_finish` attempt to remove
only installed and reverified routes. A failed withdrawal is retained until
the next reconciliation while the process is alive; shutdown cannot retry
beyond process lifetime. A daemon crash leaves kernel routes. On restart, a
route already present with exactly the selected prefix, protocol, priority and
next hops is **not** adopted: the new process cannot prove who created it.
It reports a conflict, retains selected desired state, and leaves that route
untouched. An existing protocol-192 route for the same destination but a
different Linux route priority is likewise treated as an ownership conflict. An operator must inspect and clear verified stale protocol-192
routes before reconciliation can install them using exclusive create. These restrictions avoid blanket deletions of the kernel's routing
table when the portable core has no ownership record.

**Process-identity limitation:** Linux `RTPROT_EIGRP` does not encode a daemon
identity. If multiple processes deliberately write byte-identical
protocol-192 routes in the same table, neither Netlink nor this RIB API can
distinguish the original writer. For this reason the backend never adopts an
untracked route, not even on restart. A different process can still change
an already-owned route to an *identical* one between readback and deletion;
Linux lacks kernel compare-and-delete and that race cannot be excluded by
this API. The later daemon/service task must enforce single-writer ownership
(e.g. service lock) or another kernel-verifiable scheme before claiming
strict cross-process isolation. Do not represent protocol 192 alone as
process identity.

## Integration and verification

The Linux host must link `libeigrp_linux_rib.a` instead of the UUT's
`libeigrp_uut.a` route module and call the usual `eigrp_rib_init/finish` hooks.
`CAP_NET_ADMIN` in the target network namespace is needed for kernel route
mutations. Only the default VRF is supported in the current Linux interface
and transport tasks; this backend rejects other VRFs rather than silently
using the wrong table. No standalone daemon entry point exists yet to wire
periodic reconciliation or service ownership.

`linux/test/test_rib.py` runs the production Netlink implementation and
crafted RTM_NEWROUTE tests, including real IPv4/IPv6 changes in a fresh user
and network namespace when supported. `eigrp/test/uut/rib-lifecycle/` includes two
YAML route lifecycle scenarios using the existing virtual-wire backend.
Those YAML scenarios verify the portable selection/withdrawal contract, not
Linux kernel programming. Native Netlab daemon/Cisco interoperability cannot
run until the Linux daemon's lifecycle and CLI are connected.

The current implementation validates a full kernel family dump on each
previously installed route update/withdrawal. That is safe against stale
in-memory ownership but is not a scalable batch strategy: rapid bulk
withdrawals could take superlinear time. Before high-prefix-count production
use, add a bounded route-notification cache or a kernel-supported filtered
lookup and execute large-table reconcile tests. Do not claim 100K-route
qualification from the small namespace tests.


## Linux redistribution observation

`linux/code/eigrp_linux_redist.c` observes `RT_TABLE_MAIN` unicast IPv4 and
IPv6 routes through a separate `NETLINK_ROUTE` socket subscribed to
`RTMGRP_IPV4_ROUTE` and `RTMGRP_IPV6_ROUTE`. The monitor binds before requesting
its initial IPv4 and IPv6 route dumps. Notifications that arrive during either
dump are queued (4 MiB bound) and applied after both dump streams complete.
Only kernel-sourced routes (`RTPROT_KERNEL`, connected) and explicit static
routes (`RTPROT_STATIC` or iproute2's `RTPROT_BOOT`, static) are candidates.
All other protocols, including `RTPROT_EIGRP` (192), are excluded. As a
result, neither this process's installed EIGRP routes nor another daemon's
protocol-192 entries are fed back to the core.

Subscriptions are scoped to the exact EIGRP runtime, address family and
`{source protocol, route instance}`. Only connected and static, with a zero
route-instance, and the default VRF are supported. Other sources or VRFs return
`EIGRP_RESULT_UNSUPPORTED`. A duplicate subscription is idempotent. A new
subscription replays all eligible current kernel routes; removal stops further
host notifications. The portable configuration target owns the actual DUAL
withdrawal before unsubscribing. Instance deletion and RIB finish retire all
monitor-owned references and descriptors.

Each normalized event contains the canonical prefix, source, copied next hops,
Linux priority as a **host scalar** (not an EIGRP seed vector), and interface
index. Linux realms are not EIGRP route tags: the tag is zero. For connected
and interface-only static routes, the host reads the current interface
bandwidth/MTU from `eigrp_sys_interface_walk()` and supplies the ordinary EIGRP
interface vector defaults for delay, reliability and load. Gateway static
routes supply no implicit seed; the portable core applies the configured
`redistribute ... metric` or `default-metric`, or declines advertisement.
Policy, prefix limits, metric selection, topology ownership, DUAL, RTP and
packetization remain unchanged. Unsupported indirect kernel next-hop objects
(`RTA_NH_ID`) and cross-family next hops (`RTA_VIA`) are not imported.

Kernel routing can retain multiple routes for the same prefix at different
priorities or with different route protocols. The observer does **not** choose a
preferred route. If more than one candidate maps to the same portable
`{source, prefix}`, the prefix is withdrawn until the ambiguity clears. This
is a fail-closed ingestion rule, not a competing route-selection algorithm.

The host must register `eigrp_linux_redist_fd()` in its scheduler and call
`eigrp_linux_redist_process()` when readable; a fault can replace that fd.
The host must then register the new fd. Loss, truncated/malformed Netlink
frames, failed dumps, or `NLM_F_DUMP_INTR` trigger a new snapshot and a
snapshot-versus-cache diff. `eigrp_linux_redist_resync()` is also callable
after interface-metric changes, or periodically, and refreshes existing route
vectors. This is a host-pumped service; it owns **no** threads or scheduler.
The host must serialize pumping with AF-instance destruction. When temporary
core event enqueue fails, a future process call attempts a full refresh.

`linux/test/test_redist.py` compiles and exercises the production observer,
with crafted malformed/duplicate/replace cases and real namespace route
add/delete/replacement/Netlink reconnect tests. `eigrp/test/uut/redist-lifecycle/`
has separate portable IPv4/IPv6 YAML scenarios for snapshot/replay,
subscriptions and withdrawal. These YAML scenarios use the existing virtual
wire RIB, not the native Linux observer; the Linux daemon cannot yet be
run under Netlab because no standalone `openeigrpd` executable or host event
loop is implemented.

### Operational limits

The monitor currently copies its normalized cache for a changed batch and
compares subscribed snapshots. Notifications for unsupported protocols avoid
this copy. Kernel dumps, mass updates and high-prefix-scale performance have
not been qualified for 100K routes; bulk stress and a daemon host-loop gate
remain required. No mutable Linux table entry is treated as proof of process
ownership. Netlink source snapshots report observed routes, not a
process-identity proof.
