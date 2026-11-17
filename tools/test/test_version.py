#!/usr/bin/env python3
# Copyright (C) 2026 Donnie V. Savage
"""Exercise release selection and persistent developer counters using real Git."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/version.sh"
COUNT = ROOT / "tools/build-count.py"


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
        self.git(self.repo, "tag", "-a", "rel-0.4.0", "-m", "historical")
        (self.repo / "README").write_text("0.5\n")
        self.commit()
        self.git(self.repo, "tag", "-a", "rel-0.5", "-m", "new format")
        self.git(self.repo, "remote", "add", "origin", str(self.remote))
        self.git(self.repo, "push", "-q", "origin", "master", "--tags")

    @staticmethod
    def git(repo, *args):
        return subprocess.run(["git"] + (["-C", str(repo)] if repo else []) + list(args),
                              check=True, text=True, capture_output=True)

    def commit(self):
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-qm", "fixture")

    def version(self, *args, repo=None, user="JOE"):
        return subprocess.run([str(SCRIPT), *args, "--repo", str(repo or self.repo)],
                              capture_output=True, text=True,
                              env={**os.environ, "GITHUB_EVENT_NAME": "push", "USER": user})

    def test_latest_release_and_counter(self):
        self.assertEqual(self.version("--latest").stdout.strip(), "0.5")
        for n, label in ((1, "JOE"), (2, "JOE"), (3, "dev.alpha")):
            with self.subTest(number=n):
                args = ["--local"] if label == "JOE" else ["--local", label]
                result = self.version(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f"0.5.{n}-{label}.dev")
        self.assertEqual(self.version("--current", "dev.alpha").stdout.strip(),
                         "0.5.3-dev.alpha.dev")
        self.assertEqual(self.version("--latest").stdout.strip(), "0.5")
        self.assertEqual(self.version("--current").stdout.strip(), "0.5.3-JOE.dev")

    def test_ci_is_fixed_and_does_not_allocate_a_local_counter(self):
        for run in range(1, 4):
            with self.subTest(run=run):
                result = self.version("--ci", user="invalid/user")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "0.5.0-merge.dev")
        self.assertNotEqual(self.version("--current").returncode, 0)
        self.assertEqual(self.version("--local").stdout.strip(), "0.5.1-JOE.dev")
        self.assertEqual(self.version("--ci").stdout.strip(), "0.5.0-merge.dev")
        self.assertEqual(self.version("--local").stdout.strip(), "0.5.2-JOE.dev")

    def test_ci_is_anchored_to_commit_not_moving_origin_master(self):
        previous = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        (self.repo / "README").write_text("0.6\n")
        self.commit()
        self.git(self.repo, "tag", "-a", "rel-0.6", "-m", "0.6")
        self.git(self.repo, "push", "-q", "origin", "master", "--tags")
        self.git(self.repo, "checkout", "-q", "--detach", previous)
        self.assertEqual(self.version("--latest").stdout.strip(), "0.6")
        self.assertEqual(self.version("--ci").stdout.strip(), "0.5.0-merge.dev")

    def test_pr_ci_uses_merge_target_parent_not_unreleased_pr_tag(self):
        # Construct a pull_request-style merge: HEAD^1 is master, while
        # HEAD^2 includes a release-looking tag that master does not contain.
        self.git(self.repo, "checkout", "-qb", "feature")
        (self.repo / "feature").write_text("new feature\n")
        self.commit()
        self.git(self.repo, "tag", "-a", "rel-0.6", "-m", "not released")
        self.git(self.repo, "push", "-q", "origin", "feature", "--tags")
        self.git(self.repo, "checkout", "-q", "master")
        self.git(self.repo, "merge", "-q", "--no-ff", "feature", "-m", "PR merge")
        result = subprocess.run([str(SCRIPT), "--ci", "--repo", str(self.repo)],
                                capture_output=True, text=True,
                                env={**os.environ, "GITHUB_EVENT_NAME": "pull_request"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.5.0-merge.dev")

    def test_reset_and_new_release_reset_counter(self):
        self.assertIn("0.5.1-JOE.dev", self.version("--local").stdout)
        result = subprocess.run(["python3", str(COUNT), "reset", "--repo", str(self.repo)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.version("--current").returncode, 0)
        self.assertEqual(self.version("--local").stdout.strip(), "0.5.1-JOE.dev")
        (self.repo / "README").write_text("0.6\n")
        self.commit()
        self.git(self.repo, "tag", "-a", "rel-0.6", "-m", "0.6")
        self.git(self.repo, "push", "-q", "origin", "master", "--tags")
        self.assertEqual(self.version("--local").stdout.strip(), "0.6.1-JOE.dev")

    def test_legacy_tag_can_seed_migration(self):
        self.git(self.repo, "push", "-q", "origin", ":refs/tags/rel-0.5")
        self.assertEqual(self.version("--latest").stdout.strip(), "0.4")
        self.assertEqual(self.version("--local").stdout.strip(), "0.4.1-JOE.dev")

    def test_ignores_higher_unmerged_or_invalid_tag(self):
        self.git(self.repo, "checkout", "-qb", "experiment")
        (self.repo / "README").write_text("unmerged\n")
        self.commit()
        for tag in ("rel-9.0", "rel-0.256", "rel-7.8.1"):
            self.git(self.repo, "tag", "-a", tag, "-m", tag)
        self.git(self.repo, "push", "-q", "origin", "experiment", "--tags")
        self.assertEqual(self.version("--latest").stdout.strip(), "0.5")

    def test_missing_origin_and_bad_labels_fail(self):
        for label in ("../bad", "dev_under", "bad space", "dev+alpha"):
            self.assertNotEqual(self.version("--local", label).returncode, 0)
        self.git(self.repo, "remote", "remove", "origin")
        result = self.version("--local")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_parallel_allocations_are_unique(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.version("--local"), range(12)))
        self.assertTrue(all(r.returncode == 0 for r in results),
                        [r.stderr for r in results if r.returncode])
        self.assertEqual({r.stdout.strip() for r in results},
                         {f"0.5.{n}-JOE.dev" for n in range(1, 13)})


if __name__ == "__main__":
    unittest.main(verbosity=2)
