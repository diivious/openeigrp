# Native Linux management IPC

This document specifies the Linux adapter's local control protocol. For
commands and examples use [oectl(8)](../packaging/man/oectl.8); for daemon lifecycle
use [openeigrpd(8)](../packaging/man/openeigrpd.8). Portable state is obtained through
`eigrp/code/eigrp_mgnt.h`; the Linux adapter owns framing, serialization,
permissions and client scheduling.

## Version 1 wire format

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
Operations 3-10 carry a **16-byte selector** consisting of `afi:u16`,
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
`SO_PEERCRED` for `clear events`, which only root or the daemon startup UID
may execute. No configuration mutation, neighbor reset or topology clearing
exists in IPC version 1.

The service supports at most 32 concurrent clients with nonblocking socket
I/O, bounded response buffers and an approximately five-second idle timeout.
The daemon serializes each public management walk onto the relevant AF owner
and protects shutdown from in-flight management access. Management stops
before portable state is destroyed. Heavy state walks may still occupy an AF
worker while they execute.

`oectl` validates response framing, operation, version, status, record lengths
and pagination; it fails on truncation rather than silently producing an
incomplete state dump. `show tech-support` combines read-only views for
running default-VRF instances. Nondefault VRF numeric IDs are not discoverable
from protocol listing, so such views require explicit `--asn`, `--afi` and
`--vrf-id` selectors. Current native configuration itself accepts only the
default VRF.

## Validation

```sh
python3 linux/test/test_mgnt.py
python3 linux/test/test_oectl.py
python3 linux/test/test_daemon.py
python3 linux/test/test_docs.py
```

These exercise actual IPC framing, concurrent clients, permissions, malformed
peers, process shutdown, options and documented scope handling. The portable
YAML management scenario under `eigrp/test/uut/management/` exercises
EIGRP state lifecycle **without** using Linux IPC. A privileged Netlab/Cisco
interop run is separate evidence, not implied by an IPC fixture pass.
