#!/usr/bin/env python3
"""Executable native Linux documentation checks.

Run after `make linux` for command/config tests. Missing binaries are reported
as skips so repository-wide style checks still work on macOS. Set
OPENEIGRP_DOCS_REQUIRE_BINARIES=1 for mandatory native execution and
OPENEIGRP_DOCS_REQUIRE_RENDERER=1 for mandatory man rendering.
"""

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
LINUX = ROOT / "linux"
MAN = LINUX / "packaging" / "man"
BIN = LINUX / "code"
REQUIRE_BINARIES = os.getenv("OPENEIGRP_DOCS_REQUIRE_BINARIES") == "1"
REQUIRE_RENDERER = os.getenv("OPENEIGRP_DOCS_REQUIRE_RENDERER") == "1"
PAGES = ("openeigrpd.8", "openeigrpd.conf.5", "oectl.8")


class DocumentationTests(unittest.TestCase):
    def command(self, program, *args):
        return subprocess.run(
            [str(program), *map(str, args)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=12,
            check=False,
        )

    def binary(self, name):
        path = BIN / name
        if not path.is_file() or not os.access(path, os.X_OK):
            if REQUIRE_BINARIES:
                self.fail(f"native {name} is required; run `make linux`")
            self.skipTest(f"{name} not built (run `make linux`)")
        return path

    def test_mdoc_layout_and_cross_references(self):
        for filename in PAGES:
            with self.subTest(page=filename):
                content = (MAN / filename).read_text(encoding="utf-8")
                self.assertRegex(content, r"(?m)^\.Dd \w+ \d+, \d{4}$")
                self.assertRegex(content, r"(?m)^\.Dt [A-Z.]+ [58]$")
                self.assertRegex(content, r"(?m)^\.Os$")
                self.assertNotRegex(content, r"(?m)^\.(?:TH|SH|TP|PP|BR|RI)\b")
                self.assertIn(".Sh SEE ALSO", content)
                for linked in ("openeigrpd", "openeigrpd.conf", "oectl"):
                    if linked != filename.rsplit(".", 1)[0]:
                        self.assertRegex(
                            content,
                            rf"(?m)^\.Xr {re.escape(linked)} [58]",
                        )
                self.assertEqual(content.count(".Bl "), content.count(".El"))
                self.assertEqual(content.count(".Bd "), content.count(".Ed"))

    def test_man_rendering(self):
        mandoc = shutil.which("mandoc")
        groff = shutil.which("groff")
        if not mandoc and not groff:
            if REQUIRE_RENDERER:
                self.fail("install mandoc or groff for strict man rendering")
            self.skipTest("no mandoc/groff on PATH; rendering not verified")
        for page in PAGES:
            with self.subTest(page=page):
                path = MAN / page
                args = [mandoc, "-T", "utf8", str(path)] if mandoc else [
                    groff, "-mandoc", "-Tutf8", str(path)
                ]
                result = subprocess.run(
                    args, capture_output=True, text=True, timeout=15, check=False
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(page.rsplit(".", 1)[0].upper(), result.stdout.upper())
                self.assertGreater(len(result.stdout), 150)

    def test_installation_source(self):
        builder = (LINUX / "packaging" / "build-deb.sh").read_text()
        install_makefile = (LINUX / "Makefile").read_text()
        container_builder = (LINUX / "packaging" / "build-packages.sh").read_text()
        for page in PAGES:
            source = MAN / page
            self.assertTrue(source.is_file(), source)
            self.assertIn(f'"$root/linux/packaging/man/{page}"', builder)
            self.assertIn(f'"$pkg/usr/share/man/man{page[-1]}/{page}"', builder)
            self.assertIn(f'packaging/man/{page}', install_makefile)
        self.assertNotIn("linux/man/", builder)
        self.assertNotIn("/src/linux/man", container_builder)
        self.assertIn("cp -a /src/linux/packaging /work/linux/packaging", container_builder)
        service = (LINUX / "packaging" / "openeigrpd.service").read_text()
        self.assertIn("man:openeigrpd(8)", service)
        self.assertIn("man:openeigrpd.conf(5)", service)

    def test_linux_documentation_navigation(self):
        readme = (LINUX / "README.md").read_text(encoding="utf-8")
        developer = (LINUX / "specs" / "developer.md").read_text(encoding="utf-8")
        maintainer = (LINUX / "specs" / "maintainer.md").read_text(encoding="utf-8")
        self.assertIn("[Developer guide](specs/developer.md)", readme)
        self.assertIn("[Maintainer guide](specs/maintainer.md)", readme)
        self.assertIn("Native Linux integration developer guide", developer)
        self.assertIn("make linux", developer)
        for preserved in (
            "--daemon /path/to/real/openeigrpd --output ./dist",
            "build-packages.sh --version 0.1.0 --arch arm64",
            "dpkg-deb --ctrl-tarfile",
            "OPENEIGRP_PACKAGE_SYSTEM_TEST=1",
            "try-restarted",
            "LoadCredential=",
        ):
            with self.subTest(detail=preserved):
                self.assertIn(preserved, maintainer)
        self.assertFalse((LINUX / "packaging" / "README.md").exists())
        self.assertFalse((LINUX / "man").exists())

    def test_document_links(self):
        # All repository Markdown entry points must survive document moves.
        # Skip generated build evidence; only source documentation is checked.
        failures = []
        checked = 0
        for path in sorted(ROOT.rglob("*.md")):
            relative = path.relative_to(ROOT)
            if any(part in (".git", "dist", "logs", ".pytest_cache")
                   for part in relative.parts):
                continue
            checked += 1
            content = path.read_text(encoding="utf-8")
            for match in re.finditer(r"!?\[[^]\n]*\]\(([^)\n]+)\)", content):
                link = match.group(1).strip().split(" ", 1)[0].strip("<>")
                if link.startswith(("#", "mailto:", "https:", "http:", "data:")):
                    continue
                target = unquote(link.split("#", 1)[0].split("?", 1)[0])
                if not target:
                    continue
                if not (path.parent / target).resolve().exists():
                    line = content.count("\n", 0, match.start()) + 1
                    failures.append(f"{relative}:{line}: {link}")
        self.assertGreater(checked, 0)
        self.assertFalse(failures, "Broken Markdown links:\n" + "\n".join(failures))

    def test_documented_make_targets(self):
        make = shutil.which("make")
        if not make:
            self.skipTest("make unavailable")
        result = subprocess.run(
            [make, "-n", "packages", "VERSION=1.2.3"], cwd=ROOT,
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('build-packages.sh --version "$version" --arch', result.stdout)
        for name in ("README.md", "specs/quickstart.md", "specs/developer-guide.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("make all", text)
            self.assertNotIn("make portable-build", text)
            self.assertNotIn("make check", text)
            self.assertNotIn("make help", text)

    def test_native_configuration_examples(self):
        validator = self.binary("openeigrpd-config")
        daemon = self.binary("openeigrpd")
        data = (MAN / "openeigrpd.conf.5").read_text()
        blocks = re.findall(r"(?ms)^\.Bd -literal -offset indent\n(.*?)^\.Ed$", data)
        examples = [text for text in blocks if text.startswith("router eigrp ")]
        self.assertEqual(len(examples), 2, "expected IPv4 and IPv6 examples")
        with tempfile.TemporaryDirectory(prefix="oe-doc-") as tmp:
            for label, sample in zip(("ipv4", "ipv6"), examples):
                with self.subTest(example=label):
                    path = Path(tmp) / f"{label}.conf"
                    path.write_text(sample, encoding="utf-8")
                    for executable in (validator, daemon):
                        result = self.command(executable, "-t", "-f", path)
                        self.assertEqual(result.returncode, 0, result.stderr)
            bad = Path(tmp) / "unsupported.conf"
            bad.write_text(
                "router eigrp bad\n"
                " address-family ipv6 unicast autonomous-system 6473\n"
                "  network 2001:db8::/64\n"
                " exit-address-family\nexit\n",
                encoding="utf-8",
            )
            result = self.command(validator, "-t", "-f", bad)
            self.assertNotEqual(result.returncode, 0, "unsupported IPv6 network accepted")

    def test_daemon_documented_options(self):
        daemon = self.binary("openeigrpd")
        self.assertEqual(self.command(daemon, "-h").returncode, 0)
        self.assertEqual(self.command(daemon, "-V").returncode, 0)
        self.assertEqual(self.command(daemon, "--bad-option").returncode, 2)
        self.assertEqual(
            self.command(daemon, "-t", "-f", "relative.conf").returncode, 2
        )

    def test_oectl_documented_commands(self):
        oectl = self.binary("oectl")
        for options in ("help", "version", "--version", "--help"):
            with self.subTest(option=options):
                self.assertEqual(self.command(oectl, options).returncode, 0)
        self.assertEqual(self.command(oectl, "show", "unsupported").returncode, 2)
        self.assertEqual(self.command(oectl, "show", "routes", "--afi", "5").returncode, 2)

        # A deliberately absent socket verifies each command reaches socket
        # handling rather than being rejected as an unsupported CLI command.
        with tempfile.TemporaryDirectory(prefix="oe-doc-sock-") as tmp:
            absent = str(Path(tmp) / "missing.sock")
            views = (
                "capabilities", "protocols", "neighbors", "interfaces",
                "topology", "routes", "statistics", "traffic", "events",
                "diagnostics", "tech-support", "version",
            )
            for view in views:
                with self.subTest(view=view):
                    args = ["--socket", absent, "show", view]
                    if view not in ("capabilities", "protocols", "tech-support", "version"):
                        args += ["--asn", "4453", "--afi", "4"]
                    self.assertEqual(self.command(oectl, *args).returncode, 1)
            self.assertEqual(
                self.command(oectl, "--socket", absent, "clear", "events",
                             "--asn", "4453", "--afi", "4").returncode, 1
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
