# Live interoperability tests and regression groups

Copyright (C) 2026 Donnie V. Savage

Use **one client entry point** from the repository root:

```sh
# Build the development FRR container and .deb locally first.
netlabs/tools/remote-uut.sh --all
netlabs/tools/remote-uut.sh --container
netlabs/tools/remote-uut.sh --package
netlabs/tools/remote-uut.sh --local --all
netlabs/tools/remote-uut.sh --local --tag 0.5.1 --package
netlabs/tools/remote-uut.sh --tag 0.5 --container
netlabs/tools/remote-uut.sh --rtp
netlabs/tools/remote-uut.sh --dual
netlabs/tools/remote-uut.sh --scenario 1R-lifecycle
netlabs/tools/remote-uut.sh --list
netlabs/tools/remote-uut.sh --update
netlabs/tools/remote-uut.sh --abort
```

The developer's normal build produces the AMD64 FRR development image and
native Debian `.deb`. The package artifact manifest in `dist/netlabs-amd64.json`
identifies those local artifacts and the last published official Debian upgrade
baseline if available. Test runs never rebuild OpenEIGRP on StormTrooper.

The package test environment is prepared locally from `debian:latest` with
systemd as PID 1, then transferred as a Docker image to the appliance. The
real locally built `.deb` is transferred independently and installed there.

- `--all`: run every container and Debian package regression and live scenario.
- `--container`: the original container suite (infra/RTP/DUAL and 2R-6R cases).
- `--package`: package regressions and the one disposable Debian container lifecycle.
- `--rtp` and `--dual`: retain their original protocol-specific selections.
- `--scenario ID`: run one live scenario without running regressions.
- No selector: use the latest published GHCR OpenEIGRP release installed on the
  appliance, and official release packages where applicable.
- `--local`: use the latest completed `make all` development build and its
  verified AMD64 images and packages, without requiring a developer image tag.
- `--tag X.Y.Z` with `--local`: require that exact local build number.
- `--tag X.Y` without `--local`: require the exact published release (GHCR
  `X.Y.0`); do not fall back to a newer version.
- `--list`: list runnable scenarios without contacting the test appliance.
- `--update`: refresh published FRR and `debian:latest` base images on the appliance.
- `--abort`: safely abort an interrupted test, recovering faults and teardown.

Development Debian mode requires `dist/netlabs-amd64.json` and its referenced
`.deb` files. Published mode downloads the selected official release .deb and
verifies its SHA256SUMS; if absent it reports BLOCKED rather than testing a development
build. The prebuilt Debian systemd test environment is required in either mode.
A missing or outdated local build fails before the container starts; build
the local FRR image, native `.deb`, and artifact manifest using your normal
build workflow. `--container` does not require package
artifacts. `--package` does not start an FRR Docker container. `--all` runs
independent scenarios sequentially and refuses to proceed after unverified
teardown. Each scenario's test results are separately recorded.

The `netlabs/test/` directory contains four independent groups:

```text
netlabs/test/
  infra/       # Remote runner, dispatch, network fault regressions
  rtp/                  # RTP wire/packet-capture regressions, live RTP cases
  dual/                 # DUAL regressions, live DUAL cases
  package/              # 1R Debian package systemd container lifecycle (no peers)
```

Place each new live case under the applicable protocol group:

```text
netlabs/test/rtp/3R-selective-retry/
  driver.py              # Executable Python driver, --scenario/--topology/--artifacts
  scenario.yaml          # Assertions and test inputs
  topology.yml           # Netlab topology, including OE/CE roles
  README.md              # Scope, setup, actions, evidence, expected results
```

Use `2R-` through `6R-` for protocol cases and the dedicated `1R-lifecycle`
ID for native package qualification. A group
comes from its **directory**, not a guessed keyword in the ID. Each complete
case requires `driver.py`, `scenario.yaml`, and `topology.yml`; incomplete
cases are not discovered. Duplicate IDs across groups or the transitional
`netlabs/uut/` directory are rejected to avoid ambiguous `--scenario` runs.

Older `netlabs/uut/2R-neighbor` remains supported without moving its driver.
Legacy cases declare `group: rtp` or `group: dual` in `scenario.yaml` for
selection. The existing `2R-neighbor` defaults to RTP for older checkouts.
The `2R-internal.py` driver has no complete fixture; it is **not runnable**
and must not be listed or counted as qualified. New protocol cases belong under
`netlabs/test/{rtp,dual}/`, not the legacy `netlabs/uut/` tree.
The 1R package qualification is described in
[`package/1R-lifecycle/README.md`](package/1R-lifecycle/README.md).

`--all` stages Infra/RTP/DUAL and package regressions on the Linux appliance;
`--container` excludes the Debian package suite. Package regressions run
with `--package` and `--all`. `--rtp` and `--dual` include infra safety gates.
The RTP group includes the existing neighbor driver's unit tests.
The provisioned appliance Python environment supplies PyYAML and Jinja2;
Linux provides `/proc`, `flock`, and GNU `timeout`. The macOS client needs only
its existing SSH/tar transport and system Python 3 standard library for batch
summary reporting, not local PyYAML/Jinja2 or Linux tools. Shell tests named
`test_*.sh` and Python `test_*.py` are discovered in their group directories.
Use descriptive permanent filenames, not milestones or project-phase names.

Each remote scenario saves its ordinary report and structured `result.json`.
Batch runs add `logs/netlab/batch-<selection>-<date>-<suffix>.json`,
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
complete-file overlay. Generated reports remain local under `logs/netlab/`;
`make clean` and `make distclean` do not touch personal Netlab results. Remove them explicitly with `netlabs/tools/remote-uut.sh --purge` when appropriate.
