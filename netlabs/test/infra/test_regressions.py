#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise actual staged regression discovery and result propagation."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'tools/remote/regressions.py'
spec = importlib.util.spec_from_file_location('netlabs_regressions', SCRIPT)
regressions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(regressions)


class RegressionTests(unittest.TestCase):
    def test_group_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for group in ('infra', 'rtp', 'dual'):
                folder = root / 'netlabs/test' / group
                folder.mkdir(parents=True)
                (folder / 'test_check.py').write_text('pass\n')
                (folder / 'test_check.sh').write_text('#!/bin/sh\nexit 0\n')
            (root / 'netlabs/tests').mkdir()
            (root / 'netlabs/tests/test_legacy.sh').write_text('#!/bin/sh\nexit 0\n')
            (root / 'netlabs/test/infra/test_fault_cleanup.sh').write_text('#!/bin/sh\nexit 9\n')
            all_names = [x[0] for x in regressions.select(root, 'all')]
            rtp_names = [x[0] for x in regressions.select(root, 'rtp')]
            dual_names = [x[0] for x in regressions.select(root, 'dual')]
            self.assertIn('rtp', all_names)
            self.assertIn('dual', all_names)
            self.assertIn('infra', rtp_names)
            self.assertIn('infra', dual_names)
            self.assertNotIn('dual', rtp_names)
            self.assertNotIn('rtp', dual_names)
            self.assertFalse(any('test_legacy.sh' in x for x in all_names))
            self.assertIn('netlabs/test/infra/test_fault_cleanup.sh', all_names)

    def test_pass_and_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'netlabs/test/infra'
            folder.mkdir(parents=True)
            (folder / 'test_pass.sh').write_text('#!/bin/sh\necho pass-evidence\n')
            artifacts = root / 'artifacts'
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(regressions.execute(root, 'dual', artifacts), 0)
            self.assertIn('PASS: netlabs/test/infra/test_pass.sh', output.getvalue())
            report = json.loads((artifacts / 'regressions.json').read_text())
            self.assertEqual(report['status'], 'PASS')
            self.assertEqual([i['status'] for i in report['cases']], ['PASS'])
            self.assertIn('pass-evidence', (artifacts / report['cases'][0]['log']).read_text())
            self.assertEqual((root / 'netlabs/remote-lab.cfg').read_text(),
                             'REMOTE_HOST=localhost\nREMOTE_USER=netlab\nREMOTE_SSH_PORT=22\n')

    def test_failure_is_not_masked_by_other_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'netlabs/test/infra'
            folder.mkdir(parents=True)
            (folder / 'test_a.sh').write_text('#!/bin/sh\nexit 27\n')
            (folder / 'test_b.sh').write_text('#!/bin/sh\nexit 0\n')
            artifacts = root / 'artifacts'
            # Failure here is intentional input for the reporter regression,
            # not an infra failure in this unittest itself.
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(regressions.execute(root, 'rtp', artifacts), 1)
            self.assertIn('FAIL: netlabs/test/infra/test_a.sh', output.getvalue())
            report = json.loads((artifacts / 'regressions.json').read_text())
            self.assertEqual(report['status'], 'FAIL')
            self.assertEqual([item['status'] for item in report['cases']], ['FAIL', 'PASS'])

    def test_no_tests_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'netlabs').mkdir()
            self.assertEqual(regressions.execute(root, 'dual', root / 'artifacts'), 1)
            report = json.loads((root / 'artifacts/regressions.json').read_text())
            self.assertEqual(report['status'], 'BLOCKED')
            self.assertFalse(report['cases'])


if __name__ == '__main__':
    unittest.main()
