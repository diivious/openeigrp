#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise the real AMD64 build wrapper using isolated executable builders."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class BuildBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'openeigrp'
        self.bin = Path(self.temp.name) / 'bin'
        self.bin.mkdir()
        for path in ('netlabs/tools/docker-amd.sh',
                     'netlabs/tools/package-artifacts.py',
                     'netlabs/tools/package-image.sh',
                     'frr/tools/docker.sh',
                     'linux/packaging/build-packages.sh',
                     'tools/version.sh', 'tools/build-version.sh'):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if path in ('netlabs/tools/docker-amd.sh', 'netlabs/tools/package-image.sh'):
                shutil.copy2(ROOT / path, target)
            else:
                target.write_text('#!/usr/bin/env bash\nexit 2\n')
            target.chmod(0o755)
        (self.root / 'netlabs/test/package/1R-lifecycle').mkdir(parents=True)
        shutil.copy2(ROOT / 'netlabs/test/package/1R-lifecycle/Dockerfile',
                     self.root / 'netlabs/test/package/1R-lifecycle/Dockerfile')
        self.write('tools/version.sh', '''#!/bin/sh
printf '0.5.9-test.dev\\n'
''')
        self.write('tools/build-version.sh', '''#!/bin/sh
[ "$2" = 0.5.9-test.dev ]
''')
        self.write('frr/tools/docker.sh', '''#!/bin/bash
printf 'docker-build:%s:%s\\n' "$EIGRP_BUILD_VERSION" "$*" >>"$CALL_LOG"
''')
        self.write('linux/packaging/build-packages.sh', '''#!/bin/bash
printf 'package-build:%s\\n' "$*" >>"$CALL_LOG"
printf 'new package\\n' >"$NETLAB_TEST_ROOT/dist/openeigrpd_0.5.9-test.dev_amd64.deb"
''')
        self.write('netlabs/tools/package-artifacts.py', '''#!/usr/bin/env python3
import hashlib, json, pathlib, sys
if sys.argv[1] != 'download': raise SystemExit(2)
if __import__('os').environ.get('NO_PUBLISHED_RELEASE') == '1':
    print('null')
    raise SystemExit(0)
folder = pathlib.Path(sys.argv[sys.argv.index('--dist') + 1])
name = 'openeigrpd_0.5_amd64.deb'
data = b'published package\\n'
(folder / name).write_bytes(data)
print(json.dumps({'package': name, 'version': '0.5', 'release': 'rel-0.5',
    'sha256': hashlib.sha256(data).hexdigest(),
    'url': 'https://github.com/diivious/openeigrp/releases/download/rel-0.5/' + name}))
''')
        self.write_bin('docker', '''#!/bin/bash
printf 'docker:%s\\n' "$*" >>"$CALL_LOG"
if [[ "$1" == image && "$2" == inspect ]]; then
  if [[ "$4" == '{{.Os}}/{{.Architecture}}' ]]; then
    printf 'linux/amd64\\n'
  else
    printf 'sha256:%064d\\n' 0
  fi
  exit 0
fi
if [[ "$1" == run && "$*" == *'dpkg --compare-versions'* && "${FAIL_VERSION:-0}" == 1 ]]; then
  exit 1
fi
exit 0
''')
        self.log = Path(self.temp.name) / 'calls'
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        CALL_LOG=str(self.log), NETLAB_TEST_ROOT=str(self.root))

    def write(self, name, text):
        path = self.root / name
        path.write_text(text)
        path.chmod(0o755)

    def write_bin(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def run_bundle(self, **env):
        return subprocess.run([str(self.root / 'netlabs/tools/docker-amd.sh'), 'test'],
                              capture_output=True, text=True, env={**self.env, **env})

    def test_builds_matching_versions_and_verified_release(self):
        proc = self.run_bundle()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        manifest = json.loads((self.root/'dist/netlabs-amd64.json').read_text())
        self.assertEqual(manifest['old']['release'], 'rel-0.5')
        self.assertEqual(manifest['new']['version'], '0.5.9-test.dev')
        self.assertEqual(manifest['image'], 'openeigrp:test')
        self.assertEqual(manifest['image_id'], 'sha256:' + '0'*64)
        self.assertEqual(manifest['package_image'], 'openeigrp-package:netlab')
        self.assertEqual(manifest['package_image_id'], 'sha256:' + '0'*64)
        self.assertEqual(manifest['new']['sha256'], hashlib.sha256(b'new package\n').hexdigest())
        calls = self.log.read_text()
        self.assertIn('docker-build:0.5.9-test.dev:--local test --platform linux/amd64 --image openeigrp:test', calls)
        self.assertIn('package-build:--version 0.5.9-test.dev --arch amd64', calls)
        self.assertIn('dpkg --compare-versions 0.5.9-test.dev gt 0.5', calls)
        self.assertIn('/verify-deb.sh', calls)

    def test_builds_both_local_artifacts_without_a_release(self):
        proc = self.run_bundle(NO_PUBLISHED_RELEASE='1')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        manifest = json.loads((self.root/'dist/netlabs-amd64.json').read_text())
        self.assertIsNone(manifest['old'])
        self.assertEqual(manifest['new']['version'], '0.5.9-test.dev')
        self.assertEqual(manifest['image'], 'openeigrp:test')
        self.assertIn('upgrade SKIPPED', proc.stdout)
        self.assertIn('docker-build:', self.log.read_text())
        self.assertIn('package-build:', self.log.read_text())
        self.assertIn('docker:build --platform linux/amd64', self.log.read_text())
        self.assertNotIn('docker:run --rm --platform linux/amd64 debian:12 dpkg --compare-versions',
                         self.log.read_text())

    def test_rejected_upgrade_removes_previous_manifest(self):
        output = self.root / 'dist'
        output.mkdir()
        (output/'netlabs-amd64.json').write_text('{"stale":true}')
        proc = self.run_bundle(FAIL_VERSION='1')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('not newer than published', proc.stderr)
        self.assertFalse((output/'netlabs-amd64.json').exists())
        self.assertNotIn('docker-build:', self.log.read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
