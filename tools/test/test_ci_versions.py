#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Execute the CI version selectors against one immutable release checkout."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "tools/test/uut/ci-build-version.yaml"


class CIVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="eigrp-ci-version-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        for relative in ("tools/version.sh", "tools/build-count.py",
                         "tools/build-version.sh", "tools/gen-version.py",
                         "eigrp/code/Makefile"):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, path)
        self.git("init", "-q")
        self.git("branch", "-M", "master")
        self.git("config", "user.name", "CI Version Test")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.git("tag", "-a", "rel-0.5", "-m", "baseline")
        # GitHub PR checkout is a synthetic merge commit: HEAD^1 is master.
        self.git("checkout", "-qb", "feature")
        (self.repo / "feature").write_text("PR code\n")
        self.git("add", "feature")
        self.git("commit", "-qm", "feature")
        self.git("checkout", "-q", "master")
        self.git("merge", "-q", "--no-ff", "feature", "-m", "PR merge")
        self.git("remote", "add", "origin", str(self.repo))
        self.cases = json.loads(SCENARIO.read_text())
        self.assertEqual(self.cases["schema"], "eigrp-ci-build-version-uut/v1")

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    @staticmethod
    def step(workflow, title):
        source = (ROOT / ".github/workflows" / workflow).read_text()
        block = source.split("      - name: " + title + "\n", 1)[1]
        script = block.split("        run: |\n", 1)[1]
        script = script.split("\n      - ", 1)[0]
        return textwrap.dedent(script)

    def run_selector(self, workflow, title, case, number):
        output = self.repo / (workflow + ".output")
        output.unlink(missing_ok=True)
        env = {**os.environ,
               "GITHUB_EVENT_NAME": case["event"],
               "EVENT_NAME": case["event"], "GIT_REF": case["ref"],
               "GIT_SHA": self.git("rev-parse", "HEAD"),
               "GITHUB_RUN_NUMBER": str(number), "PR_NUMBER": "27",
               "REQUESTED_VERSION": "", "MANUAL_TAG": "",
               "GITHUB_OUTPUT": str(output)}
        result = subprocess.run(["bash", "-c", self.step(workflow, title)],
                                cwd=self.repo, env=env, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    def test_architectures_and_run_numbers_share_ci_identity(self):
        for case in self.cases["cases"]:
            with self.subTest(case=case["name"]):
                for number in (1, 999999):
                    package = self.run_selector("debian.yml", "Select package version",
                                                case, number)
                    docker = self.run_selector("docker.yml", "Select immutable source refs",
                                               case, number)
                    self.assertEqual(package["version"], case["version"])
                    self.assertEqual(docker["version"], case["version"])
                    self.assertEqual(docker["release"], case["release"])
                # Repeated CI selectors must not consume the local counter.
                current = subprocess.run([str(self.repo / "tools/version.sh"),
                                          "--current", "--repo", str(self.repo)],
                                         capture_output=True, text=True)
                self.assertNotEqual(current.returncode, 0)

    def test_ci_header_and_direct_core_make_use_zero(self):
        # Reproduce both a standalone CI step and a local pre-commit test
        # runner, where recursive Make command-line assignments leak through
        # MAKEFLAGS even after EIGRP_BUILD_VERSION is removed from the environment.
        for case in self.cases["direct_make_cases"]:
            with self.subTest(case=case["name"]):
                env = {**os.environ, **case["inherited"], "GITHUB_ACTIONS": "true"}
                for variable in ("EIGRP_BUILD_VERSION", "MAKEFLAGS", "MFLAGS",
                                 "GNUMAKEFLAGS", "MAKEOVERRIDES"):
                    env.pop(variable, None)
                build = subprocess.run(["make", "-C", str(self.repo / "eigrp/code"),
                                        "eigrp_build_version.h"],
                                       env=env, capture_output=True, text=True,
                                       timeout=30)
                self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                header = (self.repo / "eigrp/code/eigrp_build_version.h").read_text()
                self.assertIn('#define EIGRP_RELEASE "0.5.0-merge.dev"', header)
                self.assertIn('#define EIGRP_VENDOR_VERSION "0.5"', header)
                current = subprocess.run([str(self.repo / "tools/version.sh"),
                                          "--current", "--repo", str(self.repo)],
                                         capture_output=True, text=True)
                self.assertNotEqual(current.returncode, 0)


if __name__ == "__main__":
    unittest.main()
