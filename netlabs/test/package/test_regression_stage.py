#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise the real remote regression staging list and staged policy tests."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
CLIENT = ROOT / "netlabs/tools/remote-uut.sh"


class RegressionStageTests(unittest.TestCase):
    @staticmethod
    def regression_function(client):
        # Extract this one function without depending on platform sed syntax.
        source = client.read_text()
        header = "run_remote_regressions() {\n"
        start = source.find(header)
        if start < 0:
            raise AssertionError("missing run_remote_regressions function")
        end = source.find("\n}\n", start)
        if end < 0:
            raise AssertionError("unterminated run_remote_regressions function")
        return source[start:end + 2]

    def stage(self, selection):
        # Run the client staging function with an isolated local tar receiver.
        # Stop before transport/runner installation: this is a staging test.
        stage = self.work / selection
        stage.mkdir()
        harness = "set -euo pipefail\n" + self.regression_function(CLIENT) + r"""
remote_new_stage() { printf '%s\n' "$STAGE"; }
remote_stage() {
    local destination="$1"
    shift
    tar -C "$REMOTE_REPO_ROOT" -cf - "$@" | tar -C "$destination" -xf - || exit 3
}
remote_install_runner() { return 1; }
record() { :; }
if run_remote_regressions "$SELECTION"; then
    echo 'unexpectedly completed runner installation' >&2
    exit 1
fi
[[ -f "$STAGE/netlabs/tools/remote/runner.sh" ]] || {
    echo 'regression staging did not execute' >&2
    exit 2
}
"""
        env = {**os.environ, "STAGE": str(stage),
               "SELECTION": selection, "REMOTE_REPO_ROOT": str(ROOT),
               "REMOTE_HOST": "mock-appliance"}
        result = subprocess.run(["bash", "-c", harness], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return stage

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openeigrp-package-stage-")
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)

    def test_package_policy_executes_from_remote_stage(self):
        stage = self.stage("package")
        self.assertTrue((stage / "linux/test/test_systemd_package.py").is_file())
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s",
             "netlabs/test/package", "-p", "test_systemd_package_policy.py"],
            cwd=stage, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stderr, r"Ran [1-9][0-9]* tests")

    def test_all_stages_policy_dependency(self):
        stage = self.stage("all")
        self.assertTrue((stage / "linux/test/test_systemd_package.py").is_file())

    def test_container_does_not_stage_package_dependency(self):
        stage = self.stage("container")
        self.assertFalse((stage / "linux/test/test_systemd_package.py").exists())

    def test_missing_staging_function_is_rejected(self):
        broken = self.work / "broken-client.sh"
        broken.write_text("#!/bin/bash\n")
        with self.assertRaisesRegex(AssertionError, "missing run_remote_regressions"):
            self.regression_function(broken)


if __name__ == "__main__":
    unittest.main(verbosity=2)
