#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable .deb tests. Optional root-gated dpkg lifecycle test is intrusive."""

import json
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
VERIFIER = ROOT / "linux/packaging/verify-deb.sh"
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
    if (argc == 2 && strcmp(argv[1], "-V") == 0) {
        printf("openeigrpd %s\n", FIXTURE_VERSION);
        return 0;
    }
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
        cls.source = source
        cls.daemons = {}
        for version in ("0.0.1", "0.0.2"):
            daemon = cls.work / ("fixture-openeigrpd-" + version)
            run("cc", "-O2", "-Wall", "-Wextra", "-Werror",
                '-DFIXTURE_VERSION="' + version + '"', source,
                "-o", daemon)
            cls.daemons[version] = daemon
        cls.daemon = cls.daemons["0.0.1"]
        cls.arch = run("dpkg-architecture", "-qDEB_HOST_ARCH").stdout.strip()
        assert cls.arch in ("amd64", "arm64"), cls.arch
        cls.other_arch = "arm64" if cls.arch == "amd64" else "amd64"
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
    def fixture_for(cls, version):
        daemon = cls.daemons.get(version)
        if daemon is None:
            daemon = cls.work / ("fixture-openeigrpd-" + version)
            run("cc", "-O2", "-Wall", "-Wextra", "-Werror",
                '-DFIXTURE_VERSION="' + version + '"', cls.source,
                "-o", daemon)
            cls.daemons[version] = daemon
        return daemon

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def build(cls, version):
        result = run(BUILDER, "--version", version, "--daemon", cls.fixture_for(version),
                     "--output", cls.out)
        archive = Path(result.stdout.strip().splitlines()[-1])
        assert archive.is_file(), result.stdout
        return archive

    def test_version_identity_uut(self):
        # Exercise the real .deb builder and packaged CLI for version changes.
        # JSON is a YAML 1.2 subset. Keep the matrix readable by the stdlib:
        # minimal Debian package builders only install python3, not PyYAML.
        scenario = json.loads((ROOT / "linux/test/uut/package-version.yaml").read_text())
        self.assertEqual(scenario["schema"], "linux-package-version-uut/v1")
        for case in scenario["cases"]:
            with self.subTest(case=case["name"]):
                version = case["package_version"]
                daemon = self.fixture_for(case["daemon_version"])
                result = run(BUILDER, "--version", version, "--daemon", daemon,
                             "--output", self.out, check=False)
                package = self.out / f"openeigrpd_{version}_{self.arch}.deb"
                if case["result"] == "daemon-rejected":
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("daemon version mismatch", result.stderr)
                    self.assertFalse(package.exists(),
                                     "rejected version must not publish a .deb")
                    continue
                self.assertEqual(case["result"], "build")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(package.is_file())
                run(VERIFIER, package, version, self.arch)
                dest = self.work / ("uut-" + case["name"])
                dest.mkdir()
                run("dpkg-deb", "-x", package, dest)
                client = dest / "usr/bin/oectl"
                identity = json.loads(run(client, "--json", "version").stdout)
                self.assertEqual(identity["kind"], "integration")
                self.assertEqual(identity["build"], case["expected_build"])
                human = run(client, "version").stdout
                self.assertIn("Host Version:", human)
                self.assertIn("  Integration build    : " + case["expected_build"],
                              human)

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
        self.assertIn(f"Architecture: {self.arch}", info)
        self.assertEqual(self.first.name, f"openeigrpd_0.0.1_{self.arch}.deb")
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

    def test_completed_package_verification_rejects_unsafe_artifacts(self):
        run(VERIFIER, self.first, "0.0.1", self.arch)
        for version, arch, error in (
                ("0.0.2", self.arch, "wrong package version"),
                ("0.0.1", self.other_arch, "wrong package architecture")):
            with self.subTest(version=version, arch=arch):
                result = run(VERIFIER, self.first, version, arch, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
        with tempfile.TemporaryDirectory(dir=self.work) as root:
            root = Path(root)
            bad = root / "truncated.deb"
            bad.write_bytes(b"not a Debian package")
            result = run(VERIFIER, bad, "0.0.1", self.arch, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid package metadata", result.stderr)

            defects = {
                "missing-cli": "missing or unsafe package entry: usr/bin/oectl",
                "exposed-config": "missing or unsafe package entry: etc/openeigrpd.conf",
                "setuid-daemon": "missing or unsafe package entry: usr/sbin/openeigrpd",
                "missing-conffile": "configuration is not a dpkg conffile",
                "external-dependency": "external routing framework or UUT dependency",
                "unexpected-file": "unexpected or unsafe package contents",
            }
            for defect, error in defects.items():
                with self.subTest(defect=defect):
                    package_dir = root / "package"
                    if package_dir.exists():
                        shutil.rmtree(package_dir)
                    shutil.copytree(self.dest, package_dir)
                    shutil.copytree(self.ctrl, package_dir / "DEBIAN")
                    control = package_dir / "DEBIAN"
                    if defect == "missing-cli":
                        (package_dir / "usr/bin/oectl").unlink()
                    elif defect == "exposed-config":
                        (package_dir / "etc/openeigrpd.conf").chmod(0o644)
                    elif defect == "setuid-daemon":
                        (package_dir / "usr/sbin/openeigrpd").chmod(0o4755)
                    elif defect == "missing-conffile":
                        (control / "conffiles").unlink()
                    elif defect == "external-dependency":
                        path = control / "control"
                        path.write_text(path.read_text().replace(
                            "Depends: ", "Depends: libfrr9, "))
                    elif defect == "unexpected-file":
                        (package_dir / "usr/bin/unexpected").write_text("extra")
                    bad = root / (defect + ".deb")
                    run("dpkg-deb", "--build", "--root-owner-group",
                        package_dir, bad)
                    result = run(VERIFIER, bad, "0.0.1", self.arch,
                                 check=False)
                    self.assertNotEqual(result.returncode, 0,
                                        f"{defect} accepted: {result.stdout}")
                    self.assertIn(error, result.stderr)

    def test_restrictive_umask_creates_traversable_package(self):
        # A 077 administrator umask previously made DEBIAN mode 0700,
        # causing dpkg-deb to refuse to build the package altogether.
        command = (f'umask 077; exec "{BUILDER}" --version 0.0.9 '
                   f'--daemon "{self.fixture_for("0.0.9")}" --output "{self.out}"')
        result = run("sh", "-c", command)
        package = Path(result.stdout.strip().splitlines()[-1])
        run(VERIFIER, package, "0.0.9", self.arch)
        contents = run("dpkg-deb", "--contents", package).stdout
        self.assertIn("drwxr-xr-x root/root", contents)

    def test_setgid_parent_does_not_taint_archive(self):
        # Shared/staged checkouts can propagate setgid onto new directories.
        # dpkg rejects a setgid DEBIAN even though it is traversable.
        output = self.work / "setgid-output"
        output.mkdir()
        output.chmod(0o2755)
        result = run(BUILDER, "--version", "0.0.10", "--daemon",
                     self.fixture_for("0.0.10"), "--output", output)
        archive = Path(result.stdout.strip().splitlines()[-1])
        run(VERIFIER, archive, "0.0.10", self.arch)
        entries = run("dpkg-deb", "--contents", archive).stdout
        self.assertNotIn("drwxr-s", entries)

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
