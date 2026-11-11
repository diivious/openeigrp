#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Qualify real .deb service lifecycle in a disposable systemd Debian/Ubuntu VM.

Unlike test_packaging.py this test installs the real daemon and starts systemd.
It is deliberately opt-in, root-only, and refuses existing installations.
"""

import grp
import os
from pathlib import Path
import pwd
import stat
import subprocess
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "linux/packaging/verify-deb.sh"
UNIT = "openeigrpd.service"
CONFIG = Path("/etc/openeigrpd.conf")
SOCKET = Path("/run/openeigrpd/control.sock")
SERVICE = Path("/usr/lib/systemd/system/openeigrpd.service")


def run(*args, check=True):
    result = subprocess.run([str(arg) for arg in args], capture_output=True,
                            text=True, timeout=60)
    if check and result.returncode:
        raise AssertionError(f"{' '.join(map(str, args))}:\n"
                             f"{result.stdout}\n{result.stderr}")
    return result


def active():
    return run("systemctl", "is-active", "--quiet", UNIT,
               check=False).returncode == 0


def until(predicate, label):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {label}")


@unittest.skipUnless(os.environ.get("OPENEIGRP_PACKAGE_SYSTEMD_TEST") == "1",
                     "opt in only inside a disposable systemd VM")
class SystemdPackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid() != 0:
            raise RuntimeError("must run as root inside a disposable VM")
        if Path("/proc/1/comm").read_text().strip() != "systemd":
            raise RuntimeError("systemd must be PID 1; a non-systemd container is insufficient")
        if CONFIG.exists() or CONFIG.is_symlink() or SERVICE.exists():
            raise RuntimeError("refusing existing OpenEIGRP configuration or service")
        if run("dpkg-query", "-W", "-f=${Status}", "openeigrpd",
               check=False).returncode == 0:
            raise RuntimeError("refusing a preexisting OpenEIGRP package")
        if SOCKET.exists() or SOCKET.is_symlink():
            raise RuntimeError("refusing an existing management socket")

        old = os.environ.get("OPENEIGRP_PACKAGE_OLD")
        new = os.environ.get("OPENEIGRP_PACKAGE_NEW")
        if not old or not new:
            raise RuntimeError("set OPENEIGRP_PACKAGE_OLD and OPENEIGRP_PACKAGE_NEW")
        cls.old = Path(old).resolve(strict=True)
        cls.new = Path(new).resolve(strict=True)
        arch = run("dpkg", "--print-architecture").stdout.strip()
        for package in (cls.old, cls.new):
            if run("dpkg-deb", "-f", package, "Package").stdout.strip() != "openeigrpd":
                raise RuntimeError(f"not an openeigrpd package: {package}")
            version = run("dpkg-deb", "-f", package, "Version").stdout.strip()
            run(VERIFY, package, version, arch)
        a = run("dpkg-deb", "-f", cls.old, "Version").stdout.strip()
        b = run("dpkg-deb", "-f", cls.new, "Version").stdout.strip()
        if run("dpkg", "--compare-versions", a, "lt", b,
               check=False).returncode != 0:
            raise RuntimeError("NEW must have a higher Debian version than OLD")

    def test_real_systemd_install_upgrade_stop_and_purge(self):
        installed = False
        try:
            run("dpkg", "-i", self.old)
            installed = True
            self.assertTrue(CONFIG.is_file())
            self.assertEqual(stat.S_IMODE(CONFIG.stat().st_mode), 0o600)
            self.assertEqual(CONFIG.stat().st_uid, 0)
            self.assertEqual(pwd.getpwnam("openeigrp").pw_name, "openeigrp")
            self.assertEqual(grp.getgrnam("openeigrpctl").gr_name, "openeigrpctl")
            self.assertFalse(active(), "install must not start routing")
            self.assertFalse(SOCKET.exists(), "install must not create a socket")
            self.assertNotEqual(run("systemctl", "is-enabled", "--quiet", UNIT,
                                    check=False).returncode, 0,
                                "install must not enable routing")
            run("openeigrpd-config", "-t", "-f", CONFIG)

            with CONFIG.open("a") as file:
                file.write("! systemd package upgrade preservation test\n")
            # A stopped, disabled service must stay stopped across upgrade.
            run("dpkg", "-i", self.new)
            self.assertFalse(active(), "stopped upgrade must not start routing")
            self.assertFalse(SOCKET.exists())
            self.assertNotEqual(run("systemctl", "is-enabled", "--quiet", UNIT,
                                    check=False).returncode, 0)
            self.assertIn("systemd package upgrade preservation test", CONFIG.read_text())
            # Downgrade while stopped, then repeat the upgrade while running.
            run("dpkg", "-i", self.old)
            self.assertFalse(active())
            run("systemctl", "enable", UNIT)
            run("systemctl", "start", UNIT)
            until(lambda: active() and SOCKET.is_socket(), "service and IPC socket")
            self.assertEqual(stat.S_IMODE(SOCKET.stat().st_mode), 0o660)
            self.assertEqual(SOCKET.stat().st_uid, pwd.getpwnam("openeigrp").pw_uid)
            self.assertEqual(SOCKET.stat().st_gid, grp.getgrnam("openeigrpctl").gr_gid)
            self.assertEqual(stat.S_IMODE(SOCKET.parent.stat().st_mode), 0o750)
            run("oectl", "show", "version")

            def check_privileges():
                pid = int(run("systemctl", "show", UNIT, "--property=MainPID",
                              "--value").stdout.strip())
                self.assertGreater(pid, 1)
                status = Path(f"/proc/{pid}/status").read_text().splitlines()
                values = dict(line.split(":", 1) for line in status if ":" in line)
                self.assertEqual(int(values["Uid"].split()[0]),
                                 pwd.getpwnam("openeigrp").pw_uid)
                self.assertEqual(int(values["Gid"].split()[0]),
                                 grp.getgrnam("openeigrpctl").gr_gid)
                # CAP_NET_ADMIN=12 and CAP_NET_RAW=13; no other effective caps.
                self.assertEqual(int(values["CapEff"].strip(), 16),
                                 (1 << 12) | (1 << 13))
                self.assertEqual(int(values["NoNewPrivs"].strip()), 1)

            check_privileges()
            run("systemctl", "restart", UNIT)
            until(lambda: active() and SOCKET.is_socket(), "restarted daemon")
            run("oectl", "show", "version")
            check_privileges()

            # A running service must be restarted after a real dpkg upgrade.
            run("dpkg", "-i", self.new)
            until(lambda: active() and SOCKET.is_socket(), "upgraded daemon")
            self.assertIn("systemd package upgrade preservation test", CONFIG.read_text())
            run("oectl", "show", "version")
            check_privileges()
            self.assertEqual(run("systemctl", "is-enabled", UNIT).stdout.strip(),
                             "enabled")

            run("systemctl", "stop", UNIT)
            until(lambda: not active() and not SOCKET.exists(), "stopped service cleanup")
            run("systemctl", "start", UNIT)
            until(lambda: active() and SOCKET.is_socket(), "service after stop/start")
            run("dpkg", "-r", "openeigrpd")
            installed = False
            until(lambda: not active() and not SOCKET.exists(), "service removal")
            self.assertTrue(CONFIG.is_file(), "remove must preserve conffile")
            self.assertFalse(SERVICE.exists(), "remove must uninstall service")
            self.assertNotEqual(run("systemctl", "is-enabled", "--quiet", UNIT,
                                    check=False).returncode, 0,
                                "remove must discard enablement")
            run("dpkg", "-P", "openeigrpd")
            self.assertFalse(CONFIG.exists(), "purge must discard conffile")
        finally:
            if installed:
                run("systemctl", "stop", UNIT, check=False)
            run("dpkg", "--purge", "openeigrpd", check=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
