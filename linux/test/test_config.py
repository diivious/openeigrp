#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Offline native configuration parsing and semantic contract regressions."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
UNIX = ROOT / "libunix" / "code"
CODE = ROOT / "linux" / "code"
TEST = ROOT / "libunix" / "test"
EXAMPLE = ROOT / "linux" / "config" / "openeigrpd.conf.example"


def run(*args):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True)


class LinuxConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="openeigrp-config-")
        cls.checker = Path(cls.tmp.name) / "openeigrpd-config"
        cls.harness = Path(cls.tmp.name) / "config-harness"
        base = [os.environ.get("CC", "cc"), "-std=c11", "-D_POSIX_C_SOURCE=200809L",
                "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "eigrp" / "code"),
                "-I", str(UNIX)]
        for name, sources in (
            (cls.checker, [CODE / "openeigrpd-config.c", UNIX / "eigrp_unix_config.c"]),
            (cls.harness, [UNIX / "eigrp_unix_config.c", UNIX / "eigrp_unix_config_apply.c",
                           TEST / "config_harness.c"]),
        ):
            built = run(*base, *(str(src) for src in sources), "-o", str(name))
            if built.returncode:
                raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def validate(self, text, valid, expected_line=None):
        path = Path(self.tmp.name) / "case.conf"
        path.write_text(text)
        result = run(str(self.checker), "-t", "-f", str(path))
        self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)
        if expected_line is not None:
            self.assertIn(f":{expected_line}:", result.stderr)
        return result

    def test_sample_dual_stack_configuration(self):
        result = run(str(self.checker), "-t", "-f", str(EXAMPLE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("syntax valid", result.stdout)

    def test_actual_portable_semantic_dispatch_and_rejection(self):
        result = run(str(self.harness))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unix config tests: PASS", result.stdout)

    def test_invalid_lexing_nesting_and_excessive_input(self):
        head = "router eigrp savage\n address-family ipv4 autonomous-system 4453\n"
        tail = " exit-address-family\nexit\n"
        cases = (
            (head + "  unimplemented policy-list bad\n" + tail, 3),
            (head + "  eigrp router-id 192.0.2.999\n" + tail, 3),
            (head + "  network 10.0.0.0 0.255.0.255\n" + tail, 3),
            (head + "  network 10.0.0.0/33\n" + tail, 3),
            (head + "  af-interface eth0\n   hello-interval -1\n"
             "  exit-af-interface\n" + tail, 4),
            (head + "  af-interface eth0\n   authentication mode md5\n"
             "  exit-af-interface\n" + tail, 4),
            (head + "  topology base\n   maximum-prefix 100\n"
             "  exit-af-topology\n" + tail, 4),
            (head + "  shutdown garbage\n" + tail, 3),
            (head + "  redistrib\n" + tail, 3),
            (head + "  " + "z" * 600 + "\n" + tail, 3),
            (head + "  topology base\n" + tail, 4),
            ("exit\n", 1),
            ("router eigrp savage\n address-family ipv7 autonomous-system 4453\n", 2),
            ("router eigrp savage\n address-family ipv6 vrf other "
             "autonomous-system 4453\n", 2),
            ("router eigrp savage\n address-family ipv4 autonomous-system 0\n", 2),
            ("router eigrp savage\n address-family ipv4 autonomous-system 65536\n", 2),
            ("router eigrp savage\n address-family ipv4 autonomous-system 4453\n"
             "  network 10.0.0.0\n" + tail + "extra\n", 6),
        )
        for text, line in cases:
            with self.subTest(case=text[:75]):
                self.validate(text, False, line)

    def test_ipv4_ipv6_scope_and_valid_directives(self):
        self.validate("router eigrp savage\n address-family ipv6 unicast "
                      "autonomous-system 4453\n  af-interface eth0\n"
                      "   passive-interface\n   no next-hop-self\n"
                      "  exit-af-interface\n exit-address-family\nexit\n", True)
        self.validate("router eigrp savage\n address-family ipv6 "
                      "autonomous-system 4453\n  network 2001:db8::/64\n"
                      " exit-address-family\nexit\n", False, 3)

    def test_validator_repeated_runs_and_failure_isolation(self):
        base = "router eigrp savage\n address-family ipv4 " \
               "autonomous-system 4453\n  shutdown\n  no shutdown\n" \
               " exit-address-family\nexit\n"
        for _ in range(3):
            self.validate(base, True)
        self.validate(base.replace("no shutdown", "filter unknown"), False, 4)
        self.validate(base, True)

    def test_missing_file_and_arguments(self):
        missing = run(str(self.checker), "-t", "-f", "/does-not-exist/openeigrpd.conf")
        self.assertEqual(missing.returncode, 1)
        for argv in ((), ("-f", str(EXAMPLE)), ("-t", "-invalid")):
            with self.subTest(argv=argv):
                result = run(str(self.checker), *argv)
                self.assertEqual(result.returncode, 2)

    def test_clang_format(self):
        formatter = shutil.which("clang-format")
        if not formatter:
            self.skipTest("clang-format unavailable")
        sources = [UNIX / "eigrp_unix_config.c", UNIX / "eigrp_unix_config_apply.c",
                   UNIX / "eigrp_unix_config_private.h", UNIX / "eigrp_unix_config.h",
                   CODE / "openeigrpd-config.c", TEST / "config_harness.c"]
        checked = run(formatter, "--dry-run", "--Werror", *map(str, sources))
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
