# Live interoperability tests and regression groups

Copyright (C) 2026 Donnie V. Savage

Use **one client entry point** from the repository root:

```sh
netlabs/tools/remote-uut.sh --list
netlabs/tools/remote-uut.sh --update
netlabs/tools/remote-uut.sh --all
netlabs/tools/remote-uut.sh --rtp
netlabs/tools/remote-uut.sh --dual
netlabs/tools/remote-uut.sh --scenario 2R-neighbor
netlabs/tools/remote-uut.sh --abort
```

- `--list`: list complete, runnable live scenarios; no remote connection.
- `--update`: explicitly refresh installed OpenEIGRP images; it is not a test.
- `--all`: execute all infra and protocol regressions on the Linux test appliance, then **all discovered live scenarios** (2R-6R) sequentially.
- `--rtp`: run infra and RTP regression tests, then all discovered live RTP scenarios.
- `--dual`: run infra and DUAL regression tests, then all discovered live DUAL scenarios.
- `--scenario ID`: run exactly one live case, without the regression suite.
- `--abort`: stop an interrupted live test or regression run on StormTrooper,
  recover packet-capture and network-fault state, and remove its topology.
  An ordinary interactive lab is stopped using `remote-lab.sh --stop` instead.

The `netlabs/test/` directory contains three independent groups:

```text
netlabs/test/
  infra/       # Remote runner, dispatch, network fault regressions
  rtp/                  # RTP wire/packet-capture regressions, live RTP cases
  dual/                 # DUAL regressions, live DUAL cases
```

Place each new live case under the applicable protocol group:

```text
netlabs/test/rtp/3R-selective-retry/
  driver.py              # Executable Python driver, --scenario/--topology/--artifacts
  scenario.yaml          # Assertions and test inputs
  topology.yml           # Netlab topology, including OE/CE roles
  README.md              # Scope, setup, actions, evidence, expected results
```

Use `2R-` through `6R-` and a descriptive purpose in the case ID. A group
comes from its **directory**, not a guessed keyword in the ID. Each complete
case requires `driver.py`, `scenario.yaml`, and `topology.yml`; incomplete
cases are not discovered. Duplicate IDs across groups or the transitional
`netlabs/uut/` directory are rejected to avoid ambiguous `--scenario` runs.

Older `netlabs/uut/2R-neighbor` remains supported without moving its driver.
Legacy cases declare `group: rtp` or `group: dual` in `scenario.yaml` for
selection. The existing `2R-neighbor` defaults to RTP for older checkouts.
The `2R-internal.py` driver has no complete fixture; it is **not runnable**
and must not be listed or counted as qualified. New cases belong under
`netlabs/test/{rtp,dual}/`, not the legacy `netlabs/uut/` tree.

`--all` stages and runs Infra/RTP/DUAL regression scripts **on the Linux appliance**.
`--rtp` and `--dual` always include infra regressions as safety gates;
The RTP group includes the existing neighbor driver's unit tests.
The provisioned appliance Python environment supplies PyYAML and Jinja2;
Linux provides `/proc`, `flock`, and GNU `timeout`. The macOS client needs only
its existing SSH/tar transport and system Python 3 standard library for batch
summary reporting, not local PyYAML/Jinja2 or Linux tools. Shell tests named
`test_*.sh` and Python `test_*.py` are discovered in their group directories.
Use descriptive permanent filenames, not milestones or project-phase names.

Each remote scenario saves its ordinary report and structured `result.json`.
Batch runs add `netlabs/results/batch-<selection>-<date>-<suffix>.json`,
with per-test status and report references. A regression failure does not hide
subsequent independent regression results, but **blocks all live cases** in
that batch. Regression logs and `regressions.json` are retrieved as a separate
report. A live assertion failure may be followed by
another live scenario **only when cleanup PASS is explicitly verified**.
Missing or failed cleanup blocks remaining live cases; the remote session is
retained when cleanup failed for deliberate recovery using `--abort`.
The abort controller uses a separate lock, validates the recorded process's
kernel identity before signaling its isolated process group, then acquires the
normal session lock for cleanup. An old running process without a valid owner
record cannot be killed safely by name and requires operator inspection.
Aborted runs produce a `BLOCKED` report with a separate cleanup status. Batch exit is nonzero for FAIL, BLOCKED, or
NOT_IMPLEMENTED; an empty DUAL suite is NOT_IMPLEMENTED, not PASS.

The suite does not make planned or absent test objectives appear qualified.
Mock regressions only exercise tooling. Wire, control-plane, and routing
qualification requires live Cisco and OpenEIGRP packets and state evidence.

Source tests live only in `netlabs/test/`. Network fault injection lives in
`netlabs/common/network_fault.py`; use its `.fault.json` state files so failed
restoration is detectable before teardown. Earlier uncommitted copies in
`netlabs/tests/` and `netlabs/uut/tests/` should be removed when applying a
complete-file overlay. Generated reports remain local under `netlabs/results/`;
`make clean` deletes them without deleting scenario definitions or `.gitignore`.
