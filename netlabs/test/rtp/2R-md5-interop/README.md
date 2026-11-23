# OE-03: Cisco-to-OpenEIGRP MD5 live qualification

Baseline: `security-audit` plus the previously validated Cisco-only ACK
analyzer correction. This is a **new live interoperability scenario**, not a
change to the EIGRP authentication algorithm.

Run from the repository root:

```sh
netlabs/tools/remote-uut.sh --local --scenario 2R-md5-interop
```

Use `make all` first so `--local` selects the current `security-audit` build
rather than an older published release. For a published-release comparison,
omit `--local`; the runner pins the selected installed GHCR image. Cisco
requires `vrnetlab/vr-csr:17.03.06` on the remote host.
The existing remote runner owns teardown and report retrieval.

The experiment uses one Cisco CSR1000v IOS XE 17.03.06 router and one
OpenEIGRP FRR router, AS 4473, IPv4 and disposable MD5 keychain `OE03C2OE`
with key ID 7. Cisco runs **classic** EIGRP, matching the successful
`2R-md5-cisco` control. OpenEIGRP runs **named-mode** EIGRP. The only
initial netlab router configuration is the native interface/loopback underlay;
the driver verifies the Cisco transit interface is up, starts packet capture
on the surviving OpenEIGRP namespace, and only then enables EIGRP with MD5.

## Objectives

| ID | Required observation |
| --- | --- |
| MDI-01 | Both expected router images deployed and checked |
| MDI-02 | Both neighbor tables show stable IPv4 EIGRP adjacency |
| MDI-03 | Both endpoints transmit at least two multicast Hellos with MD5 TLVs (key ID 7); digest validation remains separate |
| MDI-04 | Both send authenticated INIT and EOT UPDATEs with correlated remote ACKs |
| MDI-05 | Both install each other's distinct `/32` route and send acknowledged, authenticated route advertisements |
| MDI-06 | Both withdraw their own route, the remote RIB removes it, and poison UPDATE/QUERY traffic is acknowledged |
| MDI-07 | Both re-advertise the route, the remote RIB reinstalls it, and recovery UPDATE/ACK traffic is observed |

Wire requirements use captured protocol-88 packets and the existing strict
`netlabs/common/wire.py` decoder. The independent RIB gates verify the exact
remote host prefix, not a generic EIGRP route or a locally owned address.
Route advertisements, withdrawals, and recovery must occur within distinct
capture windows. A prior ACK, route, or INIT cannot satisfy a later phase.
Cisco header-only ACK Hellos **do not** count as authenticated discovery.

This test verifies configured MD5 authentication and **actual successful
interoperation with Cisco**. The presence of an Authentication TLV alone is
not proof of digest correctness; neither is an unrecognized digest candidate
proof of an invalid Cisco packet. The raw PCAP is retained for independent
Cisco MD5 digest analysis if adjacency/route exchange fails.

If an earlier phase succeeds but a later phase fails, each objective records
its own result and reason. Route and capture failures are not converted to
PASS from the other endpoint's neighbor table. Phases not reached because a
previous phase failed are marked BLOCKED rather than reported as independently
failed protocol objectives.

## Verified Cisco MD5 wire behavior (OE-03)

Cisco CSR1000v IOS XE 17.03.06 is the on-wire authority for this test.
The earlier Cisco-to-Cisco control and the 0.5.18 mixed-peer PCAP reproduce
its MD5 digest over **120 of 120 authenticated Cisco packets** (79 in the
control, 41 sent by Cisco in the mixed-peer capture). The standalone
`test_cisco_md5_digest.py` independently verifies the 79 control packets
using the standard-library PCAP reader and MD5 implementation.

Observed Cisco MD5 digest generation:

1. Clear the 16-bit EIGRP header checksum.
2. Replace the 16-byte MD5 digest field (EIGRP byte offsets 44..59) with
   the configured key, padded to 16 bytes.
3. Compute MD5 over packet bytes `[0:packet_length - 20]`.

The observed Cisco MD5 Authentication TLV key sequence is always **zero** in
these captures; OpenEIGRP must accept that Cisco-specific behavior without
relaxing nonzero sequence ordering for other peers. Cisco also transmits
20-byte Hello ACKs with no Authentication TLV after receiving reliable
packets. Neither a key sequence of zero nor a header-only ACK means that a
particular route UPDATE was accepted; the live wire and RIB gates establish
that separately.

**Security constraints of the observed algorithm:** The final 20 bytes are
not authenticated by MD5. A 60-byte INIT UPDATE hashes only the first 40
bytes, so its digest is independent of the configured key. The 20-byte ACKs
are unauthenticated. These are Cisco interoperability behaviors, **not**
security guarantees, and are deliberately retained only where required for
compatibility. EIGRP checksum validity is not equivalent to cryptographic
integrity. This case does not qualify Cisco key lengths above 16 bytes,
SHA-256, key rollover, or replay resistance.

The 0.5.18 report `uut-20261122T215006Z-5bmgtPju` confirms more than
adjacency: the Cisco RIB contains `198.18.253.1/32`, the OpenEIGRP RIB
contains `198.19.253.2/32`, and the captured add-phase UPDATEs and ACKs
satisfy MDI-05 wire checks. MDI-05 nevertheless failed because the
local-origin probe issued an invalid OpenEIGRP VTY command. The correct
named-mode topology query is:

```sh
show eigrp address-family ipv4 topology 4473 198.18.253.1/32
```

The AS must follow `topology` (not `ipv4`). Invalid topology CLI syntax
must fail immediately instead of causing a 40-second retry timeout. The
live run has **not** yet exercised withdrawal/recovery, so MDI-06/07 must
not be reported as passing based on add-phase evidence. The mixed-peer test
remains open until all seven objectives pass live.

## Artifacts and limitations

* `cisco-openeigrp-md5.pcap`: complete two-way capture; diagnosis starts here.
* `md5-packets.json`: decoded EIGRP headers, authentication TLVs, route TLVs,
  metric fields, packet timestamps and RTP sequence/acknowledgment numbers.
* `oe-md5-config.log`, `cisco-md5-config.log`: retained runtime configuration.
* `oe-*`, `ce-*`: final neighbor/RIB/configuration snapshots and container logs.
* `oe-daemon.log`: FRR daemon file containing receive authentication errors.
* `ce-debug-enable.log`, `ce-logging.log`, `ce-eigrp-events.log`: Cisco
  debug activation and packet/neighbor rejection diagnostics (collected
  before the live runner tears down the topology).
* `report.json`: objective results, exact stage boundaries, RIB assertions and
  per-phase wire statistics/failures.

The OpenEIGRP stimulus uses a dedicated Linux veth pair (`md5rt0` and
`md5rt1`), with the advertised `/32` as the primary address on `md5rt0`.
The EIGRP interface is passive. The veth and its kernel address remain up
through withdrawal; `no network` retires the EIGRP-originated route. On
recovery, `network` re-admits the same connected route. The Cisco stimulus
uses `Loopback100`. This avoids relying on a second address on `lo`, which
does not create a separate EIGRP runtime interface in OpenEIGRP. The live
driver saves `oe-origin-{add,withdraw,recover}.log` after checking the
local EIGRP topology before each remote RIB assertion.

This interoperability case deliberately uses **internal IPv4 host
routes**, avoiding a simultaneous static redistribution/metric variable. It
does not exercise IPv6, HMAC, mismatched-key rejection, rollover, or scale.
Those are separate qualification cases. A successful Cisco-to-Cisco control
is not a substitute for this live mixed-peer run.

Executable local regressions:

```sh
python3 -m pytest -q netlabs/test/rtp/test_md5_interop.py netlabs/test/rtp/test_cisco_md5_digest.py
```

The tests reuse a real Cisco-to-Cisco capture fixture previously obtained from
IOS XE 17.03.06. They exercise the actual shared EIGRP wire decoder, generated
route/withdrawal packets, CLI stimulus and report gates. Unit-test PASS does
not establish that the mixed-peer scenario passed on StormTrooper.

Security: `oe03-lab-md5` is a disposable test key. The reports may contain it;
do not publish them as production credentials.
