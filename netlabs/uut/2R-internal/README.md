# 2R-internal: bidirectional internal-route smoke qualification

This completes the existing `netlabs/uut/2R-internal.py` scenario fixture
and fixes its FRR/IOS XE route-output handling. The OE/CSR named-mode EIGRP process
and connected loopback origins are configured by native netlab, using the
shared `netlabs/config/eigrp-2r-interop.j2` extension. The driver checks each
peer's exact internal-route RIB type for both IPv4 and IPv6 and fails if
routes are not learned from the opposite router.

```
netlabs/tools/remote-uut.sh --scenario 2R-internal
```

This is **smoke coverage** only. It does not claim wire-level initialization,
metric and next-hop fidelity, retransmission, or topology ownership; the
independent `2R-routes` grouped scenario owns those deeper DUAL objectives.

On a missing learned prefix the driver retains both routers' EIGRP topology
tables, as well as their RIB snapshots. It does not turn an absent remote
route into PASS because adjacency formed, or infer route type from a
connected/local OE Zebra entry.

The OE image tested on 2026-10-27 supports `show ipv6 route`, not
`show ipv6 route eigrp`. The driver filters EIGRP rows from the full IPv6
Zebra route table and uses Cisco's supported filtered route command.

A diagnostic EIGRP PCAP is collected around a controlled link flap and
adjacency reinitialization when namespace capture privileges are available.
A capture prerequisite failure is reported separately and never changes the
internal-route RIB result; a route still missing in either RIB remains FAIL.
