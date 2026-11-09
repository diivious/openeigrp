# Native Linux configuration staging and reload design

Copyright (C) 2026 Donnie V. Savage

## Baseline and current status

`linux-support` has native interface, packet, and RIB libraries, but does not
have a runnable `openeigrpd` process or an event loop binding these libraries
to the portable EIGRP runtime. This task cannot responsibly install a SIGHUP
handler into a non-existent daemon.

The Unix parser now has two deliberately separate operations:

1. `eigrp_unix_config_parse_file()` or `_parse_text()` reads and validates the
   *entire* named-mode file into an owned command plan. It performs no EIGRP
   calls and leaves the previous plan intact when parsing fails.
2. `eigrp_unix_config_commit()` applies the staged initial plan through the
   real public `eigrp_cli.h` targets. It aborts on any semantic result other
   than `SUCCESS`, including `NOT_IMPLEMENTED` and `UNSUPPORTED`.

The compatibility `apply_file()` and `apply_text()` entry points stage the
entire file before any semantic calls. **Commit is not transactional.** Do not
call it on an active daemon to reload configuration. The caller must discard
any partially configured runtime after a failed initial start.

The standalone `openeigrpd-config -t [-f PATH]` validates configuration syntax
without creating protocol objects or requiring raw-socket privileges. The
implicit path is `/etc/openeigrpd.conf`. Offline validation cannot establish
that every host capability or runtime operation will succeed.

## Named-mode subset

- `router eigrp NAME` / `exit`, with `shutdown` / `no shutdown`.
- `address-family ipv4|ipv6 [unicast] [vrf default] autonomous-system ASN`
  / `exit-address-family` with `eigrp router-id A.B.C.D`, `shutdown` /
  `no shutdown`, and `distance eigrp INTERNAL EXTERNAL`.
- IPv4 AF `network A.B.C.D [WILDCARD]` or `network A.B.C.D/LEN`.
  Without wildcard/prefix, classful matching is retained, as in the current
  FRR named-mode parser. Noncontiguous wildcards are rejected.
- AF `neighbor ADDRESS INTERFACE`, `metric weights TOS K1 K2 K3 K4 K5 [K6]`.
- `af-interface NAME` / `exit-af-interface`: `bandwidth-percent`, `bandwidth`,
  `delay`, `hello-interval`, `hold-time`, `passive-interface`, `split-horizon`,
  `next-hop-self`, and `shutdown` (including supported `no` flag forms).
- `topology base` / `exit-af-topology`: `default-metric`, `variance`,
  `metric maximum-hops`, `maximum-paths`, and `redistribute connected|static`
  optionally followed by `metric BW DELAY RELIABILITY LOAD MTU`.

Only default-VRF unicast is exposed in the native syntax. Redistribution is
restricted to the source families implemented by the Linux Netlink observer;
external route instances and route maps are rejected. Authentication,
filtering, leak maps, summarization, maximum-prefix, and other unfinished
policy surface are rejected by the parser until they have a verified native
provider. The parser does **not** provide a substitute for portable policy or
protocol semantics.

A successful offline check says the *syntax* is accepted, not that every
semantic target is supported in every runtime. Actual semantic errors remain
fatal. Operators must not confuse staging success with applied configuration.

## Required live reload transaction (not implemented)

A correct native daemon needs the following sequence before enabling SIGHUP:

1. The signal handler only posts a flag or writes to a self-pipe/eventfd;
   parsing, allocation, logging, and EIGRP calls occur on the event loop.
2. Read a consistent file snapshot into a new, private immutable plan and
   validate all grammar, AF/VRF constraints, resource limits, and capabilities.
3. Diff that plan against the *owned* active generation by AF/interface/
   network/topology object, not by text position. Work out removals and
   additions, including shutdown, metric reset, redistribution removal, and
   AF retirement. No mutation may occur until the entire plan passes checks.
4. The portable core must expose a supported transaction mechanism or
   equivalent prevalidated, failure-atomic semantic application. A rollback
   based on deleting and recreating live AFs is **not safe**: it drops DUAL,
   adjacency and RIB state and can retire referenced descriptors.
5. Serialize the commit against Linux interface/packet/Netlink callbacks,
   preserve deferred-work ownership, then atomically publish the new config
   generation only after every semantic change is committed. On any failure,
   the former generation and runtime behavior must remain intact.
6. Retire old plan memory only after observers and deferred work can no longer
   reference it. Keep the on-disk file as the sole persistent source for
   restart; do not pretend unsuccessful reloads changed it.

Without that core/host commit facility, changed live configuration must be
rejected instead of partly applied. Installing a SIGHUP hook that replays the
initial-load parser would violate the invalid-reload requirement.

## Verification boundaries

`linux/test/test_config.py` runs crafted configuration, malformed input,
repeated staging, semantic dispatch, failure propagation and CLI tests.
`eigrp/test/uut/linux-config/ipv4-configuration-lifecycle.yaml` exercises
actual *portable* configuration change effects in UUT, not a Linux daemon
reload. Native restart, SIGHUP, parser-to-kernel full-path and Netlab Cisco
interoperability cannot be tested until the native daemon is integrated.
