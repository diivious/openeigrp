# Protocol-88 Cisco interoperability qualification (manual Netlab gate)

Copyright (C) 2026 Donnie V. Savage

**Status: not yet executable as an adjacency test.** Task 4 implements host
interface discovery and Task 5 adds native raw IPv4 packet services, but
`linux-support` does not yet have a complete `openeigrpd` executable,
config/startup lifecycle, timers, or wiring from the portable core into the
native Linux services. Task 7 supplies a standalone native Netlink RIB module,
but a Cisco neighbor cannot be established by linking the libraries alone. This test is a
Netlab topology and acceptance checklist for the first integrated daemon.
No result, capture, or Cisco adjacency is claimed here.

## Lab prerequisite and setup

Install Netlab with the libvirt provider and a properly licensed IOSv image.
The default Linux VM is Netlab's Ubuntu host; **build the native binary on
it** instead of supplying an OpenEIGRP Docker image. `topology.yml` configures
Cisco IPv4 EIGRP AS 4453 and assigns 10.88.0.1/24 to IOS and
10.88.0.2/24 to the Linux UUT. If your Netlab version assigns the IOS side to
another interface, use `netlab inspect` to identify its interface name.

```
netlab create -t topology.yml
netlab up -t topology.yml
netlab connect ios
```

On the Linux node, once the daemon exists, run it with CAP_NET_RAW and an
EIGRP AS 4453 configuration on the 10.88.0.2/24 interface. The daemon must
open the raw socket before dropping CAP_NET_RAW, join 224.0.0.10, register
`eigrp_linux_packet_fd()` for readability, and preserve the portable packet
handler. On the Linux node capture:

```
sudo tcpdump -ni <linux-data-interface> -s 0 -w /tmp/ipv4-proto88.pcap 'ip proto 88'
```

## Acceptance gate

1. Confirm `ip proto 88`, TTL 1, TOS 0xc0, valid IPv4 total length, local
   source, destination 224.0.0.10 for multicast HELLO/UPDATE/QUERY,
   unicast destination for replies/ACKs, and correct EIGRP checksum.
2. `show ip eigrp neighbors` on IOS and `oectl`/daemon state on Linux must
   show both neighbors UP and stable. Save both outputs and the pcap.
3. Inject a passive Linux-connected IPv4 prefix and confirm IOS learns it;
   inject an IOS passive IPv4 prefix and confirm Linux installs it in RIB.
4. Drop a chosen protocol-88 UPDATE in the lab, confirm RTP retransmission
   and ACK, convergence and no stale reliable queue. Confirm packets continue
   to be received after a malformed proto-88 datagram injected on the Linux
   link without causing a new adjacency or daemon crash.
5. Take the Linux interface down and up twice. Confirm neighbor loss,
   withdrawal, recovery and route installation on both sides. Restart Linux
   daemon and confirm new socket/group membership and convergence.
6. Stop the daemon and confirm fd/multicast membership cleanup; check
   CAP_NET_RAW startup failure is reported if the capability is absent.

Capture precise IOS version, Netlab and kernel versions, topology, packet
hex or pcap, per-step exit statuses and timestamps. Do not describe this gate
as PASS until an actual licensed Cisco instance and integrated Linux daemon
have completed it.

## Task 6: IPv6-only and dual-stack manual qualification

Additional fixtures:

* `topology-ipv6.yml` — IPv6-only EIGRP on the data link; Linux management
  IPv4 may still be present. IOSv must have a valid 32-bit EIGRP router ID.
* `topology-dual-stack.yml` — independent IPv4 and IPv6 EIGRP adjacencies on
  the same data link.

Netlab's EIGRP module supports the Cisco IOSv IPv6 AF; `ipv4: false` on a
link interface disables data-link IPv4 without disabling IPv6. Confirm that
IOSv has `ipv6 router eigrp 4453` and `ipv6 eigrp 4453` enabled on the data
interface after provisioning, and correct the lab configuration if the
licensed IOS image uses named-mode syntax.

```
netlab create -t topology-ipv6.yml
netlab up -t topology-ipv6.yml
netlab connect ios
```

Once the host daemon exists, run both `openeigrpd` AF runtimes for the same
Linux interface (dual-stack fixture only) and capture the full packets:

```
sudo tcpdump -ni <linux-data-interface> -s 0 -w /tmp/ipv6-proto88.pcap \
  'ip6 protochain 88'
sudo tcpdump -ni <linux-data-interface> -s 0 -w /tmp/dual-proto88.pcap \
  'ip proto 88 or ip6 protochain 88'
```

The IPv6 capture must show protocol 88, source `fe80::/10`, destination
`ff02::a` for multicast, scope-correct link-local unicast replies/ACKs, hop
limit 1, traffic class 0xc0, and the unchanged EIGRP checksum (validated over
only the EIGRP payload, without an IPv6 pseudoheader). Verify both address
families independently on IOS using `show ipv6 eigrp neighbors`,
`show ip eigrp neighbors`, `show ipv6 route eigrp`, and `show ip route eigrp`.
Capture actual IOSv version, routing tables, stable neighbor states, route
withdrawal, rejoin, retransmission after one dropped packet, recovery after
malformed-packet injection, and `ip -6 maddr show dev <interface>` before and
after interface removal and daemon shutdown. In dual-stack mode, withdrawing
IPv6 on one link must not disrupt the IPv4 adjacency, and vice versa.

**Not an automated pass:** Neither fixture can run a completed adjacency on
this branch because `openeigrpd` still lacks a native event loop, startup
configuration, and wiring from DUAL into the native RIB service. The Netlab
YAML files describe the first
manual verification gate once that executable exists.

## Task 7: kernel RIB route-lifecycle qualification (pending integrated daemon)

With a real Linux daemon connected to the portable core, repeat both address
family labs and save `ip -4 route show proto eigrp` and
`ip -6 route show proto eigrp` after each transition. Validate that:

1. IOS-connected networks appear as `proto eigrp` with correct installed
   output interface/gateway and Linux route priority; route selection remains
   controlled by DUAL.
2. Changing selected successors replaces the kernel next hop; repeated UPDATEs
   do not create duplicate routes; losing the neighbor removes the selected
   route, and restoring it reinstalls the route for both families.
3. For multiple feasible successors, `ip route` reports all selected next
   hops with the intended weights. Verify on a kernel built with ECMP support.
4. A conflicting route created by another process survives an EIGRP withdraw.
   Deliberately test a byte-identical `proto eigrp` conflict and verify it is
   never adopted or removed by the restarting daemon.
5. Inject an invalid next hop, deny `CAP_NET_ADMIN`, and interrupt the Netlink
   socket; confirm failures are reported and recovery retains selected state.
6. Graceful daemon shutdown removes only verified routes it installed. A
   crash leaves routes in the kernel; on restart the new daemon refuses
   unverified stale routes. Once an operator removes verified stale routes,
   reconciliation should install the currently selected state.

These are **manual acceptance criteria, not completed tests**. The current
`linux/test/test_rib.py` exercises the real kernel in isolated network
namespaces, while `eigrp/test/uut/linux-rib/` covers portable RIB semantics.
Neither test substitutes for Cisco/Netlab interoperability qualification.
