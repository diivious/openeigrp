#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise multi-architecture Docker orchestration without requiring Docker."""

import json
import os
import re
import shlex
import textwrap
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
                         "DOCKER_RECORD": str(self.record), "USER": "testdev"})

    def build(self, *args, **extra_env):
        env = {**self.env, **extra_env}
        return subprocess.run([str(BUILDER), "--output", str(self.output), *args],
                              env=env, capture_output=True, text=True)

    def calls(self):
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def test_staged_version_header(self):
        """Run the package builder's actual staging commands and Makefile target."""
        scenario = json.loads((ROOT / "linux/test/uut/package-build-staging.yaml").read_text())
        self.assertEqual(scenario["schema"], "linux-package-staging-uut/v1")
        source = BUILDER.read_text()
        begin = source.index("            mkdir -p /work/eigrp /work/libunix /work/linux /work/tools\n")
        end = source.index("            cd /work\n", begin)
        staging = source[begin:end]
        with tempfile.TemporaryDirectory(prefix="openeigrp-package-staging-") as directory:
            work = Path(directory) / "work"
            staging = re.sub(r"/src(?=/)|/work(?=/)",
                             lambda match: shlex.quote(str(ROOT if match.group() == "/src" else work)),
                             staging)
            result = subprocess.run(["/bin/sh", "-eu", "-c", staging],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            code = work / "eigrp/code"
            for case in scenario["cases"]:
                with self.subTest(case=case["name"]):
                    clean = subprocess.run(["make", "-C", str(code), "clean"],
                                           capture_output=True, text=True)
                    self.assertEqual(clean.returncode, 0, clean.stderr)
                    build = subprocess.run(["make", "-C", str(code),
                                            "eigrp_build_version.h",
                                            "EIGRP_BUILD_VERSION=" + case["build_version"]],
                                           capture_output=True, text=True)
                    self.assertEqual(build.returncode, 0, build.stderr)
                    header = (code / "eigrp_build_version.h").read_text()
                    self.assertIn('#define EIGRP_RELEASE "' + case["build_version"] + '"',
                                  header)
                    self.assertIn('#define EIGRP_VENDOR_MAJOR ' + str(case["major"]),
                                  header)
                    self.assertIn('#define EIGRP_VENDOR_MINOR ' + str(case["minor"]),
                                  header)

    def test_github_ci_package_versions(self):
        """Execute the workflow's version selection and package-build entrypoints."""
        scenario = json.loads((ROOT / "linux/test/uut/ci-package-version.yaml").read_text())
        self.assertEqual(scenario["schema"], "linux-ci-package-version-uut/v1")
        workflow = (ROOT / ".github/workflows/debian-ci.yml").read_text()

        def step_script(name):
            step = workflow.split("      - name: " + name + "\n", 1)[1]
            script = step.split("        run: |\n", 1)[1]
            script = re.split(r"(?m)^      - (?:name:|uses:)", script, maxsplit=1)[0]
            return textwrap.dedent(script)

        select_script = step_script("Select package version")
        build_script = step_script("Build and test native architecture in Debian 12")
        dpkg = self.bin / "dpkg-deb"
        dpkg.write_text("#!/bin/sh\ncase \"$1\" in\n"
                        "  -f) printf 'amd64\\n' ;;\n"
                        "  --info|--contents) exit 0 ;;\n"
                        "  *) exit 1 ;;\nesac\n")
        dpkg.chmod(0o755)

        for case in scenario["cases"]:
            with self.subTest(case=case["name"]), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                repo = work / "openeigrp"
                for path in ("linux/packaging/build-packages.sh", "tools/version.sh",
                             "tools/build-version.sh", "tools/build-count.py"):
                    dest = repo / path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes((ROOT / path).read_bytes())
                    dest.chmod(0o755)
                for cmd in (("init", "-q"), ("branch", "-M", "master"),
                            ("config", "user.name", "CI Version Test"),
                            ("config", "user.email", "ci@example.invalid"),
                            ("add", "."), ("commit", "-qm", "fixture"),
                            ("tag", "rel-0.5"), ("remote", "add", "origin", str(repo))):
                    subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                                   text=True, capture_output=True)
                if case["event"] != "push":
                    # Emulate the synthetic PR merge checkout used by GitHub.
                    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "feature"],
                                   check=True, capture_output=True)
                    (repo / "pr-change").write_text("source\n")
                    for cmd in (("add", "pr-change"), ("commit", "-qm", "PR change"),
                                ("checkout", "-q", "master"),
                                ("merge", "-q", "--no-ff", "feature", "-m", "PR merge")):
                        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                                       capture_output=True, text=True)
                output = work / "version-output"
                env = {**self.env, "GITHUB_EVENT_NAME": case["event"],
                       "EVENT_NAME": case["event"],
                       "GIT_REF": case["ref"],
                       "REQUESTED_VERSION": case["requested_version"],
                       "GITHUB_RUN_NUMBER": "4",
                       "GITHUB_SHA": "aa46880ba3230000000000000000000000000000",
                       "GITHUB_OUTPUT": str(output)}
                selected = subprocess.run(["bash", "-c", select_script], env=env, cwd=repo,
                                          text=True, capture_output=True)
                if "error" in case:
                    self.assertNotEqual(selected.returncode, 0)
                    self.assertIn(case["error"], selected.stderr)
                    continue
                self.assertEqual(selected.returncode, 0, selected.stderr)
                version = output.read_text().strip().removeprefix("version=")
                self.assertEqual(version, case["package_version"])

                binary = subprocess.run([str(ROOT / "tools/build-version.sh"),
                                         "--binary", version], text=True,
                                        capture_output=True)
                self.assertEqual(binary.returncode, 0, binary.stderr)
                self.assertEqual(binary.stdout.strip(), case["build_version"])
                header = work / "eigrp_build_version.h"
                generated = subprocess.run(["python3", str(ROOT / "tools/gen-version.py"),
                                            "--version", case["build_version"],
                                            "--output", str(header)], text=True,
                                           capture_output=True)
                self.assertEqual(generated.returncode, 0, generated.stderr)
                self.assertIn('#define EIGRP_RELEASE "' + case["build_version"] + '"',
                              header.read_text())

                # Execute the actual package wrapper in the same Git fixture.
                self.record.unlink(missing_ok=True)
                built = subprocess.run(["bash", "-c", build_script], cwd=repo,
                                       env={**env, "VERSION": version, "ARCH": "amd64"},
                                       text=True, capture_output=True)
                self.assertEqual(built.returncode, 0, built.stderr)
                self.assertTrue((repo / f"dist/openeigrpd_{version}_amd64.deb").exists())
                call, = self.calls()
                self.assertIn("PACKAGE_VERSION=" + version, call)
                self.assertIn("EIGRP_BUILD_VERSION=" + case["build_version"], call)

    def test_builds_both_isolated_architectures(self):
        result = self.build("--version", "1.2.3-testdev.dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in self.output.glob("*.deb")}, {
            "openeigrpd_1.2.3-testdev.dev_amd64.deb", "openeigrpd_1.2.3-testdev.dev_arm64.deb"})
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
            self.assertIn("EIGRP_BUILD_VERSION=1.2.3-testdev.dev", call)
        self.assertFalse(list(self.output.glob(".openeigrpd-build.*")))

    def test_package_uut_dispatches_linux_pr_smoke(self):
        result = self.build("--version", "0.0.1-uut.dev", "--arch", "amd64", "--uut")
        self.assertEqual(result.returncode, 0, result.stderr)
        call, = self.calls()
        self.assertIn("PACKAGE_UUT=1", call)
        self.assertIn("python3 -m unittest -v linux.test.test_mgnt "
                      "linux.test.test_ipv6_packet", call[-1])
        self.assertLess(call[-1].index("python3 -m unittest -v linux.test.test_mgnt"),
                        call[-1].index("OPENEIGRP_PACKAGE_SYSTEM_TEST=1"))

    def test_qualified_development_identity_is_used_for_uut(self):
        result = self.build("--version", "0.5.1-testdev.dev",
                            "--arch", "amd64", "--uut")
        self.assertEqual(result.returncode, 0, result.stderr)
        call, = self.calls()
        self.assertIn("PACKAGE_VERSION=0.5.1-testdev.dev", call)
        self.assertIn("EIGRP_BUILD_VERSION=0.5.1-testdev.dev", call)
        self.assertIn("PACKAGE_UUT=1", call)
        self.assertFalse(list(self.output.glob("*.deb")))

    def test_qualified_development_identity_is_packaged(self):
        result = self.build("--version", "0.5.1-testdev.dev", "--arch", "amd64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p.name for p in self.output.glob("*.deb")],
                         ["openeigrpd_0.5.1-testdev.dev_amd64.deb"])
        call = self.calls()[0]
        self.assertIn("EIGRP_BUILD_VERSION=0.5.1-testdev.dev", call)

    def test_single_architecture_selection(self):
        result = self.build("--version", "0.1.5-dev.test.dev", "--arch", "arm64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p.name for p in self.output.glob("*.deb")],
                         ["openeigrpd_0.1.5-dev.test.dev_arm64.deb"])
        self.assertEqual(len(self.calls()), 1)

    def test_invalid_input_cannot_invoke_docker(self):
        for args in (("--arch", "i386", "--version", "1.0.1-testdev.dev"),
                     ("--version", "../bad"),
                     ("--version", "1.0.0;touch /tmp/bad"),
                     ("--version",),
                     ("--version", "1.0.1-testdev.dev", "--output")):
            with self.subTest(args=args):
                self.assertNotEqual(self.build(*args).returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_fails_atomically_if_second_architecture_fails(self):
        result = self.build("--version", "1.0.1-testdev.dev", FAIL_PLATFORM="linux/arm64")
        self.assertEqual(result.returncode, 43)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(list(self.output.glob("*.deb")), [])
        self.assertFalse(list(self.output.glob(".openeigrpd-build.*")))

    def test_rejects_missing_artifact(self):
        result = self.build("--version", "1.0.1-testdev.dev", "--arch", "amd64",
                            SKIP_ARTIFACT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not produce", result.stderr)
        self.assertEqual(list(self.output.glob("*.deb")), [])

    def test_make_packages_entrypoint(self):
        result = subprocess.run(["make", "-n", "packages", "VERSION=1.2.3-testdev.dev"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('build-packages.sh --version "$version" --arch', result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
