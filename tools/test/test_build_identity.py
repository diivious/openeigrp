#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Run binary/package conversion and compiled header cases from one YAML UUT."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "tools/test/uut/build-identity.yaml"


class BuildIdentityTest(unittest.TestCase):
    def test_end_to_end_version_matrix(self):
        cases = json.loads(SCENARIO.read_text())
        self.assertEqual(cases["schema"], "eigrp-build-identity-uut/v1")
        with tempfile.TemporaryDirectory(prefix="eigrp-identity-") as temp:
            temp = Path(temp)
            for case in cases["cases"]:
                with self.subTest(case=case["name"]):
                    version = case["version"]
                    package = subprocess.run([str(ROOT / "tools/build-version.sh"),
                                              "--package", version], capture_output=True, text=True)
                    binary = subprocess.run([str(ROOT / "tools/build-version.sh"),
                                             "--binary", version], capture_output=True, text=True)
                    generated = subprocess.run(["python3", str(ROOT / "tools/gen-version.py"),
                                                "--version", version, "--output",
                                                str(temp / "eigrp_build_version.h")],
                                               capture_output=True, text=True)
                    if not case["valid"]:
                        self.assertNotEqual(package.returncode, 0)
                        self.assertNotEqual(generated.returncode, 0)
                        continue
                    for result in (package, binary, generated):
                        self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(package.stdout.strip(), version)
                    self.assertEqual(binary.stdout.strip(), version)
                    self.assertIn(f'#define EIGRP_RELEASE "{version}"',
                                  (temp / "eigrp_build_version.h").read_text())
                    if shutil.which("cc"):
                        probe = temp / "version.c"
                        probe.write_text('''#include <stdio.h>
#include "eigrp_build_version.h"
int main(void) {
  printf("%s|%s|%u|%u\\n", EIGRP_RELEASE, EIGRP_VENDOR_VERSION,
         EIGRP_VENDOR_MAJOR, EIGRP_VENDOR_MINOR);
  return 0;
}
''')
                        compile_result = subprocess.run(["cc", "-I", str(temp), str(probe),
                                                         "-o", str(temp / "version")],
                                                        capture_output=True, text=True)
                        self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
                        actual = subprocess.run([str(temp / "version")], capture_output=True,
                                                text=True, check=True).stdout.strip()
                        self.assertEqual(actual, f'{version}|{case["major"]}.{case["minor"]}'
                                                 f'|{case["major"]}|{case["minor"]}')

    def test_debian_release_ordering(self):
        if not shutil.which("dpkg"):
            self.skipTest("dpkg not installed (Debian ordering verified on Debian CI)")
        comparisons = [
            ("1.100", "1.100.1-JOE.dev"),
            ("1.100.999999-JOE.dev", "1.101"),
            ("1.100.1-JOE.dev", "1.100.2-JOE.dev"),
        ]
        for lower, upper in comparisons:
            with self.subTest(lower=lower, upper=upper):
                result = subprocess.run(["dpkg", "--compare-versions", lower, "lt", upper])
                self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
