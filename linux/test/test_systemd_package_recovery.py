#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise service recovery after a deliberately rejected package config."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("test_systemd_package.py")
spec = importlib.util.spec_from_file_location("systemd_package_recovery_uut", SCRIPT)
uut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uut)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openeigrpd-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "openeigrpd.conf"
        self.config.write_text(uut.MARKER)
        self.socket = Path(self.temp.name) / "control.sock"
        self.case = uut.SystemdPackageTest("test_service_after_restart")
        self.case.a = "0.5.2-diivious.dev"
        self.started = True
        self.enabled = True
        self.purged = False
        self.commands = []
        self.reject_recovery = False
        self.journal_output = "invalid or unsupported configuration\n"

    def command(self, *argv, check=True, **kwargs):
        self.commands.append(argv)
        if argv[:1] == ("journalctl",):
            return SimpleNamespace(returncode=0,
                                   stdout=self.journal_output,
                                   stderr="")
        if argv[:2] == ("systemctl", "disable"):
            self.enabled = False
        elif argv[:2] == ("systemctl", "stop"):
            self.started = False
        elif argv[:2] == ("systemctl", "reset-failed"):
            # Debian 13 returns this after an inactive unit is unloaded.
            raise AssertionError("Unit openeigrpd.service not loaded")
        elif argv[:2] == ("systemctl", "start"):
            invalid = "invalid-openeigrpd-netlab-directive" in self.config.read_text()
            if invalid or self.reject_recovery:
                if check:
                    raise AssertionError("service failed to start")
                return SimpleNamespace(returncode=1, stdout="", stderr="invalid config")
            self.started = True
        else:
            raise AssertionError(f"unexpected call {argv!r}")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def check(self):
        def assert_running(version):
            self.assertEqual(version, self.case.a)
            self.assertTrue(self.started)

        def assert_inactive():
            self.assertFalse(self.started)

        def purge(*argv):
            self.assertEqual(argv, ("purge", "openeigrpd"))
            self.assertFalse(self.started)
            self.purged = True

        def assert_removed(keep_config):
            self.assertFalse(keep_config)
            self.assertTrue(self.purged)

        with patch.object(uut, "CONFIG", self.config), \
             patch.object(uut, "SOCKET", self.socket), \
             patch.object(uut, "run", side_effect=self.command), \
             patch.object(uut, "active", side_effect=lambda: self.started), \
             patch.object(uut, "enabled", side_effect=lambda: self.enabled), \
             patch.object(uut, "apt", side_effect=purge), \
             patch.object(uut.time, "sleep"), \
             patch.object(self.case, "assert_package"), \
             patch.object(self.case, "assert_running", side_effect=assert_running), \
             patch.object(self.case, "assert_inactive", side_effect=assert_inactive), \
             patch.object(self.case, "assert_removed", side_effect=assert_removed):
            self.case.test_service_after_restart()

    def test_stopped_unloaded_unit_recovers_after_bad_config(self):
        self.check()
        self.assertTrue(self.purged)
        self.assertIn(uut.JOURNAL_CANARY, self.config.read_text())
        self.assertNotIn("invalid-openeigrpd-netlab-directive", self.config.read_text())
        self.assertEqual([cmd[:2] for cmd in self.commands if cmd[:1] == ("systemctl",)],
                         [("systemctl", "stop"), ("systemctl", "disable"),
                          ("systemctl", "start"), ("systemctl", "stop"),
                          ("systemctl", "start"), ("systemctl", "stop")])

    def test_journal_canary_leak_is_rejected(self):
        self.journal_output += uut.JOURNAL_CANARY + "\n"
        with self.assertRaisesRegex(AssertionError, "journal exposed configuration comment"):
            self.check()
        self.assertFalse(self.purged)

    def test_recovery_start_error_is_not_ignored(self):
        self.reject_recovery = True
        with self.assertRaisesRegex(AssertionError, "service failed to start"):
            self.check()
        self.assertFalse(self.purged)
        self.assertNotIn("invalid-openeigrpd-netlab-directive", self.config.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
