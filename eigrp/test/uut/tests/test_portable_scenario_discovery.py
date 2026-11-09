# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage
"""Exercise the real YAML runner discovery after portable scenario renaming."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
UUT = HERE.parent
sys.path.insert(0, str(UUT))
import run_all  # noqa: E402


class PortableScenarioDiscoveryTest(unittest.TestCase):
    def test_portable_scenarios_resolve_and_have_unique_reports(self):
        scenarios = [(top, path) for top, path in run_all.discover()
                     if path.is_relative_to(UUT)]
        relative = {path.relative_to(UUT).as_posix() for _, path in scenarios}
        expected = {
            "lib-boundary/scenario-archive-linkage.yaml",
            "config-lifecycle/ipv4-configuration-lifecycle.yaml",
            "interface-lifecycle/link-recovery.yaml",
            "rtp-recovery/ipv4-loss-recovery.yaml",
            "rtp-recovery/ipv6-loss-recovery.yaml",
            "filter-auth/ipv4-policy-md5-lifecycle.yaml",
            "redist-lifecycle/ipv4-source-lifecycle.yaml",
            "redist-lifecycle/ipv6-source-lifecycle.yaml",
            "rib-lifecycle/ipv4-lifecycle.yaml",
            "rib-lifecycle/ipv6-lifecycle.yaml",
        }
        self.assertTrue(expected <= relative, expected - relative)
        self.assertFalse(any(p.startswith("linux-") for p in relative))
        report_names = [run_all.scenario_name(path) for _, path in scenarios]
        self.assertEqual(len(report_names), len(set(report_names)))

    def test_example_as_number_conventions(self):
        for family, expected in (("ipv4", 4453), ("ipv6", 6473)):
            topology = run_all.document(UUT / "examples" / f"{family}-basic-core.yaml")
            self.assertEqual({router["asn"] for router in topology["routers"].values()},
                             {expected})

    def test_portable_discovery_resolves_topology_for_each_group(self):
        paths = {
            scenario.relative_to(UUT).parts[0]: topology
            for topology, scenario in run_all.discover()
            if scenario.is_relative_to(UUT) and scenario.relative_to(UUT).parts[0] in {
                "lib-boundary", "config-lifecycle", "interface-lifecycle",
                "rtp-recovery", "filter-auth", "redist-lifecycle",
                "rib-lifecycle",
            }
        }
        self.assertEqual(len(paths), 7)
        for topology in paths.values():
            self.assertEqual(run_all.document(topology)["schema"],
                             run_all.TOPOLOGY_SCHEMA)


if __name__ == "__main__":
    unittest.main()
