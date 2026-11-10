#!/usr/bin/env python3
# Copyright (C) 2026 Donnie V. Savage
"""Exercise the shared release resolver against real Git history, offline."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "version.sh"


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openeigrp-version-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.remote = self.base / "origin.git"
        self.repo = self.base / "project"
        self.git(None, "init", "-q", "--bare", str(self.remote))
        self.git(None, "init", "-q", str(self.repo))
        self.git(self.repo, "branch", "-M", "master")
        self.git(self.repo, "config", "user.name", "Version Test")
        self.git(self.repo, "config", "user.email", "version@example.invalid")
        (self.repo / "README").write_text("0.4\n")
        self.commit()
        self.git(self.repo, "tag", "-a", "rel-0.4.0", "-m", "0.4")
        (self.repo / "README").write_text("0.5\n")
        self.commit()
        self.git(self.repo, "tag", "-a", "rel-0.5.0", "-m", "0.5")
        self.git(self.repo, "remote", "add", "origin", str(self.remote))
        self.git(self.repo, "push", "-q", "origin", "master", "--tags")

    @staticmethod
    def git(repo, *args):
        cmd = ["git"] + (["-C", str(repo)] if repo else []) + list(args)
        return subprocess.run(cmd, check=True, text=True, capture_output=True)

    def commit(self):
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "fixture")

    def run_version(self, *args, repo=None, user="diivious"):
        env = {**os.environ, "USER": user}
        return subprocess.run([str(SCRIPT), *args, "--repo", str(repo or self.repo)],
                              capture_output=True, text=True, env=env)

    def test_latest_release_and_development_labels(self):
        for args, version in ((["--latest"], "0.5.0"),
                              (["--local"], "0.5.0-diivious-test"),
                              (["--local", "diivious-test"], "0.5.0-diivious-test"),
                              (["--local", "X-Y"], "0.5.0-X-Y")):
            with self.subTest(args=args):
                result = self.run_version(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), version)

    def test_tag_added_to_master_becomes_next_version(self):
        (self.repo / "README").write_text("0.6\n")
        self.commit()
        self.git(self.repo, "tag", "-a", "rel-0.6.0", "-m", "0.6")
        self.git(self.repo, "push", "-q", "origin", "master", "--tags")
        result = self.run_version("--local", "diivious-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.6.0-diivious-test")
        self.assertEqual(self.run_version("--latest").stdout.strip(), "0.6.0")

    def test_new_remote_release_visible_to_stale_local_master(self):
        # The resolver must fetch origin/master and new tags, not rely on
        # the checkout's potentially stale local master branch.
        other = self.base / "publisher"
        self.git(None, "clone", "-q", str(self.remote), str(other))
        self.git(other, "config", "user.name", "Publisher")
        self.git(other, "config", "user.email", "publisher@example.invalid")
        self.git(other, "checkout", "-q", "master")
        (other / "README").write_text("0.6\n")
        self.git(other, "add", "README")
        self.git(other, "commit", "-qm", "new release")
        self.git(other, "tag", "-a", "rel-0.6.0", "-m", "0.6")
        self.git(other, "push", "-q", "origin", "master", "--tags")
        result = self.run_version("--local", "X-Y")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.6.0-X-Y")
        self.assertEqual(self.git(self.repo, "tag", "--points-at", "HEAD").stdout.strip(),
                         "rel-0.5.0", "the local worktree must not be changed")

    def test_ignores_higher_tag_on_unmerged_branch(self):
        self.git(self.repo, "checkout", "-qb", "experiment")
        (self.repo / "README").write_text("unreleased\n")
        self.commit()
        self.git(self.repo, "tag", "-a", "rel-9.0.0", "-m", "unmerged")
        self.git(self.repo, "push", "-q", "origin", "experiment", "--tags")
        self.assertEqual(self.run_version("--latest").stdout.strip(), "0.5.0")
        self.assertEqual(self.run_version("--local", "X-Y").stdout.strip(),
                         "0.5.0-X-Y")

    def test_invalid_label_rejected(self):
        for label in ("../bad", "bad space", "dev_under", "dev+alpha", "0.5.0-foo", "foo/slash"):
            with self.subTest(label=label):
                result = self.run_version("--local", label)
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.run_version("--local", "0.6.0").stdout.strip(),
                         "0.6.0", "exact release versions must be unchanged")

    def test_deleted_remote_release_is_not_reused(self):
        self.git(self.repo, "push", "-q", "origin", ":refs/tags/rel-0.5.0")
        result = self.run_version("--local", "X-Y")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.4.0-X-Y")

    def test_missing_origin_fails_without_fallback(self):
        self.git(self.repo, "remote", "remove", "origin")
        result = self.run_version("--local", "diivious-test")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("origin", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
