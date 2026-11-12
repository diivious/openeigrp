# Developer quickstart

This page gets a new contributor from checkout to a local protocol test. It
requires a C toolchain, `make`, Python 3 and project test dependencies. A
network lab, router images, privileged sockets and integration checkout are
**not** needed for the portable build.

On Debian/Ubuntu, install a basic toolchain and test tools:

```sh
sudo apt-get install build-essential python3 python3-pytest python3-yaml clang-format
```

On macOS, use your preferred Xcode Command Line Tools, Python and
`clang-format` installation. `tools/python` selects the project interpreter.

## 1. Get the code

```sh
git clone https://github.com/diivious/openeigrp.git
cd openeigrp
```

## 2. Build

```sh
make portable-build
```

The lightweight target builds `eigrp/code/` (portable protocol) and
`libunix/code/` (shared Unix services). Plain `make` runs the full developer
qualification gate; use `make portable-build` for a first compile. On Linux, `make linux` additionally builds
`linux/code/openeigrpd`, `linux/code/oectl` and `openeigrpd-config`.

## 3. Run a small test

```sh
make check
make uut SCENARIO=examples/two-router-neighbor-route.yaml
```

`make check` uses the project's Python launcher (`tools/python`), checks style
and runs its own test programs. The example YAML scenario executes the core
against the synthetic Unix UUT transport. It does **not** install routes in
the kernel or establish a Cisco adjacency.

## 4. Find your area

| Work | Directory | Next page |
|---|---|---|
| Routing decisions, packets, RTP, DUAL | `eigrp/code/` | [Core design](../eigrp/specs/README.md) |
| Host abstraction/API | `eigrp/code/*.h` | [Public API](public-api.md) |
| Linux daemon and CLI | `linux/code/` | [Linux integration](../linux/README.md) |
| FRR integration | `frr/code/` | [FRR integration](../frr/README.md) |
| Simulated core test host | `libunix/code/` | [Test guide](testing.md) |
| Cisco interop | `netlabs/` | [Netlab guide](../netlabs/README.md) |

## Next steps

The [developer guide](developer-guide.md) covers tooling, debugging and
installation. The [testing guide](testing.md) explains UUT scenarios and
privileged interoperability labs. Read only the owning specification for the
module you plan to change.
