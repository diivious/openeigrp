#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Production Linux Netlink RIB unit and network namespace tests."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "linux" / "code"
TEST = ROOT / "linux" / "test"


def run(*args):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True)


class LinuxRibTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.uname().sysname != "Linux":
            raise unittest.SkipTest("requires Linux RTNETLINK")
        cls.tmp = tempfile.TemporaryDirectory(prefix="eigrp-linux-rib-")
        cls.binary = Path(cls.tmp.name) / "rib-harness"
        build = run(
            os.environ.get("CC", "cc"), "-std=c11", "-D_GNU_SOURCE",
            "-Wall", "-Wextra", "-Werror", "-pthread", "-g",
            "-I", str(ROOT / "eigrp" / "code"),
            str(TEST / "rib_harness.c"), "-o", str(cls.binary),
        )
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_encoding_validation(self):
        result = run(str(self.binary))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Netlink route encoding, validation, malformed dump: PASS", result.stdout)

    def test_kernel_route_lifecycle(self):
        if not shutil.which("unshare") or not shutil.which("ip"):
            self.skipTest("requires unshare and iproute2")
        probe = run("unshare", "-Urn", "/bin/true")
        if probe.returncode:
            self.skipTest("user/network namespaces unavailable: " + probe.stderr)
        result = run("unshare", "-Urn", str(self.binary), "--kernel")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in (
            "IPv4 install, idempotence, next-hop replacement, reconcile, withdraw: PASS",
            "Foreign route conflict and external replacement ownership: PASS",
            "Kernel rejection, retry and Netlink reconnect: PASS",
            "IPv6 route lifecycle and non-default VRF boundary: PASS",
            "Crash restart fail-closed recovery, instance cleanup and shutdown: PASS",
        ):
            self.assertIn(expected, result.stdout)

    def test_archive_contract(self):
        build = run("make", "-C", str(CODE), "libeigrp_linux_rib.a")
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        if shutil.which("nm"):
            symbols = run("nm", "--defined-only", str(CODE / "libeigrp_linux_rib.a"))
            self.assertEqual(symbols.returncode, 0, symbols.stderr)
            for symbol in (
                "eigrp_rib_route_add", "eigrp_rib_route_del", "eigrp_rib_instance_delete",
                "eigrp_rib_init", "eigrp_rib_finish", "eigrp_linux_rib_reconcile",
            ):
                self.assertIn(symbol, symbols.stdout)
            for forbidden in ("eigrp_uut_", "eigrp_fsm_", "eigrp_topology_"):
                self.assertNotIn(forbidden, symbols.stdout)

    def test_clang_format(self):
        formatter = shutil.which("clang-format")
        if not formatter:
            self.skipTest("clang-format unavailable")
        sources = [CODE / "eigrp_linux_rib.c", CODE / "eigrp_linux_rib.h",
                   TEST / "rib_harness.c"]
        checked = run(formatter, "--dry-run", "--Werror", *map(str, sources))
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
