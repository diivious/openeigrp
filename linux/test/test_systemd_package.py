#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Real Debian-package acceptance tests inside disposable systemd containers.

The Netlab package driver runs each fresh phase in a new Containerlab node.
The restart follow-up uses the same container writable layer and a new PID 1.
No daemon is built from the checkout or replaced with a fixture.
"""

import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
VERIFY = Path(os.environ.get("OPENEIGRP_PACKAGE_VERIFY", ROOT / "linux/packaging/verify-deb.sh"))
UNIT = "openeigrpd.service"
CONFIG = Path("/etc/openeigrpd.conf")
SOCKET = Path("/run/openeigrpd/control.sock")
RUNTIME = SOCKET.parent
SERVICE = Path("/usr/lib/systemd/system/openeigrpd.service")
SYSUSERS = Path("/usr/lib/sysusers.d/openeigrpd.conf")
MARKER = "! openeigrpd package lifecycle retained configuration\n"
SECRET = "netlab-journal-credential-must-not-leak-39482"
EXPECTED = {
    "/usr/sbin/openeigrpd": 0o755,
    "/usr/bin/eigrp": 0o755,
    "/usr/bin/openeigrpd-config": 0o755,
    "/usr/lib/systemd/system/openeigrpd.service": 0o644,
    "/usr/lib/sysusers.d/openeigrpd.conf": 0o644,
    "/usr/share/man/man5/openeigrpd.conf.5": 0o644,
    "/usr/share/man/man8/openeigrpd.8": 0o644,
    "/usr/share/man/man8/eigrp.8": 0o644,
}
CAPS = (1 << 12) | (1 << 13)


def run(*args, check=True, timeout=120, **kwargs):
    argv = [str(arg) for arg in args]
    result = subprocess.run(argv, capture_output=True, text=True,
                            timeout=timeout, **kwargs)
    if check and result.returncode:
        raise AssertionError(f"{argv!r} exited {result.returncode}:\n"
                             f"{result.stdout}\n{result.stderr}")
    return result


def apt(*args, check=True):
    return run("apt-get", "-o", "Dpkg::Options::=--force-confold", "-y", *args,
               check=check, timeout=300,
               env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})


def active():
    return run("systemctl", "is-active", "--quiet", UNIT,
               check=False).returncode == 0


def enabled():
    return run("systemctl", "is-enabled", "--quiet", UNIT,
               check=False).returncode == 0


def until(predicate, label, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {label}")


def assert_service_policy(policy=Path("/usr/sbin/policy-rc.d")):
    # Docker's stock Debian policy denies maintainer-script service actions.
    # The systemd test guest must allow removal and upgrade to manage services.
    if not os.access(policy, os.X_OK):
        return
    for action in ("stop", "try-restart"):
        result = run(policy, UNIT, action, check=False)
        if result.returncode not in (0, 104):
            raise RuntimeError(f"service policy denies {action} for {UNIT} "
                               f"(policy-rc.d exited {result.returncode})")


def field(package, name):
    return run("dpkg-deb", "-f", package, name).stdout.strip()


def installed_version():
    return run("dpkg-query", "-W", "-f=${Version}", "openeigrpd").stdout.strip()


def service_pid():
    return int(run("systemctl", "show", UNIT, "--property=MainPID", "--value")
               .stdout.strip())


def assert_clean_guest():
    if CONFIG.exists() or CONFIG.is_symlink() or SERVICE.exists() or SERVICE.is_symlink():
        raise RuntimeError("refusing preexisting OpenEIGRP configuration or service")
    override = Path("/etc/systemd/system/openeigrpd.service")
    dropins = Path("/etc/systemd/system/openeigrpd.service.d")
    if override.exists() or override.is_symlink() or dropins.exists() or dropins.is_symlink():
        raise RuntimeError("refusing preexisting service override/drop-ins")
    if SYSUSERS.exists() or SYSUSERS.is_symlink() or RUNTIME.exists() or RUNTIME.is_symlink():
        raise RuntimeError("refusing preexisting OpenEIGRP systemd state")
    if any(Path(name).exists() or Path(name).is_symlink() for name in EXPECTED):
        raise RuntimeError("refusing preexisting OpenEIGRP executable/resources")
    status = run("dpkg-query", "-W", "-f=${db:Status-Abbrev}", "openeigrpd",
                 check=False)
    if status.returncode == 0 and status.stdout.strip() not in ("", "un "):
        raise RuntimeError("refusing a preexisting OpenEIGRP package or residual config")
    if run("getent", "passwd", "openeigrp", check=False).returncode == 0 or \
       run("getent", "group", "openeigrpctl", check=False).returncode == 0:
        raise RuntimeError("refusing existing OpenEIGRP service identity")


@unittest.skipUnless(os.environ.get("OPENEIGRP_PACKAGE_SYSTEMD_TEST") == "1",
                     "opt in only inside a disposable systemd container")
class SystemdPackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid() != 0 or os.environ.get("OPENEIGRP_PACKAGE_CONTAINER_DISPOSABLE") != "1":
            raise RuntimeError("requires explicit disposable container opt-in and root")
        if Path("/proc/1/comm").read_text().strip() != "systemd":
            raise RuntimeError("systemd must be PID 1 inside the test container")
        if not Path("/.dockerenv").is_file():
            raise RuntimeError("refusing Debian package tests outside Docker")
        if not Path("/run/openeigrp-netlab-package-test").is_file():
            raise RuntimeError("Netlab container authorization marker missing")
        old, new = (os.environ.get("OPENEIGRP_PACKAGE_OLD"),
                    os.environ.get("OPENEIGRP_PACKAGE_NEW"))
        if not new:
            raise RuntimeError("set OPENEIGRP_PACKAGE_NEW")
        cls.new = Path(new).resolve(strict=True)
        cls.old = Path(old).resolve(strict=True) if old else cls.new
        cls.arch = run("dpkg", "--print-architecture").stdout.strip()
        for package in {cls.old, cls.new}:
            version = field(package, "Version")
            run(VERIFY, package, version, cls.arch)
        cls.a, cls.b = (field(p, "Version") for p in (cls.old, cls.new))
        if old and run("dpkg", "--compare-versions", cls.a, "lt", cls.b,
                       check=False).returncode != 0:
            raise RuntimeError("NEW must have a higher Debian version than OLD")

    def assert_package(self, version):
        self.assertEqual(installed_version(), version)
        self.assertEqual(run("dpkg-query", "-W", "-f=${Status}", "openeigrpd")
                         .stdout.strip(), "install ok installed")
        self.assertEqual(run("dpkg-query", "-W", "-f=${Architecture}",
                             "openeigrpd").stdout.strip(), self.arch)
        payload = set(run("dpkg", "-L", "openeigrpd").stdout.splitlines())
        for name, mode in EXPECTED.items():
            self.assertIn(name, payload)
            path = Path(name)
            self.assertTrue(path.is_file() and not path.is_symlink(), name)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode, name)
            self.assertEqual((path.stat().st_uid, path.stat().st_gid), (0, 0), name)
        self.assertIn(str(CONFIG), payload)
        self.assertTrue(CONFIG.is_file() and not CONFIG.is_symlink())
        self.assertEqual(stat.S_IMODE(CONFIG.stat().st_mode), 0o600)
        self.assertEqual((CONFIG.stat().st_uid, CONFIG.stat().st_gid), (0, 0))
        self.assertEqual(pwd.getpwnam("openeigrp").pw_shell, "/usr/sbin/nologin")
        self.assertEqual(grp.getgrnam("openeigrpctl").gr_name, "openeigrpctl")
        self.assertEqual(run("/usr/sbin/openeigrpd", "-V").stdout.strip(),
                         "openeigrpd " + version)
        self.assertEqual(json.loads(run("/usr/bin/eigrp", "--json", "version")
                                        .stdout)["build"], version)
        run("/usr/bin/openeigrpd-config", "-t", "-f", CONFIG)
        run("/usr/sbin/openeigrpd", "-t", "-f", CONFIG)

    def assert_dependency_contract(self, package):
        depends = field(package, "Depends")
        self.assertIn("libc6", depends)
        self.assertIn("systemd", depends)
        self.assertIn("init-system-helpers", depends)
        self.assertNotRegex(depends.lower(), r"frr|bird|eigrp-wire|\buut\b")
        for name in ("/usr/sbin/openeigrpd", "/usr/bin/eigrp",
                     "/usr/bin/openeigrpd-config"):
            dynamic = run("readelf", "-d", name).stdout
            self.assertNotRegex(dynamic.lower(), r"libfrr|libbird|eigrp-wire|libeigrp_uut")
            linked = run("ldd", name).stdout
            self.assertNotIn("not found", linked)
            self.assertIn("libc.so.6", linked)
            # Beyond libc, any direct NEEDED library must be a declared
            # dependency, not merely available transitively on the VM.
            for library in re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic):
                if library in ("libc.so.6", "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1"):
                    continue
                match = re.search(r"^\s*" + re.escape(library) +
                                  r"\s+=>\s+(\/\S+)", linked, re.MULTILINE)
                self.assertIsNotNone(match, f"unresolved {library}: {linked}")
                owner = run("dpkg-query", "-S", match.group(1)).stdout.split(":", 1)[0]
                self.assertRegex(depends, r"(?:^|[,| ]+)" + re.escape(owner) + r"(?:\s|[,|]|$)")

    def assert_inactive(self):
        self.assertFalse(active(), "routing must not be active")
        self.assertFalse(SOCKET.exists() or SOCKET.is_symlink())
        self.assertFalse(RUNTIME.exists() or RUNTIME.is_symlink())
        self.assertEqual(service_pid(), 0)

    def assert_credentials(self, pid):
        # systemctl show renders the structured LoadCredential property as
        # [unprintable] on some systemd versions, including Debian 13.
        unit = run("systemctl", "cat", UNIT).stdout
        self.assertRegex(unit,
                         r"(?m)^LoadCredential=openeigrpd[.]conf:/etc/openeigrpd[.]conf[ \t]*$")
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        self.assertIn(b"-f", argv, "daemon not started with a config argument")
        config = argv.index(b"-f") + 1
        self.assertLess(config, len(argv), "daemon config argument is missing")
        self.assertEqual(argv[config],
                         b"/run/credentials/openeigrpd.service/openeigrpd.conf")

    def assert_running(self, version):
        until(lambda: active() and SOCKET.is_socket(), "daemon and management socket")
        pid = service_pid()
        self.assertGreater(pid, 1)
        self.assertEqual(os.readlink(f"/proc/{pid}/exe"), "/usr/sbin/openeigrpd")
        fragment = run("systemctl", "show", UNIT, "--property=FragmentPath", "--value")
        self.assertEqual(Path(fragment.stdout.strip()).resolve(), SERVICE.resolve())
        self.assertFalse(SOCKET.is_symlink() or RUNTIME.is_symlink())
        data = dict(line.split(":", 1) for line in
                    Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in line)
        self.assertEqual(int(data["Uid"].split()[0]), pwd.getpwnam("openeigrp").pw_uid)
        self.assertEqual(int(data["Gid"].split()[0]), grp.getgrnam("openeigrpctl").gr_gid)
        for value in ("CapEff", "CapBnd", "CapAmb"):
            self.assertEqual(int(data[value].strip(), 16), CAPS, value)
        self.assertEqual(int(data["NoNewPrivs"].strip()), 1)
        self.assertEqual(stat.S_IMODE(SOCKET.stat().st_mode), 0o660)
        self.assertEqual((SOCKET.stat().st_uid, SOCKET.stat().st_gid),
                         (pwd.getpwnam("openeigrp").pw_uid,
                          grp.getgrnam("openeigrpctl").gr_gid))
        self.assertEqual(stat.S_IMODE(RUNTIME.stat().st_mode), 0o750)
        self.assertEqual((RUNTIME.stat().st_uid, RUNTIME.stat().st_gid),
                         (pwd.getpwnam("openeigrp").pw_uid,
                          grp.getgrnam("openeigrpctl").gr_gid))
        unit = run("systemctl", "show", UNIT,
                   "--property=User,Group,NoNewPrivileges,ProtectSystem,ProtectHome,"
                   "PrivateTmp,PrivateDevices,RestrictNamespaces,ProtectControlGroups,"
                   "ProtectKernelTunables,ProtectKernelModules,ProtectKernelLogs,"
                   "ProtectClock,LockPersonality,RestrictSUIDSGID,RestrictRealtime,"
                   "RuntimeDirectory,RuntimeDirectoryMode").stdout
        values = dict(line.split("=", 1) for line in unit.splitlines() if "=" in line)
        self.assertEqual(values["User"], "openeigrp")
        self.assertEqual(values["Group"], "openeigrpctl")
        self.assertEqual(values["ProtectSystem"], "strict")
        self.assertEqual(values["ProtectHome"], "yes")
        for setting in ("NoNewPrivileges", "PrivateTmp", "PrivateDevices",
                        "RestrictNamespaces", "ProtectControlGroups",
                        "ProtectKernelTunables", "ProtectKernelModules",
                        "ProtectKernelLogs", "ProtectClock", "LockPersonality",
                        "RestrictSUIDSGID", "RestrictRealtime"):
            self.assertIn(values[setting].lower(), ("yes", "true"), setting)
        self.assertIn("openeigrpd", values["RuntimeDirectory"])
        self.assert_credentials(pid)
        self.assertIn(version, run("/usr/bin/eigrp", "show", "version").stdout)
        run("/usr/bin/eigrp", "show", "protocols")
        return pid

    def assert_removed(self, keep_config):
        until(lambda: not active() and not SOCKET.exists() and not RUNTIME.exists(),
              "removed service resources")
        for name in EXPECTED:
            self.assertFalse(Path(name).exists() or Path(name).is_symlink(), name)
        self.assertFalse(enabled())
        self.assertEqual(CONFIG.is_file(), keep_config)
        # Deliberately retain service identity on purge (documented policy).
        self.assertEqual(pwd.getpwnam("openeigrp").pw_name, "openeigrp")
        self.assertEqual(grp.getgrnam("openeigrpctl").gr_name, "openeigrpctl")

    def modify_config(self):
        with CONFIG.open("a") as file:
            file.write(MARKER)
            file.write("router eigrp packagecheck\n")
            file.write(" address-family ipv4 unicast autonomous-system 4453\n")
            file.write("  eigrp router-id 192.0.2.1\n")
            file.write(" exit-address-family\nexit\n")
        run("/usr/bin/openeigrpd-config", "-t", "-f", CONFIG)

    def test_preflight(self):
        # Must execute before guest APT preparation or any package changes.
        assert_clean_guest()
        assert_service_policy()

    def test_fresh_service(self):
        assert_clean_guest()
        self.test_negative_artifacts()
        apt("install", self.old)
        self.assert_package(self.a)
        self.assert_dependency_contract(self.old)
        self.assert_inactive()
        self.assertFalse(enabled(), "installation must not enable routing")
        self.modify_config()
        run("systemctl", "start", UNIT)
        first = self.assert_running(self.a)
        run("systemctl", "restart", UNIT)
        second = self.assert_running(self.a)
        self.assertNotEqual(first, second, "restart must replace daemon")
        run("systemctl", "stop", UNIT)
        self.assert_inactive()
        time.sleep(4)  # exceeds RestartSec; an explicit stop must not auto-restart
        self.assert_inactive()
        run("systemctl", "enable", UNIT)
        self.assertTrue(enabled())
        run("systemctl", "disable", UNIT)
        self.assertFalse(enabled())
        run("systemctl", "start", UNIT)
        self.assert_running(self.a)
        run("systemctl", "enable", UNIT)
        self.assertTrue(enabled())
        # Netlab driver restarts the container and invokes test_service_after_restart.

    def test_service_after_restart(self):
        self.assert_package(self.a)
        self.assertTrue(enabled(), "enablement must survive reboot")
        self.assertIn(MARKER, CONFIG.read_text())
        self.assert_running(self.a)
        run("systemctl", "stop", UNIT)
        self.assert_inactive()
        self.assertTrue(enabled(), "stop must not silently disable service")
        run("systemctl", "disable", UNIT)
        self.assertFalse(enabled())
        # Failure reporting is distinct from the daemon being responsive.
        with CONFIG.open("a") as file:
            file.write("! " + SECRET + "\n")
            file.write("invalid-openeigrpd-netlab-directive\n")
        result = run("systemctl", "start", UNIT, check=False)
        self.assertNotEqual(result.returncode, 0, "bad config must fail startup")
        self.assertFalse(active())
        self.assertFalse(SOCKET.exists())
        time.sleep(4)  # Restart=on-failure must not turn bad config into success
        self.assertFalse(active(), "invalid configuration unexpectedly recovered")
        self.assertFalse(SOCKET.exists())
        run("systemctl", "stop", UNIT)
        journal = run("journalctl", "-u", UNIT, "-b", "--no-pager", "-n", "150").stdout
        self.assertTrue(any(s in journal.lower() for s in ("invalid", "error", "failed")),
                        "journal must give useful startup diagnostics")
        self.assertNotIn(SECRET, journal, "journal leaked configuration secret")
        CONFIG.write_text(CONFIG.read_text().replace("invalid-openeigrpd-netlab-directive\n", ""))
        # Stopping the service may unload it; a new start reloads it.
        run("systemctl", "start", UNIT)
        self.assert_running(self.a)
        run("systemctl", "stop", UNIT)
        apt("purge", "openeigrpd")
        self.assert_removed(False)

    def test_stopped_upgrade(self):
        assert_clean_guest()
        apt("install", self.old)
        self.assert_package(self.a)
        self.modify_config()
        self.assert_inactive()
        self.assertFalse(enabled())
        apt("install", self.new)
        self.assert_package(self.b)
        self.assert_inactive()
        self.assertFalse(enabled())
        self.assertIn(MARKER, CONFIG.read_text())
        run("/usr/bin/openeigrpd-config", "-t", "-f", CONFIG)
        apt("purge", "openeigrpd")
        self.assert_removed(False)

    def test_running_lifecycle(self):
        assert_clean_guest()
        sentinel = Path("/etc/openeigrp-netlab-unrelated-test")
        self.assertFalse(sentinel.exists() or sentinel.is_symlink())
        sentinel.write_text("unrelated system file\n")
        apt("install", self.old)
        self.assert_package(self.a)
        self.modify_config()
        run("systemctl", "enable", UNIT)
        run("systemctl", "start", UNIT)
        before = self.assert_running(self.a)
        apt("install", self.new)
        self.assert_package(self.b)
        after = self.assert_running(self.b)
        self.assertNotEqual(after, before, "running upgrade must restart old daemon")
        self.assertFalse(Path(f"/proc/{before}/exe").exists(),
                         "previous daemon process survived package upgrade")
        self.assertTrue(enabled())
        self.assertIn(MARKER, CONFIG.read_text())
        self.remove_reinstall_purge(sentinel)

    def test_local_lifecycle(self):
        # No published package yet: exercise non-upgrade lifecycle in a fresh container.
        assert_clean_guest()
        sentinel = Path("/etc/openeigrp-netlab-unrelated-test")
        self.assertFalse(sentinel.exists() or sentinel.is_symlink())
        sentinel.write_text("unrelated system file\n")
        apt("install", self.new)
        self.assert_package(self.b)
        self.modify_config()
        run("systemctl", "enable", UNIT)
        run("systemctl", "start", UNIT)
        self.assert_running(self.b)
        self.remove_reinstall_purge(sentinel)

    def remove_reinstall_purge(self, sentinel):
        run("systemctl", "stop", UNIT)
        self.assert_inactive()
        apt("remove", "openeigrpd")
        self.assert_removed(True)
        self.assertIn(MARKER, CONFIG.read_text())
        apt("install", self.new)
        self.assert_package(self.b)
        self.assertIn(MARKER, CONFIG.read_text(), "reinstall lost retained config")
        self.assert_inactive()
        self.assertFalse(enabled(), "reinstall must not enable service")
        run("/usr/bin/openeigrpd-config", "-t", "-f", CONFIG)
        run("systemctl", "start", UNIT)
        self.assert_running(self.b)
        apt("purge", "openeigrpd")
        self.assert_removed(False)
        self.assertEqual(sentinel.read_text(), "unrelated system file\n")
        sentinel.unlink()

    def test_negative_artifacts(self):
        """Reject bad metadata, corrupt data, and unsatisfied APT dependencies."""
        wrong_arch = "arm64" if self.arch == "amd64" else "amd64"
        wrong_arch_result = run(VERIFY, self.old, self.a, wrong_arch, check=False)
        self.assertNotEqual(wrong_arch_result.returncode, 0)
        self.assertIn("wrong package architecture", wrong_arch_result.stderr)
        wrong_version = self.b if self.a != self.b else self.a + '.invalid'
        wrong_version_result = run(VERIFY, self.old, wrong_version, self.arch, check=False)
        self.assertNotEqual(wrong_version_result.returncode, 0)
        self.assertIn("wrong package version", wrong_version_result.stderr)
        with tempfile.TemporaryDirectory(prefix="openeigrpd-negative-") as tmp:
            tmp = Path(tmp)
            broken = tmp / "broken.deb"
            broken.write_bytes(self.old.read_bytes()[:100])
            self.assertNotEqual(run(VERIFY, broken, self.a, self.arch,
                                    check=False).returncode, 0)
            # Preserve actual artifacts; only a throwaway altered copy is
            # used for the APT dependency-error simulation.
            work = tmp / "package"
            run("dpkg-deb", "-R", self.old, work)
            control = work / "DEBIAN/control"
            content = control.read_text()
            self.assertIn("Depends:", content)
            content = re.sub(r"(?m)^Depends:.*$",
                             "Depends: missing-openeigrpd-netlab-dependency-99999", content)
            control.write_text(content)
            fake = tmp / "missing-dep.deb"
            run("dpkg-deb", "--build", "--root-owner-group", work, fake)
            simulation = apt("-s", "install", fake, check=False)
            self.assertNotEqual(simulation.returncode, 0,
                                "APT falsely accepted unavailable dependency")
            self.assertIn("missing-openeigrpd-netlab-dependency-99999",
                          simulation.stdout + simulation.stderr)
            # This real APT failure is safe inside the disposable container: the
            # resolver must reject the copy before dpkg unpacks any payload.
            failure = apt("install", fake, check=False)
            self.assertNotEqual(failure.returncode, 0,
                                "APT falsely reported a successful install")
            self.assertIn("missing-openeigrpd-netlab-dependency-99999",
                          failure.stdout + failure.stderr)
            self.assertNotEqual(run("dpkg-query", "-W", "-f=${Status}",
                                    "openeigrpd", check=False).stdout.strip(),
                                "install ok installed")
            assert_clean_guest()


PHASES = {
    "preflight": "test_preflight",
    "fresh-service": "test_fresh_service",
    "service-after-restart": "test_service_after_restart",
    "stopped-upgrade": "test_stopped_upgrade",
    "running-lifecycle": "test_running_lifecycle",
    "local-lifecycle": "test_local_lifecycle",
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=PHASES, required=True)
    args = parser.parse_args()
    suite = unittest.TestSuite([SystemdPackageTest(PHASES[args.phase])])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
