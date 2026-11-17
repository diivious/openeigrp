#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise fail-fast PR scheduling from the actual GitHub Actions job graph."""

import json
import os
import subprocess
import tempfile
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
                                 f"./.github/workflows/{name}-ci.yml")
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

    def test_pr_nested_jobs_cannot_escalate_token_permissions(self):
        # Model the GitHub validation that failed in PR #60. Every job of a
        # called workflow is checked, including an 'if'-guarded publish job.
        for name in self.scenario["artifact_jobs"]:
            caller = self.core["jobs"][name]
            called = workflow(caller["uses"].rsplit("/", 1)[-1])
            granted = caller.get("permissions", self.core["permissions"])
            for job_name, job in called["jobs"].items():
                requested = job.get("permissions", called.get("permissions", granted))
                for scope, access in requested.items():
                    with self.subTest(workflow=name, job=job_name, scope=scope):
                        ceiling = granted.get(scope, "none")
                        order = {"none": 0, "read": 1, "write": 2}
                        self.assertLessEqual(order[access], order[ceiling],
                                             f"{name}/{job_name} requests {scope}: {access} "
                                             f"but PR caller grants {ceiling}")

    def test_release_and_pr_permission_boundaries(self):
        # GitHub validates all jobs in a reusable workflow even when an 'if'
        # would skip its release-publishing job. Only call build-only workflows.
        jobs = self.core["jobs"]
        for case in self.scenario["permission_cases"]:
            name = case["workflow"]
            with self.subTest(workflow=name):
                artifact = workflow(f"{name}-ci.yml")
                release = workflow(f"{name}.yml")
                self.assertEqual(artifact["on"].keys(), {"workflow_call"})
                self.assertEqual(artifact["permissions"], {"contents": "read"})
                self.assertEqual(jobs[name]["permissions"], {"contents": "read"})
                self.assertEqual(jobs[name]["uses"],
                                 f"./.github/workflows/{name}-ci.yml")
                # Calculate the effective token permission ceilings for every
                # nested job, not just the job that executes on a PR.
                caller = jobs[name]["permissions"]
                for nested in artifact["jobs"].values():
                    for scope, permission in nested.get("permissions", artifact["permissions"]).items():
                        self.assertIn(scope, caller)
                        self.assertEqual(permission, "read")
                self.assertEqual(set(artifact["jobs"]), {case["builder_job"]})
                self.assertEqual(len(artifact["jobs"][case["builder_job"]]
                                     ["strategy"]["matrix"]["include"]), 2)
                self.assertNotIn("workflow_call", release["on"])
                self.assertNotIn("pull_request", release["on"])
                self.assertEqual(release["on"]["push"]["tags"], ["rel-*"])
                self.assertEqual(release["jobs"][case["builder_job"]]["uses"],
                                 f"./.github/workflows/{name}-ci.yml")
                self.assertEqual(release["jobs"][case["builder_job"]]
                                 ["permissions"], {"contents": "read"})
                publisher = release["jobs"][case["publish_job"]]
                self.assertEqual(publisher["permissions"],
                                 {case["publish_scope"]: "write",
                                  **({"contents": "read"} if name == "docker" else {})})
                self.assertIn("github.event_name == 'push'", publisher["if"])
                self.assertIn("refs/tags/", publisher["if"])
                self.assertIn(case["builder_job"], dependencies(publisher))
                if name == "debian":
                    self.assertIn("regression", dependencies(publisher))
                    self.assertIn("github.event_name == 'push'",
                                  release["jobs"]["regression"]["if"])

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


    def test_post_merge_ubuntu_can_execute_both_docker_architectures(self):
        # The merge job builds both architectures on one runner. PR builders
        # instead use native amd64 and arm64 runners in their own matrix.
        ubuntu = self.core["jobs"]["ubuntu"]["steps"]
        names = [step.get("name") for step in ubuntu]
        setup = names.index("Enable non-native Docker architectures")
        check = names.index("Verify Docker platform execution")
        build = names.index("Run portable and native regression after merge or manual dispatch")
        self.assertLess(setup, check)
        self.assertLess(check, build)
        self.assertEqual(ubuntu[setup]["uses"], "docker/setup-qemu-action@v3")
        self.assertEqual(ubuntu[setup]["with"]["platforms"], "arm64")
        self.assertEqual(ubuntu[check]["run"], "./tools/check-docker-platforms.sh")
        self.assertEqual(ubuntu[setup]["if"], ubuntu[check]["if"])
        self.assertEqual(ubuntu[check]["if"], ubuntu[build]["if"])
        self.assertNotIn("Enable non-native Docker architectures",
                         [step.get("name") for step in
                          self.core["jobs"]["macos"]["steps"]])

    def test_docker_platform_preflight_executes_both_architectures(self):
        # Execute the real preflight with an isolated Docker CLI fixture.
        for case in self.scenario["docker_platform_cases"]:
            with self.subTest(case=case["name"]):
                with tempfile.TemporaryDirectory(prefix="docker-platform-") as tmp:
                    work = Path(tmp)
                    fake_docker = work / "docker"
                    fake_docker.write_text(
                        '#!/bin/sh\n'
                        'printf "%s\\n" "$*" >> "$MOCK_LOG"\n'
                        'test "$1" = run && test "$2" = --rm && '
                        'test "$3" = --platform && test "$5" = alpine:3.22 && '
                        'test "$6" = uname && test "$7" = -m || exit 99\n'
                        'case "$4" in\n'
                        '  linux/amd64) value=$MOCK_AMD64 ;;\n'
                        '  linux/arm64) value=$MOCK_ARM64 ;;\n'
                        '  *) exit 99 ;;\n'
                        'esac\n'
                        'if test "$value" = exec-error; then\n'
                        '  echo "exec /bin/sh: exec format error" >&2\n'
                        '  exit 255\n'
                        'fi\n'
                        'printf "%s\\n" "$value"\n'
                    )
                    fake_docker.chmod(0o755)
                    log = work / "calls"
                    env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"],
                               MOCK_LOG=str(log), MOCK_AMD64=case["amd64"],
                               MOCK_ARM64=case["arm64"])
                    result = subprocess.run(
                        ["sh", str(ROOT / "tools/check-docker-platforms.sh")],
                        env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode == 0, case["pass"],
                                     result.stdout + result.stderr)
                    self.assertIn(case["message"], result.stdout + result.stderr)
                    calls = log.read_text().splitlines()
                    expected = ["run --rm --platform linux/amd64 alpine:3.22 uname -m"]
                    if case["amd64"] == "x86_64":
                        expected.append("run --rm --platform linux/arm64 alpine:3.22 uname -m")
                    self.assertEqual(calls, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
