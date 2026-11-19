# Live Debian package qualification with Netlab

This gate tests the actual **distributed native Debian package**, not an
in-tree daemon, fake build fixture, non-systemd image, or another routing
implementation. It runs inside a disposable Containerlab Debian node with systemd as
PID 1. It installs packages into `/usr`, `/etc` and dpkg, creates service
identities, and starts native EIGRP. **Never run this on a workstation or
preexisting installation.** Package-owned accounts/groups intentionally remain
after purge to avoid reassigning a security identity.

Use the single Netlab package scenario described in
[`netlabs/test/package/1R-lifecycle/README.md`](../../netlabs/test/package/1R-lifecycle/README.md).
For automated upgrade testing, use the official last-published GitHub Release
AMD64 `.deb` as version A and the current checkout's native development `.deb`
as version B. The `docker-amd.sh` wrapper always builds B. When A exists it
checks published SHA256SUMS, verifies both packages, and rejects versions that
cannot be upgraded in Debian package ordering. Before a native release exists,
install/service/reinstall/purge tests run and upgrade phases are SKIPPED.
APT installs the local files, resolves declared
runtime dependencies, and performs the upgrades. No public APT repository is
assumed. The guest contains only selected .debs and test scripts, not the
repository source. Package contents are checked with
`linux/packaging/verify-deb.sh` before any installation.

```sh
# Build the development FRR Docker image and .deb locally first.
netlabs/tools/remote-uut.sh --image diivious-test --package
# Or combine it with all Docker-based scenarios:
netlabs/tools/remote-uut.sh --image diivious-test --all
```

The build creates `dist/netlabs-amd64.json` so the test runner uses the
verified package selection without guessing filenames. Rebuild after publishing a
new release to refresh the selected GitHub baseline. Direct runs with explicit
`--package-old` and `--package-new` remain available for diagnostics. The driver boots
and destroys a fresh Debian systemd container three times (with an upgrade
baseline) or twice (without one), **within one scenario**. It restarts the
container to verify systemd userspace startup and enabled-service persistence.
A container restart does not reboot the host kernel; kernel reboot coverage is
explicitly NOT_TESTED. The shared guest script
`linux/test/test_systemd_package.py` tests service account, permissions,
capabilities, hardening, socket, management client, upgrade policy, stopped
state, config conffile retention, reinstall and purge, bad config diagnostics,
and package failure reporting. The package's installed management CLI is
`eigrp`, not `oectl`.

Inspect `logs/netlab/` for stage logs and structured outcomes. Failure to prepare or transfer the local Debian systemd image,
Docker/Containerlab, APT dependency resolution, or required runtime capabilities is **BLOCKED/FAIL**,
never a qualified platform PASS. Repeat independently on AMD64 and ARM64, and
on each claimed distribution. The Debian `latest` AMD64 container does not
qualify other platforms or full-machine reboot behavior. This gate makes no Cisco interoperability or route-convergence
claim.
