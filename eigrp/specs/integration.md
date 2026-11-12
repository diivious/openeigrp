# Portable core integration requirements

Copyright (C) 2026 Donnie V. Savage

A host must supply the services the portable engine needs; it must not
reimplement EIGRP protocol decisions. This page is a short design-level map;
[platform integration](../../specs/platform-integration.md) and the public
headers define the exact callable contract.

| API | Direction | Responsibility |
|---|---|---|
| `eigrp.h` | Shared | EIGRP-owned value types and opaque identities |
| `eigrp_cli.h` | Host -> core | Instance, address-family, configuration and administrative semantics |
| `eigrp_mgnt.h` | Core -> host observer | Portable read-only state snapshots and supported event operations |
| `eigrp_sys.h` | Core <-> host | Time, work scheduling, interface lifecycle, packet I/O, logging, policy, keychain |
| `eigrp_rib.h` | Core <-> host | Route installation, withdrawal, source route and redistribution events |

A correct integration must normalize host values, preserve interface/VRF
identity, carry IPv4 and IPv6 packet metadata, schedule protocol timers,
serialize state access, handle shutdown and free resources safely. It must
propagate unsupported capability results without claiming success.

DUAL owns destination/path lifecycle; RTP owns reliable transmission; the
packetizer owns semantic advertisement work. A host must not free or cache
private descriptors or interpret packet TLVs to implement routing decisions.
For service ownership examples see [architecture](../../specs/architecture.md).

Integration-specific build options and tests belong in the integration's
own `specs/`, e.g. [Linux](../../linux/specs/configuration.md),
[FRR](../../frr/specs/integration.md), or
[Unix](../../libunix/specs/integration.md).
