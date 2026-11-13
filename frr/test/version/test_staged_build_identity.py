#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Stage real FRR source and assert one generated OpenEIGRP version header."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
YANG = ROOT / "docker/test/fixtures/frr-eigrpd-native.yang"
INSTALL = ROOT / "frr/tools/frr-install.sh"


class StagedBuildIdentityTests(unittest.TestCase):
    def test_frr_stage_reuses_generated_release_contract(self):
        with tempfile.TemporaryDirectory(prefix="oe-frr-stage-version-") as temp:
            frr = Path(temp) / "frr"
            for directory in ("tests", "yang", "vtysh"):
                (frr / directory).mkdir(parents=True)
            (frr / "bootstrap.sh").write_text("#!/bin/sh\n")
            (frr / "yang/frr-eigrpd.yang").write_bytes(YANG.read_bytes())
            (frr / "vtysh/vtysh.c").write_text(
                'router eigrp <(1-65535)|WORD> [vrf NAME]\n'
                'install_element(EIGRP_NODE, &router_eigrp_cmd);\n')
            subprocess.run(["git", "init", "-q", str(frr)], check=True)
            features = (ROOT / "eigrp/code/eigrp_features.h").read_bytes()
            for release, major, minor in (("diivious-0.5.0-dev", 0, 5),
                                          ("diivious-100.10.0-dev", 100, 10)):
                with self.subTest(release=release):
                    result = subprocess.run(
                        ["bash", str(INSTALL), "--no-tests", "--frr-root", str(frr)],
                        env={**os.environ, "EIGRP_BUILD_VERSION": release},
                        text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    stage = frr / "eigrpd"
                    self.assertEqual((stage / "eigrp_features.h").read_bytes(), features)
                    header = (stage / "eigrp_build_version.h").read_text()
                    self.assertIn(f'#define EIGRP_RELEASE "{release}"', header)
                    self.assertIn(f"#define EIGRP_VENDOR_MAJOR {major}", header)
                    self.assertIn(f"#define EIGRP_VENDOR_MINOR {minor}", header)
                    self.assertEqual((frr / "yang/frr-eigrpd.yang").read_bytes(),
                                     YANG.read_bytes())


if __name__ == "__main__":
    unittest.main()
