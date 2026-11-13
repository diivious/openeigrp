#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable tests for the generated core release and Hello vendor bytes."""

from pathlib import Path
import os
import shutil
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
GENERATE = ROOT / "tools/gen-version.py"


class BuildIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="eigrp-build-identity-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def generate(self, version):
        result = subprocess.run(["python3", str(GENERATE), "--version", version,
                                 "--output", str(self.path / "eigrp_build_version.h")],
                                capture_output=True, text=True)
        return result

    def test_versions_and_boundaries(self):
        cases = (
            ("diivious-0.5.0-dev", 0, 5),
            ("diivious-0.6.15-dev", 0, 6),
            ("diivious-0.100.10-dev", 0, 100),
            ("diivious-100.10.0-dev", 100, 10),
            ("0.5.0", 0, 5),
            ("255.255.999", 255, 255),
        )
        for release, major, minor in cases:
            with self.subTest(release=release):
                result = self.generate(release)
                self.assertEqual(result.returncode, 0, result.stderr)
                content = (self.path / "eigrp_build_version.h").read_text()
                self.assertIn(f'#define EIGRP_RELEASE "{release}"', content)
                self.assertIn(f"#define EIGRP_VENDOR_MAJOR {major}", content)
                self.assertIn(f"#define EIGRP_VENDOR_MINOR {minor}", content)

    def test_rejects_overflow_and_unidentified_build(self):
        for release in ("diivious-256.0.0-dev", "0.256.0", "development",
                        "diivious-0.5.0", "0.5", "testdev-0.6.15-staging",
                        "diivious-0.5.0-dev\";", "-0.5.0-dev"):
            with self.subTest(release=release):
                result = self.generate(release)
                self.assertNotEqual(result.returncode, 0)

    def test_header_is_stable_across_identical_generation(self):
        self.assertEqual(self.generate("testdev-0.5.0-dev").returncode, 0)
        destination = self.path / "eigrp_build_version.h"
        stat = destination.stat().st_mtime_ns
        self.assertEqual(self.generate("testdev-0.5.0-dev").returncode, 0)
        self.assertEqual(stat, destination.stat().st_mtime_ns)

    @unittest.skipUnless(shutil.which("cc") and shutil.which("make"),
                         "C compiler and make required")
    def test_unix_host_callback_uses_generated_identity(self):
        # Compile the real Unix host implementation. Link only the live
        # eigrp_sys_software_version() section to avoid stubbing unrelated
        # sockets, timers and UUT services.
        for version, major, minor in (("diivious-0.5.0-dev", 0, 5),
                                      ("diivious-100.10.0-dev", 100, 10)):
            with self.subTest(version=version):
                self.assertEqual(self.generate(version).returncode, 0)
                code = self.path / "probe.c"
                code.write_text('''#include <stdint.h>
#include "eigrp_sys.h"
#include "eigrp_build_version.h"
int main(void) {
    uint8_t major = 255, minor = 255;
    eigrp_sys_software_version(&major, &minor);
    return major != EIGRP_VENDOR_MAJOR || minor != EIGRP_VENDOR_MINOR ||
           major != EXPECT_MAJOR || minor != EXPECT_MINOR;
}
''')
                obj = self.path / "host.o"
                args = [os.environ.get("CC", "cc"), "-std=c11", "-D_POSIX_C_SOURCE=200809L",
                        "-ffunction-sections", "-fdata-sections", "-I", str(self.path),
                        "-I", str(ROOT / "eigrp/code"),
                        "-I", str(ROOT / "libunix/code")]
                result = subprocess.run([*args, "-c", str(ROOT / "libunix/code/eigrp_unix_sys.c"),
                                         "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                binary = self.path / "probe"
                gc_sections = "-Wl,-dead_strip" if sys.platform == "darwin" else "-Wl,--gc-sections"
                result = subprocess.run([*args, f"-DEXPECT_MAJOR={major}",
                                         f"-DEXPECT_MINOR={minor}", str(code), str(obj),
                                         "-pthread", gc_sections, "-o", str(binary)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run([binary], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
