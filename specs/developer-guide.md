# Developer guide

This continues the [quickstart](quickstart.md). Read it when working on a
nontrivial change, not as a prerequisite for evaluating the project.

## Repository boundaries

| Area | Responsibility |
|---|---|
| `eigrp/code`, `eigrp/specs`, `eigrp/test` | Portable protocol implementation, design and tests |
| `libunix/` | Shared Unix scheduling, plus separate simulated UUT services |
| `linux/` | Native Linux daemon, management and kernel integration |
| `frr/` | FRR configuration, Zebra, runtime, build adapter |
| `bird/` | Deferred BIRD integration boundary |
| `netlabs/` | Privileged Cisco/OpenEIGRP interoperability testing |
| `specs/`, `tools/` | Repository-wide development and build tooling |

The portable core determines neighbor, DUAL, topology, packetization and RTP
behavior. Host integrations supply configuration, RIB, interfaces, sockets,
logging and event scheduling through EIGRP-owned APIs. See
[architecture](architecture.md) and [integration contract](platform-integration.md).

## Build and inspect

```sh
make                       # host compilation and full-tree style checks
make linux                 # Linux only: native daemon and oectl
make clean
make all                   # fast unit/YAML UUTs, quick scale, Docker and Debian builds
make pre-commit            # full scale, Docker and package lifecycle UUTs
```

Compile with the repository toolchain and its `.clang-format`; never format
unrelated files. For formatting commands and naming rules, see
[code conventions](code-conventions.md) and [contributing](contributing.md).

Use `gdb` or `lldb` with debug symbols on the executable under test. The
portable UUT keeps networking unprivileged and deterministic; native Linux
raw sockets and Netlink route writes need a suitable privileged test machine.
Avoid debugging an integration defect by adding host-native types to the core.

## Working on public APIs

The cross-boundary entry points are in `eigrp/code/eigrp.h`, `eigrp_cli.h`,
`eigrp_mgnt.h`, `eigrp_rib.h` and `eigrp_sys.h`. Read the
[public API reference](public-api.md) for the exact caller, provider, ownership
and lifecycle contract. Keep configuration semantic targets portable. Do not
access private DUAL/topology objects from integration code.

## Installation or integration work

Use the owning guide rather than generic recipes: [Linux](../linux/README.md),
[FRR](../frr/README.md), or [Unix test host](../libunix/README.md).
Production Linux packages use the [native packaging guide](../linux/specs/maintainer.md).
BIRD and OpenWrt are not ready-to-install integrations in this branch.

## Verify a change

Run focused executable tests, then applicable portable UUT YAML scenarios and
`make all`. For changes to live packet behavior or platform services, use
integration tests and Cisco/Netlab evidence on a qualified environment.
Document any skipped checks in the PR. See [testing](testing.md).
