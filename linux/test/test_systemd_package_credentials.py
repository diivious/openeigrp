#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Verify credential assertions used by the real Debian systemd package UUT."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("test_systemd_package.py")
spec = importlib.util.spec_from_file_location("systemd_package_uut", SCRIPT)
uut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uut)

UNIT = """# /usr/lib/systemd/system/openeigrpd.service
[Service]
LoadCredential=openeigrpd.conf:/etc/openeigrpd.conf
ExecStart=/usr/sbin/openeigrpd -f ${CREDENTIALS_DIRECTORY}/openeigrpd.conf
"""
CREDENTIAL = b"/run/credentials/openeigrpd.service/openeigrpd.conf"


class CredentialChecks(unittest.TestCase):
    def setUp(self):
        self.case = uut.SystemdPackageTest("test_fresh_service")

    def check_credential(self, unit=UNIT, argv=None):
        if argv is None:
            argv = b"/usr/sbin/openeigrpd\0-f\0" + CREDENTIAL + b"\0"
        with patch.object(uut, "run", return_value=SimpleNamespace(stdout=unit)) as run, \
             patch.object(uut.Path, "read_bytes", autospec=True, return_value=argv) as read:
            self.case.assert_credentials(459)
        run.assert_called_once_with("systemctl", "cat", "openeigrpd.service")
        read.assert_called_once_with(Path("/proc/459/cmdline"))

    def test_debian_13_unprintable_property_does_not_block_valid_credential(self):
        # The production check never reads the unprintable systemctl show
        # property. It verifies the installed unit and running daemon instead.
        self.check_credential()

    def test_missing_load_credential_is_rejected(self):
        with self.assertRaises(AssertionError):
            self.check_credential(unit=UNIT.replace("LoadCredential=openeigrpd.conf:/etc/openeigrpd.conf\n", ""))

    def test_commented_out_load_credential_is_rejected(self):
        with self.assertRaises(AssertionError):
            self.check_credential(unit=UNIT.replace("LoadCredential=", "# LoadCredential="))

    def test_daemon_using_unprotected_config_is_rejected(self):
        with self.assertRaises(AssertionError):
            self.check_credential(argv=b"/usr/sbin/openeigrpd\0-f\0/etc/openeigrpd.conf\0")

    def test_daemon_missing_config_argument_is_rejected(self):
        with self.assertRaises(AssertionError):
            self.check_credential(argv=b"/usr/sbin/openeigrpd\0--foreground\0")

    def test_daemon_missing_config_path_is_rejected(self):
        with self.assertRaises(AssertionError):
            self.check_credential(argv=b"/usr/sbin/openeigrpd\0-f\0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
