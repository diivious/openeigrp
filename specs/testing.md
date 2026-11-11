# Testing OpenEIGRP

There are three different levels of evidence. Choose the least expensive
level that actually exercises the behavior under test.

| Level | What it proves | What it does *not* prove |
|---|---|---|
| Executable unit tests | Actual C behavior and failure/ownership paths | Cisco interoperability |
| YAML UUT | Portable EIGRP convergence over synthetic Unix transport | Kernel raw sockets, real RIB, Cisco peers |
| Netlab interoperability | Packets and routing decisions across real OpenEIGRP/Cisco instances | Every portable corner case or memory invariant |

## Local portable qualification

```sh
make check SOURCE=all
make uut
make uut SCENARIO=examples/two-router-neighbor-route.yaml
make uut SUITE=rtp-core
make scale
make all
make pre-commit
```

`make all` is the default developer gate: it builds the Linux daemon on
Linux (portable code on other hosts), runs full-tree style and unit checks,
portable UUT, quick scale, all named scale suites, and native Linux tests on
Linux. `make pre-commit` additionally builds both FRR Docker architectures
and both Debian architectures, then exercises package-lifecycle tests. For a
fast first compile, use `make portable-build`; a targeted heavy scale check
can be selected with `make scale SUITE=100k`.

Scenarios are rooted in `eigrp/test/uut/` and use its `run.py`, `run_all.py`
and Makefile. Start with [the UUT README](../eigrp/test/uut/README.md) for
schema, topology and execution details. An existing scenario extended with
additional assertions is still one scenario, not a new scenario count.

## Integration-specific tests

- Native Linux: `python3 linux/test/test_daemon.py`, `test_config.py`,
  `test_oectl.py`, `test_mgnt.py`, plus [documentation checks](../linux/test/test_docs.py).
  Raw-socket and systemd tests require a privileged Linux environment.
- FRR: see [FRR tests](../frr/test/README.md).
- Shared Unix host: see [Unix host tests](../libunix/README.md).
- Cisco packet and route interoperability: see [Netlabs](../netlabs/README.md)
  and the dedicated [test README](../netlabs/test/README.md).

Never label a UUT virtual-wire scenario as a native Linux or Cisco lab pass.
Collect packet captures and state transitions for protocol interoperability
claims. Record prerequisites, commands, versions, pass/fail results and skipped
steps. Cleanup and descriptor retirement often require internal assertions;
absence from filtered operational output is insufficient.
