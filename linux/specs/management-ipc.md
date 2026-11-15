# Native Linux management IPC

This document specifies the Linux adapter's local control protocol. For
commands and examples use [eigrp(8)](../packaging/man/eigrp.8); for daemon lifecycle
use [openeigrpd(8)](../packaging/man/openeigrpd.8). Portable state is obtained through
`eigrp/code/eigrp_mgnt.h`; the Linux adapter owns framing, serialization,
permissions and client scheduling.

## Version 2 wire format

The transport is an `AF_UNIX` `SOCK_STREAM` service. Integer fields are
network-byte-order; never transmit raw C structures. Every request and
response begins with a 16-byte header:

| Field | Width | Value |
|---|---:|---|
| magic | 32 bits | `0x45474d54` (EGMT) |
| version | 16 bits | 1 |
| operation | 16 bits | Operation code |
| status | 16 bits | Request must be zero |
| flags | 16 bits | Request must be zero |
| length | 32 bits | Exact payload byte length |

Operations 1 (`capabilities`) and 2 (`protocols`) have empty request payloads.
Operation 11 (`identity`) carries a 128-byte client release identity.
Operations 3-10 and 12-13 carry a **16-byte selector** consisting of `afi:u16`,
`asn:u16`, `vrf_id:u32`, `topology_id:u16`, reserved `flags:u16`, and
`offset:u32`. Valid AFI is 4 or 6; ASN must be nonzero; only base topology
ID 0 is accepted; selector flags are zero; offset is at most 8192.

| Code | Operation | Portable content |
|---:|---|---|
| 1 | capabilities | Core release and AF/TLV capabilities |
| 2 | protocols | Configured protocol AF instances |
| 3 | neighbors | Neighbor runtime and queues |
| 4 | interfaces | Interface settings and traffic counters |
| 5 | topology | DUAL prefixes and paths |
| 6 | routes | Successor and feasible paths in portable topology |
| 7 | traffic | EIGRP packet statistics |
| 8 | events | Event ring and summary |
| 9 | diagnostics | Capabilities, debug state, timers |
| 10 | clear events | Privileged event-ring reset |
| 11 | identity | Daemon/client release handshake |
| 12 | clear neighbors | Privileged neighbor reset through `eigrp_nbr_clear()` |
| 13 | clear topology | Privileged topology reset through `eigrp_topology_clear()` |

Operations 12 and 13 append a fixed 128-byte target to the 16-byte selector.
The target layout is `type:u8`, `soft:u8`, `prefix_length:u8`, one reserved
byte, a 16-byte address payload and trailing zero padding. Type 0 means all,
type 1 means an interface name (NUL-terminated at offset 4, maximum 63
characters), and type 2 means an IPv4/IPv6 neighbor address (operation 12)
or prefix (operation 13). Soft is valid only for neighbor clear. Only
operations 12 and 13 accept this extension; all other operation payloads
retain their original shape. The daemon rejects nonzero reserved bytes,
malformed address/prefix fields and extraneous data.

Responses use status 0=success, 1=invalid, 2=unsupported version,
3=denied, 4=not found, 5=unsupported, 6=busy, or 7=internal.
Success responses contain newline-delimited JSON objects, at most 32 records
and 16 KiB per page. Response flag bit 0 indicates more records. Clients
advance the offset by the number of records returned. All other response
statuses have empty payloads.

Malformed magic, length, reserved bits or framing is rejected with bounded
resource use. There is no stable snapshot guarantee between pages.
The portable topology iterator cannot resume from a cursor, so an offset
above 8192 is rejected rather than allowing unlimited replay cost.
This limits very large topology dumps until a portable cursor API exists.
The route operation is a **topology view**, not the Linux kernel RIB.

## Permissions and lifecycle

The daemon publishes `/run/openeigrpd/control.sock` after a successful
initial configuration load. The default socket mode is 0660, typically
with group `openeigrpctl`. The parent must be a real, owner-controlled
(non-group/world-writable) directory. Startup refuses to unlink another
process's existing socket; shutdown only removes the socket it created.

Read-only clients need Unix socket access, not root. The daemon validates
`SO_PEERCRED` for **every** clear operation; only root or the daemon startup UID
may execute a neighbor, topology or event-log clear. No live configuration
mutation is exposed.

The service supports at most 32 concurrent clients with nonblocking socket
I/O, bounded response buffers and an approximately five-second idle timeout.
The daemon serializes each public management walk onto the relevant AF owner
and protects shutdown from in-flight management access. Management stops
before portable state is destroyed. Heavy state walks may still occupy an AF
worker while they execute.

`eigrp` validates response framing, operation, version, status, record lengths
and pagination; it fails on truncation rather than silently producing an
incomplete state dump. `show tech-support` combines read-only views for
running default-VRF instances. Nondefault VRF numeric IDs are not discoverable
from protocol listing, so such views require explicit `--asn`, `--afi` and
`--vrf-id` selectors. The native configuration and runtime currently accept
only the default VRF. When AFI/ASN are omitted, `eigrp` enumerates matching
running default-VRF contexts, rather than failing on multiple matches.

## Validation

```sh
python3 linux/test/test_mgnt.py
python3 linux/test/test_eigrp_linux_ctl.py
python3 linux/test/test_daemon.py
python3 linux/test/test_docs.py
```

These exercise actual IPC framing, concurrent clients, permissions, malformed
peers, process shutdown, options and documented scope handling. The portable
YAML management scenario under `eigrp/test/uut/management/` exercises
EIGRP state lifecycle **without** using Linux IPC. A privileged Netlab/Cisco
interop run is separate evidence, not implied by an IPC fixture pass.
