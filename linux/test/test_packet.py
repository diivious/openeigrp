#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable native Linux IPv4 packet transport regression tests."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "linux" / "code"
TEST = ROOT / "linux" / "test"


def run(*argv):
    return subprocess.run(argv, cwd=ROOT, text=True, capture_output=True,
                          check=False)


class LinuxPacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.uname().sysname != "Linux":
            raise unittest.SkipTest("requires Linux raw IP socket interfaces")
        cls.tempdir = tempfile.TemporaryDirectory(prefix="eigrp-linux-packet-")
        cls.binaries = {}
        for name in ("packet_harness", "packet_privilege"):
            binary = Path(cls.tempdir.name) / name
            compiled = run(
                os.environ.get("CC", "cc"), "-std=c11", "-D_GNU_SOURCE",
                "-Wall", "-Wextra", "-Werror", "-pthread", "-g",
                "-I", str(ROOT / "eigrp" / "code"),
                str(TEST / (name + ".c")), "-o", str(binary),
            )
            if compiled.returncode:
                raise AssertionError(compiled.stdout + compiled.stderr)
            cls.binaries[name] = binary

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tempdir"):
            cls.tempdir.cleanup()

    def test_crafted_packet_and_socket_lifecycle(self):
        executed = run(str(self.binaries["packet_harness"]))
        self.assertEqual(executed.returncode, 0,
                         executed.stdout + executed.stderr)
        for required in (
            "IPv4 raw socket, privileges, multicast, cleanup: PASS",
            "IPv4 multicast/unicast TX and send failures: PASS",
            "Crafted IPv4 framing, metadata, packet loss/recovery: PASS",
            "Repeated shutdown and recovery: PASS",
        ):
            self.assertIn(required, executed.stdout)

    def test_real_kernel_privilege_boundary(self):
        executed = run(str(self.binaries["packet_privilege"]))
        self.assertEqual(executed.returncode, 0,
                         executed.stdout + executed.stderr)
        self.assertIn("real raw socket", executed.stdout)

    def test_archive_symbols(self):
        if not shutil.which("nm"):
            self.skipTest("nm unavailable")
        built = run("make", "-C", str(CODE), "libeigrp_linux_packet.a")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        symbols = run("nm", "--defined-only", str(CODE / "libeigrp_linux_packet.a"))
        self.assertEqual(symbols.returncode, 0, symbols.stderr)
        for required in (
            "eigrp_sys_socket_open", "eigrp_sys_socket_close",
            "eigrp_sys_ipv4_packet_receive", "eigrp_sys_ipv4_packet_send",
            "eigrp_sys_multicast_join", "eigrp_sys_multicast_leave",
            "eigrp_sys_packet_send", "eigrp_sys_packet_receive",
        ):
            self.assertIn(required, symbols.stdout)
        for forbidden in ("eigrp_uut_", "eigrp_packet_read", "eigrp_fsm_"):
            self.assertNotIn(forbidden, symbols.stdout)

    def test_format(self):
        formatter = shutil.which("clang-format")
        if not formatter:
            self.skipTest("clang-format unavailable in this environment")
        files = [CODE / "eigrp_linux_packet.c", CODE / "eigrp_linux_packet.h",
                 TEST / "packet_harness.c", TEST / "packet_privilege.c"]
        formatted = run(formatter, "--dry-run", "--Werror", *map(str, files))
        self.assertEqual(formatted.returncode, 0,
                         formatted.stdout + formatted.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
