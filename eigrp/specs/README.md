# Portable EIGRP design

These documents describe **implementation internals**, not first-build
instructions. Start with the part relevant to your change.

| Topic | Design document |
|---|---|
| ACTIVE/PASSIVE state, feasibility, QUERY/REPLY, descriptor ownership | [DUAL](dual.md) |
| Successor selection, path order and feasible successors | [Route selection](route-selection.md) |
| Packetization, queues, reliable delivery, retransmission and TLVs | [RTP and packetizer](rtp.md) |
| What a host integration must provide | [Core integration requirements](integration.md) |
| Cisco on-wire evidence, RFC differences and unresolved decisions | [Cisco/RFC errata](cisco-errata.md) |

The **core** implements the protocol under `eigrp/code/`. It does not own
host CLI parsers, sockets, systemd, FRR/YANG objects or RIB implementation.
Repository-wide build and testing guidance lives in
[the developer guide](../../specs/developer-guide.md) and
[the testing guide](../../specs/testing.md).
