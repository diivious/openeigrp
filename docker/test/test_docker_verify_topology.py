#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise the production Docker topology-entry verifier with FRR CLI output.

The function is executed unchanged from docker-verify.sh, with only the Docker
CLI replaced by a stub. Full integration remains covered by docker-uut.
"""

import os
from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "frr/tools/docker-verify.sh"
SCENARIO = ROOT / "docker/test/uut/docker-topology-entry.yaml"


def shell_function(source, name):
    marker = f"{name}() {{\n"
    if source.count(marker) != 1:
        raise AssertionError(f"expected exactly one {name} function")
    start = source.index(marker)
    end = source.index("\n}\n", start) + 2
    return source[start:end]


class DockerTopologyEntryTests(unittest.TestCase):
    def test_topology_entry_matrix(self):
        source = VERIFY.read_text()
        checks = "\n".join(shell_function(source, name) for name in
                           ("check_cli_output", "learned_via_peer"))
        scenario = yaml.safe_load(SCENARIO.read_text())
        self.assertEqual(scenario["schema"], "docker-topology-entry-uut/v1")
        harness = """set -euo pipefail
            topology_command='show eigrp address-family ipv4 topology 4453'
            fail() { printf 'FAIL: %s\\n' "$*" >&2; exit 1; }
            docker() {
                [[ "$1" == exec && "$3" == vtysh && "$4" == -d &&
                   "$5" == eigrpd && "$6" == -c && "$7" == "$EXPECTED_CLI" ]] || return 2
                printf '%s\\n' "$MOCK_OUTPUT"
            }
        """ + checks + "\nlearned_via_peer r1 \"$PREFIX\" \"$PEER\"\n"
        for case in scenario["cases"]:
            with self.subTest(case=case["name"]):
                env = dict(os.environ,
                           MOCK_OUTPUT=case["output"],
                           PREFIX=case["prefix"],
                           PEER=case["peer"],
                           EXPECTED_CLI=("show eigrp address-family ipv4 "
                                         "topology 4453 " + case["prefix"]))
                result = subprocess.run(["bash", "-c", harness], env=env,
                                        text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode == 0, case["present"],
                                 f'{case["name"]}: {result.stderr}')


if __name__ == "__main__":
    unittest.main()
