# OpenEIGRP architecture

Copyright (C) 2026 Donnie V. Savage

This page describes **repository ownership**. For portable DUAL, topology,
route selection or RTP details, follow [core design](../eigrp/specs/README.md).
For a concrete host, follow its own README and specifications.

## One protocol implementation, multiple integrations

![Portable EIGRP with host integrations](images/project-architecture.svg)

The portable engine in `eigrp/code/` owns all routing decisions: neighbor
protocol state, metrics, topology, DUAL, RTP, packetizer and TLV encoding.
Host integrations supply process, configuration, interfaces, timers, sockets,
logging, management transport and RIB mechanisms. The same normalized input
must produce the same EIGRP decision regardless of host.

![Northbound and southbound API directions](images/public-api-directions.svg)

- **Northbound:** integration configuration and administrative operations call
  semantic targets in `eigrp_cli.h`. Management reads use `eigrp_mgnt.h`.
- **Southbound:** core requests host services through `eigrp_sys.h` and
  `eigrp_rib.h`. `eigrp.h` provides shared public types.
- **Isolation:** host-native objects are converted at the boundary. No
  integration may implement DUAL, interpret route TLVs, or directly own
  private topology descriptors.

[Public API](public-api.md) contains detailed callable contracts;
[platform integration](platform-integration.md) describes the required
host responsibilities. No newcomer needs to read both before building.

## Ownership map

| Location | What belongs there |
|---|---|
| `specs/` | Build/development how-tos, onboarding, conventions, testing and public API documentation |
| `eigrp/specs/` | Core protocol design, DUAL/RTP internals, integration requirements and Cisco/RFC errata |
| `<integration>/specs/` | Integration-specific architecture, build/configuration/installation details |
| `eigrp/test/` | Portable unit tests and YAML UUT scenarios |
| `<integration>/test/` | Tests specific to that host |
| `netlabs/` | Real Cisco/OpenEIGRP interoperability evidence |

Subdirectories may keep small README files alongside fixtures or tools to
explain how to use them. They are not additional top-level guides.

## Protocol authority and deviations

For wire formats and packet semantics, verified Cisco captures and decodes
are the highest-priority evidence. Observed Cisco documentation/IOS behavior
supports protocol semantics; RFC 7868 is a reference where consistent.
Ambiguous interoperability decisions remain with the project owner. Record
verified differences and their evidence in
[core errata](../eigrp/specs/cisco-errata.md); do not infer authority from an
FRR, BIRD, Linux or OpenWrt implementation.

OpenEIGRP is the project; EIGRP is the routing protocol. Preserve established
`eigrp_` symbols, wire terminology and configuration syntax. Significant API
renaming is a separate design decision. Preserve copyright and author history.

## Integration readiness is not feature readiness

A portable EIGRP feature may exist without a given host exposing the matching
configuration or networking path. A test-host feature does not imply native
Linux support, and a parsed command is not proof of a functioning semantic
operation. See [compatibility](compatibility.md) and the owning integration
specification for limitations.
