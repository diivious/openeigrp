# Native Linux IPv6 transport

Copyright (C) 2026 Donnie V. Savage

## Scope and portable contract

`linux/code/eigrp_linux_packet.c` now owns native IPv4 **and** IPv6 IP protocol
88 socket services for the `eigrp_sys.h` host interface. Each EIGRP AF runtime
has its own nonblocking, close-on-exec raw socket, fd and multicast memberships.
The existing IPv4 socket path and `libunix` virtual-wire UUT backend are not
modified by IPv6 runtime dispatch.

A native IPv6 raw socket on Linux **receives only the EIGRP payload**; the
kernel removes the IPv6 header and any extension headers. The host fills the
existing public `eigrp_packet_rx_meta_t` with `network_header_length = 0`,
`eigrp_length` equal to the returned payload length, `destination_multicast`
from `IPV6_PKTINFO`, and `ingress_vrf_id = EIGRP_VRF_DEFAULT`. The `source` from
`sockaddr_in6` and `destination` from `IPV6_PKTINFO` are normalized into
`eigrp_address_t`. The ifindex comes from `in6_pktinfo.ipi6_ifindex`, not an
address-derived guess. The receive buffer is bounded to 65,535 bytes, the maximum IPv6 payload
length without a jumbogram (the IPv6 header is excluded).

Transmit sends the already constructed portable EIGRP bytes unchanged using
`IPV6_PKTINFO` with the interface's link-local source and ifindex. Scoped
unicast link-local destinations and the link-local multicast destination
`ff02::a` use `sin6_scope_id` equal to the interface index. The socket requests
hop limit 1 for unicast and multicast, traffic class 0xc0, and disables
multicast loopback. It joins and leaves `ff02::a` per interface with
`IPV6_JOIN_GROUP`/`IPV6_LEAVE_GROUP`. Leaving uses only the interface
index so an address-removal event cannot prevent membership cleanup.
Interface shutdown and reactivation are
owned by the existing portable `eigrp_intf_down()` and
`eigrp_intf_multicast_update()` lifecycle; closing the raw socket also drops
remaining kernel group memberships.

## Checksum and input validation

Portable EIGRP's Internet checksum covers the EIGRP message. It is **not** an
IPv6 transport checksum with a pseudoheader. `IPV6_CHECKSUM` is explicitly set
to `-1` (disabled) for protocol 88, so Linux neither overwrites the checksum on
transmit nor discards packets using an incompatible IPv6 pseudoheader check.
The portable packet receive path remains responsible for validating EIGRP
checksums, opcodes, authentication, RTP and TLV lengths. This behavior is
consistent with Linux's IPv6 raw socket API (RFC 3542, section 3.1).

The Linux host validates the IPv6 transport envelope only: receive must have
at least a 20-byte EIGRP header, no `MSG_TRUNC`/`MSG_CTRUNC`, a complete IPv6
source `sockaddr_in6`, a link-local source, consistent interface scope,
exactly one valid `IPV6_PKTINFO`, exactly one valid `IPV6_HOPLIMIT` of 1, and a
published default-VRF IPv6 link-local interface matching the receive ifindex.
The destination must be `ff02::a` or a link-local unicast address. Unexpected
destination multicast groups, global-scope sources, missing metadata, and
truncated/oversize payloads are dropped without closing the socket. Nothing in
`linux/code/` parses EIGRP TLVs or reliable-transport state.

## Integration constraints

RTNETLINK provider still publishes only default-VRF interfaces;
nondefault VRFs remain unsupported. `CAP_NET_RAW` is required for socket open.
The future native daemon must register the borrowed descriptor returned by
`eigrp_linux_packet_fd()` for readiness after `eigrp_sys_socket_open()`, invoke
portable `eigrp_packet_read()`, and unregister the fd before closing it.
IPv4 and IPv6 runtime sockets can coexist; the host must independently drive
both AF callbacks. IPv6 support does not supply the missing daemon event loop,
CLI, configuration startup, kernel RIB, or per-VRF raw socket isolation.

## Verification and qualification

* `python3 linux/test/test_packet.py` retains the IPv4 crafted-packet suite.
* `python3 linux/test/test_ipv6_packet.py` tests native socket setup failures,
  protocol-88 AF separation, multicast join/leave and close, source/destination
  scope, send data, missing/invalid cmsgs, short/truncated packets, failed
  sends and receive recovery. Actual raw-socket privilege denial is checked
  against the running kernel; packet I/O is syscall-mocked when no
  `CAP_NET_RAW` is granted.
* `eigrp/test/uut/rtp-recovery/ipv6-loss-recovery.yaml` exercises portable
  IPv6 RTP and convergence during interface loss, dropped UPDATE and recovery.
  It does not exercise the Linux raw socket. The existing IPv4 and IPv6 core
  UUT gates remain relevant regressions. The current UUT topology schema
  permits only one AF, so dual-stack host socket coexistence is tested in the
  native harness instead of misrepresenting UUT coverage.
* `linux/test/netlab/topology-ipv6.yml` and `topology-dual-stack.yml` provide
  manual IOSv fixtures and expected packet/neighbor evidence. End-to-end Cisco
  adjacency cannot be qualified until a complete native daemon is available.

The Cisco `ff02::a` and link-local source requirements are grounded in Cisco's
EIGRP for IPv6 behavior. The exact Linux raw socket checksum semantics are
from RFC 3542 and Linux raw socket behavior, not from RFC 7868's wire TLVs.
