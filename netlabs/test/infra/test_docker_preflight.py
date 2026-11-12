#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise Docker-daemon preflight at native packaging and Netlab entry points."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class DockerPreflightTest(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix='docker-preflight-')
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.docker_log = self.root / 'docker.log'
        self.ssh_log = self.root / 'ssh.log'
        docker = self.bin / 'docker'
        docker.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$DOCKER_MOCK_LOG"\n'
            'if [ "$1" = info ]; then exit "${DOCKER_MOCK_INFO_RC:-0}"; fi\n'
            'echo "unexpected Docker operation: $*" >&2\n'
            'exit 99\n', encoding='utf8')
        docker.chmod(0o755)
        ssh = self.bin / 'ssh'
        ssh.write_text(
            '#!/bin/sh\n'
            'for arg do last=$arg; done\n'
            'printf "%s\\n" "$last" >> "$SSH_MOCK_LOG"\n'
            'case "$last" in\n'
            '  true) exit 0 ;;\n'
            '  *"docker info"*) exit "${REMOTE_DOCKER_MOCK_INFO_RC:-0}" ;;\n'
            'esac\n'
            'echo "unexpected SSH operation: $last" >&2\n'
            'exit 99\n', encoding='utf8')
        ssh.chmod(0o755)
        self.remote_config = self.root / 'remote.cfg'
        self.remote_config.write_text(
            'REMOTE_HOST=mock-appliance\n'
            'REMOTE_USER=netlab\n'
            'REMOTE_SSH_PORT=22\n', encoding='utf8')
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        DOCKER_MOCK_LOG=str(self.docker_log),
                        SSH_MOCK_LOG=str(self.ssh_log),
                        REMOTE_LAB_CONFIG=str(self.remote_config),
                        DOCKER_MOCK_INFO_RC='1')

    def invoke(self, *args, **env):
        return subprocess.run(list(args), cwd=ROOT, env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=15)

    def test_netlab_image_mode_fails_before_remote_regressions(self):
        result = self.invoke('bash', 'netlabs/tools/remote-uut.sh',
                             '--all', '--image', 'dev-test')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('local Docker daemon unavailable', result.stderr)
        self.assertFalse(self.ssh_log.exists())
        self.assertEqual(self.docker_log.read_text().splitlines(), ['info'])
        self.assertNotIn('Regression summary:', result.stdout)

    def test_netlab_single_scenario_image_mode_fails_early(self):
        result = self.invoke('bash', 'netlabs/tools/remote-uut.sh',
                             '--scenario', '2R-neighbor', '--image', 'dev-test')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('local Docker daemon unavailable', result.stderr)
        self.assertFalse(self.ssh_log.exists())

    def test_netlab_release_image_checks_remote_daemon_first(self):
        result = self.invoke('bash', 'netlabs/tools/remote-uut.sh',
                             '--all', REMOTE_DOCKER_MOCK_INFO_RC='1')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Docker daemon unavailable on mock-appliance', result.stderr)
        self.assertFalse(self.docker_log.exists())
        self.assertEqual(len(self.ssh_log.read_text().splitlines()), 2)
        self.assertNotIn('Running all regressions', result.stdout)

    def test_netlab_list_does_not_require_any_docker(self):
        result = self.invoke('bash', 'netlabs/tools/remote-uut.sh', '--list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.ssh_log.exists())
        self.assertFalse(self.docker_log.exists())

    def test_remote_daemon_fails_before_allocating_session(self):
        data = self.root / 'remote-data'
        stage = data / 'runs' / 'upload.fixture'
        work = stage / 'netlabs' / 'labs' / '101'
        work.mkdir(parents=True)
        (work / 'topology.yml').write_text('name: eigrp-test\n')
        result = self.invoke('bash', 'netlabs/tools/remote/runner.sh',
                             'lab-start', str(stage), '101',
                             REMOTE_LAB_DATA=str(data),
                             REMOTE_LAB_STATE=str(data / 'session'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Docker daemon unavailable on Netlab appliance', result.stderr)
        self.assertFalse((data / 'session' / 'session').exists())
        self.assertEqual(self.docker_log.read_text().splitlines(), ['info'])


if __name__ == '__main__':
    unittest.main()
