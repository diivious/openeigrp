# Cisco on-wire compatibility and RFC 7868 errata

Copyright (C) 2026 Donnie V. Savage

This is the **core-owned record** for verified differences between Cisco
EIGRP on-wire behavior, OpenEIGRP implementation and RFC 7868. It is not a
list of suspected bugs or a second copy of the RFC.

## Evidence order

1. Verified Cisco packet captures and independently checked TLV decodes
   establish on-wire field widths, ordering, lengths and encodings.
2. Cisco documentation and observed IOS/IOS XE behavior support semantics.
3. [RFC 7868](https://www.rfc-editor.org/rfc/rfc7868.html) supplies the
   reference behavior where consistent with Cisco evidence.
4. Ambiguous decisions, differences among Cisco versions, and intentional
   deviations require project-owner approval.

FRR, BIRD, Linux and OpenWrt are integration environments, **not** protocol
specification authorities. A passing test is evidence only for the tested
implementation, version and topology.

## Recording a difference

For each verified discrepancy, record: Cisco platform/software version,
packet-capture or verified decode reference, exact TLV/packet bytes, RFC
section, observed behavior, OpenEIGRP behavior, test or scenario proving it,
and the explicit project decision. Keep unverified hypotheses separate.

The branch's [Netlabs interoperability suite](../../netlabs/README.md) and
[portable packet tests](../test/README.md) contain evidence and fixtures.
Do not fabricate an erratum or claim a wire difference from an unverified
interpretation of the RFC. Each entry below identifies its own evidence and
qualification limits.

## MD5 authentication: verified Cisco compatibility limitations (OE-03)

**Status:** Implemented and live-interoperability qualified for the configuration
below. The MD5 security weaknesses described here are **intentionally retained**
for Cisco compatibility. Do not silently replace this wire behavior with a
stronger but incompatible calculation.

**Reference and evidence.** RFC 7868, Sections
[6.5](https://www.rfc-editor.org/rfc/rfc7868.html#section-6.5) and
[6.7.2.1](https://www.rfc-editor.org/rfc/rfc7868.html#section-6.7.2.1),
describes the EIGRP header and MD5 Authentication TLV, but does not establish
the exact MD5 input coverage found in these captures. Cisco CSR1000v running
IOS XE 17.3.6 is the observed reference. A live Cisco-to-Cisco control
(`2R-md5-cisco`) passed C2C-01 through C2C-05; a live Cisco-to-OpenEIGRP test
(`2R-md5-interop`) passed MDI-01 through MDI-07 with OpenEIGRP
`0.5.19-diivious.dev` on November 22, 2026. The mixed-peer report ID is
`uut-20261122T230647Z-YPN2YGx2`. The captures and byte-exact tests, not the
passing objective labels alone, establish the digest calculation.

### Observed digest construction

For the tested key length (12 ASCII octets), Cisco's EIGRP MD5 operation is:

1. Take the complete EIGRP payload, starting with its **20-byte EIGRP
   header**, excluding any IP header.
2. Set the EIGRP header checksum to zero for the calculation.
3. Replace the 16 digest octets at EIGRP byte offsets **44..59** with the
   configured key, padded on the right with zero octets to 16 bytes.
4. Calculate **MD5 over the first `packet_length - 20` octets** of this
   temporary packet. `packet_length` is the EIGRP packet length, not the IP
   packet length. Store the resulting 16-byte digest at offsets 44..59.
5. Calculate the ordinary EIGRP header checksum for transmission separately.

The 40-byte MD5 Authentication TLV occupies offsets **20..59** (type
`0x0002`, length 40, MD5 authentication type `0x0002`, digest length 16,
key ID, key sequence, reserved octets and digest). The digest algorithm is
**not HMAC-MD5**. Its authenticated span is a *prefix* of the packet, not the
complete packet with only the digest omitted. For example:

| EIGRP length | Hashed offsets | Unhashed offsets | Consequence |
| --- | --- | --- | --- |
| 60-byte INIT UPDATE | 0..39 | 40..59 | Key and digest are outside the hashed span; the INIT digest is not keyed |
| 80-byte Hello | 0..59 | 60..79 | A trailing 20-byte Parameter/Version TLV area is not protected by MD5 |
| Longer UPDATE | 0..length-21 | Final 20 bytes | Trailing route data can be outside the MD5 integrity check |

The unhashed bytes remain subject to the ordinary EIGRP checksum, **not**
cryptographic authentication; an attacker who can modify the packet can also
recalculate that checksum. In the 80-byte Hello case, the Parameter TLV can
be altered without invalidating the MD5 digest. This is relevant to the
Hello/Goodbye manipulation reported in OE-03. The exact unprotected TLVs
vary with the packet's total length and TLV ordering.

Cisco's extended Hellos can also include TLV `0x00f5`; they use the same
observed digest calculation. These findings describe the captured Cisco
profile only. Cisco behavior with keys longer than 16 octets has **not**
been verified: OpenEIGRP currently retains its previous long-key calculation
rather than claiming Cisco compatibility for that case.

### Authentication key sequence and RTP ACKs

- **MD5 authentication key sequence:** All observed Cisco MD5 packets in the
  reference captures use **zero**, independently of the EIGRP header's RTP
  sequence number. OpenEIGRP accepts MD5 key sequence zero only while the
  neighbor's stored MD5 authentication sequence remains zero. Once it has
  accepted a nonzero sequence, normal serial-number ordering continues;
  SHA-256 sequence processing is unchanged. The Cisco zero-sequence profile
  provides no authentication-sequence replay protection.
- **Header-only ACK:** Cisco transmits 20-byte EIGRP Hello packets with a
  nonzero ACK, zero RTP sequence and flags, and **no Authentication TLV**.
  OpenEIGRP admits this narrowly shaped packet on an MD5 interface only
  from an existing eligible unicast neighbor after normal packet framing
  and checksum checks. The AF-owned RTP logic still matches the ACK to an
  outstanding reliable sequence. It cannot establish a neighbor or refresh
  the neighbor hold time, and the exception does not apply to SHA-256.
  Matching a received ACK to an outstanding sequence is **not** cryptographic
  authentication: a source-spoofed, correctly sequenced ACK remains possible.

### Explicit OpenEIGRP decision and security boundary

**Retain Cisco-compatible MD5 behavior intentionally.** This includes the
`packet_length - 20` digest span, Cisco zero-valued key sequences on receive,
and narrowly admitted unkeyed RTP ACKs. These are **known security limitations**,
not OpenEIGRP assurances of complete packet integrity, strong replay
protection, or authenticated acknowledgments. In particular, the 60-byte
INIT digest can be computed without the shared secret.

Do not try to fix these shortcomings by changing the shared MD5 calculation,
rejecting zero-sequence Cisco packets, or requiring an Authentication TLV on
Cisco's header-only ACKs: doing so breaks the demonstrated interoperability.
Likewise, do **not** broaden the unauthenticated ACK exception to discovery,
multicast, liveness, SHA-256, or arbitrary packets. An alternative hardened
profile must be a separate, explicitly approved design with its own
interoperability tests; it must not silently change the Cisco-compatible
profile.

### Regression evidence and qualification boundary

- `eigrp/test/packet/test_cisco_md5_wire.py` validates captured standard and
  extended Hellos, 60-byte INITs and route UPDATEs against the production
  digest calculation. Binary fixtures are under
  `eigrp/test/packet/fixtures/cisco-md5-*.bin`.
- `eigrp/test/packet/test_cisco_md5_zero.py` checks zero-sequence acceptance
  and nonzero ordering; `eigrp/test/packet/md5_ack.py` checks Cisco bare-ACK
  ingress restrictions.
- `netlabs/test/rtp/2R-md5-cisco/` is the Cisco-only control;
  `netlabs/test/rtp/2R-md5-interop/` exercises OpenEIGRP/Cisco adjacency,
  INIT and ACK exchange, bidirectional route installation, withdrawal and
  recovery. The passing MDI-03 objective alone records MD5 TLV presence,
  **not** independent cryptographic proof. Successful end-to-end exchange
  plus the byte-exact capture tests provide the combined evidence.
- The separate connected-route withdrawal fix is a DUAL/topology lifecycle
  correction, not a Cisco MD5 exception. Its portable regression is
  `eigrp/test/uut/interface-lifecycle/connected-withdrawal.yaml`.

**Not qualified:** other IOS/IOS XE releases, other Cisco platforms, keys
longer than 16 octets, key rollover/lifetime edge cases, and all possible
packet/TLV permutations. Revisit the observed rules only when new Cisco
capture evidence and explicit project-owner approval justify doing so.
