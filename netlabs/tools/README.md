# Remote netlab execution (StormTrooper)

These commands are invoked from an OpenEIGRP checkout on macOS or Debian.
The remote runner uses `ssh` and `tar` (or `sshpass` without SSH keys).
Building the local development image and Debian artifacts requires Docker.
`--local` and Debian package tests require a local Docker daemon for artifact
verification and transfer. Published container tests use the appliance image.
The client does not require a local Netlab/Containerlab installation.

## Layout

All remote netlab source files live inside `netlabs/`: this README and the
entry points are under `netlabs/tools/`, the default configuration is
`netlabs/remote-lab.cfg`, and scenarios and instructional labs are under
`netlabs/uut/` and `netlabs/labs/`. The repository root `tools/` is not used.

## Authentication and configuration

Edit `netlabs/remote-lab.cfg` (a trusted Bash-sourced configuration file):

```sh
REMOTE_HOST=StormTrooper
REMOTE_USER=netlab
REMOTE_PASSWORD=netlab
REMOTE_SSH_PORT=22
```

The requested default password is included **only as a default setting**. The
provisioned `netlab` account currently has no password. To use password SSH,
set a password on StormTrooper and enable password authentication in its SSH
server, then install `sshpass` on the development machine. The scripts use
`SSHPASS` via the environment, not a password on the command line.

**SSH keys are preferred**: the client first tests noninteractive public-key
authentication, then falls back to `sshpass`. Config files containing real
secrets should not be shared or committed. Use `REMOTE_LAB_CONFIG=/path/to/private.cfg`
for a private override, and `chmod 600` for it.

## Interactive labs

```sh
netlabs/tools/remote-lab.sh --lab 101
netlabs/tools/remote-lab.sh --status
netlabs/tools/remote-lab.sh --connect
netlabs/tools/remote-lab.sh --stop
```

`--lab NNN` only accepts implemented labs with a
`netlabs/labs/NNN/topology.yml`. The `netlabs/README.md` curriculum is currently
**a planned catalog, not executable labs**, so no lab numbers are deployable in
this initial framework. A successful start retains the lab until `--stop`.
`--connect` prompts for a router and runs `netlab connect` interactively.

## OpenEIGRP image updates

The registry publishes immutable numbered releases (`0.3.0`, `0.4.0`, etc.).
A `latest` tag is **not** assumed to exist, and the test never relabels a
versioned image to a different version.

Run this from Cruella (or another configured client) when you want to refresh
StormTrooper's installed OpenEIGRP image:

```sh
netlabs/tools/remote-uut.sh --update
```

`--update` contacts GHCR, identifies the highest published `X.Y.Z` tag,
and pulls it on the appliance. It also pulls `debian:latest` (the moving
stable Debian tag) for package test prerequisites. Earlier installed images
are retained. It does not pull or change Cisco images. The appliance refuses an update if a lab or
UUT owns the shared session lock.

**By default, UUT runs select the highest versioned published
OpenEIGRP image installed on the appliance.** `--tag X.Y` pins GHCR `X.Y.0`
exactly; `--local` instead uses the last completed `make all` build.
Normal container test runs never query
the registry. The selected image is inserted into that run's temporary netlab
`topology.yml`; the checked-in topology is unchanged. `image.json` records the
selected tag, local Docker image ID and available repo digests, and the test
report records the OpenEIGRP image reference. The image therefore remains
identifiable even when newer releases are installed later.

Unversioned aliases such as `current` and architecture-only suffixes are not
eligible. If the appliance has no installed numbered release, the test fails
with an image-selection error instead of silently deploying another image.
Network or registry failures during `--update` are reported as failures and
do not remove previously installed images.

## Automated UUT

```sh
netlabs/tools/remote-uut.sh --list
netlabs/tools/remote-uut.sh --scenario 2R-neighbor
make all
netlabs/tools/remote-uut.sh --all
netlabs/tools/remote-uut.sh --container
netlabs/tools/remote-uut.sh --package
netlabs/tools/remote-uut.sh --local --all
netlabs/tools/remote-uut.sh --local --tag 0.5.1 --package
netlabs/tools/remote-uut.sh --tag 0.5 --container
netlabs/tools/remote-uut.sh --abort
```

`--all` runs the original container suite plus the 1R Debian package
lifecycle. `--container` selects the original container suite alone;
`--package` selects the native Debian lifecycle alone. By default these modes
select published artifacts. Published package runs require an official .deb for
the selected release; they report BLOCKED rather than using a development package when
none is published. `--local` selects the last completed local build, and
`--local --tag X.Y.Z` must match that build's numeric version exactly.
`--tag X.Y` without `--local` pins the matching published release (GHCR X.Y.0).
The two tag formats cannot be interchanged or matched by prefix.
Build the development
FRR Docker image, Debian systemd test image and native AMD64 `.deb` with
`make all`. The package manifest
in `dist/netlabs-amd64.json` identifies the package and published upgrade
baseline, if any. Tests refuse stale or modified package artifacts.

`make all` builds `openeigrp-package:netlab` from the repository Dockerfile
using `debian:latest` and `linux/amd64`, and records its exact image ID in
the build manifest. The client checks that identity and transfers the image
to StormTrooper using the existing Docker import path. This image contains Debian and systemd,
**not OpenEIGRP**. The client separately transfers the developer's already
built `.deb`, which is installed using APT inside the fresh Containerlab node.
`remote-uut.sh` never builds or pulls local images or packages; it fails
immediately when artifacts from `make all` are missing or have changed.
No image is built on StormTrooper, and no Vagrant or libvirt provider is needed.

With `--local`, if no official Debian baseline has been published, the
scenario runs installation, service, removal and purge, and reports upgrade
stages as SKIPPED rather than PASS. In published mode, a missing published
Debian package marks the package lifecycle BLOCKED; independent container
scenarios still run under `--all`. No local substitution is permitted. The exact Debian base and test image IDs are checked at execution.

A `docker restart` checks PID 1 restart and enabled-service persistence, but
is not a kernel reboot. The report records kernel reboot as NOT_TESTED.
Docker and Containerlab on StormTrooper remain the only runtime prerequisites.
A missing image, inaccessible Docker daemon, or unsupported systemd container
is not counted as a successful qualification.

The neighbor UUT tests actual bidirectional IPv4 and IPv6 EIGRP adjacency
between the selected OpenEIGRP-FRR release and CSR1000v 17.03.06. It uses
native netlab provisioning and the shared `netlabs/config/eigrp-named.j2`
template. At least one versioned OpenEIGRP image and the pinned Cisco image
must exist on StormTrooper. Use `--update` to fetch the latest published
OpenEIGRP release. The UUT produces JSON PASS/FAIL results and CLI/log
artifacts in `logs/netlab/` on the development host.

The internal-route UUT separately checks EIGRP-internal IPv4/IPv6 routes
learned and installed in **both** routers. See `netlabs/uut/COMPATIBILITY.md`
for the status of additional Cisco interoperability checks.

Live compatibility scenarios have their own YAML schemas.
It does not replace portable YAML UUT tests run by `make uut`; that harness has
internal and broker assertions unavailable to physical VM peers.

## Exclusivity, recovery, and security

StormTrooper alone owns the session marker and an exclusive `flock` lock under
`/home/netlab/.remote-lab/`. Both commands are serialized across clients.
A running lab persists after SSH disconnect. A live UUT holds the lock through
validation, evidence collection, and teardown. Unrecoverable cleanup errors
retain the session marker instead of reporting success or permitting a second
run. `netlabs/tools/remote-uut.sh --abort` installs the current recovery handler,
then requests cancellation of a running UUT or regression job. New remote test
jobs have a dedicated process group and record the worker PID plus its Linux
start time. The recovery handler signals only a verified owner, waits for the
exclusive lock, restores fault/capture state, then tears down the netlab
resources. Partial diagnostics are preserved in a new report. A failed
restoration retains the original workspace and state for further recovery.
An older in-flight job that has no registered worker cannot be force-killed
safely; `--abort` rejects it if the lock is still held. Inspect that specific
worker on the appliance before terminating it, then retry `--abort` to recover
the stale session. Do not delete the lock file or kill all netlab containers.
Interactive persistent labs continue to use `remote-lab.sh --stop`.

The staging stream uses POSIX USTAR archives instead of PAX metadata; this
avoids GNU tar warnings about macOS `SCHILY.fflags` extended headers.

Uploaded execution files go to `/home/netlab-data/runs/`; results remain in
`/home/netlab-data/results/`. The shell runner is installed at
`/home/netlab/.remote-lab/runner.sh` on first use.

Docker/Containerlab groups on StormTrooper are effectively root-equivalent.
Grant SSH access only to trusted development machines. Lab inputs and remote
commands are repository-controlled, not downloaded from mutable upstream URLs.

## Test and qualification status

```sh
python3 -m unittest discover -s netlabs/test/rtp -p 'test_neighbor.py' -v
netlabs/test/infra/test_remote_runtime.sh
```

The tests exercise Python neighbor validation and Bash session transitions with
controlled substitutes for remote tools. They cannot prove Cisco IOS XE boots,
OpenEIGRP interoperates, or cleanup works on StormTrooper. Those require an
actual run of `remote-uut.sh` on the provisioned hardware.

## Moving from earlier remote-lab overlays

A complete-file overlay does not delete files in the receiving checkout. If the
first remote-lab overlay has already been applied, the following are obsolete
**after verifying the replacement files**:

```text
tools/remote/live-neighbor.py
eigrp/test/uut/tests/test_remote_neighbor.py
eigrp/test/uut/tests/test_remote_runtime.sh
netlabs/uut/neighbor-two-router/
```

Remove only these earlier remote-lab additions, after checking for local edits.
Leave the existing portable UUT framework and its other tests untouched.

If the earlier layout overlay was applied, after checking for local edits
remove its obsolete `tools/remote-lab.sh`, `tools/remote-uut.sh`,
`tools/remote-lab.cfg`, and `tools/remote/` files. Do not remove unrelated
repository `tools/` scripts.

## Native netlab integration

For live UUTs, netlab owns topology, interface addressing, Docker/KVM startup,
and the initial configuration of both platform types.
The shared `netlabs/config/eigrp-named.j2` is a named-mode EIGRP configuration
rendered once per router; its output is delivered by netlab's platform-specific
FRR/IOS XE transport. The UUT driver performs **read-only** CLI validation.

`remote-uut.sh` stages `netlabs/config/` along with the selected scenario.
Remote cleanup invokes `netlab down`, while interactive labs retain the
existing explicit `remote-lab.sh --stop` lifecycle.

## Live execution progress

The UUT client prints remote progress immediately (staging, deployment and
configuration, readiness, IPv4 validation, IPv6 validation, and teardown). Both
SSH and runner output are streamed through `tee`; the remote report still contains
`runner.log` and diagnostics. Final PASS/FAIL is based on the original runner
exit code, not `tee` or successful cleanup.

Native `netlab up` performs deployment **and** initial configuration as one
operation; progress reports this combined phase rather than implying there is a
separately timed configuration step. Details from netlab are saved to `deploy.log`.
