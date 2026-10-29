# 2R-transport: OE/CSR wire and reliable transport qualification

**One OpenEIGRP FRR router, one Cisco CSR1000v; process OpenEIGRP, AS 4473.**
Native netlab initializes both devices and their IPv4/IPv6 connected loopbacks.
The scenario uses only explicit reversible route, metric, AS, interface and
restart stimuli after initial provisioning. The runner owns teardown.

Run independently from the repository root:

```
netlabs/tools/remote-uut.sh --scenario 2R-transport
python3 -m unittest discover -s netlabs/test/rtp -p 'test_2r_transport.py'
python3 -m unittest discover -s netlabs/test/infra -p 'test_2r_interop.py'
```

The scenario reports each AF independently, with these objectives:

- RTP-01: both directions of HELLO, NULL INIT UPDATE, reliable ACK, EOT,
  and neighbor recovery after a controlled interface reset.
- RTP-02: Cisco and OE K1 mismatches, plus a Cisco autonomous-system change.
  Cisco K1 is set with the transit link down, then link-up HELLOs must
  actually advertise K1=2 while the peers remain down. IOS XE's special
  all-255 goodbye HELLO is not mismatched-K evidence. Recovery requires a
  healthy peer after resetting the stimulus.
- RTP-03: exact internal/external TLV2 framing and codec observation per
  sender, required for both IPv4 and IPv6. The combined TLV1/TLV2 codec
  matrix remains in the report as an **informational** objective. Missing
  TLV1 evidence is BLOCKED, never PASS, but does not fail this multiprotocol
  transport scenario. Legacy TLV1 qualification is deferred.
- RTP-04/05: 128 individually known external prefixes per sender, one and
  multiple route TLVs, multiple EIGRP packets, complete prefix set and MTU.
  Adjacency is temporarily taken down while routes are originated to induce
  full UPDATE exchange rather than assuming batching from CLI commands.
  After burst withdrawal, RTP-04 injects exactly one additional route per
  sender in separate captures, since startup EOT may contain several TLVs.
  RTP-06 also includes those active-adjacency single-prefix transmissions,
  requiring actual multicast packets rather than interpreting unicast EOT
  synchronization as a multicast test.
- RTP-06/07: multicast discovery/reliable UPDATE, unicast INIT and pure ACK,
  bidirectional sequence ordering and ACK closure.
- RTP-08: separate packet loss and *exact ACK sequence* suppression for each
  sender and AF. Each holds the impairment until the exact route UPDATE is observed while
  the fault is active and a nonzero measured `tc` drop counter confirms loss.
  Packet-loss faults run at the **receiver ingress**, preserving the
  sender-side transmit capture and allowing the original UPDATE and
  retransmissions to be observed. Exact ACK-loss faults continue at
  **receiver egress**. Each case requires a healthy adjacency before fault
  injection; missing captures and unexercised filters remain failures.
  If a fault fails during setup, its original error takes precedence over a
  secondary empty-capture error, which is retained in a diagnostic log.
  UPDATE-only packet suppression preserves HELLO adjacency. Each requires
  repeated UPDATE sequence, receiver-side packet evidence, final ACK reaching
  the sender, and route convergence. The ACK sequence is predicted
  using a preceding measured reliable UPDATE; a prediction mismatch fails
  rather than silently relaxing the fault criterion.
- RTP-11: all burst prefixes learned, ACKs resolved and the real neighbor
  queue-count column drained on both implementations.
RTP-12 cold restart is isolated in the independently deployed `2R-restart`
scenario. Transport qualification never restarts a node mid-address-family run.

Each capture is started before its mutation. Output includes PCAPs, decoded
JSON, router diagnostics, actual image ID/digest, per-objective states and
`report.json`. Failure to capture or decode the required packets is a failure,
not evidence inferred from an RIB or neighbor entry. Runtime network faults
are restored in `finally`, with runner-owned recovery for interrupted tests.

## Limits and live prerequisites

Cisco CSR1000v 17.03.06 and the locally selected OpenEIGRP release must accept
the named-mode metric, IPv6 loopback, redistribution and AS configuration.
Host requires `tcpdump`, privileged `nsenter`, `tc`, Docker, `sshpass`, netlab
and Containerlab. The netlab image selection and cleanup contract is unchanged.

The static named-mode configuration is not assumed to exercise classic TLV1
and multiprotocol TLV2 together. All observed codec combinations remain
recorded, and the combined matrix remains BLOCKED while TLV1 evidence is
missing. The current scenario requires the per-AF TLV2 assertions, not the
combined matrix; a separate TLV1 fixture is deferred to a later increment.
An installed route does not prove internal topology descriptor retirement or
exactly-once processing;
portable UUT is still responsible for those properties.

**No live result is bundled with this source overlay.** A clean deployment and
mock regression PASS are not Cisco interoperability qualification.

The native netlab deployment ID is `eigrp-2r-rtp`, respecting netlab's
16-character name limit. The original longer name never deployed; no
RTP packet exchange was exercised in that failed run.

## Host capture prerequisite

This scenario needs noninteractive `sudo nsenter` on StormTrooper for both
`tcpdump` capture and `tc` fault injection. Run the administrator-owned
`netlabs/tools/remote/enable-packet-access.sh` on the remote host once.
A missing prerequisite is `BLOCKED` before `netlab up`, not repeated RTP
`FAIL` results. A permitted but unsuccessful capture remains a test failure.

### Loss capture and burst diagnostic prerequisites

When the OE transit interface is deliberately disabled, startup, K-value, and
route-burst captures run in the **surviving Cisco container namespace**. IPv6
loss checks retain the observed link-local peers from before the outage; no
address is rediscovered from a down interface. A burst reads each router RIB
once per poll regardless of prefix count. If the bidirectional route check
fails, `burst-<family>-<role>-{config,static,topology}.log` records the
configured redistribution, origin RIB, and topology state for diagnosis.
No route or RTP result is marked PASS based solely on configuration.
