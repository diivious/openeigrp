# 2R-transport-ipv6

Independent IPV6 Cisco CSR1000v / OpenEIGRP RTP interoperability gate.
Runs the full existing RTP-01 through RTP-11 transport implementation, including
peer initialization, TLV2, burst packetization, sequencing, selective UPDATE
and exact-ACK loss, retransmission, and queue drain. The combined TLV1/TLV2
codec matrix remains informational, not a passing required objective.

```sh
netlabs/tools/remote-uut.sh --scenario 2R-transport-ipv6
```

This case stages `netlabs/test/rtp/2R-transport/driver.py` as a shared
validation engine. The original `2R-transport` combined scenario is retained
for compatibility and remains runnable; `--rtp` continues discovering it.
