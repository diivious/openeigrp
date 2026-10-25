#!/usr/bin/env python3
"""Exercise the real FRR setup clone/verify shell block with a mocked git."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / 'tools' / 'frr-setup.sh'
SHA = 'f8c0b08dcb0c78f9e42b9b86ae70b049e4e617c1'


class FrrSetupTagTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='frr-setup-tag-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.clone_log = self.root / 'clones'
        source = SCRIPT.read_text()
        self.block = source.split('if [[ "$skip_clone" -eq 0 && ! -e "$frr_root" ]]; then', 1)[1].split(
            '"$script_dir/frr-install.sh"', 1)[0]
        self.block = 'if [[ "$skip_clone" -eq 0 && ! -e "$frr_root" ]]; then' + self.block
        script = self.bin / 'git'
        script.write_text(
            '#!/bin/bash\n'
            'if [[ "$1" == clone ]]; then\n'
            '  printf "%s\\n" "$*" >> "$TEST_CLONE_LOG"\n'
            '  mkdir -p "${@: -1}/.git"\n'
            'elif [[ "$1" == -C && "$3" == rev-parse ]]; then\n'
            '  if [[ "$4" == HEAD ]]; then printf "%s\\n" "$TEST_FRR_SHA";\n'
            '  else printf "true\\n"; fi\n'
            'else exit 5; fi\n'
        )
        script.chmod(0o755)

    def run_block(self, skip=False, existing=False, actual=SHA):
        (self.root / 'frr-orig').mkdir(exist_ok=True)
        marker = self.root / 'frr-orig' / 'manual'
        marker.write_text('keep')
        if existing:
            (self.root / 'frr').mkdir(exist_ok=True)
        environment = dict(os.environ, PATH=f'{self.bin}:{os.environ.get("PATH", "")}',
                           TEST_CLONE_LOG=str(self.clone_log), TEST_FRR_SHA=actual)
        code = (
            f'fail() {{ echo "$*" >&2; exit 2; }}\n'
            f'skip_clone={int(skip)}\n'
            f'frr_root="{self.root}/frr"\n'
            'frr_url=https://github.com/FRRouting/frr.git\n'
            'frr_tag=frr-10.7.1\n'
            f'frr_sha={SHA}\n'
            + self.block
        )
        result = subprocess.run(['bash', '-c', code], env=environment, text=True, capture_output=True)
        self.assertEqual(marker.read_text(), 'keep')
        return result, self.clone_log.read_text() if self.clone_log.exists() else ''

    def test_missing_checkout_clones_official_tag_once(self):
        result, calls = self.run_block()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.count('clone '), 1)
        self.assertIn('https://github.com/FRRouting/frr.git', calls)
        self.assertIn('--branch frr-10.7.1', calls)
        self.assertNotIn('frr-orig', calls)

    def test_existing_checkout_is_not_modified(self):
        result, calls = self.run_block(existing=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, '')

    def test_existing_checkout_at_wrong_commit_fails_without_clone(self):
        result, calls = self.run_block(existing=True, actual='0' * 40)
        self.assertEqual(result.returncode, 2)
        self.assertIn('expected frr-10.7.1', result.stderr)
        self.assertEqual(calls, '')

    def test_skip_clone_requires_checkout(self):
        result, calls = self.run_block(skip=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('checkout not found', result.stderr)
        self.assertEqual(calls, '')


if __name__ == '__main__':
    unittest.main()
