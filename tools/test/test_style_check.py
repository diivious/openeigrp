#!/usr/bin/env python3
"""Executable regression tests for the read-only pre-commit style checker."""

# Copyright (C) 2026 Donnie V. Savage

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "style-check.py"


class StyleCheckTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="style-check-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tools").mkdir()
        shutil.copy2(SCRIPT, self.root / "tools/style-check.py")
        (self.root / ".clang-format").write_text("BasedOnStyle: LLVM\n")
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", ".clang-format", "tools/style-check.py")
        self.git("commit", "-qm", "Initial")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        formatter = self.bin / "clang-format"
        formatter.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "if '--version' in sys.argv:\n"
            "    print('clang-format version 23.1.4')\n"
            "    raise SystemExit(0)\n"
            "source = sys.stdin.buffer.read()\n"
            "sys.stdout.buffer.write(source.replace(b'BADFMT', b'GOODFMT'))\n"
        )
        formatter.chmod(0o755)
        self.env = dict(os.environ)
        self.env["PATH"] = str(self.bin) + os.pathsep + os.environ.get("PATH", "")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args])

    def file(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def run_check(self, *args, env=None):
        return subprocess.run([sys.executable, str(self.root / "tools/style-check.py"), *args],
                              cwd=self.root, capture_output=True, text=True,
                              env=env or self.env)

    def assert_fails_with(self, needle, *args):
        result = self.run_check(*args)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(needle, result.stdout)

    def test_clean_tree_and_read_only(self):
        before = self.git("status", "--porcelain", "-z")
        self.assertEqual(self.run_check().returncode, 0)
        self.assertEqual(before, self.git("status", "--porcelain", "-z"))

    def test_staged_content_checked_even_if_worktree_fixed(self):
        p = self.file("eigrp/code/check.c", "int main(void) { sprintf(buf, \"hello\"); }\n")
        self.git("add", "eigrp/code/check.c")
        p.write_text("int main(void) { snprintf(buf, n, \"hello\"); }\n")
        self.assert_fails_with("[staged] eigrp/code/check.c:1: sprintf()")
        self.assertIn("sprintf", self.git("show", ":eigrp/code/check.c").decode())

    def test_unstaged_violation_detected_separately(self):
        p = self.file("eigrp/code/check.c", "int value;\n")
        self.git("add", "eigrp/code/check.c")
        p.write_text("int value;\nvoid f(void) { strcat(a, b); }\n")
        self.assert_fails_with("[working] eigrp/code/check.c:2: strcat()")

    def test_untracked_files_are_not_changed(self):
        p = self.file("eigrp/code/check.c", "sprintf(a, \"x\");\n")
        before = p.read_bytes()
        self.assert_fails_with("sprintf()")
        self.assertEqual(before, p.read_bytes())
        self.assertIn("?? eigrp/code/check.c", self.git("status", "--short", "--untracked-files=all").decode())

    def test_comment_string_and_identifier_ignored(self):
        self.file("eigrp/code/check.c", "/* sprintf(buf, x); */\n"
                  "const char *s = \"strcpy(x)\";\n"
                  "void my_sprintf(void) {}\n")
        self.assertEqual(self.run_check().returncode, 0)

    def test_untracked_unsafe_api_and_full_tree(self):
        self.file("unix/code/check.c", "void f(void) { strcpy(a, b); }\n")
        self.assert_fails_with("[untracked] unix/code/check.c:1: strcpy()")
        self.git("add", "unix/code/check.c")
        self.git("commit", "-qm", "Legacy issue")
        self.assertEqual(self.run_check().returncode, 0)
        self.assert_fails_with("[tree] unix/code/check.c:1: strcpy()", "--all")

    def test_base_checks_committed_range(self):
        old = self.git("rev-parse", "HEAD").decode().strip()
        self.file("eigrp/code/check.c", "int f(void) { return sprintf(a, \"x\"); }\n")
        self.git("add", "eigrp/code/check.c")
        self.git("commit", "-qm", "New violation")
        self.assertEqual(self.run_check().returncode, 0)
        self.assert_fails_with("[commit] eigrp/code/check.c:1: sprintf()", "--base", old)

    def test_multi_commit_range_covers_both_commits(self):
        old = self.git("rev-parse", "HEAD").decode().strip()
        self.file("eigrp/code/a.c", "strcpy(a, b);\n")
        self.git("add", "eigrp/code/a.c")
        self.git("commit", "-qm", "First")
        self.file("eigrp/code/b.c", "sprintf(a, \"x\");\n")
        self.git("add", "eigrp/code/b.c")
        self.git("commit", "-qm", "Second")
        result = self.run_check("--base", old)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("eigrp/code/a.c:1: strcpy()", result.stdout)
        self.assertIn("eigrp/code/b.c:1: sprintf()", result.stdout)

    def test_unchanged_old_violation_not_rejected_locally(self):
        self.file("eigrp/code/check.c", "int a = sprintf(buffer, \"x\");\nint b;\n")
        self.git("add", "eigrp/code/check.c")
        self.git("commit", "-qm", "Legacy")
        self.file("eigrp/code/check.c", "int a = sprintf(buffer, \"x\");\nint b = 3;\n")
        self.assertEqual(self.run_check().returncode, 0)

    def test_format_whitespace_and_missing_newline(self):
        self.file("eigrp/code/check.c", "int BADFMT;\n")
        self.assert_fails_with("clang-format mismatch")
        self.file("tools/misc.py", "a = 1  \n")
        self.assert_fails_with("trailing whitespace")
        self.file("tools/misc.py", "a = 1")
        self.assert_fails_with("missing newline at EOF")

    def test_frr_rule_scoped_to_adapter(self):
        self.file("eigrp/code/check.c", "snprintf(buf, n, \"x\");\n")
        self.assertEqual(self.run_check().returncode, 0)
        self.file("frr/code/check.c", "snprintf(buf, n, \"x\");\n")
        self.assert_fails_with("use snprintfrr()")

    def test_formatter_version_accepts_distribution_metadata(self):
        self.file("eigrp/code/check.c", "int a;\n")
        formatter = self.bin / "clang-format"
        formatter.write_text(formatter.read_text().replace(
            "clang-format version 23.1.4",
            "clang-format version 23.1.4 (https://github.com/conda-forge/clangdev-feedstock f01eefd7)"))
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_formatter_version_rejects_longer_version_number(self):
        self.file("eigrp/code/check.c", "int a;\n")
        formatter = self.bin / "clang-format"
        formatter.write_text(formatter.read_text().replace(
            "clang-format version 23.1.4", "clang-format version 23.1.40"))
        result = self.run_check()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("expected clang-format version 23.1.4", result.stderr)

    def test_incorrect_formatter_version_is_an_error(self):
        self.file("eigrp/code/check.c", "int a;\n")
        formatter = self.bin / "clang-format"
        formatter.write_text("#!/bin/sh\necho 'clang-format version 18.1.8'\n")
        formatter.chmod(0o755)
        result = self.run_check()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("expected clang-format version 23.1.4", result.stderr)

    def test_missing_formatter_is_an_error(self):
        self.file("eigrp/code/check.c", "int a;\n")
        env = dict(self.env)
        # Isolate PATH so installed system formatters cannot satisfy the check.
        git_bin = self.root / "git-only-bin"
        git_bin.mkdir()
        (git_bin / "git").symlink_to(shutil.which("git"))
        env["PATH"] = str(git_bin)
        self.assertIsNone(shutil.which("clang-format", path=env["PATH"]))
        result = self.run_check(env=env)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("clang-format not found", result.stderr)

    def test_ignored_untracked_not_checked(self):
        self.file(".gitignore", "local.c\n")
        self.file("local.c", "sprintf(a, \"x\");\n")
        self.assertEqual(self.run_check().returncode, 0)

    def test_deleted_lines_do_not_fail(self):
        self.file("eigrp/code/check.c", "sprintf(a, \"x\");\nint a;\n")
        self.git("add", "eigrp/code/check.c")
        self.git("commit", "-qm", "Legacy")
        self.file("eigrp/code/check.c", "int a;\n")
        self.assertEqual(self.run_check().returncode, 0)


if __name__ == "__main__":
    unittest.main()
