#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable eigrp CLI regression over actual Task 11 Unix IPC."""

import concurrent.futures
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "linux" / "code"
TEST = ROOT / "linux" / "test"
HEADER = "!IHHHHI"
MAGIC = 0x45474D54


@unittest.skipUnless(sys.platform == "linux", "Linux AF_UNIX required")
class ControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="eigrp-test-")
        os.chmod(cls.tmp.name, 0o755)
        cls.client = Path(cls.tmp.name) / "eigrp"
        cls.fixture = Path(cls.tmp.name) / "mgnt-fixture"
        flags = [os.environ.get("CC", "cc"), "-std=c11", "-D_GNU_SOURCE",
                 "-Wall", "-Wextra", "-Werror", "-pthread", "-g",
                 "-I", str(ROOT / "eigrp" / "code"), "-I", str(CODE)]
        for binary, sources in (
            (cls.client, [CODE / "eigrp_linux_ctl.c", CODE / "eigrp_linux_version.c"]),
            (cls.fixture, [TEST / "mgnt_fixture.c", CODE / "eigrp_linux_mgnt.c",
                           CODE / "eigrp_linux_version.c"]),
        ):
            done = subprocess.run([*flags, *map(str, sources), "-o", str(binary)],
                                  text=True, capture_output=True)
            if done.returncode:
                raise AssertionError(done.stdout + done.stderr)
        cls.release = json.loads(subprocess.run(
            [str(cls.client), "--json", "version"],
            check=True, capture_output=True, text=True).stdout)["build"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def start(self, multi=False, paths=False, timers=False, penalty=False,
              inactive=False, no_config=False, vrfs=False, vrf_runtime=False, large=False, many=False, no_interfaces=False):
        env = dict(os.environ)
        if multi:
            env["EIGRP_LINUX_CTL_TEST_MULTI"] = "1"
        if paths:
            env["EIGRP_LINUX_CTL_TEST_PATHS"] = "1"
        if timers:
            env["EIGRP_LINUX_CTL_TEST_TIMERS"] = "1"
        if penalty:
            env["EIGRP_LINUX_CTL_TEST_PENALTY"] = "1"
        if inactive:
            env["EIGRP_LINUX_CTL_TEST_INACTIVE"] = "1"
        if no_config:
            env["EIGRP_LINUX_CTL_TEST_NO_CONFIG"] = "1"
        if vrfs:
            env["EIGRP_LINUX_CTL_TEST_VRF"] = "1"
        if vrf_runtime:
            env["EIGRP_LINUX_CTL_TEST_VRF_RUNTIME"] = "1"
        if large:
            env["EIGRP_LINUX_CTL_TEST_LARGE"] = "1"
        if many:
            env["EIGRP_LINUX_CTL_TEST_MANY"] = "1"
        if no_interfaces:
            env["EIGRP_LINUX_CTL_TEST_NO_INTERFACES"] = "1"
        self.daemon = subprocess.Popen([str(self.fixture), str(self.path)],
                                       stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True,
                                       env=env)
        self.assertEqual(self.daemon.stdout.readline(), "READY\n")

    def stop(self):
        if self.daemon.poll() is None:
            self.daemon.stdin.write("q\n")
            self.daemon.stdin.flush()
        out, err = self.daemon.communicate(timeout=6)
        self.assertEqual(self.daemon.returncode, 0, out + err)

    def setUp(self):
        self.path = Path(self.tmp.name) / (self._testMethodName + ".sock")
        self.start()

    def tearDown(self):
        self.stop()

    def ctl(self, *args, uid=None, timeout=10):
        preexec = None
        if uid is not None:
            def drop():
                os.setgroups([uid])
                os.setgid(uid)
                os.setuid(uid)
            preexec = drop
        return subprocess.run([str(self.client), "--socket", str(self.path),
                               *map(str, args)], capture_output=True,
                              text=True, timeout=timeout, preexec_fn=preexec)

    def test_read_views_and_render(self):
        expected = {
            "capabilities": 'OpenEIGRP capabilities (release test"fixture)',
            "protocols": "EIGRP-IPv4 Protocol for AS(4453)",
            "neighbors": "192.0.2.2",
            "interfaces": 'eth"0\\u000asecond',
            "topology": "198.51.100.48/32",
            "statistics": "Hellos sent/received: 900/0",
            "events": "Event information for AS 4453:",
            "diagnostics": "Timer 0, Interface eth0",
        }
        for view, marker in expected.items():
            with self.subTest(view=view):
                result = self.ctl("show", view)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(marker, result.stdout)
        protocols = self.ctl("show", "protocols")
        self.assertIn("  Router-ID: 192.0.2.1", protocols.stdout)
        self.assertNotIn("  Effective router-ID:", protocols.stdout)
        topology = self.ctl("show", "topology")
        self.assertEqual(len([l for l in topology.stdout.splitlines()
                              if l.startswith("P ")]), 48)
        self.assertEqual(topology.stdout.count("successors"), 48)
        self.assertEqual(topology.stdout.count("via 192.0.2.2"), 48)

    def test_cisco_style_scope_abbreviations_and_detail(self):
        for command in (("show", "addr", "ipv4", "4453", "topo"),
                        ("show", "address-f", "ipv4", "top"),
                        ("show", "4453", "neig"),
                        ("show", "nei")):
            result = self.ctl(*command)
            self.assertEqual(result.returncode, 0, (command, result.stderr))
            self.assertIn("AS(4453)", result.stdout)
        result = self.ctl("show", "addr", "ipv4", "4453",
                          "interfaces", "detail", 'eth"0\nsecond')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Hello interval: 5 seconds", result.stdout)
        self.assertNotIn('eth"0\nsecond', result.stdout)
        result = self.ctl("show", "neighbors", "detail")
        self.assertIn("Retransmissions:", result.stdout)
        missing = self.ctl("show", "interfaces", "detail", "Loopback1")
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertIn("  No EIGRP interfaces matched Loopback1", missing.stdout)
        for command in (("show", "t"), ("show", "address-family", "i", "topo"),
                        ("show", "addr", "ipv4", "6473", "top")):
            result = self.ctl(*command)
            self.assertNotEqual(result.returncode, 0)
        result = self.ctl("show", "routes")
        self.assertEqual(result.returncode, 2)
        self.assertIn("host RIB", result.stderr)
        result = self.ctl("show", "addr", "ipv4", "4453", "topo", "--json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len([json.loads(x) for x in result.stdout.splitlines()]), 96)

    def test_omitted_af_and_as_walks_all_instances(self):
        self.stop()
        self.start(multi=True)
        text = self.ctl("show", "nei")
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("EIGRP-IPv4", text.stdout)
        self.assertIn("EIGRP-IPv6", text.stdout)
        self.assertIn("AS(4453)", text.stdout)
        self.assertIn("AS(6473)", text.stdout)
        filtered = self.ctl("show", "addr", "ipv6", "6473", "nei")
        self.assertEqual(filtered.returncode, 0, filtered.stderr)
        self.assertIn("EIGRP-IPv6", filtered.stdout)
        self.assertNotIn("EIGRP-IPv4", filtered.stdout)
        selected = self.ctl("show", "6473", "neighbors", "--json")
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(len(selected.stdout.splitlines()), 1)
        cleared = self.ctl("clear", "events")
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertEqual(cleared.stdout.count("Events cleared"), 2)

    def test_named_show_parity_uut(self):
        # JSON-compatible YAML: executable CLI cases against the real IPC
        # implementation and fixture, not source-text pattern matching.
        cases = json.loads((TEST / "uut/named-show-parity.yaml").read_text())
        self.assertEqual(cases["schema"], "linux-named-show-uut/v1")
        mode = None
        for case in cases["cases"]:
            fixture = case.get("fixture", "default")
            if fixture != mode:
                self.stop()
                self.start(no_interfaces=fixture == "no-interfaces",
                           many=fixture == "many")
                mode = fixture
            with self.subTest(case=case["name"]):
                result = self.ctl(*case["argv"], timeout=30)
                self.assertEqual(result.returncode, case["returncode"],
                                 result.stderr)
                for marker in case.get("stdout_contains", []):
                    self.assertIn(marker, result.stdout)
                for marker in case.get("stdout_omits", []):
                    self.assertNotIn(marker, result.stdout)
                for marker in case.get("stderr_contains", []):
                    self.assertIn(marker, result.stderr)
                if "stdout_equals_file" in case:
                    golden = TEST / case["stdout_equals_file"]
                    self.assertEqual(result.stdout, golden.read_text(),
                                     case["name"])
                if "json_lines" in case:
                    self.assertEqual(len(result.stdout.splitlines()),
                                     case["json_lines"])
                if "heading_count" in case:
                    self.assertEqual(result.stdout.count(case["heading_count"][0]),
                                     case["heading_count"][1])
                if "prefix_lines" in case:
                    self.assertEqual(sum(line.startswith("P ")
                                         for line in result.stdout.splitlines()),
                                     case["prefix_lines"])

    def test_named_topology_link_selection(self):
        self.stop()
        self.start(paths=True)
        ordinary = self.ctl("show", "topology")
        self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
        self.assertNotIn("192.0.2.3", ordinary.stdout)
        self.assertNotIn("192.0.2.4", ordinary.stdout)
        all_links = self.ctl("show", "topology", "all-links")
        self.assertEqual(all_links.returncode, 0, all_links.stderr)
        self.assertIn("via 192.0.2.3 (", all_links.stdout)
        self.assertIn("via 192.0.2.4 (", all_links.stdout)
        self.assertIn("Inaccessible", all_links.stdout)
        entry = self.ctl("show", "address-family", "ipv4", "topology",
                         "4453", "198.51.100.48", "all-links")
        self.assertEqual(entry.returncode, 0, entry.stderr)
        self.assertIn("Topology Entry for AS(4453)", entry.stdout)
        self.assertIn("for 198.51.100.48/32", entry.stdout)
        self.assertIn("via 192.0.2.3", entry.stdout)
        self.assertIn("FD is 12288, serno 48\n", entry.stdout)
        self.assertEqual(entry.stdout.count("State is Passive"), 1)
        normalized = self.ctl("show", "topology", "198.51.100.49/24")
        self.assertIn("% Network not in table", normalized.stdout)
        for target in ("198.51.100.48/33", "not-a-prefix", "2001:db8::1"):
            invalid = self.ctl("show", "-4", "topology", target)
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("malformed topology destination", invalid.stderr)
        raw = self.ctl("show", "topology", "--json")
        self.assertEqual(raw.returncode, 0, raw.stderr)
        self.assertEqual(len(raw.stdout.splitlines()), 98)
        self.assertEqual(sum(json.loads(line).get("next_hop") == "192.0.2.3"
                             for line in raw.stdout.splitlines()), 1)

    def test_vrf_config_identity_and_runtime_selection(self):
        self.stop()
        self.start(vrfs=True)
        all_views = self.ctl("show", "tech-support")
        self.assertEqual(all_views.returncode, 0, all_views.stderr)
        for scope in ("VRF default", "VRF blue", "VRF red"):
            self.assertIn(scope, all_views.stdout)
        blue = self.ctl("show", "address-family", "ipv4", "vrf", "blue",
                        "4453", "interfaces", "detail")
        self.assertEqual(blue.returncode, 0, blue.stderr)
        self.assertIn("VRF(blue)", blue.stdout)
        self.assertIn("runtime not active", blue.stdout)
        self.assertNotIn("VRF(red)", blue.stdout)
        static = self.ctl("show", "address-family", "ipv4", "vrf", "red",
                          "4453", "neighbors", "static")
        self.assertEqual(static.returncode, 0, static.stderr)
        self.assertIn("VRF(red)", static.stdout)
        self.assertIn("192.0.2.9", static.stdout)
        self.assertNotIn("VRF(blue)", static.stdout)
        clear = self.ctl("clear", "address-family", "ipv4", "vrf", "blue",
                         "4453", "events")
        self.assertNotEqual(clear.returncode, 0)
        self.assertIn("not running", clear.stderr)
        self.stop()
        self.start(vrfs=True, vrf_runtime=True)
        runtime = self.ctl("--vrf-id", "22", "--afi", "4", "--asn", "4453",
                           "show", "neighbors")
        self.assertEqual(runtime.returncode, 0, runtime.stderr)
        self.assertIn("VRF(blue)", runtime.stdout)
        self.assertIn("192.0.2.2", runtime.stdout)
        self.assertNotIn("VRF(red)", runtime.stdout)
        self.assertNotIn("VR(eigrprulez)", runtime.stdout)
        self.stop()
        self.start(vrfs=True)
        clear_all = self.ctl("clear", "events")
        self.assertEqual(clear_all.returncode, 0, clear_all.stderr)
        self.assertEqual(clear_all.stdout.count("Events cleared"), 1)
        self.assertNotIn("VRF blue", clear_all.stdout)

    def test_topology_beyond_old_ipc_page_limit(self):
        self.stop()
        self.start(large=True)
        result = self.ctl("show", "topology", "--json", timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 8200)
        self.assertEqual(records[-2]["prefix"], "198.51.116.36/32")
        self.assertEqual(records[-1]["prefix"], "198.51.116.36/32")
        self.assertEqual(records[-1]["kind"], "path")

    def test_empty_interface_views_match_frr(self):
        self.stop()
        self.start(no_interfaces=True)
        heading = "EIGRP-IPv4 VR(eigrprulez) Address-family Interfaces for AS(4453)"
        for args in (("show", "interfaces"),
                     ("show", "interfaces", "detail"),
                     ("show", "interfaces", "eth0"),
                     ("show", "interfaces", "eth0", "detail")):
            with self.subTest(args=args):
                result = self.ctl(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(heading, result.stdout)
                self.assertIn("  No EIGRP interfaces matched", result.stdout)
                self.assertNotIn("No active EIGRP interfaces", result.stdout)
        raw = self.ctl("show", "interfaces", "--json")
        self.assertEqual(raw.returncode, 0, raw.stderr)
        self.assertEqual(raw.stdout, "")

    def test_more_than_128_configured_scopes(self):
        self.stop()
        self.start(many=True)
        result = self.ctl("show", "interfaces", timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("Address-family Interfaces for AS("), 141)
        self.assertIn("VR(bulk-139) Address-family Interfaces for AS(5139)",
                      result.stdout)
        protocols = self.ctl("show", "protocols", "--json")
        self.assertEqual(protocols.returncode, 0, protocols.stderr)
        self.assertEqual(len(protocols.stdout.splitlines()), 141)
        self.assertEqual(json.loads(protocols.stdout.splitlines()[-1])["asn"], 5139)
        selected = self.ctl("show", "address-family", "ipv4", "5139",
                            "interfaces")
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(selected.stdout.count("Address-family Interfaces for AS("), 1)
        tech = self.ctl("show", "tech-support", timeout=30)
        self.assertEqual(tech.returncode, 0, tech.stderr)
        self.assertEqual(tech.stdout.count("EIGRP technical support:"), 141)
        self.assertIn("EIGRP technical support: bulk-139 IPv4 AS 5139", tech.stdout)
        cleared = self.ctl("clear", "events", timeout=30)
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertEqual(cleared.stdout.count("Events cleared"), 141)

    def test_core_state_exposed_without_fabrication(self):
        neighbor = json.loads(self.ctl("show", "neighbors", "--json").stdout)
        self.assertEqual((neighbor["os_major"], neighbor["os_minor"],
                          neighbor["tlv_major"], neighbor["tlv_minor"],
                          neighbor["tlv_version"]), (15, 2, 1, 4, 2))
        self.assertEqual((neighbor["srtt_valid"], neighbor["srtt"],
                          neighbor["retry"], neighbor["retransmissions"]),
                         (True, 33, 3, 7))
        interface = json.loads(self.ctl("show", "interfaces", "--json").stdout)
        self.assertEqual((interface["peers"], interface["up_peers"],
                          interface["mean_srtt_valid"], interface["mean_srtt"]),
                         (2, 1, True, 42))
        for key, val in (("reliability", 240), ("load", 5),
                         ("tlv1_peers", 1), ("tlv2_peers", 1),
                         ("pacing_bytes", 8000), ("retransmissions", 7)):
            self.assertEqual(interface[key], val)
        rows = [json.loads(row) for row in
                self.ctl("show", "topology", "--json").stdout.splitlines()]
        prefix = next(row for row in rows if row["kind"] == "prefix")
        route = next(row for row in rows if row["kind"] == "path")
        self.assertEqual((prefix["dual_state"], prefix["pending_replies"]), (2, 3))
        self.assertEqual((route["total_wide_bw"], route["total_wide_delay"],
                          route["total_mtu"], route["total_hops"]),
                         (50000, 5000000, 1500, 2))
        self.assertEqual((route["reported_wide_bw"],
                          route["reported_wide_delay"]), (10000, 1000000))
        protocol = json.loads(self.ctl("show", "protocols", "--json").stdout)
        self.assertEqual((protocol["k1"], protocol["variance"],
                          protocol["topology_prefixes"]), (1, 2, 48))

    def test_timer_destinations_and_penalized_neighbor(self):
        self.stop()
        self.start(timers=True, penalty=True)
        timer_rows = [json.loads(row) for row in
                      self.ctl("show", "timers", "--json").stdout.splitlines()]
        self.assertEqual([row["type"] for row in timer_rows], [0, 1, 2])
        self.assertEqual([row["remaining_ms"] for row in timer_rows],
                         [4123, 2200, 3250])
        self.assertEqual(timer_rows[2]["destination"], "198.51.100.48/32")
        timers = self.ctl("show", "timers")
        self.assertEqual(timers.returncode, 0, timers.stderr)
        self.assertIn("| 3.250 ACTIVE/SIA (198.51.100.48/32)", timers.stdout)
        self.assertIn("| 2.200 Peer holding (192.0.2.2 via eth0)", timers.stdout)
        self.assertNotIn("No ACTIVE/SIA deadlines", timers.stdout)
        penalized = self.ctl("show", "neighbors", "detail")
        self.assertEqual(penalized.returncode, 0, penalized.stderr)
        self.assertIn("Prefix-limit: P, restarts: 2, restart/reset: 60/120", penalized.stdout)
        self.assertIn("Current retries: 3", penalized.stdout)
        self.assertRegex(penalized.stdout, r"eth0\s+-\s+-\s+-\s+-\s+0\s+-")

    def test_client_identity_uut(self):
        scenarios = json.loads((TEST / "uut/client-identity.yaml").read_text())
        self.assertEqual(scenarios["schema"], "linux-client-identity-uut/v1")
        for case in scenarios["cases"]:
            with self.subTest(case=case["name"]):
                result = subprocess.run(
                    [str(self.client), *case["argv"]],
                    text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, case["returncode"],
                                 result.stderr)
                if "json_fields" in case:
                    payload = json.loads(result.stdout)
                    for key, value in case["json_fields"].items():
                        self.assertEqual(payload[key], value)
                if "stdout_contains" in case:
                    self.assertIn(case["stdout_contains"], result.stdout)
                if "stderr_contains" in case:
                    self.assertIn(case["stderr_contains"], result.stderr)

    def test_client_has_no_core_link_dependency(self):
        symbols = subprocess.run(["nm", "-u", str(self.client)],
                                 text=True, capture_output=True)
        self.assertEqual(symbols.returncode, 0, symbols.stderr)
        self.assertNotIn(" eigrp_", symbols.stdout)
        version = self.ctl("version", "--json")
        self.assertEqual(version.returncode, 0, version.stderr)
        data = json.loads(version.stdout)
        self.assertEqual(data["kind"], "integration")
        self.assertEqual(data["client"], "eigrp")
        self.assertEqual(data["ipc"], 5)
        self.assertIn("kernel", data)
        self.assertIn("distribution", data)

    def test_json_pages_match_daemon_and_no_terminal_injection(self):
        result = self.ctl("show", "topology", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 96)
        self.assertEqual(records[0]["prefix"], "198.51.100.1/32")
        self.assertEqual(records[-1]["next_hop"], "192.0.2.2")
        result = self.ctl("show", "interfaces")
        self.assertIn("eth\"0\\u000asecond", result.stdout)
        self.assertNotIn('eth"0\nsecond', result.stdout)

    def test_clear_operations_execute_portable_targets(self):
        self.assertIn("1  event", self.ctl("show", "events").stdout)
        cleared = self.ctl("-a", "4453", "-4", "clear", "events")
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertIn("Events cleared", cleared.stdout)
        self.assertIn("  No events recorded", self.ctl("show", "events").stdout)
        self.assertEqual(self.ctl("clear", "nei", "192.0.2.2", "soft").returncode, 0)
        self.assertIn("192.0.2.2", self.ctl("show", "nei").stdout)
        not_found = self.ctl("clear", "neighbors", "203.0.113.99")
        self.assertEqual(not_found.returncode, 1)
        self.assertIn("not running", not_found.stderr)
        self.assertEqual(self.ctl("clear", "neighbors", "eth0").returncode, 0)
        self.assertNotIn("192.0.2.2", self.ctl("show", "neighbors").stdout)
        # Wrong-AFI targets must not silently succeed without a core call.
        unsupported = self.ctl("clear", "neighbors", "2001:db8::5")
        self.assertEqual(unsupported.returncode, 1)
        self.assertIn("no selected address family matches", unsupported.stderr)
        unsupported = self.ctl("clear", "topology", "2001:db8::1/128")
        self.assertEqual(unsupported.returncode, 1)
        self.assertIn("no selected address family matches", unsupported.stderr)
        self.assertEqual(self.ctl("clear", "topology", "198.51.100.48/32").returncode, 0)
        self.assertNotIn("198.51.100.48/32", self.ctl("show", "topology").stdout)
        for command in (("set", "debug"), ("configure", "terminal"),
                        ("reset", "routes")):
            result = self.ctl(*command)
            self.assertEqual(result.returncode, 2)
            self.assertIn("unsupported", result.stderr)

    def test_cli_grammar_and_scope_errors(self):
        self.assertEqual(self.ctl("help").returncode, 0)
        local = self.ctl("version")
        self.assertIn("OpenEIGRP Client:\n", local.stdout)
        self.assertIn("  Management IPC       : 5", local.stdout)
        self.assertIn("Host Version:\n", local.stdout)
        self.assertNotIn("OpenEIGRP Version:", local.stdout)
        self.assertEqual(self.ctl("--version").returncode, 0)
        detailed = self.ctl("show", "version")
        self.assertEqual(detailed.returncode, 0, detailed.stderr)
        self.assertIn("OpenEIGRP Version:\n", detailed.stdout)
        self.assertIn('openeigrp            : test"fixture', detailed.stdout)
        self.assertIn("ipv4-af              : Supported", detailed.stdout)
        self.assertIn("bfd                  : Not Supported", detailed.stdout)
        self.assertIn("evn                  : Not Supported", detailed.stdout)
        self.assertNotIn("Daemon identity:", detailed.stdout)
        self.assertNotIn("capability: release=", detailed.stdout)
        self.assertEqual(detailed.stdout.count("OpenEIGRP Version:"), 1)
        # Human view and JSON retain the same underlying daemon capabilities.
        detailed_json = self.ctl("show", "version", "--json")
        self.assertEqual(detailed_json.returncode, 0, detailed_json.stderr)
        records = [json.loads(line) for line in detailed_json.stdout.splitlines()]
        self.assertEqual([r["kind"] for r in records],
                         ["integration", "identity", "capability"])
        self.assertEqual(records[-1]["release"], 'test"fixture')
        for argv, marker in (
            (("show",), "missing show view"),
            (("--what", "show", "neighbors"), "unknown option"),
            (("--asn" ,), "missing value"),
            (("--asn", "65536", "show", "neighbors"), "invalid value"),
            (("--afi", "5", "show", "neighbors"), "AFI must be"),
            (("--vrf-id", "22", "show", "neighbors"), "requires explicit AFI and ASN"),
            (("--asn", "1", "--afi", "4", "show", "neighbors"),
             "no matching configured"),
            (("--asn", "4453", "--afi", "6", "show", "neighbors"),
             "no matching configured"),
            (("--asn", "4453", "show", "capabilities"), "scope options"),
        ):
            with self.subTest(argv=argv):
                result = self.ctl(*argv)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(marker, result.stderr)

    def test_tech_support_covers_readonly_views(self):
        result = self.ctl("show", "tech-support")
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("EIGRP features:",
                     "  integration          : Native Linux EIGRP",
                     "  ipv4-af              : IPv4 Routing Protocol Support",
                     "  evn                  : Not Supported",
                     "EIGRP technical support: eigrprulez IPv4 AS 4453 VRF default",
                     "EIGRP-IPv4 Protocol for AS(4453)",
                     "Address-family Neighbors for AS(4453)",
                     "Address-family Interfaces for AS(4453)",
                     "Topology Table for AS(4453)",
                     "Traffic Statistics for AS(4453)",
                     "Address-family Timers for AS(4453)",
                     "Accounting for AS(4453)",
                     "Events for AS(4453)",
                     "198.51.100.48/32", "Event log capacity 500 stored 1"):
            self.assertIn(text, result.stdout)
        self.assertEqual(result.stdout.count("198.51.100.48/32"), 1)
        self.assertNotIn("OpenEIGRP Client:", result.stdout)
        self.assertNotIn("=== Protocols ===", result.stdout)
        result = self.ctl("show", "tech-support", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(isinstance(json.loads(x), dict)
                            for x in result.stdout.splitlines()))
        records = [json.loads(line) for line in result.stdout.splitlines()]
        identities = [item for item in records if item.get("kind") == "identity"]
        self.assertEqual(len(identities), 1)
        daemon = identities[0]
        self.assertEqual(daemon["daemon"], "openeigrpd")
        self.assertEqual(daemon["release"],
                         json.loads(self.ctl("--json", "version").stdout)["build"])
        self.assertEqual(daemon["integration"], "Native Linux EIGRP")
        self.assertEqual(daemon["os"], "Linux")
        for key in ("kernel", "architecture", "distribution",
                    "distribution_version", "distribution_codename"):
            self.assertIn(key, daemon)
        capabilities = [item for item in records if item.get("kind") == "capability"]
        self.assertTrue(capabilities)
        # The fixture deliberately returns a distinct portable capability
        # release so the client does not silently overwrite daemon records.
        self.assertEqual(capabilities[0]["release"], 'test"fixture')
        for feature in ("ipv4", "ipv6", "tlv1", "tlv2", "wide_metrics",
                        "bfd", "snmp", "manet", "mtr", "evn"):
            self.assertIn(feature, capabilities[0])
        self.assertIn('"kind":"timer"', result.stdout)
        self.assertIn('"kind":"scope","asn":4453,"afi":4', result.stdout)
        result = self.ctl("tech-support", "-a", "4453", "-4")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_protocols_frr_named_exact(self):
        expected = (
            "\nEIGRP-IPv4 Protocol for AS(4453)\n"
            "  Named instance: eigrprulez, VRF: default\n"
            "  Address-family state: active\n"
            "  Router-ID: 192.0.2.1\n"
            "  Topology: base (0)\n"
            "  Metric weights: K1=1, K2=0, K3=1, K4=0, K5=0, K6=0\n"
            "  Metric version: classic (32-bit)\n"
            "  Administrative distance: internal 90, external 170\n"
            "  Maximum paths: 4, maximum hops: 100\n"
            "  Variance: 2\n"
            "  Active-time configured: 180 seconds\n"
            "  Reachable topology prefixes: 48\n"
            "  Redistributed prefixes: 3\n"
        )
        result = self.ctl("show", "protocols")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)
        tech = self.ctl("show", "tech-support")
        self.assertEqual(tech.returncode, 0, tech.stderr)
        self.assertIn(expected, tech.stdout)
        headings = (
            "Address-family Interfaces", "Address-family Neighbors",
            "Topology Table", "Traffic Statistics", "Address-family Timers",
            "Accounting", "Events",
        )
        offsets = [tech.stdout.find("VR(eigrprulez) " + heading)
                   for heading in headings]
        self.assertEqual(offsets, sorted(offsets))
        self.assertTrue(all(offset >= 0 for offset in offsets))

    def test_static_neighbors_accept_frr_detail_option(self):
        ordinary = self.ctl("show", "neighbors", "static")
        detailed = self.ctl("show", "neighbors", "static", "detail")
        self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
        self.assertEqual(detailed.returncode, 0, detailed.stderr)
        self.assertEqual(detailed.stdout, ordinary.stdout)
        self.assertIn("Static Neighbors for AS(4453)", detailed.stdout)

    def test_inactive_named_address_family(self):
        self.stop()
        self.start(inactive=True)
        proto = self.ctl("show", "protocols")
        self.assertEqual(proto.returncode, 0, proto.stderr)
        self.assertIn("Address-family state: shutdown (named instance)", proto.stdout)
        self.assertIn("Router-ID: automatic", proto.stdout)
        self.assertIn("Protocol parameters: runtime not available", proto.stdout)
        self.assertNotIn("  Metric weights:", proto.stdout)
        for view, marker in (
            ("neighbors", "No EIGRP neighbors matched"),
            ("topology", "Topology table is empty"),
            ("traffic", "Runtime traffic counters are not available"),
            ("timers", "Runtime timer data is not available"),
            ("accounting", "Runtime accounting data is not available"),
            ("events", "Event history is unavailable while the address-family is not running"),
        ):
            with self.subTest(view=view):
                result = self.ctl("show", view)
                self.assertEqual(result.returncode, 0, result.stderr)
                if view != "accounting":
                    self.assertIn("Runtime state: address-family not active", result.stdout)
                else:
                    self.assertNotIn("  Runtime state:", result.stdout)
                self.assertIn(marker, result.stdout)
                self.assertNotIn("192.0.2.2", result.stdout)
        no_active = self.ctl("show", "interfaces")
        self.assertEqual(no_active.stdout.count("No active EIGRP interfaces"), 1)
        inactive_detail = self.ctl("show", "interfaces", "detail")
        self.assertEqual(inactive_detail.returncode, 0, inactive_detail.stderr)
        self.assertIn("Configured interface", inactive_detail.stdout)
        self.assertNotIn("No active EIGRP interfaces", inactive_detail.stdout)
        interfaces = self.ctl("show", "interfaces", "detail")
        self.assertEqual(interfaces.returncode, 0, interfaces.stderr)
        self.assertIn("Configured interface", interfaces.stdout)
        self.assertNotIn("TLV1 peers:", interfaces.stdout)
        tech = self.ctl("show", "tech-support")
        self.assertEqual(tech.returncode, 0, tech.stderr)
        self.assertIn("Protocol parameters: runtime not available", tech.stdout)
        self.assertNotIn("No ACTIVE/SIA deadlines scheduled", tech.stdout)
        denied = self.ctl("clear", "events")
        self.assertNotEqual(denied.returncode, 0)

    def test_no_configured_address_families(self):
        self.stop()
        self.start(no_config=True)
        protocols = self.ctl("show", "protocols")
        self.assertEqual(protocols.returncode, 0, protocols.stderr)
        self.assertEqual(protocols.stdout,
                         "No named EIGRP address families are configured\n")
        tech = self.ctl("show", "tech-support")
        self.assertEqual(tech.returncode, 0, tech.stderr)
        self.assertIn("EIGRP features:", tech.stdout)
        self.assertTrue(tech.stdout.endswith(
            "\nNo named EIGRP address families are configured\n"))
        self.assertNotIn("EIGRP technical support:", tech.stdout)

    def test_readonly_nonroot_and_clear_denied(self):
        if os.geteuid() != 0:
            self.skipTest("dropping to a separate UID requires root")
        readonly = self.ctl("show", "neighbors", uid=65534)
        self.assertEqual(readonly.returncode, 0, readonly.stderr)
        denied = self.ctl("clear", "events", uid=65534)
        self.assertEqual(denied.returncode, 1)
        self.assertIn("permission denied", denied.stderr)
        self.assertIn("1  event", self.ctl("show", "events").stdout)

    def test_mismatched_build_is_blocked_but_local_version_remains_available(self):
        alternate = Path(self.tmp.name) / "eigrp-alternate"
        # Compile a genuine second OpenEIGRP build identity rather than
        # overriding the canonical generated header on the command line.
        (Path(self.tmp.name) / "eigrp_features.h").write_text(
            (ROOT / "eigrp/code/eigrp_features.h").read_text())
        generated = subprocess.run([
            sys.executable, str(ROOT / "tools/gen-version.py"),
            "--version", "0.9.9", "--output",
            str(Path(self.tmp.name) / "eigrp_build_version.h"),
        ], capture_output=True, text=True)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        built = subprocess.run([
            os.environ.get("CC", "cc"), "-std=c11", "-D_GNU_SOURCE",
            "-Wall", "-Wextra", "-Werror",
            "-I", self.tmp.name, "-I", str(ROOT / "eigrp/code"),
            "-I", str(CODE),
            str(CODE / "eigrp_linux_ctl.c"), str(CODE / "eigrp_linux_version.c"),
            "-o", str(alternate),
        ], capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        def invoke(*args):
            return subprocess.run([str(alternate), "--socket", str(self.path),
                                   *args], capture_output=True, text=True)
        refused = invoke("show", "capabilities")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("incompatible", refused.stderr)
        self.assertNotIn("kind=capability", refused.stdout)
        version = invoke("--version")
        self.assertEqual(version.returncode, 0)
        self.assertIn("Integration build    : 0.9.9", version.stdout)
        self.assertEqual(self.ctl("show", "capabilities").returncode, 0)

    def test_version_option_order(self):
        earlier = self.ctl("--json", "--version")
        later = self.ctl("--version", "--json")
        self.assertEqual(earlier.returncode, 0, earlier.stderr)
        self.assertEqual(later.returncode, 0, later.stderr)
        self.assertEqual(json.loads(earlier.stdout), json.loads(later.stdout))
        self.assertEqual(json.loads(earlier.stdout)["ipc"], 5)

    def test_unavailable_restart_and_concurrent_clients(self):
        self.stop()
        missing = self.ctl("show", "neighbors")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("cannot connect to daemon", missing.stderr)
        self.assertEqual(self.ctl("version").returncode, 0)
        self.start()
        self.assertEqual(self.ctl("show", "neighbors").returncode, 0)
        def run(_):
            return self.ctl("show", "topology", "--json")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(24)))
        self.assertTrue(all(r.returncode == 0 for r in results),
                        [r.stderr for r in results if r.returncode])


class InvalidReplyTests(unittest.TestCase):
    """Use crafted IPC replies to check client framing, not server validation."""

    @classmethod
    def setUpClass(cls):
        ControlTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        ControlTests.tearDownClass.__func__(cls)

    ctl = ControlTests.ctl

    def setUp(self):
        self.path = Path(self.tmp.name) / (self._testMethodName + "-mock.sock")
        self.daemon = None

    def tearDown(self):
        if self.path.exists():
            self.path.unlink()

    def mock(self, header, body=b""):
        errors = []
        ready = threading.Event()

        def serve():
            try:
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.bind(str(self.path))
                    sock.listen(1)
                    ready.set()
                    with sock.accept()[0] as conn:
                        # Every client now identifies before operational IPC.
                        pending = 16 + 128
                        while pending:
                            received = conn.recv(pending)
                            if not received:
                                raise OSError("missing identity exchange")
                            pending -= len(received)
                        identity = (json.dumps({"kind": "identity",
                                                "release": self.release},
                                               separators=(",", ":")) + "\n").encode()
                        conn.sendall(struct.pack(HEADER, MAGIC, 5, 11, 0, 0,
                                                 len(identity)) + identity)
                        request = bytearray()
                        while len(request) < 16:
                            received = conn.recv(16 - len(request))
                            if not received:
                                raise OSError("missing operation")
                            request.extend(received)
                        if struct.unpack(HEADER, request)[2] == 2:
                            pending = 4
                            while pending:
                                received = conn.recv(pending)
                                if not received:
                                    raise OSError("missing protocol offset")
                                pending -= len(received)
                        conn.sendall(header + body)
            except OSError as ex:
                errors.append(ex)
                ready.set()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(2))
        return thread, errors

    def test_invalid_headers_and_payloads(self):
        for header, body, expected in (
            (struct.pack(HEADER, 0, 1, 1, 0, 0, 0), b"", "incompatible"),
            (struct.pack(HEADER, MAGIC, 1, 1, 0, 0, 0), b"", "incompatible"),
            (struct.pack(HEADER, MAGIC, 5, 2, 0, 0, 0), b"", "incompatible"),
            (struct.pack(HEADER, MAGIC, 5, 1, 0, 0, 16385), b"", "malformed"),
            (struct.pack(HEADER, MAGIC, 5, 1, 0, 2, 0), b"", "malformed"),
            (struct.pack(HEADER, MAGIC, 5, 1, 0, 0, 3), b"BAD", "JSON-lines"),
            (struct.pack(HEADER, MAGIC, 5, 1, 0, 1, 0), b"", "pagination"),
            (struct.pack(HEADER, MAGIC, 5, 1, 5, 0, 0), b"", "unsupported"),
        ):
            with self.subTest(expected=expected):
                thread, errors = self.mock(header, body)
                result = self.ctl("show", "capabilities")
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)
                thread.join(timeout=2)
                self.assertFalse(errors, errors)
                self.path.unlink()

    def test_multiple_instances_are_not_reported_as_ambiguous(self):
        # The client accepts the protocol listing. With this single-shot mock,
        # the scoped read fails at the absent daemon rather than at selection.
        body = (b'{"kind":"protocol","instance":"one","runtime":true,'
                b'"afi":4,"asn":4453,"vrf":"default"}\n'
                b'{"kind":"protocol","instance":"two","runtime":true,'
                b'"afi":6,"asn":4453,"vrf":"default"}\n')
        header = struct.pack(HEADER, MAGIC, 5, 2, 0, 0, len(body))
        thread, errors = self.mock(header, body)
        result = self.ctl("show", "neighbors")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("multiple matching instances", result.stderr)
        thread.join(timeout=2)
        self.assertFalse(errors)

    def test_disconnect_mid_reply(self):
        thread, _ = self.mock(struct.pack(HEADER, MAGIC, 5, 1, 0, 0, 16), b"abc")
        result = self.ctl("show", "capabilities")
        self.assertEqual(result.returncode, 1)
        self.assertIn("connection lost", result.stderr)
        thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
