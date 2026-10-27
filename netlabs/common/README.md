# Live interoperability evidence and controlled faults

Copyright (C) 2026 Donnie V. Savage

These helpers are netlab interoperability **infrastructure**, not portable
EIGRP protocol implementation. They are staged by `remote-uut.sh` with every
scenario and are deliberately kept under `netlabs/common/`.

## Runner contract

`netlabs/tools/remote-uut.sh --list` discovers only complete `2R-` through
`6R-` driver/fixture triples. The runner checks that a topology name is in
the `eigrp-*` or `oeig-lab-*` namespace, so it can identify stranded Docker
containers even when the session state file is missing. Arbitrary 2R–6R
names work within that namespace. A failed Docker inspection is not treated
as proof of an empty host.

Results still contain `report.json` (the driver's own assertion results),
`image.json`, `runner.log`, `cleanup.log`, and `run-status.txt`. Every completed
run also produces `result.json` (`openeigrp-netlabs-result/v1`) recording
scenario ID, router count, timestamp, driver exit, image-selection exit,
image evidence, report, artifacts, and separate cleanup result. `PASS` means
only that the driver returned success and teardown succeeded; it does *not*
automatically upgrade existing `2R-neighbor` checks to wire-level RTP-01.
Driver failure is `FAIL`; unavailable image selection is `BLOCKED`. A cleanup
failure is `FAIL`, retains the remote session, and blocks further runs until
explicit `--stop` recovery succeeds.

## Packet capture and decoder

`capture.py` runs host `tcpdump` using a bounded capture filter (`ip proto 88
or ip6 proto 88`). An explicit link interface is mandatory. With
`--container`, capture runs on the **host's filesystem** in that node's network
namespace using `sudo -n nsenter` (only the network namespace is entered).
Rootless capture works only if the selected interface permits it; otherwise
the capture step fails. The host needs `tcpdump`, Docker and, for namespace
capture, passwordless permission to execute `nsenter` safely. Limit that
permission to the dedicated netlab appliance; do not grant broad sudo to
untrusted account users.

Start after deployment has created the link, but **before** initiating the
relevant protocol event. For startup-HELLO evidence, trigger a controlled
subsequent adjacency restart after capture starts; a post-deployment capture
cannot prove the initial boot exchange.

```sh
python3 netlabs/common/capture.py start \
  --container clab-eigrp-neighbor-oe --interface eth1 \
  --pcap "$ARTIFACTS/oe-eth1.pcap" \
  --state "$ARTIFACTS/oe.capture.json"
# Run the documented stimulus; inspect both vendor ends.
python3 netlabs/common/capture.py stop \
  --state "$ARTIFACTS/oe.capture.json" --require-packets
python3 netlabs/common/wire.py "$ARTIFACTS/oe-eth1.pcap" \
  --output "$ARTIFACTS/oe-eth1.events.json" --require-opcode HELLO
```

`wire.read_pcap()` accepts Ethernet including up to two VLAN tags, Linux cooked
SLL v1/v2, and raw IPv4/IPv6 PCAP. It handles normal IPv4 options and common
IPv6 extension headers, but **rejects fragments** rather than inferring EIGRP
payload. It decodes header fields, flags, sequence/ACK, TLV framing/types,
route codec, metrics, prefixes, and external provenance where defined. Opaque
route extended attributes are preserved as hex. Decoding or assertion failure
is nonzero; a missing packet or empty capture is never a PASS. Use
`wire.evidence(events, opcode=..., source=..., destination=..., sequence=...,
ack=..., tlv=..., prefix=..., family=...)` for exact packet evidence.

The CLI `--allow-errors` mode is only for diagnostic extraction. Do not treat
its partial list as qualifying evidence. Caller-provided assertions must pair
wire evidence with neighbor/DUAL/RIB checks appropriate to the objective.
A packet capture from one direction alone does not prove receipt by the peer.

## Controlled network faults

`network_fault.py` applies *one active fault per interface*, with a state file under
the same artifacts directory. State must use the suffix `.fault.json` so
runner cleanup discovers it. Supported modes are `drop` (all protocol-88
outbound packets), `delay` (fixed 1–10000 ms to protocol-88 traffic only using
a `tc` PRIO + netem band), and `ack` (drop IPv4/IPv6 EIGRP HELLOs whose
sequence is zero and whose ACK is an explicitly supplied nonzero reliable
sequence number). The ACK value must come from a real earlier capture; no
assumption that every HELLO is an ACK is allowed.

```sh
python3 netlabs/common/network_fault.py apply \
  --container clab-eigrp-neighbor-oe --interface eth1 --family ipv4 \
  --mode ack --ack 12345 --state "$ARTIFACTS/oe.fault.json"
# Generate/observe expected retransmission and recipient-specific outcome.
python3 netlabs/common/network_fault.py clear \
  --state "$ARTIFACTS/oe.fault.json"
```

Changes **egress** of the named container/interface, not the remote endpoint.
For multicast selectivity, use the correct lagging receiver's egress interface
and capture both relevant sender and receiver interfaces. IPv4 ACK filtering
assumes a 20-byte IPv4 header; IPv6 filtering assumes no extension headers.
Reject or skip tests using those header forms rather than claiming an
unobserved ACK suppression. A drop persists until explicitly cleared, making
loss deterministic within that interval, **not a guarantee of an exact
packet count**. For packet-count-specific cases, assert matching `tc` filter
counters and PCAP contents in the scenario. No arbitrary-delay timer in these
helpers automatically re-enables traffic.

The helper refuses unknown existing `tc` qdiscs rather than overwriting host
policy. Use only isolated Containerlab veth links. Driver should clear faults
as soon as evidence is collected; the runner also calls both network fault and
capture cleanup *before* `netlab down`, including when the test times out or
fails. Failed restoration retains the session, state and live namespaces for operator recovery; the runner does not execute `netlab down` until marker cleanup succeeds.
Do not use these helpers against production interfaces.

## Regression checks

Use the unified client interface from the repository root:

```sh
netlabs/tools/remote-uut.sh --list
netlabs/tools/remote-uut.sh --all
netlabs/tools/remote-uut.sh --rtp
netlabs/tools/remote-uut.sh --dual
netlabs/tools/remote-uut.sh --scenario 2R-neighbor
```

`--all` runs mocked/unit regressions on the Linux test appliance, then
all discoverable live scenarios. RTP and DUAL select their respective groups. A run cannot
continue to the next live case unless successful teardown is confirmed from
the retrieved `result.json`. See `netlabs/test/README.md` for test placement
and legacy-compatibility rules.

These tests execute the packet decoder, capture process lifecycle, fault
command/lifecycle logic, and the real remote runner using isolated mocks.
They are not evidence that StormTrooper, Cisco CSR1000v, `sudo nsenter`, or
`tc` has been exercised on live links. The separately required live smoke
check is a captured OE/CE exchange followed by `netlab down` with no
remaining `clab-eigrp-*` containers.
