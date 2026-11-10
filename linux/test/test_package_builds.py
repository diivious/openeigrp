#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise multi-architecture Docker orchestration without requiring Docker."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "linux/packaging/build-packages.sh"

FAKE_DOCKER = '''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args[0] == "info":
    sys.exit(0)
assert args[0] == "run", args
with open(os.environ["DOCKER_RECORD"], "a") as handle:
    handle.write(json.dumps(args) + "\\n")
platform = args[args.index("--platform") + 1]
if platform == os.environ.get("FAIL_PLATFORM"):
    sys.exit(43)
env = {}
for i, arg in enumerate(args):
    if arg == "-e":
        key, value = args[i + 1].split("=", 1)
        env[key] = value
mounts = [args[i + 1] for i, arg in enumerate(args) if arg == "--mount"]
output = next(m for m in mounts if ",target=/out" in m)
staging = Path(output.split(",target=/out", 1)[0].split("source=", 1)[1])
if os.environ.get("SKIP_ARTIFACT") != "1":
    (staging / ("openeigrpd_%s_%s.deb" %
        (env["PACKAGE_VERSION"], env["PACKAGE_ARCH"]))).write_bytes(b"test artifact")
'''


class MultiArchitectureBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openeigrp-docker-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        self.record = self.work / "docker.jsonl"
        self.output = self.work / "dist"
        self.env = os.environ.copy()
        self.env.update({"PATH": str(self.bin) + os.pathsep + self.env["PATH"],
                         "DOCKER_RECORD": str(self.record)})

    def build(self, *args, **extra_env):
        env = {**self.env, **extra_env}
        return subprocess.run([str(BUILDER), "--output", str(self.output), *args],
                              env=env, capture_output=True, text=True)

    def calls(self):
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def test_builds_both_isolated_architectures(self):
        result = self.build("--version", "1.2.3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in self.output.glob("*.deb")}, {
            "openeigrpd_1.2.3_amd64.deb", "openeigrpd_1.2.3_arm64.deb"})
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        for call, arch in zip(calls, ("amd64", "arm64")):
            self.assertEqual(call[call.index("--platform") + 1],
                             "linux/" + arch)
            self.assertEqual(call[-4], "debian:12")
            self.assertIn("make linux", call[-1])
            self.assertIn("make -C linux/code clean", call[-1])
            self.assertIn("rm -f linux/code/openeigrpd", call[-1])
            self.assertIn("python3 linux/test/test_packaging.py", call[-1])
            self.assertIn("linux/packaging/build-deb.sh", call[-1])
            self.assertIn("linux/packaging/verify-deb.sh", call[-1])
            self.assertIn("native openeigrpd was not built", call[-1])
            self.assertIn("readonly", " ".join(call))
            self.assertIn("PACKAGE_ARCH=" + arch, call)
        self.assertFalse(list(self.output.glob(".openeigrpd-build.*")))

    def test_single_architecture_selection(self):
        result = self.build("--version", "0.1.0~test+5", "--arch", "arm64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p.name for p in self.output.glob("*.deb")],
                         ["openeigrpd_0.1.0~test+5_arm64.deb"])
        self.assertEqual(len(self.calls()), 1)

    def test_invalid_input_cannot_invoke_docker(self):
        for args in (("--arch", "i386", "--version", "1.0.0"),
                     ("--version", "../bad"),
                     ("--version", "1.0.0;touch /tmp/bad"),
                     ("--version",),
                     ("--version", "1.0.0", "--output")):
            with self.subTest(args=args):
                self.assertNotEqual(self.build(*args).returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_fails_atomically_if_second_architecture_fails(self):
        result = self.build("--version", "1.0.0", FAIL_PLATFORM="linux/arm64")
        self.assertEqual(result.returncode, 43)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(list(self.output.glob("*.deb")), [])
        self.assertFalse(list(self.output.glob(".openeigrpd-build.*")))

    def test_rejects_missing_artifact(self):
        result = self.build("--version", "1.0.0", "--arch", "amd64",
                            SKIP_ARTIFACT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not produce", result.stderr)
        self.assertEqual(list(self.output.glob("*.deb")), [])

    def test_make_packages_entrypoint(self):
        result = subprocess.run(["make", "-n", "packages", "VERSION=1.2.3"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('build-packages.sh --version "1.2.3"', result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
