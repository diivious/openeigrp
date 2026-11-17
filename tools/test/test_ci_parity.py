#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise CI and pre-commit target parity against observable Make invocations."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "tools/test/uut/ci-precommit.yaml"


class CIPreCommitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = json.loads(SCENARIO.read_text())
        assert cls.scenario["schema"] == "ci-precommit-parity-uut/v1"

    def harness(self):
        # Use the existing executable Makefile fixture, not a source-only test.
        from tools.test.test_make_targets import MakeTargetsTest

        harness = MakeTargetsTest("test_help_lists_every_public_target")
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        return harness

    def check_smoke(self, events):
        lines = [line for line in events if line.startswith("PY ")]
        for path in self.scenario["pytest"]:
            self.assertTrue(any(path in line for line in lines), path)
        for path in self.scenario["scenarios"]:
            self.assertTrue(any("run_all.py --scenario " + path in line
                                for line in lines), path)
        self.assertEqual(sum("run_all.py --scenario" in line for line in lines),
                         len(self.scenario["scenarios"]))

    def test_executable_ci_gate_matrix(self):
        for case in self.scenario["cases"]:
            with self.subTest(case=case["name"]):
                harness = self.harness()
                result, events = harness.make(case["target"],
                                              "HOST_OS=" + case["host"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.check_smoke(events)
                package_calls = [event for event in events
                                 if event.startswith("PACKAGES ")]
                docker_calls = [event for event in events
                                if event.startswith("DOCKER ")]
                if case["docker"]:
                    self.assertEqual(len(docker_calls), 4)
                    self.assertTrue(all("--pinned-frr" in call for call in docker_calls
                                        if call.startswith("DOCKER --local")))
                    self.assertEqual(len(package_calls), 2)
                    self.assertIn("PACKAGES --version " + case["local_version"]
                                  + " --arch all", package_calls)
                    self.assertIn("PACKAGES --version " + case["local_version"]
                                  + " --arch all --uut", package_calls)
                else:
                    self.assertEqual((package_calls, docker_calls), ([], []))
                self.assertEqual(any("-m pytest -q linux/test" in event
                                     for event in events), case["linux_native"])

    def test_github_fast_gate_pins_identity_before_separate_test_step(self):
        workflow = (ROOT / ".github/workflows/core.yml").read_text()
        setup = workflow.index("      - name: Pin CI build identity across workflow steps")
        build = workflow.index("      - name: Build native Linux daemon and portable sources")
        tests = workflow.index("      - name: Run native Linux management and IPv6 packet regressions")
        self.assertLess(setup, build)
        self.assertLess(build, tests)
        self.assertIn("EIGRP_BUILD_VERSION=%s", workflow)

    def test_smoke_failure_stops_before_docker_build(self):
        harness = self.harness()
        harness._script("tools/python", """
printf 'PY %s\\n' "$*" >> "$LOG_FILE"
case "$*" in
  *'pytest -q eigrp/test/packet/pacing.py'*) exit 17 ;;
esac
""")
        result, events = harness.make("pre-commit", "HOST_OS=Darwin")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("pytest -q eigrp/test/packet/pacing.py" in event
                            for event in events))
        self.assertFalse(any(event.startswith(("DOCKER --local", "PACKAGES "))
                             for event in events))

    def test_github_core_uses_same_executable_gate(self):
        workflow = (ROOT / ".github/workflows/core.yml").read_text()
        self.assertGreaterEqual(workflow.count("run: make _ci-smoke"), 2)
        self.assertIn('if [[ "$RUNNER_OS" == Linux ]]; then', workflow)
        self.assertIn("make all", workflow)
        self.assertIn("make _ci-core", workflow)
        # The common target was exercised above on both host types.

    def test_package_uut_runs_native_pr_regressions(self):
        source = (ROOT / "linux/packaging/build-packages.sh").read_text()
        command = "python3 -m unittest -v " + " ".join(self.scenario["native"])
        self.assertIn(command, source)
        uut = source.index('if [ "$PACKAGE_UUT" = 1 ]; then',
                           source.index("            cd /work\n"))
        native = source.index(command, uut)
        package = source.index("OPENEIGRP_PACKAGE_SYSTEM_TEST=1", uut)
        self.assertLess(native, package)

    def test_ci_artifact_workflows_are_called_only_after_gates(self):
        # PR builds must call read-only reusable builders after every gate;
        # release publication stays in separately triggered workflows.
        import yaml

        root = ROOT / ".github/workflows"
        core = yaml.load((root / "core.yml").read_text(), Loader=yaml.BaseLoader)
        for name in ("docker", "debian"):
            with self.subTest(workflow=name):
                path = root / (name + "-ci.yml")
                artifact = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
                self.assertIn("workflow_call", artifact["on"])
                self.assertNotIn("pull_request", artifact["on"])
                self.assertEqual(core["jobs"][name]["needs"], ["ubuntu"])
                self.assertEqual(core["jobs"][name]["uses"],
                                 "./.github/workflows/" + name + "-ci.yml")
                self.assertNotIn("push", artifact["on"])
                self.assertEqual(artifact["permissions"], {"contents": "read"})
                self.assertEqual(core["jobs"][name]["permissions"],
                                 {"contents": "read"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
