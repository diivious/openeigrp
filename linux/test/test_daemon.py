#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable native daemon startup, IPC, signal and cleanup regressions.

The empty-config lifecycle requires no raw socket privilege. The configured
AF test also exercises denial of CAP_NET_RAW without ever treating it as a
successful routing instance; with the capability it tests a live AF instead.
"""

import concurrent.futures
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
DAEMON = ROOT / "linux" / "code" / "openeigrpd"
CLIENT = ROOT / "linux" / "code" / "oectl"


class NativeDaemonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "linux":
            raise unittest.SkipTest("native Linux only")
        result = subprocess.run(["make", "linux"], cwd=ROOT,
                                capture_output=True, text=True, timeout=100)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openeigrpd-native-")
        self.directory = Path(self.tmp.name)
        os.chmod(self.directory, 0o700)
        self.config = self.directory / "daemon.conf"
        self.config.write_text("! Empty configuration must be supported\n")
        self.socket = self.directory / "control.sock"
        self.proc = None

    def tearDown(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.communicate(timeout=5)
                self.fail("daemon failed to terminate on SIGTERM")
        self.tmp.cleanup()

    def invoke(self, *options, timeout=7):
        return subprocess.run([str(DAEMON), *options], capture_output=True,
                              text=True, timeout=timeout)

    def start(self):
        self.proc = subprocess.Popen([str(DAEMON), "-f", str(self.config),
                                      "-s", str(self.socket)],
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.socket.is_socket():
                return
            if self.proc.poll() is not None:
                self.fail("daemon exited before socket: " +
                          self.proc.communicate(timeout=3)[1])
            time.sleep(.04)
        self.fail("daemon did not publish control socket")

    def ctl(self, *args):
        return subprocess.run([str(CLIENT), "--socket", str(self.socket),
                               *args], capture_output=True, text=True,
                              timeout=5)

    def test_empty_configuration_ipc_and_shutdown(self):
        self.start()
        self.assertEqual(self.socket.stat().st_mode & 0o777, 0o660)
        report = self.ctl("show", "capabilities")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("ipv4=true", report.stdout)
        self.assertIn("ipv6=true", report.stdout)
        self.assertNotEqual(self.ctl("show", "protocols", "--asn", "4453").returncode, 0)
        self.proc.send_signal(signal.SIGTERM)
        output, errors = self.proc.communicate(timeout=8)
        self.assertEqual(self.proc.returncode, 0, output + errors)
        self.assertFalse(self.socket.exists())

    def test_repeat_restart_and_sighup_never_partially_applies(self):
        self.start()
        self.config.write_text("unrecognized directive\n")
        self.proc.send_signal(signal.SIGHUP)
        time.sleep(.15)
        self.assertIsNone(self.proc.poll())
        self.assertEqual(self.ctl("show", "capabilities").returncode, 0)
        self.proc.send_signal(signal.SIGINT)
        _, errors = self.proc.communicate(timeout=8)
        self.assertEqual(self.proc.returncode, 0, errors)
        self.assertIn("reload rejected", errors)
        self.assertFalse(self.socket.exists())
        self.config.write_text("! now valid\n")
        self.start()
        self.assertEqual(self.ctl("show", "capabilities").returncode, 0)

    def test_multiple_concurrent_and_sequential_management_clients(self):
        self.start()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.ctl("show", "capabilities"),
                                    range(40)))
        self.assertTrue(all(r.returncode == 0 for r in results),
                        "\n".join(r.stderr for r in results if r.returncode))
        self.assertEqual(self.ctl("show", "capabilities").returncode, 0)

    def test_validator_bad_configuration_and_no_side_effects(self):
        good = self.invoke("-f", str(self.config), "-t")
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn("openeigrpd", self.invoke("-V").stdout)
        self.config.write_text("not a directive\n")
        invalid = self.invoke("-f", str(self.config), "-s", str(self.socket))
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("invalid configuration", invalid.stderr)
        self.assertFalse(self.socket.exists())

    def test_reject_unsafe_socket_directory_and_collision(self):
        bad_socket = "/tmp/openeigrpd-unsafe-%d.sock" % os.getpid()
        invalid = self.invoke("-f", str(self.config), "-s", bad_socket)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertFalse(Path(bad_socket).exists())
        self.start()
        clash = self.invoke("-f", str(self.config), "-s", str(self.socket))
        self.assertNotEqual(clash.returncode, 0)
        self.assertIn("management socket", clash.stderr)
        self.assertEqual(self.ctl("show", "capabilities").returncode, 0)

    def test_configured_af_requires_real_raw_socket_privileges(self):
        self.config.write_text(
            "router eigrp linux-test\n"
            " address-family ipv4 unicast autonomous-system 4453\n"
            "  eigrp router-id 192.0.2.1\n"
            "  network 127.0.0.0 0.255.255.255\n"
            " exit-address-family\nexit\n")
        self.assertEqual(self.invoke("-f", str(self.config), "-t").returncode, 0)
        # Query the real Linux kernel, not process UID: even root can lack
        # CAP_NET_RAW in an unprivileged CI/container environment.
        try:
            raw = socket.socket(socket.AF_INET, socket.SOCK_RAW, 88)
        except OSError:
            no_privilege = True
        else:
            no_privilege = False
            raw.close()
        if no_privilege:
            result = self.invoke("-f", str(self.config), "-s", str(self.socket))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unable to open an EIGRP socket", result.stderr)
            self.assertFalse(self.socket.exists())
        else:
            self.start()
            result = self.ctl("show", "protocols")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("4453", result.stdout)
            self.assertEqual(self.ctl("show", "neighbors", "--asn", "4453",
                                      "--afi", "4").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
