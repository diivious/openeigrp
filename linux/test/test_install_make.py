#!/usr/bin/env python3
"""Execute native DESTDIR installation and verify config preservation."""
# Copyright (C) 2026 Donnie V. Savage
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

class NativeInstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="linux-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "openeigrp"
        for directory in ("linux", "eigrp/code", "libunix/code", "linux/code", "linux/packaging", "linux/packaging/man"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "linux/Makefile", self.root / "linux/Makefile")
        for directory in ("eigrp/code", "libunix/code", "linux/code"):
            (self.root / directory / "Makefile").write_text(".PHONY: all\nall:\n\t@true\n")
        for name in ("openeigrpd", "oectl", "openeigrpd-config"):
            f = self.root / "linux/code" / name
            f.write_text("#!/bin/sh\nexit 0\n")
            f.chmod(0o755)
        for name in ("openeigrpd.conf", "openeigrpd.service", "openeigrpd.sysusers"):
            (self.root / "linux/packaging" / name).write_text(name)
        for name in ("openeigrpd.8", "oectl.8", "openeigrpd.conf.5"):
            (self.root / "linux/packaging/man" / name).write_text(name)
        self.dest = self.root / "stage"

    def install(self, host="Linux"):
        return subprocess.run(["make", "--no-print-directory", "-C", str(self.root / "linux"),
                               "install", f"HOST_OS={host}", f"DESTDIR={self.dest}"],
                              text=True, capture_output=True)

    def test_layout_repeat_preserves_user_config_and_does_not_enable_service(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("usr/sbin/openeigrpd", "usr/bin/oectl", "usr/bin/openeigrpd-config",
                     "etc/openeigrpd.conf", "usr/lib/systemd/system/openeigrpd.service",
                     "usr/lib/sysusers.d/openeigrpd.conf", "usr/share/man/man8/oectl.8",
                     "usr/share/man/man8/openeigrpd.8", "usr/share/man/man5/openeigrpd.conf.5"):
            self.assertTrue((self.dest / name).is_file(), name)
        config = self.dest / "etc/openeigrpd.conf"
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        config.write_text("user configuration\n")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_text(), "user configuration\n")
        self.assertIn("Preserving existing", result.stdout)
        self.assertNotIn("systemctl", result.stdout)

    def test_dangling_config_symlink_not_overwritten(self):
        config = self.dest / "etc/openeigrpd.conf"
        config.parent.mkdir(parents=True)
        config.symlink_to("missing-external-config")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(config.is_symlink())
        self.assertFalse(config.exists())

    def test_nonlinux_host_rejected_before_staging(self):
        self.assertNotEqual(self.install("Darwin").returncode, 0)
        self.assertFalse((self.dest / "usr").exists())

if __name__ == "__main__":
    unittest.main()
