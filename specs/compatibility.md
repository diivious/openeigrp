# Compatibility and qualification

This page separates **portable protocol coverage** from **integration
readiness**. It describes implementation in the `linux-support` branch,
not production certification of any particular host.

## Core protocol

| Area | Portable implementation | Important qualification |
|---|---|---|
| IPv4 and IPv6 EIGRP | Implemented | Each integration must provide real AF packet and RIB services |
| DUAL, successor and feasible-successor selection | Implemented | Regression-tested by portable UUT; cross-vendor live testing is separate |
| Reliable transport (RTP), acknowledgments and retransmission | Implemented | Cisco wire captures remain the encoding authority |
| Classic and wide metrics | Implemented | Validate platform interface metrics and interop on deployment target |
| Named-mode configuration semantics | Public portable API | Actual accepted syntax depends on the integration |
| EIGRP Stub runtime | **Out of scope** | Do not infer support from feature names or partial configuration |

## Integration coverage

| Host | IPv4 | IPv6 | Configuration / operations | Status |
|---|---|---|---|---|
| Native Linux | Raw protocol-88 socket implemented | Native IPv6 packet transport implemented | Named-mode subset, `oectl`, systemd and `.deb` | Native integration implemented; live privileged and Cisco qualification required |
| FRR | Adapter implemented | Adapter implemented | FRR host CLI/management | Qualify against selected FRR tree; feature surface differs from native Linux |
| Unix UUT | Synthetic | Synthetic | Scenario-driven | Test-only host, no live kernel routing |
| BIRD | Not implemented | Not implemented | Not implemented | Deferred |
| OpenWrt | Not implemented | Not implemented | Not implemented | Planned |

### Native Linux limitations

- Configuration is initial-load only. SIGHUP leaves the running configuration
  unchanged; validate and restart to apply changes.
- Default Linux VRF and unicast AFs only in current configuration grammar.
  Redistribution supports Linux `connected` and `static` sources.
- MD5 key-chain authentication is supported; HMAC-SHA-256 and encoded type-7
  key material are not supported by the native configuration path.
- `oectl` is observational except `clear events`. Its `show routes` is a
  portable topology view, **not** a Linux kernel route dump.
- Management IPC pagination is bounded (offset at most 8192) and cannot
  guarantee a stable snapshot while the topology changes.

For specific Linux commands, see [Linux README](../linux/README.md),
[configuration manual](../linux/packaging/man/openeigrpd.conf.5) and
[control-client manual](../linux/packaging/man/oectl.8).

## Protocol authority

Verified Cisco packet captures and decodes take precedence for wire format;
observed Cisco IOS behavior supports semantics. RFC 7868 is the reference
where consistent with those observations. Record conflicts with evidence in
[core errata](../eigrp/specs/cisco-errata.md) rather than silently changing
packet formats. Integration behavior does not redefine the protocol.
