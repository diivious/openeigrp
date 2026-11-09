#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable .deb tests. Optional root-gated dpkg lifecycle test is intrusive."""

import os
from pathlib import Path
import pwd
import grp
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "linux/packaging/build-deb.sh"
CODE = ROOT / "linux/code"
FIXTURE = r'''
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static volatile sig_atomic_t running = 1;
static void stop(int sig) { (void)sig; running = 0; }
int main(int argc, char **argv)
{
    FILE *config;
    if (argc != 3 || strcmp(argv[1], "-f") != 0)
        return 2;
    config = fopen(argv[2], "r");
    if (!config)
        return 1;
    fclose(config);
    signal(SIGTERM, stop);
    while (running)
        pause();
    return 0;
}
'''


def run(*args, check=True, **kwargs):
    result = subprocess.run([str(arg) for arg in args], text=True,
                            capture_output=True, **kwargs)
    if check and result.returncode:
        raise AssertionError(f"{args}:\n{result.stdout}\n{result.stderr}")
    return result


@unittest.skipUnless(sys.platform == "linux", "native Linux package test")
@unittest.skipUnless(all(shutil.which(name) for name in (
    "cc", "make", "dpkg", "dpkg-deb", "dpkg-shlibdeps", "dpkg-architecture",
    "readelf")), "native Debian package build dependencies missing")
class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="openeigrpd-package-")
        cls.work = Path(cls.temp.name)
        source = cls.work / "fixture.c"
        source.write_text(FIXTURE)
        cls.daemon = cls.work / "fixture-openeigrpd"
        run("cc", "-O2", "-Wall", "-Wextra", "-Werror", source,
            "-o", cls.daemon)
        cls.out = cls.work / "dist"
        cls.out.mkdir()
        cls.first = cls.build("0.0.1")
        cls.second = cls.build("0.0.2")
        cls.dest = cls.work / "extract"
        cls.ctrl = cls.work / "control"
        cls.dest.mkdir()
        cls.ctrl.mkdir()
        run("dpkg-deb", "-x", cls.first, cls.dest)
        run("dpkg-deb", "-e", cls.first, cls.ctrl)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def build(cls, version):
        result = run(BUILDER, "--version", version, "--daemon", cls.daemon,
                     "--output", cls.out)
        archive = Path(result.stdout.strip().splitlines()[-1])
        assert archive.is_file(), result.stdout
        return archive

    def test_production_build_fails_without_daemon(self):
        # No fallback to a fake daemon, a library, or a bundled UUT broker.
        result = run(BUILDER, "--version", "0.0.3", "--daemon",
                     self.work / "missing", "--output", self.out,
                     check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("real daemon executable missing", result.stderr)
        self.assertEqual(list(self.out.glob("openeigrpd_0.0.3_*.deb")), [])

    def test_reject_non_elf_daemon(self):
        fake = self.work / "script"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        result = run(BUILDER, "--version", "0.0.3", "--daemon", fake,
                     "--output", self.out, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-native or non-ELF", result.stderr)
        # A native ELF linked to a routing suite must also be rejected.
        library = self.work / "libfrr.so"
        library_source = self.work / "library.c"
        library_source.write_text("int frr_marker(void) { return 1; }\n")
        run("cc", "-fPIC", "-shared", "-Wl,-soname,libfrr.so",
            library_source, "-o", library)
        linked_source = self.work / "linked.c"
        linked_source.write_text("extern int frr_marker(void);\n"
                                 "int main(void) { return frr_marker(); }\n")
        linked = self.work / "linked-daemon"
        run("cc", linked_source, "-L", self.work, "-Wl,-rpath,$ORIGIN",
            "-lfrr", "-o", linked)
        result = run(BUILDER, "--version", "0.0.3", "--daemon", linked,
                     "--output", self.out, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("external routing framework", result.stderr)

    def test_reject_bad_version(self):
        result = run(BUILDER, "--version", "../../oops", "--daemon", self.daemon,
                     "--output", self.out, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.out.glob("*oops*")), [])

    def test_archive_manifest_ownership_and_security(self):
        dest = self.dest
        ctrl = self.ctrl
        paths = sorted("/" + str(path.relative_to(dest)) for path in
                       dest.rglob("*") if path.is_file())
        self.assertEqual(paths, [
            "/etc/openeigrpd.conf", "/usr/bin/oectl",
            "/usr/bin/openeigrpd-config", "/usr/lib/systemd/system/openeigrpd.service",
            "/usr/lib/sysusers.d/openeigrpd.conf", "/usr/sbin/openeigrpd",
            "/usr/share/man/man5/openeigrpd.conf.5",
            "/usr/share/man/man8/oectl.8", "/usr/share/man/man8/openeigrpd.8",
        ])
        self.assertEqual((ctrl / "conffiles").read_text().strip(),
                         "/etc/openeigrpd.conf")
        self.assertEqual(stat.S_IMODE((dest / "etc/openeigrpd.conf").stat().st_mode),
                         0o600)
        for name in ("usr/sbin/openeigrpd", "usr/bin/oectl",
                     "usr/bin/openeigrpd-config"):
            path = dest / name
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
            self.assertEqual(path.read_bytes()[:4], b"\x7fELF")
        self.assertNotIn("libeigrp", "\n".join(paths))
        self.assertNotIn("eigrp-wire", "\n".join(paths))
        self.assertNotIn("frr", "\n".join(paths))
        info = run("dpkg-deb", "-f", self.first).stdout
        self.assertIn("Package: openeigrpd", info)
        self.assertIn("Version: 0.0.1", info)
        self.assertIn("Maintainer: Donnie V. Savage <diivious@hotmail.com>", info)
        self.assertIn("systemd", info)
        self.assertIn("libc6", info)
        for unwanted in ("frr", "bird", "uut"):
            self.assertNotIn(f"Depends: {unwanted}", info.lower())
        # dpkg's data archive must preserve root:root without fakeroot.
        contents = run("dpkg-deb", "--contents", self.first).stdout
        self.assertIn("root/root", contents)
        self.assertIn("./etc/openeigrpd.conf", contents)
        self.assertIn("#!/bin/sh", (ctrl / "postinst").read_text())
        self.assertTrue(os.access(ctrl / "postinst", os.X_OK))

    def test_credentials_and_service_boundaries(self):
        dest = self.dest
        service = (dest / "usr/lib/systemd/system/openeigrpd.service").read_text()
        self.assertIn("User=openeigrp", service)
        self.assertIn("Group=openeigrpctl", service)
        self.assertIn("LoadCredential=openeigrpd.conf:/etc/openeigrpd.conf", service)
        self.assertIn("ExecStart=/usr/sbin/openeigrpd -f ${CREDENTIALS_DIRECTORY}/openeigrpd.conf", service)
        self.assertIn("RuntimeDirectory=openeigrpd", service)
        self.assertIn("CAP_NET_RAW CAP_NET_ADMIN", service)
        self.assertIn("NoNewPrivileges=yes", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertNotIn("ExecReload", service)
        postinst = (self.ctrl / "postinst").read_text()
        self.assertNotIn("systemctl enable", postinst)
        self.assertNotIn("deb-systemd-invoke start", postinst)
        self.assertIn("deb-systemd-invoke try-restart", postinst)
        self.assertIn("deb-systemd-invoke stop", (self.ctrl / "prerm").read_text())
        if shutil.which("systemd-analyze"):
            result = run("systemd-analyze", "verify",
                         dest / "usr/lib/systemd/system/openeigrpd.service",
                         check=False)
            # The unit references paths not installed in the build host.
            self.assertNotIn("Failed to parse", result.stderr)
            self.assertNotIn("Unknown key", result.stderr)

    def test_default_config_validates_using_real_parser(self):
        src = ROOT / "linux/packaging/openeigrpd.conf"
        run(CODE / "openeigrpd-config", "-t", "-f", src)

    @unittest.skipUnless(os.geteuid() == 0 and
                         os.getenv("OPENEIGRP_PACKAGE_SYSTEM_TEST") == "1",
                         "set OPENEIGRP_PACKAGE_SYSTEM_TEST=1 as root in a disposable VM")
    def test_real_dpkg_install_upgrade_remove_purge(self):
        # Does not test actual routing. Fixture daemon is NOT a release binary.
        self.assertFalse(Path("/run/systemd/system").exists(),
                         "use separate VM test for systemd service behavior")
        package = run("dpkg-query", "-W", "-f=${Status}", "openeigrpd",
                      check=False)
        self.assertNotEqual(package.returncode, 0, "package already installed")
        config = Path("/etc/openeigrpd.conf")
        self.assertFalse(config.exists(), "do not disturb existing config")
        try:
            run("dpkg", "-i", self.first)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            self.assertEqual(config.stat().st_uid, 0)
            self.assertTrue((Path("/usr/sbin/openeigrpd")).exists())
            self.assertEqual(run("oectl", "version").returncode, 0)
            run("openeigrpd-config", "-t", "-f", config)
            self.assertEqual(pwd.getpwnam("openeigrp").pw_name, "openeigrp")
            self.assertEqual(grp.getgrnam("openeigrpctl").gr_name, "openeigrpctl")
            self.assertFalse(Path("/run/openeigrpd").exists())
            wants = Path("/etc/systemd/system/multi-user.target.wants/openeigrpd.service")
            self.assertFalse(wants.exists() or wants.is_symlink(),
                             "fresh installation must not enable the unit")
            # Enable without starting (systemd is not PID 1 in this container).
            run("systemctl", "--no-reload", "enable", "openeigrpd.service")
            wants = Path("/etc/systemd/system/multi-user.target.wants/openeigrpd.service")
            self.assertTrue(wants.is_symlink())
            with config.open("a") as handle:
                handle.write("! site-specific preserved config\n")
            run("dpkg", "-i", self.second)
            self.assertIn("site-specific preserved config", config.read_text())
            self.assertTrue(wants.is_symlink(), "upgrade must retain enablement")
            self.assertIn("0.0.2", run("dpkg-query", "-W", "-f=${Version}",
                                       "openeigrpd").stdout)
            run("dpkg", "-r", "openeigrpd")
            self.assertTrue(config.exists())
            self.assertFalse(Path("/usr/sbin/openeigrpd").exists())
            self.assertFalse(Path("/usr/lib/systemd/system/openeigrpd.service").exists())
            self.assertFalse(wants.exists() or wants.is_symlink(),
                             "removal must remove enablement link")
            run("dpkg", "-P", "openeigrpd")
            self.assertFalse(config.exists())
        finally:
            run("dpkg", "--purge", "openeigrpd", check=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
