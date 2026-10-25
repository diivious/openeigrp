#!/usr/bin/env python3
"""Exercise public Make dispatch against fake build and test tools."""

# Copyright (C) 2026 Donnie V. Savage

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


class MakeTargetsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="make-targets-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "Makefile").write_bytes((ROOT / "Makefile").read_bytes())
        (self.root / "tools").mkdir()
        self.log = self.root / "events"
        runner = self.root / "tools/python"
        runner.write_text('#!/bin/sh\nprintf "PY %s\\n" "$*" >> "$LOG_FILE"\n')
        runner.chmod(0o755)
        docker = self.root / "tools/docker.sh"
        docker.write_text('#!/bin/sh\nprintf "DOCKER %s\\n" "$*" >> "$LOG_FILE"\n')
        docker.chmod(0o755)
        for name in ("eigrp/code", "unix/code", "eigrp/test/uut", "eigrp/test/scale", "frr/test/build"):
            path = self.root / name
            path.mkdir(parents=True)
            (path / "Makefile").write_text(
                '.PHONY: all all-scenarios eigrp-uut-node ipv4-basic-core '
                'ipv6-basic-core rtp-core core-topology mesh-topology basic-core md5-mixed-lan md5-security dual-diamond kite-topology active-topology summary-topology peers '
                'internal external line kite peer-routes withdraw mcast linkdown '
                'mixed-tlv churn auth-md5 clean\n'
                'all all-scenarios eigrp-uut-node ipv4-basic-core ipv6-basic-core '
                'rtp-core core-topology mesh-topology basic-core md5-mixed-lan md5-security dual-diamond kite-topology active-topology summary-topology peers internal external '
                'line kite peer-routes withdraw mcast linkdown mixed-tlv churn '
                'auth-md5 clean:\n'
                '\t@printf "CHILD %s %s\\n" "$(CURDIR)" "$@" >> "$(LOG_FILE)"\n'
            )
        (self.root / "eigrp/test/packet").mkdir(parents=True)
        (self.root / "eigrp/test/rtp").mkdir(parents=True)
        (self.root / "eigrp/test/scale").mkdir(parents=True, exist_ok=True)

    def make(self, *args):
        # Recursive make exports command-line selectors through MAKEFLAGS.
        # Keep each fixture invocation independent of the outer test target.
        env = os.environ.copy()
        for name in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEOVERRIDES",
                     "MAKELEVEL", "SOURCE", "STYLE_CHECK_BASE", "SUITE",
                     "SCENARIO", "CASE", "GITHUB", "FRR"):
            env.pop(name, None)
        env.update(LOG_FILE=str(self.log), USER="testdev",
                   PYTHON=str(self.root / "tools/python"))
        result = subprocess.run(["make", "--no-print-directory", *args],
                                cwd=self.root, env=env, text=True, capture_output=True)
        events = self.log.read_text().splitlines() if self.log.exists() else []
        return result, events

    def test_inherited_python_does_not_override_mock_runner(self):
        with patch.dict(os.environ, {"PYTHON": "/not-a-python-executable"}):
            result, events = self.make("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PY tools/style-check.py", events)
        self.assertIn("PY tools/test/test_style_check.py", events)

    def test_inherited_make_selectors_do_not_override_fixture(self):
        inherited = {"MAKEFLAGS": " -- SOURCE=all GITHUB=latest",
                     "MAKEOVERRIDES": "SOURCE=all GITHUB=latest",
                     "SOURCE": "all", "GITHUB": "latest",
                     "STYLE_CHECK_BASE": "wrong-base"}
        with patch.dict(os.environ, inherited):
            result, events = self.make("check")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PY tools/style-check.py", events)
            self.assertNotIn("PY tools/style-check.py --all", events)
            self.log.unlink()
            result, events = self.make("check", "STYLE_CHECK_BASE=HEAD~1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PY tools/style-check.py --base HEAD~1", events)
            self.log.unlink()
            result, events = self.make("docker")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(events[-1], "DOCKER --local testdev-test")

    def test_default_build_is_not_full_gate(self):
        result, events = self.make()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum("CHILD" in e for e in events), 2)
        self.assertFalse(any(e.startswith("PY") for e in events))

    def test_all_sequentially_runs_full_check_uut_and_quick_scale(self):
        result, events = self.make("all")
        self.assertEqual(result.returncode, 0, result.stderr)
        style = next(i for i, e in enumerate(events) if e == "PY tools/style-check.py --all")
        uut = next(i for i, e in enumerate(events) if e == "PY -m pytest eigrp/test")
        scale = next(i for i, e in enumerate(events) if "pacing.py" in e)
        self.assertLess(style, uut)
        self.assertLess(uut, scale)
        self.assertIn("PY tools/test/test_style_check.py", events)

    def test_check_changed_all_and_invalid_selection(self):
        result, events = self.make("check", "STYLE_CHECK_BASE=HEAD~1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PY tools/style-check.py --base HEAD~1", events)
        self.log.unlink()
        result, events = self.make("check", "SOURCE=all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PY tools/style-check.py --all", events)
        result, _ = self.make("check", "SOURCE=anything")
        self.assertNotEqual(result.returncode, 0)

    def test_uut_suite_and_single_scenario(self):
        result, events = self.make("uut", "SUITE=rtp-core")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("rtp-core" in e for e in events))
        self.log.unlink()
        result, events = self.make("uut", "SCENARIO=x/example.yaml")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PY eigrp/test/uut/run_all.py --scenario x/example.yaml", events)
        result, _ = self.make("uut", "SUITE=rtp-core", "SCENARIO=x.yaml")
        self.assertNotEqual(result.returncode, 0)

    def test_scale_suite_and_single_case(self):
        result, events = self.make("scale", "CASE=mcast")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(" mcast" in e for e in events))
        self.log.unlink()
        result, events = self.make("scale", "SUITE=auth")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(" auth-md5" in e for e in events))
        result, _ = self.make("scale", "CASE=invalid")
        self.assertNotEqual(result.returncode, 0)

    def test_docker_local_and_github_and_conflict(self):
        for args, expected in (
            ((), "DOCKER --local testdev-test"),
            (("FRR=../test-frr",), "DOCKER --local testdev-test --frr-root ../test-frr"),
            (("GITHUB=latest",), "DOCKER --build latest"),
            (("GITHUB=1.2.3",), "DOCKER --build 1.2.3"),
        ):
            result, events = self.make("docker", *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(events[-1], expected)
        result, _ = self.make("docker", "FRR=../frr", "GITHUB=1.2.3")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
