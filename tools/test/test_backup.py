#!/usr/bin/env python3
"""Check that repository backups omit local test evidence and build outputs."""

# Copyright (C) 2026 Donnie V. Savage

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]


class BackupCleanupTest(unittest.TestCase):
    def test_results_and_compiler_outputs_are_not_archived(self):
        with tempfile.TemporaryDirectory(prefix="backup-results-") as temp:
            work = Path(temp)
            repo = work / "openeigrp"
            (repo / "tools").mkdir(parents=True)
            shutil.copy2(ROOT / "tools/backup.sh", repo / "tools/backup.sh")
            source = repo / "linux/code/openeigrpd.c"
            source.parent.mkdir(parents=True)
            source.write_text("/* source */\n")
            for name in ("linux/code/openeigrpd", "eigrp/code/libeigrp-core.a",
                         "logs/uut/eigrp/run-one/output.log",
                         "logs/netlab/uut-one/capture.pcap"):
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("generated\n")
            output = work / "backup.zip"
            run = subprocess.run(["bash", str(repo / "tools/backup.sh"),
                                  "--zip", str(output)], cwd=work,
                                 text=True, capture_output=True,
                                 env={**os.environ, "HOME": str(work)})
            self.assertEqual(run.returncode, 0, run.stderr)
            with ZipFile(output) as archive:
                paths = archive.namelist()
                self.assertIn("openeigrp/linux/code/openeigrpd.c", paths)
                self.assertFalse(any("/logs/" in path for path in paths))
                self.assertFalse(any(path.endswith(".a") for path in paths))
                self.assertNotIn("openeigrp/linux/code/openeigrpd", paths)
            # Snapshot cleanup must not modify the developer's source checkout.
            self.assertTrue((repo / "logs/netlab/uut-one/capture.pcap").exists())


if __name__ == "__main__":
    unittest.main()
