#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Build real binaries and a Debian package; verify embedded releases."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(
    sys.platform == "linux" and all(shutil.which(tool) for tool in
                                    ("cc", "make", "dpkg-deb", "dpkg-shlibdeps",
                                     "dpkg-architecture", "readelf")),
    "native Linux and Debian build tools required")
class ReleaseIdentityTests(unittest.TestCase):
    def test_incremental_rebuild_and_real_package_identity(self):
        with tempfile.TemporaryDirectory(prefix="eigrp-version-build-") as tmp:
            work = Path(tmp)
            for name in ("eigrp/code", "libunix/code", "linux/code",
                         "linux/packaging"):
                shutil.copytree(ROOT / name, work / name,
                                ignore=shutil.ignore_patterns(
                                    "*.o", "*.a", "*.pyc", "__pycache__",
                                    "openeigrpd", "openeigrpd-config", "oectl",
                                    "eigrp_build_version.h", "eigrp-wire"))
            (work / "tools").mkdir()
            for name in ("Makefile", "linux/Makefile", "tools/build-version.sh",
                         "tools/gen-version.py"):
                shutil.copy2(ROOT / name, work / name)

            def invoke(*args):
                result = subprocess.run(args, cwd=work, capture_output=True,
                                        text=True, timeout=120)
                self.assertEqual(result.returncode, 0,
                                 f"{' '.join(map(str, args))}:\n{result.stdout}\n{result.stderr}")
                return result.stdout.strip()

            def versions():
                daemon = invoke(str(work / "linux/code/openeigrpd"), "-V")
                client = json.loads(invoke(str(work / "linux/code/oectl"),
                                           "--json", "version"))
                return daemon, client

            for version, vendor in (("testdev-0.5.0-dev", (0, 5)),
                                    ("testdev-0.6.0-dev", (0, 6))):
                invoke("make", "linux", f"EIGRP_BUILD_VERSION={version}")
                daemon, client = versions()
                self.assertEqual(daemon, f"openeigrpd {version}")
                self.assertEqual(client["build"], version)
                # The core, daemon, client and Unix HELLO callback must share
                # the same generated OpenEIGRP build identity.
                header = (work / "eigrp/code/eigrp_build_version.h").read_text()
                self.assertIn(f'#define EIGRP_RELEASE "{version}"', header)
                self.assertIn(f"#define EIGRP_VENDOR_MAJOR {vendor[0]}", header)
                self.assertIn(f"#define EIGRP_VENDOR_MINOR {vendor[1]}", header)

            output = work / "dist"
            invoke(str(work / "linux/packaging/build-deb.sh"),
                   "--version", "0.6.0~testdev.dev", "--output", str(output))
            arch = invoke("dpkg-architecture", "-qDEB_HOST_ARCH")
            package = output / f"openeigrpd_0.6.0~testdev.dev_{arch}.deb"
            self.assertTrue(package.exists())
            self.assertEqual(invoke("dpkg-deb", "-f", str(package), "Version"),
                             "0.6.0~testdev.dev")
            extract = work / "extract"
            invoke("dpkg-deb", "-x", str(package), str(extract))
            self.assertEqual(invoke(str(extract / "usr/sbin/openeigrpd"), "-V"),
                             "openeigrpd testdev-0.6.0-dev")
            self.assertEqual(json.loads(invoke(str(extract / "usr/bin/oectl"),
                                             "--json", "version"))["build"],
                             "testdev-0.6.0-dev")


if __name__ == "__main__":
    unittest.main()
