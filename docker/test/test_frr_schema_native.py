#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Execute the real Docker build-stage native-FRR integration gate offline.

Upstream FRR owns EIGRP named-mode YANG and VTY support; no source patching
is permitted. The real installer validates both before the staged build.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "docker/test/uut/frr-schema-native.yaml"
YANG = ROOT / "docker/test/fixtures/frr-eigrpd-native.yang"


def build_stage():
    lines = (ROOT / "docker/Dockerfile").read_text().splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith("RUN set -eux;"))
    end = start + 1
    while lines[end - 1].rstrip().endswith("\\"):
        end += 1
    return "\n".join(lines[start:end])[4:]


class DockerFrrNativeSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oe-frr-native-schema-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.eigrp = self.root / "src-eigrp"
        self.frr = self.root / "src-frr"
        self.image = self.root / "image"
        self.trace = self.root / "build.log"
        (self.eigrp / "eigrp/code").mkdir(parents=True)
        (self.eigrp / "frr/code").mkdir(parents=True)
        (self.eigrp / "frr/tools").mkdir(parents=True)
        (self.frr / "yang").mkdir(parents=True)
        (self.frr / "vtysh").mkdir(parents=True)
        (self.frr / "tools/etc/frr").mkdir(parents=True)
        (self.frr / "bootstrap.sh").write_text("#!/bin/sh\n")
        (self.frr / "tools/etc/frr/daemons").write_text("eigrpd=yes\n")
        shutil.copy2(ROOT / "frr/tools/frr-install.sh",
                     self.eigrp / "frr/tools/frr-install.sh")
        (self.eigrp / "frr/tools/frr.sh").write_text(
            "#!/bin/sh\n"
            'printf "frr %s\\n" "$*" >> "$FRR_NATIVE_BUILD_TRACE"\n'
            'if [ "${REJECT_INSTALL:-0}" = 1 ] && [ "$1" = --install ]; then exit 20; fi\n'
        )
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "make").write_text(
            "#!/bin/sh\n"
            'printf "make %s\\n" "$*" >> "$FRR_NATIVE_BUILD_TRACE"\n'
        )
        (bin_dir / "make").chmod(0o755)
        self.bin_dir = bin_dir
        subprocess.run(["git", "init", "-q", str(self.frr)], check=True)

    def prepare(self, source):
        yang = YANG.read_text()
        vty = ("router eigrp <(1-65535)|WORD> [vrf NAME]\n"
               "install_element(EIGRP_NODE, &router_eigrp_cmd);\n")
        if source == "missing-named":
            yang = yang.replace("list named {", "list removed_named {", 1)
        elif source == "wrong-af-key":
            yang = yang.replace('key "afi vrf asn";', 'key "afi asn";', 1)
        elif source == "invalid-redistribution":
            yang = yang.replace("typedef eigrp-redistribution-protocol {",
                                "typedef invalid-redistribution-protocol {", 1)
        elif source == "missing-vty-router":
            vty = vty.replace("router eigrp <(1-65535)|WORD>",
                              "router eigrp (1-65535)")
        elif source == "missing-vty-reentry":
            vty = vty.replace("install_element(EIGRP_NODE, &router_eigrp_cmd);",
                              "install_element(CONFIG_NODE, &router_eigrp_cmd);")
        elif source not in ("native", "stale-embed"):
            self.fail("unknown source type " + source)
        (self.frr / "yang/frr-eigrpd.yang").write_text(yang)
        (self.frr / "vtysh/vtysh.c").write_text(vty)
        (self.frr / "yang/frr-eigrpd.yang.c").write_text("STALE_GENERATED_SCHEMA\n")

    def test_native_schema_build_matrix(self):
        scenario = yaml.safe_load(SCENARIO.read_text())
        self.assertEqual(scenario["schema"], "docker-frr-schema-uut/v1")
        for case in scenario["cases"]:
            with self.subTest(case=case["name"]):
                self.prepare(case["source"])
                self.trace.unlink(missing_ok=True)
                # The installer may remove generated schema, but must never
                # rewrite FRR-owned YANG or VTY source.
                source_yang = (self.frr / "yang/frr-eigrpd.yang").read_bytes()
                source_vty = (self.frr / "vtysh/vtysh.c").read_bytes()
                stage = build_stage()
                self.assertNotIn("--patch", stage)
                stage = stage.replace("/src-eigrp", str(self.eigrp))
                stage = stage.replace("/src-frr", str(self.frr))
                stage = stage.replace("/image", str(self.image))
                env = dict(os.environ,
                           PATH=f"{self.bin_dir}:{os.environ['PATH']}",
                           FRR_SHA="d" * 40, EIGRP_VERSION="dev", BUILD_JOBS="1",
                           FRR_NATIVE_BUILD_TRACE=str(self.trace))
                if case["result"] == "install-error":
                    env["REJECT_INSTALL"] = "1"
                result = subprocess.run(["bash", "-c", stage], cwd=self.eigrp,
                                        env=env, text=True, capture_output=True)
                trace = self.trace.read_text() if self.trace.exists() else ""
                self.assertEqual((self.frr / "yang/frr-eigrpd.yang").read_bytes(),
                                 source_yang, "installer must not alter upstream YANG")
                self.assertEqual((self.frr / "vtysh/vtysh.c").read_bytes(),
                                 source_vty, "installer must not alter upstream VTY")
                if case["result"] == "build":
                    self.assertEqual(result.returncode, 0, f"{case['name']}: {result.stderr}")
                    self.assertIn("--install", trace)
                    self.assertIn("--configure", trace)
                    self.assertIn("--build", trace)
                    self.assertIn("make -C", trace)
                    self.assertFalse((self.frr / "yang/frr-eigrpd.yang.c").exists(),
                                     "the stale generated YANG must be discarded")
                elif case["result"] in ("schema-error", "vty-error"):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("lacks required upstream EIGRP named-mode support", result.stderr)
                    self.assertNotIn("--install", trace)
                    self.assertNotIn("--build", trace)
                else:
                    self.assertEqual(result.returncode, 20)
                    self.assertIn("--install", trace)
                    self.assertNotIn("--configure", trace)

    def test_upstream_pin_is_immutable(self):
        config = (ROOT / "docker/docker.cfg").read_text()
        pins = dict(line.split("=", 1) for line in config.splitlines()
                    if line.startswith("FRR_") and "=" in line)
        self.assertRegex(pins["FRR_REF"], r"^[a-f0-9]{40}$")
        self.assertEqual(pins["FRR_REF"], pins["FRR_SHA"])


if __name__ == "__main__":
    unittest.main()
