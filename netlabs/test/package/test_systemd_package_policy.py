#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Execute the Debian package lifecycle policy preflight against real scripts."""

import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "package_systemd", ROOT / "linux/test/test_systemd_package.py")
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class PackageServicePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openeigrpd-policy-")
        self.addCleanup(self.temp.cleanup)
        self.policy = Path(self.temp.name) / "policy-rc.d"

    def write_policy(self, body):
        self.policy.write_text("#!/bin/sh\n" + body)
        self.policy.chmod(0o755)

    def test_missing_policy_allows_service_actions(self):
        PACKAGE.assert_service_policy(self.policy)

    def test_docker_deny_all_policy_rejected(self):
        self.write_policy("exit 101\n")
        with self.assertRaisesRegex(RuntimeError, "denies stop.*101"):
            PACKAGE.assert_service_policy(self.policy)

    def test_stop_allowed_but_upgrade_blocked_is_rejected(self):
        self.write_policy('if [ "$2" = "try-restart" ]; then exit 101; fi\nexit 0\n')
        with self.assertRaisesRegex(RuntimeError, "denies try-restart.*101"):
            PACKAGE.assert_service_policy(self.policy)

    def test_allowed_policy_actions(self):
        self.write_policy('case "$2" in stop|try-restart) exit 0;; esac\nexit 101\n')
        PACKAGE.assert_service_policy(self.policy)

    def test_explicit_allow_code(self):
        self.write_policy("exit 104\n")
        PACKAGE.assert_service_policy(self.policy)

    def test_non_executable_policy_is_ignored(self):
        self.write_policy("exit 101\n")
        self.policy.chmod(stat.S_IRUSR | stat.S_IWUSR)
        PACKAGE.assert_service_policy(self.policy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
