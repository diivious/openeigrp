# Local management IPC

The native Linux integration exports a versioned, binary-framed Unix-domain
`SOCK_STREAM` service (`eigrp_linux_mgnt_start` / `eigrp_linux_mgnt_stop`).
The daemon supplies an absolute pathname, peer-visible socket group, and a
**synchronous protocol-context executor**. The library does not create or run a
daemon. The standalone `linux/code/oectl.c` client consumes this protocol
without linking the portable EIGRP core. No virtual-wire UUT transport
or `eigrp/code/` source changes are required.

## Frame format (version 1)

All integer fields are network-byte-order; never transmit C structures.
The 16-byte header has `magic:u32` (0x45474d54, ASCII EGMT), `version:u16`
(1), `operation:u16`, `status:u16`, `flags:u16`, and `length:u32`.
Request status and flags must be zero. `length` must be exactly 0 or 16,
depending on the operation. Wrong frame lengths, reserved fields, or magic
are rejected without receiving arbitrary client-supplied payloads; the
connection is then closed. Unsupported version returns status 2.

Operations:

| Operation | Code | Request | Response contents |
|---|---:|---|---|
| capabilities | 1 | empty | release, supported AF/TLV capabilities |
| protocols | 2 | empty | configured protocol instances |
| neighbors | 3 | selector | live neighbors and queue/timing data |
| interfaces | 4 | selector | runtime/configuration and packet interface metrics |
| topology | 5 | selector | DUAL prefixes and all advertised paths |
| routes | 6 | selector | DUAL prefixes and successor/feasible paths |
| traffic | 7 | selector | portable sent/received counters |
| events | 8 | selector | event summary and formatted event ring |
| diagnostics | 9 | selector | image capabilities, debug state and timers |
| clear events | 10 | selector | clear ring via public `eigrp_eventlog_clear` semantic API |

The selector is `afi:u16`, `asn:u16`, `vrf_id:u32`, `topology_id:u16`,
`flags:u16` (must be zero), and `offset:u32`. AFI is 4 or 6, ASN must
be nonzero, topology ID must be base (0), and offset must be at most 8192. Configuration is matched through
public `eigrp_af_config_context_read()` and runtime VRF identity, not private
lists. Only a configured **running** AF is addressable by a scoped query.
Route information here means the public topology/path snapshot, **not** the
Linux kernel RIB (the public `eigrp_mgnt.h` API has no kernel-RIB walker).

Response status is 0=success, 1=invalid, 2=unsupported version, 3=denied,
4=not found, 5=unsupported, 6=busy, 7=internal. Response `flags&1` means
more rows. A successful response contains newline-delimited JSON objects;
each object is one record. Other status responses have empty bodies. Pages
contain at most 32 records and 16,384 bytes. Increment `offset` by the
number of JSON objects returned and use the same selector for the next page.
The APIs do not guarantee a stable snapshot between pages; clients must
reconcile routing changes. Pagination of topology paths can begin with a
path whose prefix row appeared in a previous page; every path carries its
own destination prefix. Objects are never serialized as pointers.

The public topology iterator only accepts a full walk or exact prefix; it
cannot resume at a cursor. Therefore a page with a high offset necessarily
resets the iterator at its beginning. The offset cap bounds this work, but
also limits access to the first 8,224 records. Large-topology correctness
requires a future *portable* cursor API, rather than bypassing public state
or holding private DNDB/DRDB pointers inside the Linux layer. This is an
explicit pre-production scale limitation.

## Access and lifecycle

The daemon must create a protected directory (e.g. `/run/openeigrpd`, root
owned, mode 0755 or stricter) and call `start()` with a pathname such as
`/run/openeigrpd/control.sock`. The immediate parent must be a real
directory owned by root or the daemon and not group/world writable.
Modes are limited to 0600 or 0660; the
specified group grants access to **read-only** commands. Mutating commands
require a `SO_PEERCRED` UID equal to 0 or the daemon's effective UID at
startup. Client-supplied UID/flags are never trusted. Changing the daemon
UID after creating the listener does not change the authenticated admin UID.
No configuration updates, neighbor resets, topology clearing, or arbitrary
debug mutations are exposed by this protocol.

The socket is created only when no path already exists. Startup never removes
an existing socket or symlink. Shutdown removes the path only if its
filesystem device/inode still identifies the service's own socket. The
server accepts at most 32 clients, uses nonblocking `accept4`, `recv`, and
`send(MSG_NOSIGNAL)`, and drops idle clients after five seconds. Each client
has one bounded outstanding response; slow readers cannot stall other socket
I/O. The service's separate poll worker does not parse EIGRP packets or
schedule protocol timers. Stop signals this worker, joins it, closes all
client descriptors and listener, and removes its own socket path.

**Protocol ownership is delegated explicitly to the daemon executor.** The
server invokes it synchronously for every portable-core read or semantic
mutation. That executor must schedule reads on a state-safe protocol context,
and prevent the walk from racing with AF teardown; the library refuses to
start without one. This is not a guarantee of latency isolation: large
walks still occupy the owning protocol context while they execute. Daemon
startup/termination/reload wiring and real AF-thread concurrency tests cannot
run until the native daemon is available. The API must not be wired to an
executor that merely takes the shared Unix scheduler mutex while AF threads
continue to mutate topology independently.

Native IPC tests (`python3 linux/test/test_mgnt.py`) compile the actual
server code against public management callback fixtures and exercise framing,
privileges, public-state rendering, pagination, concurrent clients,
backpressure, disconnects and cleanup. The YAML UUT scenario under
`eigrp/test/uut/management/` separately verifies the underlying portable
neighbor/topology/route state lifecycle; it is not an IPC integration test.

## Command-line client

`oectl` provides a one-shot client for all ten IPC operations. It validates
response magic/version/operation, flags, lengths, and JSON-line framing;
fetches pages until complete; and fails rather than silently truncating when
the server's offset limit is reached. It cannot make a changing topology into
a snapshot. A temporary stalled or disconnected socket produces an error.
Only `clear events` can modify daemon state, and the daemon is responsible
for authorization. `oectl` never elevates privileges or changes socket modes.

`show tech-support` collects capabilities, protocols and the read-only scoped
views for running default-VRF instances. JSON output includes a local
`integration` record and `scope` separators for the selected instances. A nondefault VRF cannot be identified
by numeric ID through the v1 protocol listing, so `--asn`, `--afi` and
`--vrf-id` are mandatory to collect it. Integration version output uses the
host-neutral `eigrp_integration_version_t` fields and distinguishes local
integration build information from the daemon's portable core release.

CLI regression (`python3 linux/test/test_oectl.py`) executes the actual client
against the real Task 11 server with public management-state fixtures and
crafted broken-peer replies. Portable UUT management scenarios continue to
exercise routing state independently; they do not execute the Linux CLI.
