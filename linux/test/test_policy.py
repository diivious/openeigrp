#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Native Linux policy evaluation is executed, not source-inspected."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class LinuxPolicyTests(unittest.TestCase):
    def test_policy_service_with_live_callbacks(self):
        with tempfile.TemporaryDirectory(prefix="oe-linux-policy-") as directory:
            executable = Path(directory) / "policy-harness"
            compile_result = subprocess.run(
                [os.getenv("CC", "cc"), "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-pthread", "-I", str(ROOT / "eigrp/code"), "-I", str(ROOT / "linux/code"),
                 str(ROOT / "linux/test/policy_harness.c"),
                 str(ROOT / "linux/code/eigrp_linux_policy.c"), "-o", str(executable)],
                capture_output=True, text=True, cwd=ROOT,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            result = subprocess.run([str(executable)], cwd=ROOT,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("linux policy tests: PASS", result.stdout)

    def test_clang_format(self):
        formatter = shutil.which("clang-format")
        if not formatter:
            self.skipTest("clang-format unavailable")
        sources = [ROOT / rel for rel in (
            "linux/code/eigrp_linux_policy.c", "linux/code/eigrp_linux_policy.h",
            "linux/code/eigrp_linux_config.c", "linux/code/eigrp_linux_config.h",
            "linux/test/policy_harness.c", "libunix/code/eigrp_unix_config.c",
            "libunix/code/eigrp_unix_config_apply.c",
            "libunix/code/eigrp_unix_config_private.h",
            "libunix/test/config_harness.c",
        )]
        result = subprocess.run([formatter, "--dry-run", "--Werror",
                                 *map(str, sources)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
