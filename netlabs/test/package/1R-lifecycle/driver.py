#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""One Debian package lifecycle scenario using fresh Containerlab Linux nodes.

The same installed package and systemd test are run inside a disposable Debian
container. Container restart checks userspace service boot, not a kernel reboot.
No installed package tests are performed on the Netlab appliance itself.
"""

import argparse
import datetime
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[4]
TEST = ROOT / "linux/test/test_systemd_package.py"
VERIFY = ROOT / "linux/packaging/verify-deb.sh"
GUEST = "/var/tmp/openeigrp-netlab-package"
NODE = "clab-eigrp_pkg-uut"
STAGES = ("fresh-service", "stopped-upgrade", "running-lifecycle")
LOCAL_STAGES = ("fresh-service", "local-lifecycle")
IMAGE_RE = re.compile(r"openeigrp-package:[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


def command(argv, *, cwd=None, timeout=600, check=True, log=None):
    argv = [str(arg) for arg in argv]
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True,
                            timeout=timeout)
    if log is not None:
        with Path(log).open("a") as output:
            output.write("$ " + shlex.join(argv) + "\n")
            output.write(result.stdout + result.stderr + f"[exit {result.returncode}]\n")
    if check and result.returncode:
        raise RuntimeError(f"{shlex.join(argv)} failed ({result.returncode}):\n"
                           f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}")
    return result


def required(value, message):
    if not value:
        raise RuntimeError(message)


def docker_nodes():
    result = command(["docker", "ps", "-a", "--format", "{{.Names}}"])
    return set(result.stdout.splitlines())


def host_prerequisites(image, image_id):
    """Check only existing Docker and Netlab prerequisites on the appliance."""
    for name in ("netlab", "docker"):
        required(shutil.which(name), f"{name} is not installed on the Netlab appliance")
    command(["docker", "info"], timeout=30)
    result = command(["docker", "image", "inspect", "--format",
                      "{{.Id}} {{.Os}}/{{.Architecture}}", image], timeout=30)
    required(result.stdout.strip() == f"{image_id} linux/amd64",
             "package test image is missing, changed, or not linux/amd64")
    required(NODE not in docker_nodes(),
             f"refusing preexisting Containerlab node {NODE}; recover it first")


class Container:
    def __init__(self, topology, artifacts, old, new, image_id):
        self.image_id = image_id
        self.work = topology.parent
        self.artifacts = artifacts
        self.old = old
        self.new = new
        self.running = False
        self.log = artifacts / "netlab-operations.log"

    def exec(self, *args, timeout=120, check=True, log=None):
        return command(["docker", "exec", "--user", "0", NODE, *args],
                       cwd=self.work, timeout=timeout, check=check,
                       log=log or self.log)

    def start(self):
        required(not self.running, "container already running")
        required(NODE not in docker_nodes(), "refusing existing package test container")
        # Set before netlab up so cleanup executes even after partial startup.
        self.running = True
        command(["netlab", "up"], cwd=self.work, timeout=900, log=self.log)
        image = command(["docker", "inspect", "--format", "{{.Image}}", NODE],
                        log=self.log).stdout.strip()
        required(image == self.image_id, "Containerlab launched an unexpected Debian image")
        pidmode = command(["docker", "inspect", "--format",
                           "{{.HostConfig.PidMode}}", NODE], log=self.log).stdout.strip()
        required(pidmode != "host", "package test must not share the host PID namespace")
        identity = self.exec("/bin/sh", "-c",
                             "cat /etc/os-release; cat /proc/1/comm; dpkg --print-architecture")
        required("ID=debian" in identity.stdout and "systemd" in identity.stdout,
                 "refusing non-Debian or non-systemd container")
        arch = identity.stdout.splitlines()[-1].strip()
        required(arch in ("amd64", "arm64"), f"unsupported container architecture {arch}")
        for pkg in (self.old, self.new):
            if pkg is None:
                continue
            actual = command(["dpkg-deb", "-f", pkg, "Architecture"],
                             log=self.log).stdout.strip()
            required(actual == arch,
                     f"package architecture {actual} differs from container {arch}")
        self.exec("mkdir", "-p", GUEST)
        transfers = [(self.new, "new.deb"), (TEST, "test_systemd_package.py"),
                     (VERIFY, "verify-deb.sh")]
        if self.old is not None:
            transfers.append((self.old, "old.deb"))
        for path, name in transfers:
            command(["docker", "cp", path, f"{NODE}:{GUEST}/{name}"],
                    log=self.log, timeout=120)
        self.exec("chmod", "755", f"{GUEST}/verify-deb.sh")
        self.exec("touch", "/run/openeigrp-netlab-package-test")
        # Check preexisting configuration before installing any dependencies.
        self.test("preflight")
        self.exec("apt-get", "update", timeout=300)

    def test(self, phase):
        required(phase in (*STAGES, *LOCAL_STAGES, "service-after-restart", "preflight"),
                 "invalid package test phase")
        args = ["docker", "exec", "--user", "0", "-e",
                "OPENEIGRP_PACKAGE_SYSTEMD_TEST=1", "-e",
                "OPENEIGRP_PACKAGE_CONTAINER_DISPOSABLE=1", "-e",
                f"OPENEIGRP_PACKAGE_NEW={GUEST}/new.deb", "-e",
                f"OPENEIGRP_PACKAGE_VERIFY={GUEST}/verify-deb.sh"]
        if self.old is not None:
            args += ["-e", f"OPENEIGRP_PACKAGE_OLD={GUEST}/old.deb"]
        args += [NODE, "/usr/bin/python3", f"{GUEST}/test_systemd_package.py",
                 "--phase", phase]
        log = self.artifacts / f"{phase}.log"
        result = command(args, cwd=self.work, timeout=420, check=False, log=log)
        if result.returncode:
            # Netlab teardown destroys the guest journal. Capture service
            # diagnostics before the container is removed, without changing
            # the unit or treating the diagnostic command as the test result.
            diagnostics = (
                ("systemctl", "status", "openeigrpd.service", "--no-pager", "--full"),
                ("systemctl", "show", "openeigrpd.service",
                 "--property=Result,ExecMainCode,ExecMainStatus,ActiveState,SubState"),
                ("journalctl", "-b", "-u", "openeigrpd.service", "--no-pager", "-n", "100"),
            )
            for argv in diagnostics:
                try:
                    self.exec(*argv, timeout=30, check=False, log=log)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    with log.open("a") as output:
                        output.write(f"diagnostics unavailable: {type(exc).__name__}\n")
        required(result.returncode == 0, f"guest {phase} failed; see {phase}.log")

    def restart(self):
        before = self.exec("/bin/cat", "/proc/1/stat").stdout.split()[21]
        command(["docker", "restart", "--time", "30", NODE],
                cwd=self.work, log=self.log, timeout=120)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            time.sleep(2)
            result = self.exec("/bin/cat", "/proc/1/stat", check=False, timeout=15)
            if result.returncode != 0:
                continue
            fields = result.stdout.split()
            if len(fields) > 21 and fields[21] != before:
                ready = self.exec("systemctl", "show", "--property=Version",
                                  check=False, timeout=15)
                if ready.returncode == 0:
                    self.exec("touch", "/run/openeigrp-netlab-package-test")
                    return
        raise RuntimeError("systemd container restart or recovery timed out")

    def stop(self):
        if not self.running:
            return
        # A failed deployment can leave a container or a partial lab. Always
        # call Netlab teardown, then verify the exact owned node is absent.
        result = command(["netlab", "down"], cwd=self.work, check=False,
                         timeout=600, log=self.log)
        remaining = NODE in docker_nodes()
        required(not remaining,
                 f"package test container {NODE} survived Netlab teardown")
        required(result.returncode == 0,
                 "Netlab teardown returned an error; cleanup cannot be verified")
        self.running = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--package-old", type=Path)
    parser.add_argument("--package-new", type=Path, required=True)
    parser.add_argument("--package-image", required=True)
    parser.add_argument("--package-image-id", required=True)
    args = parser.parse_args()
    required(IMAGE_RE.fullmatch(args.package_image), "invalid Debian test image tag")
    required(ID_RE.fullmatch(args.package_image_id), "invalid Debian test image ID")
    spec = yaml.safe_load(args.scenario.read_text())
    required(spec.get("name") == "1R-lifecycle" and spec.get("group") == "package" and
             spec.get("stages") == list(STAGES) and
             spec.get("no_release_stages") == list(LOCAL_STAGES),
             "unexpected package scenario contract")
    topology = yaml.safe_load(args.topology.read_text())
    required(topology.get("name") == "eigrp_pkg" and
             topology.get("provider") == "clab" and
             list(topology.get("nodes", {})) == ["uut"] and
             topology["nodes"]["uut"].get("device") == "linux",
             "package qualification requires exactly one Containerlab Linux node")
    required(topology["nodes"]["uut"].get("clab", {}).get("cmd") ==
             "/lib/systemd/systemd", "package node must start systemd as PID 1")
    args.artifacts.mkdir(parents=True, exist_ok=True)
    new = args.package_new.resolve(strict=True)
    old = args.package_old.resolve(strict=True) if args.package_old else None
    b = command(["dpkg-deb", "-f", new, "Version"]).stdout.strip()
    required(command(["dpkg-deb", "-f", new, "Package"]).stdout.strip() == "openeigrpd",
             "development artifact must be openeigrpd")
    a = None
    if old is not None:
        required(old != new, "two distinct versioned packages required")
        a = command(["dpkg-deb", "-f", old, "Version"]).stdout.strip()
        required(command(["dpkg", "--compare-versions", a, "lt", b],
                         check=False).returncode == 0, "new package must be newer")
        required(command(["dpkg-deb", "-f", old, "Package"]).stdout.strip() == "openeigrpd",
                 "published artifact must be openeigrpd")
    topology["nodes"]["uut"]["image"] = args.package_image
    args.topology.write_text(yaml.safe_dump(topology, sort_keys=False))
    report = {"schema": "openeigrp-package-netlab/v1", "scenario": spec["name"],
              "platform": "Containerlab Debian systemd", "versions": [a, b],
              "objectives": [], "upgrade": "QUALIFIED" if old else "SKIPPED",
              "kernel_reboot": "NOT_TESTED"}
    if old is None:
        report["objectives"].extend(
            {"name": stage, "status": "SKIPPED", "required": False,
             "reason": "no published native Debian release package"}
            for stage in ("stopped-upgrade", "running-lifecycle"))
        print("SKIPPED: stopped and running upgrades; no published Debian baseline", flush=True)
    try:
        host_prerequisites(args.package_image, args.package_image_id)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        report["status"] = "BLOCKED"
        report["objectives"].append({
            "name": "container-prerequisites", "status": "BLOCKED", "required": True,
            "error": str(exc)})
        report["timestamp_utc"] = datetime.datetime.now(
            datetime.timezone.utc).isoformat()
        (args.artifacts / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"BLOCKED: {exc}", flush=True)
        return 1
    guest = Container(args.topology, args.artifacts, old, new, args.package_image_id)
    failed = False
    try:
        for stage in (STAGES if old is not None else LOCAL_STAGES):
            item = {"name": stage, "status": "NOT_RUN", "required": True}
            report["objectives"].append(item)
            try:
                guest.start()
                guest.test(stage)
                if stage == "fresh-service":
                    guest.restart()
                    guest.test("service-after-restart")
                item["status"] = "PASS"
            except Exception as exc:
                item["status"] = "FAIL"
                item["error"] = str(exc)
                failed = True
                print(f"FAIL: {stage}: {exc}", flush=True)
            finally:
                try:
                    guest.stop()
                except Exception as exc:
                    failed = True
                    item["status"] = "FAIL"
                    item["cleanup_error"] = str(exc)
                    print(f"FAIL: container cleanup after {stage}: {exc}", flush=True)
            if failed:
                break
            print(f"PASS: {stage}", flush=True)
    finally:
        report["status"] = "FAIL" if failed else "PASS" if old is not None else "PARTIAL"
        report["timestamp_utc"] = datetime.datetime.now(
            datetime.timezone.utc).isoformat()
        (args.artifacts / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"FAIL: package Netlab preflight: {exc}", file=sys.stderr)
        sys.exit(1)
