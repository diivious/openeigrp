#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Native IPv6 packet and simultaneous IPv4/IPv6 transport regression tests."""

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


class IPv6PacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.uname().sysname != "Linux":
            raise unittest.SkipTest("requires Linux")
        cls.tempdir = tempfile.TemporaryDirectory(prefix="eigrp-linux-ipv6-")
        cls.binaries = {}
        for name in ("packet_ipv6_harness", "packet_privilege"):
            binary = Path(cls.tempdir.name) / name
            compiled = run(
                os.environ.get("CC", "cc"), "-std=c11", "-D_GNU_SOURCE",
                "-Wall", "-Wextra", "-Werror", "-pthread", "-g",
                "-I", str(ROOT / "eigrp" / "code"),
                "-I", str(ROOT / "libunix" / "code"),
                str(TEST / (name + ".c")), "-o", str(binary),
            )
            if compiled.returncode:
                raise AssertionError(compiled.stdout + compiled.stderr)
            cls.binaries[name] = binary

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tempdir"):
            cls.tempdir.cleanup()

    def test_ipv6_crafted_frames_and_dual_stack(self):
        executed = run(str(self.binaries["packet_ipv6_harness"]))
        self.assertEqual(executed.returncode, 0,
                         executed.stdout + executed.stderr)
        for section in (
            "IPv6-only socket options and privilege failures: PASS",
            "IPv6 multicast FF02::A join/leave, address change, shutdown: PASS",
            "IPv6 scoped multicast/unicast TX, checksum ownership, failures: PASS",
            "IPv6 payload framing, cmsgs, scope, loss/recovery: PASS",
            "IPv4/IPv6 concurrent send/receive, shutdown and restart: PASS",
            "IPv4/IPv6 independent final shutdown: PASS",
        ):
            self.assertIn(section, executed.stdout)

    def test_real_kernel_privilege_and_checksum(self):
        executed = run(str(self.binaries["packet_privilege"]))
        self.assertEqual(executed.returncode, 0,
                         executed.stdout + executed.stderr)
        self.assertIn("real IPv6 raw socket", executed.stdout)

    def test_archive_exports_and_portable_boundary(self):
        if not shutil.which("nm"):
            self.skipTest("nm unavailable")
        built = run("make", "-C", str(CODE), "libeigrp_linux_packet.a")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        symbols = run("nm", "--defined-only", str(CODE / "libeigrp_linux_packet.a"))
        self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
        for required in (
            "eigrp_sys_ipv4_packet_receive", "eigrp_sys_ipv4_packet_send",
            "eigrp_sys_ipv6_packet_receive", "eigrp_sys_ipv6_packet_send",
            "eigrp_sys_packet_send", "eigrp_sys_packet_receive",
            "eigrp_sys_multicast_join", "eigrp_sys_multicast_leave",
        ):
            self.assertIn(required, symbols.stdout)
        for forbidden in ("eigrp_packet_read", "eigrp_fsm_", "eigrp_uut_"):
            self.assertNotIn(forbidden, symbols.stdout)

    def test_clang_format(self):
        formatter = shutil.which("clang-format")
        if not formatter:
            self.skipTest("clang-format unavailable")
        files = [CODE / "eigrp_linux_packet.c", CODE / "eigrp_linux_packet.h",
                 TEST / "packet_ipv6_harness.c", TEST / "packet_harness.c",
                 TEST / "packet_privilege.c"]
        formatted = run(formatter, "--dry-run", "--Werror", *map(str, files))
        self.assertEqual(formatted.returncode, 0,
                         formatted.stdout + formatted.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
