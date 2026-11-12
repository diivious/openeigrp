#!/usr/bin/env python3
# Copyright (C) 2026 Donnie V. Savage
"""Exercise named YAML UUT Makefile selectors against real scenario files."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
UUT = ROOT / "eigrp/test/uut"

GROUPS = {
    "md5-mixed-lan": {"auth-topology/md5-mixed-lan.yaml"},
    "md5-security": {
        "auth-topology/md5-duplicate.yaml", "auth-topology/md5-lost-ack.yaml",
        "auth-topology/md5-mixed-lan.yaml",
    },
    "dual-diamond": {"dual-diamond/feasible-successor.yaml"},
    "kite-topology": {
        "dual-kite/a-destination-disappears.yaml", "dual-kite/b-r1-r3-failure.yaml",
        "dual-kite/c-r2-r3-failure.yaml", "dual-kite/d-sequential-recovery.yaml",
        "dual-kite/query-only-retirement.yaml", "dual-kite/query-route-recovery.yaml",
    },
    "core-topology": {
        "core-topology/square.yaml", "core-topology/square-destination-loss.yaml",
        "core-topology/square-r1-isolation.yaml", "core-topology/cross.yaml",
        "core-topology/cross-destination-loss.yaml",
        "core-topology/cross-local-successor-loss.yaml",
        "core-topology/cross-r1-isolation.yaml",
        "core-topology/cross-retained-alternate-loss.yaml",
    },
    "mesh-topology": {
        "mesh-topology/k6-a-single-link.yaml",
        "mesh-topology/k6-b-concurrent-multiprefix.yaml",
        "mesh-topology/k6-c-simultaneous-double-link.yaml",
        "mesh-topology/k6-d-failure-during-active.yaml",
        "mesh-topology/k6-e-query-storm-watch.yaml",
    },
    "active-topology": {
        "active-topology/sia-failure.yaml", "active-topology/sia-positive.yaml",
    },
    "summary-topology": {
        "summary-topology/query-boundary.yaml",
        "summary-topology/metric-selection.yaml",
        "summary-topology/static-metric.yaml",
    },
}


class UutMakeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="uut-make-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "openeigrp"
        self.uut = self.root / "eigrp/test/uut"
        self.uut.mkdir(parents=True)
        shutil.copy2(UUT / "Makefile", self.uut / "Makefile")
        for group in {name.split("/")[0] for paths in GROUPS.values() for name in paths}:
            shutil.copytree(UUT / group, self.uut / group)

        # Provide up-to-date build outputs so the real Make recipes run without
        # compiling the portable core in this dispatch-only regression.
        src = self.root / "libunix/code"
        src.mkdir(parents=True)
        for name in ("eigrp_unix_sys.c", "eigrp_unix_format.c",
                     "eigrp_unix_keychain.c", "eigrp_uut_interface.c",
                     "eigrp_uut_rib.c", "eigrp_uut_segment.c",
                     "eigrp_uut_wire.c", "eigrp_uut_policy.c"):
            (src / name).touch()
        (self.root / "eigrp/code").mkdir(parents=True)
        for name in ("libeigrp_unix.a", "libeigrp_uut.a"):
            (src / name).touch()
        (self.root / "eigrp/code/libeigrp-core.a").touch()
        (self.uut / "node.c").touch()
        (self.uut / "eigrp-uut-node").touch()

        self.log = self.root / "calls.txt"
        self.wrapper = self.root / "python"
        self.wrapper.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "[ \"$#\" -eq 7 ] && [ \"$2\" = uut/eigrp ] && [ \"$3\" = -- ] && "
            "[ \"$5\" = run.py ] || exit 12\n"
            "grep -Fq 'schema: eigrp-uut-topology/v1' \"$6\" || exit 13\n"
            "grep -Fq 'schema: eigrp-uut-test/v1' \"$7\" || exit 14\n"
            "printf '%s\\n' \"$7\" >> \"$LOG_FILE\"\n")
        self.wrapper.chmod(0o755)

    def make(self, target):
        result = subprocess.run(["make", "--no-print-directory", "-s", "-C", str(self.uut),
                                 target, f"PYTHON={self.wrapper}"],
                                text=True, capture_output=True,
                                env={**os.environ, "MAKEFLAGS": "", "LOG_FILE": str(self.log)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_groups_select_existing_scenarios(self):
        for target, expected in GROUPS.items():
            with self.subTest(target=target):
                if self.log.exists():
                    self.log.unlink()
                self.make(target)
                selected = self.log.read_text().splitlines()
                self.assertEqual(len(selected), len(expected))
                self.assertEqual(set(selected), expected)

    def test_clean_removes_macos_debug_bundle(self):
        bundle = self.uut / "eigrp-uut-node.dSYM"
        bundle.mkdir()
        (bundle / "Contents").touch()
        self.make("clean")
        self.assertFalse(bundle.exists())
        self.assertFalse((self.uut / "eigrp-uut-node").exists())


if __name__ == "__main__":
    unittest.main()
