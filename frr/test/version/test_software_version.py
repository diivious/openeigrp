#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile and execute the FRR software-version host callback."""

import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
MOCK = ROOT / "frr/test/build/include"
DRIVER = r'''
#include <assert.h>
#include <stdint.h>
#include "eigrp_sys.h"

int main(void)
{
    uint8_t major = 255, minor = 255;

    eigrp_sys_software_version(&major, &minor);
    assert(major == EXPECT_MAJOR && minor == EXPECT_MINOR);

    major = 255;
    eigrp_sys_software_version(&major, NULL);
    assert(major == EXPECT_MAJOR);

    minor = 255;
    eigrp_sys_software_version(NULL, &minor);
    assert(minor == EXPECT_MINOR);

    eigrp_sys_software_version(NULL, NULL);
    return 0;
}
'''


@unittest.skipUnless(platform.system() == "Linux", "Linux FRR mock headers")
class SoftwareVersionTests(unittest.TestCase):
    def compile_and_run(self, framework_version, major, minor):
        with tempfile.TemporaryDirectory(prefix="eigrp-frr-hello-version-") as temp:
            temp = Path(temp)
            (temp / "lib").mkdir()
            (temp / "lib/version.h").write_text(
                f'#define FRR_VERSION "{framework_version}"\n'
            )
            (temp / "driver.c").write_text(DRIVER)
            cc = os.environ.get("CC", "cc")
            source = ROOT / "frr/code/eigrp_southbound.c"
            obj = temp / "eigrp_southbound.o"
            binary = temp / "version-test"
            includes = ["-I", str(temp), "-I", str(MOCK),
                        "-I", str(ROOT / "eigrp/code"),
                        "-I", str(ROOT / "frr/code")]
            compile_cmd = [cc, "-std=gnu11", "-ffunction-sections",
                           "-fdata-sections", "-DEIGRP_TEST_STRUCT_OPTION",
                           "-Wno-unused-parameter",
                           "-Wno-unused-function", "-DEIGRP_STANDALONE_BUILD",
                           *includes, "-c", str(source), "-o", str(obj)]
            result = subprocess.run(compile_cmd, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            link_cmd = [cc, "-std=gnu11", "-Wl,--gc-sections",
                        f"-DEXPECT_MAJOR={major}", f"-DEXPECT_MINOR={minor}",
                        *includes, str(temp / "driver.c"), str(obj),
                        "-o", str(binary)]
            result = subprocess.run(link_cmd, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_release_with_suffix(self):
        self.compile_and_run("10.4.1-test", 10, 4)

    def test_zero_version(self):
        self.compile_and_run("0.0", 0, 0)

    def test_unparseable_version_falls_back_to_zero(self):
        self.compile_and_run("unstable", 0, 0)


if __name__ == "__main__":
    unittest.main()
