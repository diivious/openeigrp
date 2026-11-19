#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Regression tests for Containerlab Debian package lifecycle orchestration."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CASE = Path(__file__).parent / "1R-lifecycle"
SPEC = importlib.util.spec_from_file_location("package_netlab_driver", CASE / "driver.py")
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)
IMAGE = "openeigrp-package:test"
IMAGE_ID = "sha256:" + "a" * 64


class PackageDriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openeigrp-package-driver-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.old, self.new = self.work / "a.deb", self.work / "b.deb"
        self.old.write_bytes(b"fixture only, never installed")
        self.new.write_bytes(b"fixture only, never installed")
        self.artifacts = self.work / "artifacts"
        self.topology = self.work / "topology.yml"
        self.topology.write_text((CASE / "topology.yml").read_text())
        self.calls = []

    def args(self, no_release=False):
        args = ["driver.py", "--scenario", str(CASE / "scenario.yaml"),
                "--topology", str(self.topology), "--artifacts", str(self.artifacts),
                "--package-new", str(self.new), "--package-image", IMAGE,
                "--package-image-id", IMAGE_ID]
        if not no_release:
            args += ["--package-old", str(self.old)]
        return args

    def execute(self, fail_phase=None, invalid_version=False, no_release=False):
        calls = self.calls

        def command(argv, **kwargs):
            if argv[:3] == ["dpkg-deb", "-f", self.old]:
                return subprocess.CompletedProcess(argv, 0,
                    "0.5.9-diivious.dev\n" if argv[3] == "Version" else "openeigrpd\n", "")
            if argv[:3] == ["dpkg-deb", "-f", self.new]:
                return subprocess.CompletedProcess(argv, 0,
                    "0.5.10-diivious.dev\n" if argv[3] == "Version" else "openeigrpd\n", "")
            if argv[:2] == ["dpkg", "--compare-versions"]:
                return subprocess.CompletedProcess(argv, 1 if invalid_version else 0, "", "")
            raise AssertionError(f"unexpected subprocess command {argv}")

        class FakeContainer:
            def __init__(self, *args):
                pass

            def start(self):
                calls.append("up")

            def test(self, phase):
                calls.append(phase)
                if phase == fail_phase:
                    raise RuntimeError("guest failure")

            def restart(self):
                calls.append("restart")

            def stop(self):
                calls.append("down")

        with patch.object(sys, "argv", self.args(no_release)), \
             patch.object(DRIVER, "command", command), \
             patch.object(DRIVER, "host_prerequisites"), \
             patch.object(DRIVER, "Container", FakeContainer), \
             contextlib.redirect_stdout(io.StringIO()):
            if invalid_version:
                with self.assertRaisesRegex(RuntimeError, "new package must be newer"):
                    DRIVER.main()
                return None
            return DRIVER.main()

    def test_all_phases_recreate_container_and_restart(self):
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.calls, ["up", "fresh-service", "restart",
                                      "service-after-restart", "down",
                                      "up", "stopped-upgrade", "down",
                                      "up", "running-lifecycle", "down"])
        result = json.loads((self.artifacts / "report.json").read_text())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["kernel_reboot"], "NOT_TESTED")
        self.assertEqual([case["status"] for case in result["objectives"]], ["PASS"] * 3)

    def test_no_release_reports_partial(self):
        self.assertEqual(self.execute(no_release=True), 0)
        self.assertEqual(self.calls, ["up", "fresh-service", "restart",
                                      "service-after-restart", "down",
                                      "up", "local-lifecycle", "down"])
        report = json.loads((self.artifacts / "report.json").read_text())
        self.assertEqual(report["status"], "PARTIAL")
        self.assertEqual(report["upgrade"], "SKIPPED")
        self.assertEqual(sorted((x["name"], x["status"]) for x in report["objectives"]),
                         [("fresh-service", "PASS"), ("local-lifecycle", "PASS"),
                          ("running-lifecycle", "SKIPPED"),
                          ("stopped-upgrade", "SKIPPED")])

    def test_failure_stops_and_reports(self):
        self.assertEqual(self.execute(fail_phase="stopped-upgrade"), 1)
        self.assertEqual(self.calls.count("up"), 2)
        self.assertEqual(self.calls.count("down"), 2)
        self.assertNotIn("running-lifecycle", self.calls)

    def test_no_release_failure_stops(self):
        self.assertEqual(self.execute(fail_phase="local-lifecycle", no_release=True), 1)
        self.assertEqual(self.calls.count("down"), 2)
        self.assertEqual(json.loads((self.artifacts / "report.json").read_text())["status"], "FAIL")

    def test_version_ordering_before_deployment(self):
        self.execute(invalid_version=True)
        self.assertEqual(self.calls, [])

    def test_missing_docker_reports_blocked(self):
        def command(argv, **kwargs):
            if argv[:3] == ["dpkg-deb", "-f", self.new]:
                return subprocess.CompletedProcess(argv, 0,
                    "0.5.10-diivious.dev\n" if argv[3] == "Version" else "openeigrpd\n", "")
            raise AssertionError(argv)

        with patch.object(sys, "argv", self.args(no_release=True)), \
             patch.object(DRIVER, "command", command), \
             patch.object(DRIVER, "host_prerequisites",
                          side_effect=RuntimeError("Docker missing")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(DRIVER.main(), 1)
        result = json.loads((self.artifacts / "report.json").read_text())
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["objectives"][-1]["name"], "container-prerequisites")
        self.assertIn("BLOCKED: Docker missing", output.getvalue())

    def test_prerequisite_rejects_unavailable_image(self):
        def command(argv, **kwargs):
            if argv[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(argv, 0, "sha256:" + "f" * 64 + " linux/amd64\n", "")
            raise AssertionError(argv)

        with patch.object(DRIVER.shutil, "which", return_value="/usr/bin/netlab"), \
             patch.object(DRIVER, "command", command):
            with self.assertRaisesRegex(RuntimeError, "missing, changed"):
                DRIVER.host_prerequisites(IMAGE, IMAGE_ID)

    def test_prerequisite_rejects_existing_node(self):
        def command(argv, **kwargs):
            output = f"{IMAGE_ID} linux/amd64\n" if argv[:3] == ["docker", "image", "inspect"] else ""
            return subprocess.CompletedProcess(argv, 0, output, "")
        with patch.object(DRIVER.shutil, "which", return_value="/usr/bin/docker"), \
             patch.object(DRIVER, "command", command), \
             patch.object(DRIVER, "docker_nodes", return_value={DRIVER.NODE}):
            with self.assertRaisesRegex(RuntimeError, "preexisting Containerlab node"):
                DRIVER.host_prerequisites(IMAGE, IMAGE_ID)

    def test_partial_netlab_up_is_cleaned(self):
        self.artifacts.mkdir()
        node = DRIVER.Container(self.topology, self.artifacts, self.old, self.new, IMAGE_ID)
        calls = []
        def command(argv, **kwargs):
            calls.append(argv)
            if argv == ["netlab", "up"]:
                raise RuntimeError("partial Containerlab deployment")
            return subprocess.CompletedProcess(argv, 0, "", "")
        with patch.object(DRIVER, "command", command), \
             patch.object(DRIVER, "docker_nodes", return_value=set()):
            with self.assertRaisesRegex(RuntimeError, "partial Containerlab deployment"):
                node.start()
            self.assertTrue(node.running)
            node.stop()
        self.assertIn(["netlab", "down"], calls)
        self.assertFalse(node.running)

    def test_preflight_before_apt_and_container_copy(self):
        self.artifacts.mkdir()
        node = DRIVER.Container(self.topology, self.artifacts, self.old, self.new, IMAGE_ID)
        events = []
        def command(argv, **kwargs):
            events.append(argv)
            if argv[:3] == ["docker", "inspect", "--format"]:
                return subprocess.CompletedProcess(argv, 0, IMAGE_ID + "\n" if
                                                   argv[3] == "{{.Image}}" else "\n", "")
            if argv[:3] == ["docker", "exec", "--user"]:
                if "/bin/sh" in argv:
                    return subprocess.CompletedProcess(argv, 0, "ID=debian\nsystemd\namd64\n", "")
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[:3] == ["dpkg-deb", "-f", self.old] or argv[:3] == ["dpkg-deb", "-f", self.new]:
                return subprocess.CompletedProcess(argv, 0, "amd64\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        with patch.object(DRIVER, "command", command), \
             patch.object(DRIVER, "docker_nodes", return_value=set()), \
             patch.object(node, "test", lambda phase: events.append(["test", phase])):
            node.start()
        self.assertIn(["test", "preflight"], events)
        update = next(i for i, item in enumerate(events) if "apt-get" in item)
        self.assertLess(events.index(["test", "preflight"]), update)
        stage = "/var/tmp/openeigrp-netlab-package"
        self.assertIn(["docker", "exec", "--user", "0", DRIVER.NODE,
                       "mkdir", "-p", stage], events)
        copies = [item[-1] for item in events if item[:2] == ["docker", "cp"]]
        self.assertEqual(copies, [f"{DRIVER.NODE}:{stage}/{name}" for name in
                                  ("new.deb", "test_systemd_package.py",
                                   "verify-deb.sh", "old.deb")])

    def test_guest_failure_preserves_service_diagnostics(self):
        self.artifacts.mkdir()
        node = DRIVER.Container(self.topology, self.artifacts, None, self.new, IMAGE_ID)
        calls = []

        def command(argv, **kwargs):
            calls.append((argv, kwargs.get("log")))
            failed = "--phase" in argv
            return subprocess.CompletedProcess(argv, 1 if failed else 0, "", "")

        with patch.object(DRIVER, "command", command):
            with self.assertRaisesRegex(RuntimeError, "guest fresh-service failed"):
                node.test("fresh-service")
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(log == self.artifacts / "fresh-service.log"
                            for _, log in calls))
        self.assertIn("systemctl", calls[1][0])
        self.assertIn("status", calls[1][0])
        self.assertIn("systemctl", calls[2][0])
        self.assertIn("show", calls[2][0])
        self.assertIn("journalctl", calls[3][0])
        self.assertIn("--no-pager", calls[3][0])
        self.assertIn("-n", calls[3][0])

    def test_guest_diagnostics_timeout_does_not_mask_original_failure(self):
        self.artifacts.mkdir()
        node = DRIVER.Container(self.topology, self.artifacts, None, self.new, IMAGE_ID)
        calls = []

        def command(argv, **kwargs):
            calls.append(argv)
            if "--phase" in argv:
                return subprocess.CompletedProcess(argv, 1, "", "failed")
            raise subprocess.TimeoutExpired(argv, 30)

        with patch.object(DRIVER, "command", command):
            with self.assertRaisesRegex(RuntimeError, "guest fresh-service failed"):
                node.test("fresh-service")
        self.assertEqual(len(calls), 4)
        self.assertIn("diagnostics unavailable", (self.artifacts / "fresh-service.log").read_text())

    def test_rejects_old_vm_topology_and_invalid_name(self):
        self.topology.write_text("name: eigrp-package-lifecycle\nprovider: libvirt\nnodes:\n  uut:\n    device: linux\n")
        with patch.object(sys, "argv", self.args(no_release=True)), \
             patch.object(DRIVER, "command", return_value=subprocess.CompletedProcess([], 0, "0.5\n", "")):
            with self.assertRaisesRegex(RuntimeError, "exactly one Containerlab"):
                DRIVER.main()


if __name__ == "__main__":
    unittest.main(verbosity=2)
