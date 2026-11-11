#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise linux/packaging/package.sh and the real Docker build orchestration API."""

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "linux/packaging/package.sh"

FAKE_DOCKER = '''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args == ["info"]:
    sys.exit(0)
assert args[0] == "run", args
with open(os.environ["DOCKER_RECORD"], "a") as record:
    record.write(json.dumps(args) + "\\n")
if os.environ.get("FAIL_DOCKER") == "1":
    sys.exit(43)
env = {}
for i, arg in enumerate(args):
    if arg == "-e":
        key, value = args[i + 1].split("=", 1)
        env[key] = value
mounts = [args[i + 1] for i, arg in enumerate(args) if arg == "--mount"]
staging = next(m for m in mounts if ",target=/out" in m)
staging = Path(staging.split(",target=/out", 1)[0].split("source=", 1)[1])
if env["PACKAGE_UUT"] == "0":
    name = f"openeigrpd_{env['PACKAGE_VERSION']}_{env['PACKAGE_ARCH']}.deb"
    (staging / name).write_bytes(b"fake package for wrapper test")
else:
    (staging / "fixture.deb").write_bytes(b"not for publication")
'''


class PackageCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openeigrp-package-cli-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        self.record = self.work / "docker.jsonl"
        self.output = self.work / "packages"
        self.repo = self.work / "openeigrp"
        (self.repo / "tools").mkdir(parents=True)
        (self.repo / "linux/packaging").mkdir(parents=True)
        for source in (SCRIPT, ROOT / "tools/version.sh",
                       ROOT / "linux/packaging/build-packages.sh"):
            target = self.repo / source.relative_to(ROOT)
            shutil.copy2(source, target)
        self.git("init", "-q")
        self.git("branch", "-M", "master")
        self.git("config", "user.name", "Package Test")
        self.git("config", "user.email", "package@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.git("tag", "-a", "rel-0.5.0", "-m", "0.5")
        self.git("remote", "add", "origin", str(self.repo))
        self.script = self.repo / "linux/packaging/package.sh"
        self.env = {**os.environ, "USER": "diivious", "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "DOCKER_RECORD": str(self.record)}

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                       text=True, capture_output=True)

    def invoke(self, *args, env=None):
        return subprocess.run([str(self.script), *args], env={**self.env, **(env or {})},
                              capture_output=True, text=True)

    def calls(self):
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def test_local_build_and_image_override(self):
        result = self.invoke("--local", "1.2.3", "--platform", "linux/arm64",
                             "--image", "openeigrp:1.2.3", "--builder-image", "ubuntu:24.04", "--output", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p.name for p in self.output.glob("*.deb")],
                         ["openeigrpd_1.2.3_arm64.deb"])
        call, = self.calls()
        self.assertEqual(call[call.index("--platform") + 1], "linux/arm64")
        self.assertEqual(call[-4], "ubuntu:24.04")
        self.assertIn("PACKAGE_UUT=0", call)
        self.assertIn("PACKAGE_VERSION=1.2.3", call)
        self.assertIn("linux/packaging/verify-deb.sh", call[-1])
        self.assertIn("readonly", " ".join(call))

    def test_named_local_tag_uses_valid_debian_version(self):
        result = self.invoke("--local", "diivious-test", "--platform", "linux/amd64",
                             "--image", "openeigrp:diivious-test", "--output", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.output / "openeigrpd_0.5.0-diivious-test_amd64.deb").exists())
        call, = self.calls()
        self.assertIn("PACKAGE_VERSION=0.5.0-diivious-test", call)
        self.assertIn("diivious-test", result.stderr)
        self.assertIn("0.5.0-diivious-test", result.stderr)

    def test_named_local_tag_other_architecture(self):
        result = self.invoke("--local", "dev.alpha", "--platform", "linux/arm64",
                             "--output", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.output / "openeigrpd_0.5.0-dev.alpha_arm64.deb").exists())
        self.assertIn("PACKAGE_VERSION=0.5.0-dev.alpha", self.calls()[0])

    def test_new_master_release_changes_package_version(self):
        (self.repo / "release-note").write_text("0.6\n")
        self.git("add", "release-note")
        self.git("commit", "-qm", "release")
        self.git("tag", "-a", "rel-0.6.0", "-m", "0.6")
        result = self.invoke("--local", "X-Y", "--platform", "linux/amd64",
                             "--image", "openeigrp:X-Y", "--output", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.output / "openeigrpd_0.6.0-X-Y_amd64.deb").exists())
        self.assertIn("PACKAGE_VERSION=0.6.0-X-Y", self.calls()[0])

    def test_invalid_named_local_tag_fails_without_docker(self):
        for tag in ("diivious_test", "dev/slash", "dev:tag", "../bad", "bad space"):
            with self.subTest(tag=tag):
                result = self.invoke("--local", tag, "--output", str(self.output))
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_non_debian_builder_image_reports_actionable_error(self):
        result = self.invoke("--local", "1.2.3", "--builder-image", "openeigrp:diivious-test",
                             "--output", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        call, = self.calls()
        # Execute the real container command in an environment without apt-get.
        # This is equivalent to choosing an Alpine-based build image.
        run = subprocess.run(["/bin/sh", "-euc", call[-1]],
                             env={"PATH": str(self.bin), "PACKAGE_UUT": "0",
                                  "PACKAGE_ARCH": "amd64"},
                             capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("Debian/Ubuntu", run.stderr)
        self.assertIn("--image", run.stderr)

    def test_default_local_version_and_platform(self):
        expected = {"x86_64": "amd64", "amd64": "amd64",
                    "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
        if expected is None:
            self.skipTest("unknown build host architecture")
        result = self.invoke("--local", "--output", str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.output / f"openeigrpd_0.5.0-diivious-test_{expected}.deb").exists())
        self.assertEqual(self.calls()[0][self.calls()[0].index("--platform") + 1],
                         "linux/" + expected)

    def test_uut_executes_dpkg_lifecycle_without_publishing_package(self):
        result = self.invoke("--uut", "--platform", "linux/amd64")
        self.assertEqual(result.returncode, 0, result.stderr)
        call, = self.calls()
        self.assertIn("PACKAGE_UUT=1", call)
        self.assertIn("OPENEIGRP_PACKAGE_SYSTEM_TEST=1", call[-1])
        self.assertIn("python3 linux/test/test_packaging.py", call[-1])
        self.assertIn("apt-get install -y -qq --no-install-recommends systemd", call[-1])
        mounts = [call[i + 1] for i, arg in enumerate(call) if arg == "--mount"]
        dest = next(m for m in mounts if ",target=/out" in m)
        staging = Path(dest.split(",target=/out", 1)[0].split("source=", 1)[1])
        self.assertFalse(staging.exists(), "UUT temporary artifacts must be removed")
        self.assertNotIn("real daemon executable missing", result.stderr)

    def test_invalid_options_rejected_before_docker(self):
        for args in (("--local", "1.0.0", "--platform", "linux/i386"),
                     ("--local", "1.0.0", "--platform"),
                     ("--local", "--image", "bad image"),
                     ("--local", "X-Y", "--image", "openeigrp:wrong"),
                     ("--local", "--image"),
                     ("--local", "--builder-image"),
                     ("--uut", "--output", str(self.output)),
                     ("--uut", "--image", "openeigrp:diivious-test"),
                     ("--uut", "1.0.0"),
                     ("--local", "--bogus"),
                     ("--local", "../bad"),
                     ("--build",),
                     ()):
            with self.subTest(args=args):
                self.assertNotEqual(self.invoke(*args).returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_failure_does_not_publish_package(self):
        result = self.invoke("--local", "1.0.0", "--platform", "linux/amd64",
                             "--output", str(self.output),
                             env={"FAIL_DOCKER": "1"})
        self.assertEqual(result.returncode, 43)
        self.assertFalse(list(self.output.glob("*.deb")))
        self.assertFalse(list(self.output.glob(".openeigrpd-build.*")))

    def test_uut_failure_propagates(self):
        result = self.invoke("--uut", "--platform", "linux/arm64",
                             "--builder-image", "debian:12", env={"FAIL_DOCKER": "1"})
        self.assertEqual(result.returncode, 43)
        self.assertIn("PACKAGE_UUT=1", self.calls()[0])

    def test_help_is_docker_free(self):
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--uut", result.stdout)
        self.assertIn("--local", result.stdout)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
