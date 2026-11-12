#!/usr/bin/env python3
"""Execute integration tool dispatch from outside the repository root."""

# Copyright (C) 2026 Donnie V. Savage

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class IntegrationToolDispatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="integration-tools-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "openeigrp"
        self.tools = self.root / "frr/tools"
        self.tools.mkdir(parents=True)
        for name in ("docker.sh", "docker-test.sh", "docker-verify.sh"):
            shutil.copy2(ROOT / "frr/tools" / name, self.tools / name)
        # Integration helpers are replaced, not the public dispatch being tested.
        for name, label in (("docker-test.sh", "TEST"),
                            ("docker-verify.sh", "VERIFY")):
            helper = self.tools / name
            helper.write_text(f"#!/bin/sh\nprintf '{label}:%s\\n' \"$1\"\n")
            helper.chmod(0o755)
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text("#!/bin/sh\n[ \"$1\" = image ] && "
                          "[ \"$2\" = inspect ]\n")
        docker.chmod(0o755)

    def test_dispatch_works_from_unrelated_working_directory(self):
        for mode, expected in (("--test", "TEST"), ("--verify", "VERIFY")):
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.tools / "docker.sh"), mode,
                                         "openeigrp:fixture"], cwd=self.bin,
                                        env={**os.environ,
                                             "PATH": str(self.bin) + os.pathsep + os.environ["PATH"]},
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"{expected}:openeigrp:fixture", result.stdout)

    def test_missing_image_fails_without_invoking_helpers(self):
        docker = self.bin / "docker"
        docker.write_text("#!/bin/sh\nexit 1\n")
        result = subprocess.run([str(self.tools / "docker.sh"), "--verify",
                                 "openeigrp:absent"], cwd=self.bin,
                                env={**os.environ,
                                     "PATH": str(self.bin) + os.pathsep + os.environ["PATH"]},
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("image not found", result.stderr)
        self.assertNotIn("VERIFY:", result.stdout)


if __name__ == "__main__":
    unittest.main()
