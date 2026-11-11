#!/usr/bin/env python3
"""Execute the shared test log runner across working directories and in parallel."""

# Copyright (C) 2026 Donnie V. Savage

import concurrent.futures
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools/test-log.py"


class TestLogResults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="test-log-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "openeigrp"
        (self.root / "tools").mkdir(parents=True)
        shutil.copy2(RUNNER, self.root / "tools/test-log.py")
        (self.root / "external").mkdir()

    def execute(self, cwd, code, group="uut/eigrp"):
        return subprocess.run(
            [sys.executable, str(self.root / "tools/test-log.py"), group, "--",
             sys.executable, "-c", code], cwd=cwd, capture_output=True,
            text=True)

    def test_concurrent_runs_have_distinct_evidence(self):
        commands = [(self.root, "print('first')"),
                    (self.root / "external", "print('second')")]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda args: self.execute(*args), commands))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        reports = list((self.root / "logs/uut/eigrp").glob("run-*/result.json"))
        self.assertEqual(len(reports), 2)
        self.assertEqual({json.loads(p.read_text())["result"] for p in reports},
                         {"PASS"})
        self.assertEqual({p.parent.joinpath("output.log").read_text().strip()
                          for p in reports}, {"first", "second"})

    def test_failure_status_is_retained(self):
        result = self.execute(self.root, "import sys; print('failed'); sys.exit(7)",
                              "linux")
        self.assertEqual(result.returncode, 7)
        report, = (self.root / "logs/linux").glob("run-*/result.json")
        self.assertEqual(json.loads(report.read_text())["returncode"], 7)
        self.assertEqual(json.loads(report.read_text())["result"], "FAIL")
        self.assertIn("failed", report.with_name("output.log").read_text())

    def test_reject_unknown_group_without_writing_outside_logs(self):
        result = self.execute(self.root, "print('x')", "../outside")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "outside").exists())


if __name__ == "__main__":
    unittest.main()
