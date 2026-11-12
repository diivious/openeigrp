#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable native Linux interface tests (no synthetic production backend)."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "linux" / "code"
TEST = ROOT / "linux" / "test"


def run(*args, **kwargs):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, **kwargs)


class LinuxInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.uname().sysname != "Linux":
            raise unittest.SkipTest("Netlink is Linux-only")
        cls.tempdir = tempfile.TemporaryDirectory(prefix="eigrp-linux-intf-")
        cls.binary = Path(cls.tempdir.name) / "interface-harness"
        compiler = os.environ.get("CC", "cc")
        build = run(
            compiler, "-std=c11", "-D_GNU_SOURCE", "-Wall", "-Wextra",
            "-Werror", "-pthread", "-g", "-I", str(ROOT / "eigrp" / "code"),
            str(TEST / "interface_harness.c"), "-o", str(cls.binary),
        )
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tempdir"):
            cls.tempdir.cleanup()

    def test_fixture_and_kernel_snapshot(self):
        result = run(str(self.binary))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fixture Netlink lifecycle: PASS", result.stdout)
        self.assertIn("kernel Netlink recovery/restart: PASS", result.stdout)

    def test_real_netns_lifecycle(self):
        if not shutil.which("unshare") or not shutil.which("ip"):
            self.skipTest("requires util-linux unshare and iproute2")
        probe = run("unshare", "-Urn", "/bin/true")
        if probe.returncode:
            self.skipTest("user+network namespace unavailable: " + probe.stderr.strip())
        result = run("unshare", "-Urn", str(self.binary), "--namespace")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("network namespace: link/address change and recreation: PASS", result.stdout)

    def test_archive_boundary(self):
        if not shutil.which("nm"):
            self.skipTest("nm unavailable")
        build = run("make", "-C", str(CODE), "all")
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        archive = CODE / "libeigrp_linux_interface.a"
        symbols = run("nm", "--defined-only", str(archive))
        self.assertEqual(symbols.returncode, 0, symbols.stderr)
        self.assertIn("eigrp_sys_interface_walk", symbols.stdout)
        for forbidden in ("eigrp_uut_", "eigrp_unix_interface_", "eigrp_sys_packet_send", "eigrp_rib_route_install"):
            self.assertNotIn(forbidden, symbols.stdout)
        source = (CODE / "eigrp_linux_interface.c").read_text()
        for forbidden in ("eigrp_structs.h", "eigrp_topology.h", "eigrp_dual.h", "frr/", "bird/", "eigrp_uut_"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
