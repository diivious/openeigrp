#!/usr/bin/env python3
"""Executable Git/worktree regression tests for the source formatter."""

# Copyright (C) 2026 Donnie V. Savage

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
SCENARIO = Path(__file__).resolve().parent / "uut/style-fix.yaml"


class StyleFixTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="style-fix-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tools").mkdir()
        shutil.copy2(TOOLS / "style-fix.py", self.root / "tools/style-fix.py")
        shutil.copy2(TOOLS / "style-check.py", self.root / "tools/style-check.py")
        (self.root / ".clang-format").write_text("BasedOnStyle: LLVM\n")
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", ".clang-format", "tools")
        self.git("commit", "-qm", "Initial")

        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.formatter = self.bin / "clang-format"
        self.formatter.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "argv = sys.argv[1:]\n"
            "if '--version' in argv:\n"
            "    print(os.environ.get('FAKE_VERSION', 'clang-format version 18.1.8'))\n"
            "    sys.exit(0)\n"
            "if os.environ.get('FORMAT_FAIL'):\n"
            "    sys.exit(7)\n"
            "if '-i' not in argv or '--style=file' not in argv:\n"
            "    sys.exit(9)\n"
            "if os.environ.get('STYLE_FIX_LOG'):\n"
            "    with open(os.environ['STYLE_FIX_LOG'], 'a') as f:\n"
            "        f.write(json.dumps(argv) + '\\n')\n"
            "ranges = [a[8:].split(':') for a in argv if a.startswith('--lines=')]\n"
            "selected = {n for start, end in ranges\n"
            "            for n in range(int(start), int(end) + 1)}\n"
            "for name in (a for a in argv if not a.startswith('-')):\n"
            "    p = pathlib.Path(name)\n"
            "    lines = p.read_bytes().splitlines(keepends=True)\n"
            "    for i, line in enumerate(lines, 1):\n"
            "        if not ranges or i in selected:\n"
            "            lines[i - 1] = line.replace(b'BADFMT', b'GOODFMT')\n"
            "    p.write_bytes(b''.join(lines))\n"
        )
        self.formatter.chmod(0o755)
        self.env = dict(os.environ)
        self.env.pop("CLANG_FORMAT", None)
        self.env["PATH"] = str(self.bin) + os.pathsep + os.environ.get("PATH", "")
        self.env["STYLE_FIX_LOG"] = str(self.root / "format.log")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args])

    def file(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def fix(self, *args, env=None, cwd=None):
        return subprocess.run([sys.executable, str(self.root / "tools/style-fix.py"), *args],
                              cwd=cwd or self.root, env=env or self.env,
                              capture_output=True, text=True)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_selection_scenarios(self):
        scenarios = json.loads(SCENARIO.read_text())
        # Restore the same baseline between command modes.
        changed = self.file("eigrp/code/changed.c", "int BADFMT;\n")
        untouched = self.file("eigrp/code/untouched.c", "int BADFMT;\n")
        self.file(".gitignore", "ignored.c\n")
        self.git("add", "eigrp/code", ".gitignore")
        self.git("commit", "-qm", "Baseline")
        for case in scenarios["cases"]:
            with self.subTest(case=case["name"]):
                changed.write_text("int BADFMT;\nint BADFMT;\n")
                untracked = self.file("linux/code/new.c", "int BADFMT;\n")
                staged = self.file("linux/code/new-staged.h", "int BADFMT;\n")
                self.git("add", "linux/code/new-staged.h")
                ignored = self.file("ignored.c", "int BADFMT;\n")

                self.assert_success(self.fix(*case["args"]))
                self.assertEqual(changed.read_text(),
                                 f"int {case['changed_first']};\nint GOODFMT;\n")
                self.assertEqual(untouched.read_text(),
                                 f"int {case['unchanged']};\n")
                self.assertEqual(untracked.read_text(), "int GOODFMT;\n")
                self.assertEqual(staged.read_text(), "int GOODFMT;\n")
                self.assertEqual(ignored.read_text(), "int BADFMT;\n")
                # No staged index writes; consumers must restage after formatting.
                self.assertIn(b"BADFMT", self.git("show", ":linux/code/new-staged.h"))
                self.git("reset", "--hard", "-q", "HEAD")
                for path in (untracked, staged, ignored):
                    if path.exists():
                        path.unlink()

    def test_idempotence_and_path_with_spaces(self):
        changed = self.file("linux/code/file with spaces.c", "int BADFMT;\n")
        self.assert_success(self.fix())
        self.assertEqual(changed.read_text(), "int GOODFMT;\n")
        after_first = changed.read_bytes()
        self.assert_success(self.fix())
        self.assertEqual(changed.read_bytes(), after_first)

    def test_deleted_and_symlink_source_are_skipped(self):
        deleted = self.file("linux/code/removed.c", "int BADFMT;\n")
        self.git("add", "linux/code/removed.c")
        self.git("commit", "-qm", "Add old file")
        deleted.unlink()
        target = self.file("target.txt", "int BADFMT;\n")
        (self.root / "linux/code/link.c").symlink_to(target)
        self.assert_success(self.fix())
        self.assertEqual(target.read_text(), "int BADFMT;\n")
        self.assertFalse((self.root / "format.log").exists())

    def test_all_batches_more_than_hundred_files(self):
        paths = [self.file(f"linux/code/file{i:03d}.c", "int BADFMT;\n")
                 for i in range(101)]
        self.assert_success(self.fix("--all"))
        self.assertTrue(all(p.read_text() == "int GOODFMT;\n" for p in paths))
        log = (self.root / "format.log").read_text().splitlines()
        self.assertEqual(len(log), 2)
        self.assertEqual(len(json.loads(log[0])), 102)  # options + 100 files
        self.assertEqual(len(json.loads(log[1])), 3)  # options + one file

    def test_explicit_formatter_override(self):
        self.file("linux/code/check.c", "int BADFMT;\n")
        alternate = self.bin / "clang-format-14"
        shutil.copy2(self.formatter, alternate)
        env = dict(self.env, CLANG_FORMAT=str(alternate))
        result = self.fix(env=env)
        self.assert_success(result)
        self.assertIn(str(alternate), result.stdout)
        self.assertEqual((self.root / "linux/code/check.c").read_text(),
                         "int GOODFMT;\n")

    def test_versioned_binary_discovery(self):
        self.file("linux/code/check.c", "int BADFMT;\n")
        only_bin = self.root / "isolated-bin"
        only_bin.mkdir()
        shutil.copy2(self.formatter, only_bin / "clang-format-18")
        (only_bin / "git").symlink_to(shutil.which("git"))
        env = dict(self.env, PATH=str(only_bin))
        result = self.fix(env=env)
        self.assert_success(result)
        self.assertIn("clang-format-18", result.stdout)

    def test_invalid_version_refused_without_modification(self):
        path = self.file("linux/code/check.c", "int BADFMT;\n")
        result = self.fix(env=dict(self.env, FAKE_VERSION="not clang-format"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid clang-format executable", result.stderr)
        self.assertEqual(path.read_text(), "int BADFMT;\n")

    def test_missing_override_refused_without_modification(self):
        path = self.file("linux/code/check.c", "int BADFMT;\n")
        result = self.fix(env=dict(self.env, CLANG_FORMAT="nonexistent-clang-format"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("CLANG_FORMAT=", result.stderr)
        self.assertEqual(path.read_text(), "int BADFMT;\n")

    def test_formatter_failure_propagated(self):
        path = self.file("linux/code/check.c", "int BADFMT;\n")
        result = self.fix(env=dict(self.env, FORMAT_FAIL="1"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("exit code 7", result.stderr)
        self.assertEqual(path.read_text(), "int BADFMT;\n")

    def test_no_changes_requires_no_formatter(self):
        result = self.fix(env=dict(self.env, CLANG_FORMAT="nonexistent-clang-format"))
        self.assert_success(result)
        self.assertIn("No source files need formatting", result.stdout)

    def test_current_directory_inside_repo(self):
        self.file("linux/code/check.c", "int BADFMT;\n")
        self.assert_success(self.fix(cwd=self.root / "linux/code"))
        self.assertEqual((self.root / "linux/code/check.c").read_text(),
                         "int GOODFMT;\n")


if __name__ == "__main__":
    unittest.main()
