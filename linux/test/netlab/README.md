# Protocol-88 Cisco interoperability qualification (manual Netlab gate)

Copyright (C) 2026 Donnie V. Savage

**Status: not yet executable as an adjacency test.** Task 4 implements host
interface discovery and Task 5 adds native raw IPv4 packet services, but
`linux-support` does not yet have a complete `openeigrpd` executable,
config/startup lifecycle, timers, or native RIB integration. A Cisco neighbor
cannot be established by linking the transport library alone. This test is a
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
