#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Test the build-time AMD64 Debian image without a Docker daemon."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'netlabs/tools/package-image.sh'
DOCKERFILE = ROOT / 'netlabs/test/package/1R-lifecycle/Dockerfile'
PACKAGE = 'sha256:' + '2' * 64


class LocalPackageImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.bin = self.dir / 'bin'
        self.bin.mkdir()
        self.dockerfile = self.dir / 'Dockerfile'
        shutil.copy2(DOCKERFILE, self.dockerfile)
        self.log = self.dir / 'docker.log'
        stub = self.bin / 'docker'
        stub.write_text('''#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >>"$MOCK_LOG"
case "$1" in
    info) exit 0 ;;
    build) [[ "${FAIL_BUILD:-0}" != 1 ]] || exit 4 ;;
    image)
        [[ "$2" == inspect && "$3" == --format ]] || exit 5
        case "$4" in
            '{{.Id}}') echo "$PKG_ID" ;;
            '{{.Os}}/{{.Architecture}}') echo "${OUTPUT_PLATFORM:-linux/amd64}" ;;
            *) exit 6 ;;
        esac ;;
    *) exit 7 ;;
esac
''')
        stub.chmod(0o755)
        self.env = {**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                    'MOCK_LOG': str(self.log), 'PKG_ID': PACKAGE}

    def run_helper(self, *args, **env):
        argv = ['bash', str(SCRIPT), *(args or ('build', str(self.dockerfile)))]
        return subprocess.run(argv, text=True, capture_output=True,
                              env={**self.env, **env})

    def test_amd64_build_uses_platform_not_native_debian_alias(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), PACKAGE)
        calls = self.log.read_text()
        self.assertIn('build --platform linux/amd64 --pull --tag '
                      'openeigrp-package:netlab', calls)
        self.assertNotIn('pull ', calls.replace(' --pull ', ' '))
        self.assertNotIn('debian:latest', calls)

    def test_init_sets_mount_propagation_before_exec(self):
        log = self.dir / 'init.log'
        target = self.bin / 'systemd-test'
        target.write_text('#!/bin/sh\nprintf "exec %s\\n" "$*" >> "$INIT_LOG"\n')
        target.chmod(0o755)
        mount = self.bin / 'mount'
        mount.write_text('#!/bin/sh\nprintf "mount %s\\n" "$*" >> "$INIT_LOG"\n')
        mount.chmod(0o755)
        script = DOCKERFILE.parent / 'init.sh'
        for _ in range(2):  # first boot and docker restart
            result = subprocess.run(['/bin/sh', str(script), str(target), '--system'],
                                    capture_output=True, text=True,
                                    env={**self.env, 'INIT_LOG': str(log)})
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.read_text().splitlines(),
                         ['mount --make-rshared /', 'exec --system'] * 2)

    def test_init_rejects_unavailable_mount_propagation(self):
        log = self.dir / 'init.log'
        mount = self.bin / 'mount'
        mount.write_text('#!/bin/sh\nprintf "mount %s\\n" "$*" >> "$INIT_LOG"\nexit 7\n')
        mount.chmod(0o755)
        script = DOCKERFILE.parent / 'init.sh'
        result = subprocess.run(['/bin/sh', str(script), '/bin/true'],
                                capture_output=True, text=True,
                                env={**self.env, 'INIT_LOG': str(log)})
        self.assertEqual(result.returncode, 7)
        self.assertEqual(log.read_text().strip(), 'mount --make-rshared /')

    def test_init_is_the_image_entrypoint(self):
        dockerfile = DOCKERFILE.read_text()
        self.assertIn('COPY init.sh /usr/local/sbin/openeigrp-package-init', dockerfile)
        self.assertIn('ENTRYPOINT ["/usr/local/sbin/openeigrp-package-init"]', dockerfile)
        self.assertIn('CMD ["/lib/systemd/systemd"]', dockerfile)
        self.assertIn('iproute2 mount python3', dockerfile)

    def test_systemd_image_allows_package_service_actions(self):
        # The stock Debian Docker policy vetoes maintainer-script stop.
        # The disposable systemd guest must remove it after apt setup.
        dockerfile = DOCKERFILE.read_text()
        marker = 'RUN rm -f /usr/sbin/policy-rc.d'
        self.assertIn(marker, dockerfile)
        self.assertLess(dockerfile.index('apt-get install'), dockerfile.index(marker))

    def test_failed_build_is_reported(self):
        result = self.run_helper(FAIL_BUILD='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(PACKAGE, result.stdout)
        self.assertIn('build failed', result.stderr)

    def test_rejects_wrong_built_architecture(self):
        result = self.run_helper(OUTPUT_PLATFORM='linux/arm64')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('linux/arm64', result.stderr)

    def test_rejects_missing_dockerfile(self):
        result = self.run_helper('build', str(self.dir / 'missing' / 'Dockerfile'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Dockerfile is missing', result.stderr)
        self.assertFalse(self.log.exists())

    def test_only_build_mode_is_supported(self):
        result = self.run_helper('ensure', str(self.dockerfile))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Usage:', result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
