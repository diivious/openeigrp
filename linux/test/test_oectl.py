#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable oectl CLI regression over actual Task 11 Unix IPC."""

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
        cls.tmp = tempfile.TemporaryDirectory(prefix="oectl-test-")
        os.chmod(cls.tmp.name, 0o755)
        cls.client = Path(cls.tmp.name) / "oectl"
        cls.fixture = Path(cls.tmp.name) / "mgnt-fixture"
        flags = [os.environ.get("CC", "cc"), "-std=c11", "-D_GNU_SOURCE",
                 "-Wall", "-Wextra", "-Werror", "-pthread", "-g",
                 "-I", str(ROOT / "eigrp" / "code"), "-I", str(CODE)]
        for binary, sources in (
            (cls.client, [CODE / "oectl.c", CODE / "eigrp_linux_version.c"]),
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

    def start(self):
        self.daemon = subprocess.Popen([str(self.fixture), str(self.path)],
                                       stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
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
            "capabilities": "capability: release=test\"fixture",
            "protocols": "protocol: instance=eigrprulez",
            "neighbors": "192.0.2.2",
            "interfaces": "interface: name=eth\"0\\u000asecond",
            "topology": "198.51.100.48/32",
            "routes": "198.51.100.48/32",
            "statistics": "sent_hello=900",
            "events": "event_summary: capacity=500",
            "diagnostics": "timer: type=",
        }
        for view, marker in expected.items():
            with self.subTest(view=view):
                result = self.ctl("show", view)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(marker, result.stdout)
        topology = self.ctl("show", "topology")
        self.assertEqual(topology.stdout.count("/32"), 96)
        self.assertEqual(topology.stdout.count("successor"), 96)

    def test_client_has_no_core_link_dependency(self):
        symbols = subprocess.run(["nm", "-u", str(self.client)],
                                 text=True, capture_output=True)
        self.assertEqual(symbols.returncode, 0, symbols.stderr)
        self.assertNotIn(" eigrp_", symbols.stdout)
        version = self.ctl("version", "--json")
        self.assertEqual(version.returncode, 0, version.stderr)
        data = json.loads(version.stdout)
        self.assertEqual(data["kind"], "integration")
        self.assertEqual(data["ipc"], 2)
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

    def test_clear_is_the_only_mutation(self):
        self.assertIn("count=1", self.ctl("show", "events").stdout)
        cleared = self.ctl("-a", "4453", "-4", "clear", "events")
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertIn("Events cleared", cleared.stdout)
        self.assertIn("count=0", self.ctl("show", "events").stdout)
        for command in (("clear", "neighbors"), ("set", "debug"),
                        ("configure", "terminal"), ("reset", "routes")):
            result = self.ctl(*command)
            self.assertEqual(result.returncode, 2)
            self.assertIn("unsupported command", result.stderr)

    def test_cli_grammar_and_scope_errors(self):
        self.assertEqual(self.ctl("help").returncode, 0)
        local = self.ctl("version")
        self.assertIn("OpenEIGRP Client:\n", local.stdout)
        self.assertIn("  Management IPC       : 2", local.stdout)
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
            (("show",), "unsupported command"),
            (("show", "neighbor"), "unsupported command"),
            (("--what", "show", "neighbors"), "unknown option"),
            (("--asn" ,), "missing value"),
            (("--asn", "65536", "show", "neighbors"), "invalid value"),
            (("--afi", "5", "show", "neighbors"), "AFI must be"),
            (("--vrf-id", "22", "show", "neighbors"), "requires --asn and --afi"),
            (("--asn", "1", "--afi", "4", "show", "neighbors"),
             "not running"),
            (("--asn", "4453", "--afi", "6", "show", "neighbors"),
             "not running"),
            (("--asn", "4453", "show", "capabilities"), "scope options"),
        ):
            with self.subTest(argv=argv):
                result = self.ctl(*argv)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(marker, result.stderr)

    def test_tech_support_covers_readonly_views(self):
        result = self.ctl("show", "tech-support")
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("OpenEIGRP Client:", "Host Version:",
                     "  Integration          : Native Linux EIGRP",
                     "OpenEIGRP Version:",
                     "  ipv4-af              : Supported",
                     "  evn                  : Not Supported",
                     "=== Protocols ===", "=== neighbors (AS 4453",
                     "=== interfaces (AS 4453", "=== topology (AS 4453",
                     "=== routes (AS 4453", "=== statistics (AS 4453",
                     "=== events (AS 4453", "=== diagnostics (AS 4453",
                     "198.51.100.48/32", "event: number="):
            self.assertIn(text, result.stdout)
        self.assertEqual(result.stdout.count("198.51.100.48/32"), 4)
        self.assertNotIn("=== Capabilities ===", result.stdout)
        self.assertNotIn("=== Daemon identity ===", result.stdout)
        self.assertEqual(result.stdout.count("OpenEIGRP Version:"), 1)
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

    def test_readonly_nonroot_and_clear_denied(self):
        if os.geteuid() != 0:
            self.skipTest("dropping to a separate UID requires root")
        readonly = self.ctl("show", "neighbors", uid=65534)
        self.assertEqual(readonly.returncode, 0, readonly.stderr)
        denied = self.ctl("clear", "events", uid=65534)
        self.assertEqual(denied.returncode, 1)
        self.assertIn("permission denied", denied.stderr)
        self.assertIn("count=1", self.ctl("show", "events").stdout)

    def test_mismatched_build_is_blocked_but_local_version_remains_available(self):
        alternate = Path(self.tmp.name) / "oectl-alternate"
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
            str(CODE / "oectl.c"), str(CODE / "eigrp_linux_version.c"),
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
        self.assertEqual(json.loads(earlier.stdout)["ipc"], 2)

    def test_unavailable_restart_and_concurrent_clients(self):
        self.stop()
        missing = self.ctl("show", "neighbors")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("cannot connect to daemon", missing.stderr)
        self.assertEqual(self.ctl("version").returncode, 0)
        self.start()
        self.assertEqual(self.ctl("show", "neighbors").returncode, 0)
        def run(_):
            return self.ctl("show", "routes", "--json")
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
                        conn.sendall(struct.pack(HEADER, MAGIC, 2, 11, 0, 0,
                                                 len(identity)) + identity)
                        pending = 16
                        while pending:
                            received = conn.recv(pending)
                            if not received:
                                raise OSError("missing operation")
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
            (struct.pack(HEADER, MAGIC, 2, 2, 0, 0, 0), b"", "incompatible"),
            (struct.pack(HEADER, MAGIC, 2, 1, 0, 0, 16385), b"", "malformed"),
            (struct.pack(HEADER, MAGIC, 2, 1, 0, 2, 0), b"", "malformed"),
            (struct.pack(HEADER, MAGIC, 2, 1, 0, 0, 3), b"BAD", "JSON-lines"),
            (struct.pack(HEADER, MAGIC, 2, 1, 0, 1, 0), b"", "pagination"),
            (struct.pack(HEADER, MAGIC, 2, 1, 5, 0, 0), b"", "unsupported"),
        ):
            with self.subTest(expected=expected):
                thread, errors = self.mock(header, body)
                result = self.ctl("show", "capabilities")
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)
                thread.join(timeout=2)
                self.assertFalse(errors, errors)
                self.path.unlink()

    def test_ambiguous_scope_is_rejected(self):
        body = (b'{"kind":"protocol","instance":"one","runtime":true,'
                b'"afi":4,"asn":4453,"vrf":"default"}\n'
                b'{"kind":"protocol","instance":"two","runtime":true,'
                b'"afi":6,"asn":4453,"vrf":"default"}\n')
        header = struct.pack(HEADER, MAGIC, 2, 2, 0, 0, len(body))
        thread, errors = self.mock(header, body)
        result = self.ctl("show", "neighbors")
        self.assertEqual(result.returncode, 1)
        self.assertIn("multiple matching instances", result.stderr)
        thread.join(timeout=2)
        self.assertFalse(errors)

    def test_disconnect_mid_reply(self):
        thread, _ = self.mock(struct.pack(HEADER, MAGIC, 2, 1, 0, 0, 16), b"abc")
        result = self.ctl("show", "capabilities")
        self.assertEqual(result.returncode, 1)
        self.assertIn("connection lost", result.stderr)
        thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
