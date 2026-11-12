#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Native Linux Netlink source-route observation regression tests."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "linux" / "test"
CODE = ROOT / "linux" / "code"


def run(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


class LinuxRedistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.uname().sysname != "Linux":
            raise unittest.SkipTest("requires Linux RTNETLINK")
        cls.directory = tempfile.TemporaryDirectory(prefix="eigrp-redist-")
        cls.binary = Path(cls.directory.name) / "redist-harness"
        cmd = [os.environ.get("CC", "cc"), "-std=c11", "-D_GNU_SOURCE",
               "-Wall", "-Wextra", "-Werror", "-pthread", "-g",
               "-I", str(ROOT / "eigrp" / "code"),
               str(TEST / "redist_harness.c"), "-o", str(cls.binary)]
        result = run(*cmd)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_crafted_route_lifecycle(self):
        result = run(str(self.binary))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Crafted source, replacement, ambiguity, deletion, malformed, loop guard: PASS", result.stdout)

    def test_live_kernel_lifecycle(self):
        if not shutil.which("unshare") or not shutil.which("ip"):
            self.skipTest("requires unshare and iproute2")
        probe = run("unshare", "-Urn", "/bin/true")
        if probe.returncode:
            self.skipTest("user/network namespaces unavailable: " + probe.stderr.strip())
        result = run("unshare", "-Urn", str(self.binary), "--kernel")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Netlink kernel snapshot, add/replace/delete, subscriptions, loss and recovery: PASS", result.stdout)

    def test_linux_archive_contract(self):
        result = run("make", "-C", str(CODE), "libeigrp_linux_rib.a")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if shutil.which("nm"):
            symbols = run("nm", "--defined-only", str(CODE / "libeigrp_linux_rib.a"))
            self.assertEqual(symbols.returncode, 0, symbols.stderr)
            for symbol in ("eigrp_rib_redistribute_add", "eigrp_rib_redistribute_remove",
                           "eigrp_linux_redist_process", "eigrp_linux_redist_resync",
                           "eigrp_linux_redist_stop"):
                self.assertIn(symbol, symbols.stdout)

    def test_clang_format(self):
        formatter = shutil.which("clang-format")
        if not formatter:
            self.skipTest("clang-format unavailable")
        paths = [CODE / "eigrp_linux_redist.c", CODE / "eigrp_linux_redist.h",
                 TEST / "redist_harness.c"]
        result = run(formatter, "--dry-run", "--Werror", *map(str, paths))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
