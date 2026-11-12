#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Offline native configuration parsing and semantic contract regressions."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
UNIX = ROOT / "libunix" / "code"
CODE = ROOT / "linux" / "code"
TEST = ROOT / "libunix" / "test"
EXAMPLE = ROOT / "linux" / "config" / "openeigrpd.conf.example"


def run(*args):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True)


class LinuxConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="openeigrp-config-")
        cls.checker = Path(cls.tmp.name) / "openeigrpd-config"
        cls.harness = Path(cls.tmp.name) / "config-harness"
        base = [os.environ.get("CC", "cc"), "-std=c11", "-D_POSIX_C_SOURCE=200809L",
                "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "eigrp" / "code"),
                "-I", str(UNIX), "-I", str(CODE), "-pthread"]
        for name, sources in (
            (cls.checker, [CODE / "openeigrpd-config.c", UNIX / "eigrp_unix_config.c"]),
            (cls.harness, [UNIX / "eigrp_unix_config.c", UNIX / "eigrp_unix_config_apply.c",
                           TEST / "config_harness.c", CODE / "eigrp_linux_policy.c",
                           CODE / "eigrp_linux_config.c",
                           UNIX / "eigrp_unix_keychain.c"]),
        ):
            built = run(*base, *(str(src) for src in sources), "-o", str(name))
            if built.returncode:
                raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def validate(self, text, valid, expected_line=None):
        path = Path(self.tmp.name) / "case.conf"
        path.write_text(text)
        result = run(str(self.checker), "-t", "-f", str(path))
        self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)
        if expected_line is not None:
            self.assertIn(f":{expected_line}:", result.stderr)
        return result

    def test_unix_dispatch_compiles_without_linux_headers(self):
        # Portable regression harnesses compile this source with only the
        # shared EIGRP/Unix includes, including on macOS.
        source = UNIX / "eigrp_unix_config_apply.c"
        object_path = Path(self.tmp.name) / "unix-config-apply.o"
        built = run(os.environ.get("CC", "cc"), "-std=c11",
                    "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra",
                    "-Werror", "-I", str(ROOT / "eigrp" / "code"),
                    "-I", str(UNIX), "-c", str(source),
                    "-o", str(object_path))
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)

    def test_sample_dual_stack_configuration(self):
        sample = EXAMPLE.read_text()
        self.assertIn("router eigrp eigrprulez", sample)
        self.assertIn("address-family ipv4 unicast autonomous-system 4453", sample)
        self.assertIn("address-family ipv6 unicast autonomous-system 6473", sample)
        result = run(str(self.checker), "-t", "-f", str(EXAMPLE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("syntax valid", result.stdout)

    def test_actual_portable_semantic_dispatch_and_rejection(self):
        result = run(str(self.harness))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unix config tests: PASS", result.stdout)

    def test_invalid_lexing_nesting_and_excessive_input(self):
        head = "router eigrp eigrprocks\n address-family ipv4 autonomous-system 4453\n"
        tail = " exit-address-family\nexit\n"
        cases = (
            (head + "  unimplemented policy-list bad\n" + tail, 3),
            (head + "  eigrp router-id 192.0.2.999\n" + tail, 3),
            (head + "  network 10.0.0.0 0.255.0.255\n" + tail, 3),
            (head + "  network 10.0.0.0/33\n" + tail, 3),
            (head + "  af-interface eth0\n   hello-interval -1\n"
             "  exit-af-interface\n" + tail, 4),
            (head + "  af-interface eth0\n   authentication mode hmac-sha-256\n"
             "  exit-af-interface\n" + tail, 4),
            (head + "  topology base\n   maximum-prefix 100\n"
             "  exit-af-topology\n" + tail, 4),
            (head + "  shutdown garbage\n" + tail, 3),
            (head + "  redistrib\n" + tail, 3),
            (head + "  " + "z" * 600 + "\n" + tail, 3),
            (head + "  topology base\n" + tail, 4),
            ("exit\n", 1),
            ("router eigrp eigrprocks\n address-family ipv7 autonomous-system 4453\n", 2),
            ("router eigrp eigrprocks\n address-family ipv6 vrf other "
             "autonomous-system 6473\n", 2),
            ("router eigrp eigrprocks\n address-family ipv4 autonomous-system 0\n", 2),
            ("router eigrp eigrprocks\n address-family ipv4 autonomous-system 65536\n", 2),
            ("router eigrp eigrprocks\n address-family ipv4 autonomous-system 4453\n"
             "  network 10.0.0.0\n" + tail + "extra\n", 6),
        )
        for text, line in cases:
            with self.subTest(case=text[:75]):
                self.validate(text, False, line)

    def test_ipv4_ipv6_scope_and_valid_directives(self):
        self.validate("router eigrp eigrprocks\n address-family ipv6 unicast "
                      "autonomous-system 6473\n  af-interface eth0\n"
                      "   passive-interface\n   no next-hop-self\n"
                      "  exit-af-interface\n exit-address-family\nexit\n", True)
        self.validate("router eigrp eigrprocks\n address-family ipv6 "
                      "autonomous-system 6473\n  network 2001:db8::/64\n"
                      " exit-address-family\nexit\n", False, 3)

    def test_validator_repeated_runs_and_failure_isolation(self):
        base = "router eigrp eigrprocks\n address-family ipv4 " \
               "autonomous-system 4453\n  shutdown\n  no shutdown\n" \
               " exit-address-family\nexit\n"
        for _ in range(3):
            self.validate(base, True)
        self.validate(base.replace("no shutdown", "filter unknown"), False, 4)
        self.validate(base, True)

    def test_native_policy_grammar_and_reference_validation(self):
        # Objects can precede or follow their consumers in the staged plan.
        valid = ("router eigrp eigrprocks\n"
                 " address-family ipv4 autonomous-system 4453\n"
                 "  distribute-list prefix lan-in in eth0\n"
                 "  topology base\n"
                 "   redistribute static route-map static-map\n"
                 "  exit-af-topology\n"
                 " exit-address-family\nexit\n"
                 "ip prefix-list lan-in seq 10 permit 10.0.0.0/8 ge 16 le 32\n"
                 "ip access-list lan-out seq 7 deny 198.51.100.0/24\n"
                 "route-map static-map deny 5 match tag 17\n"
                 "route-map static-map permit 10 match prefix-list lan-in\n")
        self.validate(valid, True)
        v6 = ("ipv6 prefix-list six seq 1 permit 2001:db8::/32 ge 48 le 64\n"
              "ipv6 access-list six-acl seq 10 deny 2001:db8::/48\n"
              "router eigrp eigrprocks\n"
              " address-family ipv6 autonomous-system 6473\n"
              "  distribute-list prefix six out\n"
              " exit-address-family\nexit\n")
        self.validate(v6, True)
        self.validate(v6.replace("  distribute-list prefix six out",
                                 "  distribute-list access six-acl out eth0"), True)
        self.validate(v6.replace("prefix six out", "prefix other out")
                      + "ip prefix-list other seq 1 permit 10.0.0.0/8\n",
                      False, 5)
        # Native MD5 activation is qualified for IPv4 only for now.
        self.validate("key-chain md5-ipv6 key 7 secret secret-value\n"
                      "router eigrp eigrprocks\n"
                      " address-family ipv6 autonomous-system 6473\n"
                      "  af-interface eth0\n"
                      "   authentication key-chain md5-ipv6\n"
                      "   authentication mode md5\n"
                      "  exit-af-interface\n exit-address-family\nexit\n",
                      False, 5)
        self.validate(valid.replace("ip prefix-list lan-in seq 10 permit "
                                    "10.0.0.0/8 ge 16 le 32\n", ""), False, 3)
        self.validate(valid.replace("route-map static-map permit 10 match prefix-list lan-in",
                                    "route-map static-map permit 10 match prefix-list missing"),
                      False, 12)
        self.validate(valid.replace("seq 10 permit 10.0.0.0/8 ge 16 le 32",
                                    "seq 10 permit 10.0.0.0/8 ge 25 le 24"),
                      False, 9)
        self.validate(valid + "route-map static-map deny 5\n", False, 13)
        self.validate(valid.replace("distribute-list prefix lan-in",
                                    "distribute-list route-map lan-in"), False, 3)
        self.validate(valid.replace("redistribute static route-map static-map",
                                    "redistribute ospf route-map static-map"), False, 5)

    def test_keychain_permissions_and_auth_ownership(self):
        secure = ("key-chain lan-key key 10 secret sample-secret\n"
                  "router eigrp eigrprocks\n"
                  " address-family ipv4 autonomous-system 4453\n"
                  "  af-interface eth0\n"
                  "   authentication key-chain lan-key\n"
                  "   authentication mode md5\n"
                  "  exit-af-interface\n"
                  " exit-address-family\nexit\n")
        path = Path(self.tmp.name) / "secure.conf"
        path.write_text(secure)
        path.chmod(0o600)
        result = run(str(self.checker), "-t", "-f", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("sample-secret", result.stdout + result.stderr)
        path.chmod(0o644)
        result = run(str(self.checker), "-t", "-f", str(path))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("sample-secret", result.stdout + result.stderr)
        self.validate(secure.replace("lan-key key 10 secret sample-secret",
                                     "missing-key key 10 secret sample-secret"), False, 1)
        path.write_text(secure.replace("   authentication key-chain lan-key\n", ""))
        path.chmod(0o600)
        result = run(str(self.checker), "-t", "-f", str(path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(":5:", result.stderr)
        # Comment markers inside a secret must not truncate key material.
        path.write_text(secure.replace("sample-secret", "sample#secret!strong"))
        path.chmod(0o600)
        result = run(str(self.checker), "-t", "-f", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        path.write_text(secure.replace("sample-secret", "sample-secret # comment"))
        result = run(str(self.checker), "-t", "-f", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        path.write_text(secure.replace("sample-secret", "sample-secret unexplained"))
        result = run(str(self.checker), "-t", "-f", str(path))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("sample-secret", result.stderr)
        path.write_text(secure.replace("   authentication mode md5\n", ""))
        result = run(str(self.checker), "-t", "-f", str(path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(":5:", result.stderr)
        self.validate("router eigrp eigrprocks\n address-family ipv4 "
                      "autonomous-system 4453\n  af-interface eth0\n"
                      "   authentication mode hmac-sha-256 7 encoded\n"
                      "  exit-af-interface\n exit-address-family\nexit\n", False, 4)

    def test_keychain_secret_storage_boundary(self):
        # The parser and the Unix keychain must agree on the 81-byte limit.
        path = Path(self.tmp.name) / "secret-limit.conf"
        for length, valid in ((81, True), (82, False)):
            path.write_text("key-chain test-key key 1 secret "
                            + ("x" * length) + "\n")
            path.chmod(0o600)
            checked = run(str(self.checker), "-t", "-f", str(path))
            self.assertEqual(checked.returncode == 0, valid,
                             checked.stdout + checked.stderr)

    def test_missing_file_and_arguments(self):
        missing = run(str(self.checker), "-t", "-f", "/does-not-exist/openeigrpd.conf")
        self.assertEqual(missing.returncode, 1)
        for argv in ((), ("-f", str(EXAMPLE)), ("-t", "-invalid")):
            with self.subTest(argv=argv):
                result = run(str(self.checker), *argv)
                self.assertEqual(result.returncode, 2)

    def test_clang_format(self):
        formatter = shutil.which("clang-format")
        if not formatter:
            self.skipTest("clang-format unavailable")
        sources = [UNIX / "eigrp_unix_config.c", UNIX / "eigrp_unix_config_apply.c",
                   UNIX / "eigrp_unix_config_private.h", UNIX / "eigrp_unix_config.h",
                   CODE / "openeigrpd-config.c", TEST / "config_harness.c", CODE / "eigrp_linux_policy.c",
                   CODE / "eigrp_linux_config.c", CODE / "eigrp_linux_config.h",
                   UNIX / "eigrp_unix_keychain.c"]
        checked = run(formatter, "--dry-run", "--Werror", *map(str, sources))
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
