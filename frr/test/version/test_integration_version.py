#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile and execute FRR integration-version discovery against a mock build."""

import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]

DRIVER = r'''
#include <assert.h>
#include <string.h>
#include <sys/utsname.h>
#include "eigrp_frr_version.h"

int main(int argc, char **argv)
{
    eigrp_integration_version_t version;
    struct utsname host;

    assert(argc == 6);
    eigrp_frr_version_read(NULL);
    memset(&version, 0xa5, sizeof(version));
    eigrp_frr_version_read(&version);
    assert(strcmp(version.integration, "FRRouting EIGRP") == 0);
    assert(strcmp(version.integration_version, argv[5]) == 0);
    assert(strcmp(version.framework, "FRRouting") == 0);
    assert(strcmp(version.framework_version, "99.7-framework-test") == 0);
    assert(strcmp(version.distribution, argv[1]) == 0);
    assert(strcmp(version.distribution_version, argv[2]) == 0);
    assert(strcmp(version.distribution_codename, argv[3]) == 0);
    assert(uname(&host) == 0);
    assert(strcmp(version.operating_system, host.sysname) == 0);
    assert(strcmp(version.kernel_version, host.release) == 0);
    assert(strcmp(version.architecture, host.machine) == 0);
    assert(strcmp(version.operating_system_version, argv[4]) == 0);
    return 0;
}
'''


@unittest.skipUnless(platform.system() == "Linux", "Linux os-release tests")
class IntegrationVersionTests(unittest.TestCase):
    def compile_and_run(self, os_release, expected, mac=False, adapter_release=True):
        with tempfile.TemporaryDirectory(prefix="eigrp-version-test-") as tmp:
            tmp = Path(tmp)
            (tmp / "lib").mkdir()
            (tmp / "lib" / "version.h").write_text(
                '#define FRR_VERSION "99.7-framework-test"\n'
            )
            (tmp / "eigrp_features.h").write_text('#define EIGRP_RELEASE "core-release-test"\n')
            if os_release is not None:
                (tmp / "os-release").write_text(os_release)
            (tmp / "test.c").write_text(DRIVER)
            if mac:
                (tmp / "sys").mkdir()
                (tmp / "sys" / "sysctl.h").write_text(
                    "#include <stddef.h>\n"
                    "int sysctlbyname(const char *, void *, size_t *, "
                    "const void *, size_t);\n"
                )
                (tmp / "mac.c").write_text(
                    "#include <string.h>\n#include <stddef.h>\n"
                    "int sysctlbyname(const char *name, void *value, "
                    "size_t *length, const void *new_value, size_t new_length) "
                    "{\n"
                    "  const char text[] = \"26.1\";\n"
                    "  (void)new_value; (void)new_length;\n"
                    "  if (strcmp(name, \"kern.osproductversion\") "
                    "|| *length < sizeof(text)) return -1;\n"
                    "  memcpy(value, text, sizeof(text));\n"
                    "  *length = sizeof(text);\n  return 0;\n}\n"
                )
            binary = tmp / "version-test"
            command = [
                    os.environ.get("CC", "cc"),
                    "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                    "-I", str(tmp),
                    "-I", str(ROOT / "eigrp/code"),
                    "-I", str(ROOT / "frr/code"),
                    '-DEIGRP_FRR_INTEGRATION_RELEASE="adapter-build-test"',
                    f'-DEIGRP_OS_RELEASE_PATH="{tmp / "os-release"}"',
                    str(ROOT / "frr/code/eigrp_frr_version.c"),
                    str(tmp / "test.c"),
                ]
            if mac:
                command += ["-D__APPLE__", str(tmp / "mac.c")]
            if not adapter_release:
                command.remove('-DEIGRP_FRR_INTEGRATION_RELEASE="adapter-build-test"')
            command += ["-o", str(binary)]
            compile_result = subprocess.run(
                command,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            expected += ["adapter-build-test" if adapter_release else "core-release-test"]
            subprocess.run([str(binary), *expected], check=True)

    def test_debian_version_and_codename(self):
        self.compile_and_run(
            'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n'
            'NAME="Debian GNU/Linux"\nVERSION_ID="13"\n'
            'VERSION_CODENAME=trixie\n',
            ["Debian GNU/Linux", "13", "trixie", ""],
        )

    def test_adapter_release_falls_back_to_build_release(self):
        self.compile_and_run(
            'NAME=Debian\nVERSION_ID=13\n',
            ["Debian", "13", "", ""],
            adapter_release=False,
        )

    def test_missing_release_values_are_not_invented(self):
        self.compile_and_run(
            '# a minimal system\nNAME=Alpine\n',
            ["Alpine", "", "", ""],
        )

    def test_quotes_long_lines_and_unknown_keys(self):
        self.compile_and_run(
            'NAME=' + 'x' * 700 + '\n'
            'VERSION_CODENAME="edge"\nNAME=Fedora\nVERSION_ID=42\n'
            'VERSION=unknown\n',
            ["Fedora", "42", "edge", ""],
        )

    def test_darwin_uses_product_version_without_linux_distribution(self):
        self.compile_and_run(
            'NAME="Linux distro which must not appear"\n',
            ["", "", "", "26.1"],
            mac=True,
        )

    def test_unavailable_os_release_file(self):
        # Verifies parsing does not manufacture distro identity or version.
        self.compile_and_run(None, ["", "", "", ""])


if __name__ == "__main__":
    unittest.main()
