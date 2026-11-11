#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Verify local build preflights before Git or Docker build work starts."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class DockerBuildPreflightTest(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix='docker-build-preflight-')
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        self.docker_log = self.root / 'docker.log'
        self.git_log = self.root / 'git.log'
        docker = bin_dir / 'docker'
        docker.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$DOCKER_MOCK_LOG"\n'
            'if [ "$1" = info ]; then exit 1; fi\n'
            'echo "unexpected Docker operation: $*" >&2\n'
            'exit 99\n', encoding='utf8')
        docker.chmod(0o755)
        git = bin_dir / 'git'
        git.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$GIT_MOCK_LOG"\n'
            'exit 99\n', encoding='utf8')
        git.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bin_dir) + ':' + os.environ['PATH'],
                        DOCKER_MOCK_LOG=str(self.docker_log),
                        GIT_MOCK_LOG=str(self.git_log))

    def invoke(self, *args):
        return subprocess.run(args, cwd=ROOT, env=self.env,
                              capture_output=True, text=True, timeout=15)

    def test_container_build_stops_before_git_or_docker_work(self):
        result = self.invoke('bash', 'frr/tools/docker.sh', '--local', 'dev-test')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Docker daemon unavailable', result.stderr)
        self.assertEqual(self.docker_log.read_text().splitlines(), ['info'])
        self.assertFalse(self.git_log.exists())

    def test_package_build_stops_before_creating_dist(self):
        output = self.root / 'dist'
        result = self.invoke('sh', 'linux/packaging/build-packages.sh',
                             '--version', '1.2.3', '--output', str(output))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Docker daemon unavailable', result.stderr)
        self.assertEqual(self.docker_log.read_text().splitlines(), ['info'])
        self.assertFalse(self.git_log.exists())
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
