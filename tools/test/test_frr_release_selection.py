#!/usr/bin/env python3
"""Validate Docker release selection using the actual shell helpers, offline."""

# Copyright (C) 2026 Donnie V. Savage

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "docker.sh"
TAG = "frr-10.7.1"
SHA = "f8c0b08dcb0c78f9e42b9b86ae70b049e4e617c1"


class DockerReleaseSelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="frr-release-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.source = SCRIPT.read_text().split('[[ $# -gt 0 ]] || { usage;', 1)[0]
        self.assertIn("load_frr_config()", self.source)
        self.config = self.root / "docker.cfg"
        self.config.write_text(
            "FRR_REPO=https://github.com/FRRouting/frr.git\n"
            f"FRR_TAG={TAG}\nFRR_SHA={SHA}\n"
        )
        git = self.bin / "git"
        git.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  'ls-remote --tags https://github.com/diivious/openeigrp.git refs/tags/rel-*')\n"
            "    printf '%s\\trefs/tags/rel-1.9.0\\n' " + "a" * 40 + "\n"
            "    printf '%s\\trefs/tags/rel-1.10.0\\n' " + "b" * 40 + "\n"
            "    printf '%s\\trefs/tags/rel-1.10.0^{}\\n' " + "c" * 40 + "\n"
            "    printf '%s\\trefs/tags/rel-testing\\n' " + "d" * 40 + "\n"
            "    ;;\n"
            f"  'ls-remote https://github.com/FRRouting/frr.git refs/tags/{TAG} refs/tags/{TAG}^{{}}')\n"
            f"    printf '%s\\trefs/tags/{TAG}\\n%s\\trefs/tags/{TAG}^{{}}\\n' "
            f"5481ece12b4fbd873f70225ae50d32100bad1b91 {SHA}\n"
            "    ;;\n"
            "  *) exit 9 ;;\n"
            "esac\n"
        )
        git.chmod(0o755)

    def execute(self, command):
        env = dict(os.environ, PATH=str(self.bin) + ":" + os.environ.get("PATH", ""))
        return subprocess.run(["bash", "-c", self.source + "\n" + command],
                              cwd=self.root, env=env, text=True, capture_output=True)

    def test_configuration_selects_official_frr_tag_and_sha(self):
        result = self.execute(f'load_frr_config "{self.config}"; '
                              'test "$frr_url" = https://github.com/FRRouting/frr.git; '
                              'test "$frr_release_tag" = frr-10.7.1; '
                              'test "$pinned_frr_sha" = "$(resolve_sha "$frr_url" "$frr_release_tag")"')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_latest_selects_numeric_version_not_lexical_sort(self):
        result = self.execute('latest_version')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '1.10.0')

    def test_configuration_requires_commit_sha(self):
        self.config.write_text('FRR_REPO=https://github.com/FRRouting/frr.git\n'
                               f'FRR_TAG={TAG}\n')
        result = self.execute(f'load_frr_config "{self.config}"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid FRR_SHA', result.stderr)

    def test_configuration_rejects_malformed_repo(self):
        self.config.write_text('FRR_REPO=not-a-repository\n'
                               f'FRR_TAG={TAG}\nFRR_SHA={SHA}\n')
        result = self.execute(f'load_frr_config "{self.config}"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid FRR_REPO', result.stderr)

    def test_release_tag_and_development_ref_distinct(self):
        result = self.execute('ref_valid frr-10.7.1 && '
                              'ref_valid refs/heads/docker-update && '
                              '! source_git_tag refs/heads/docker-update')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
