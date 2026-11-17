#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise fail-fast PR scheduling from the actual GitHub Actions job graph."""

import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
SCENARIO = ROOT / "tools/test/uut/ci-fail-fast.yaml"


def workflow(name):
    # GitHub uses YAML 1.2; BaseLoader preserves the literal 'on' key.
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def dependencies(job):
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else needs


def run_gates(jobs, failed):
    """Run actual needs-based scheduling semantics for required PR stages."""
    status = {"select": "success"}
    remaining = set(jobs) - {"select", "pipeline"}
    while remaining:
        ready = [name for name in sorted(remaining)
                 if all(dep in status for dep in dependencies(jobs[name]))]
        if not ready:
            raise AssertionError("Cyclic or missing GitHub Actions dependency")
        for name in ready:
            deps = dependencies(jobs[name])
            status[name] = ("skipped" if any(status[d] != "success" for d in deps)
                            else "failure" if name == failed else "success")
            remaining.remove(name)
    return status


class CIFailFastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = workflow("core.yml")
        cls.scenario = json.loads(SCENARIO.read_text())
        assert cls.scenario["schema"] == "ci-fail-fast-uut/v1"

    def test_required_gates_and_pr_artifacts_have_strict_dependencies(self):
        jobs = self.core["jobs"]
        prior = "select"
        for name in self.scenario["required_order"]:
            with self.subTest(gate=name):
                self.assertEqual(dependencies(jobs[name]), [prior])
                prior = name
        for name in self.scenario["artifact_jobs"]:
            with self.subTest(artifact=name):
                self.assertEqual(dependencies(jobs[name]), [prior])
                self.assertEqual(jobs[name]["if"],
                                 "github.event_name == 'pull_request'")
                self.assertEqual(jobs[name]["uses"],
                                 f"./.github/workflows/{name}.yml")
        self.assertEqual(set(dependencies(jobs["pipeline"])),
                         set(jobs) - {"pipeline"})
        self.assertIn("always()", jobs["pipeline"]["if"])

    def test_failure_prevents_expensive_builds(self):
        jobs = self.core["jobs"]
        for case in self.scenario["cases"]:
            with self.subTest(case=case["name"]):
                status = run_gates(jobs, case["fail"])
                self.assertEqual(status["style"],
                                 "failure" if case["fail"] == "style"
                                 else "success")
                for name in self.scenario["artifact_jobs"]:
                    self.assertEqual(status[name],
                                     "skipped" if case["fail"] else "success")
                if case["fail"]:
                    self.assertEqual(status[case["fail"]], "failure")
                    chain = self.scenario["required_order"]
                    for later in chain[chain.index(case["fail"]) + 1:]:
                        self.assertEqual(status[later], "skipped")

    def test_release_workflows_do_not_start_on_independent_pr_events(self):
        for name in self.scenario["artifact_jobs"]:
            with self.subTest(workflow=name):
                artifact = workflow(f"{name}.yml")
                events = artifact["on"]
                self.assertIn("workflow_call", events)
                self.assertNotIn("pull_request", events)
                self.assertIn("workflow_dispatch", events)
                self.assertEqual(events["push"]["tags"], ["rel-*"])
                jobs = artifact["jobs"]
                if name == "docker":
                    self.assertEqual(len(jobs["build"]["strategy"]["matrix"]["include"]), 2)
                    self.assertEqual(dependencies(jobs["publish"]), ["build"])
                else:
                    self.assertEqual(len(jobs["packages"]["strategy"]["matrix"]["include"]), 2)
                    self.assertEqual(set(dependencies(jobs["release"])),
                                     {"packages", "regression"})

    def test_required_check_names_and_build_documentation(self):
        jobs = self.core["jobs"]
        self.assertEqual(jobs["macos"]["name"], "macos-latest / make test")
        self.assertEqual(jobs["ubuntu"]["name"], "ubuntu-latest / make test")
        self.assertEqual(jobs["fast"]["name"], "Fast Gate")
        diagram = ROOT / "specs/images/ci-fail-fast.svg"
        ET.parse(diagram)
        doc = (ROOT / "specs/testing.md").read_text()
        self.assertIn("![Pull request CI fail-fast pipeline](images/ci-fail-fast.svg)", doc)
        self.assertIn("`PR merge gate`", doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
