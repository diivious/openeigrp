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
interpretation of the RFC. No additional Cisco/RFC discrepancy is asserted
by this documentation-only reorganization.
