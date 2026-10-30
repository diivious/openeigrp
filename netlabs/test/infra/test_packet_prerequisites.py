#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executed packet-access gate; missing sudo privilege must never claim interop FAIL."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'common'))
spec = importlib.util.spec_from_file_location('interop_gate', ROOT/'common/interop.py')
interop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(interop)


class PacketAccessTests(unittest.TestCase):
    def test_missing_tool_blocks_before_privileged_action(self):
        with mock.patch.object(interop.shutil, 'which', side_effect=lambda name: None if name == 'tc' else '/usr/bin/' + name), \
             mock.patch.object(interop.subprocess, 'run') as run:
            with self.assertRaisesRegex(interop.CapturePrerequisiteError, 'missing host packet tools: tc'):
                interop.require_packet_access()
            run.assert_not_called()

    def test_real_sudo_denial_is_blocked(self):
        denied = subprocess.CompletedProcess([], 1, stdout='sudo: a password is required\n')
        with mock.patch.object(interop.shutil, 'which', return_value='/usr/bin/tool'), \
             mock.patch.object(interop.subprocess, 'run', return_value=denied) as run:
            with self.assertRaisesRegex(interop.CapturePrerequisiteError, 'password is required'):
                interop.require_packet_access()
            self.assertEqual(run.call_args.args[0],
                             ['sudo', '-n', 'nsenter', '-t', '1', '-n', '--', 'true'])

    def test_privileges_granted_uses_nonmutating_probe(self):
        allowed = subprocess.CompletedProcess([], 0, stdout='')
        with mock.patch.object(interop.shutil, 'which', return_value='/usr/bin/tool'), \
             mock.patch.object(interop.subprocess, 'run', return_value=allowed) as run:
            self.assertIsNone(interop.require_packet_access())
        self.assertEqual(run.call_args.kwargs['timeout'], 15)

    def test_both_live_drivers_block_before_deployment(self):
        import yaml
        for group, name in (('rtp', '2R-transport'), ('dual', '2R-routes')):
            script = ROOT / 'test' / group / name / 'driver.py'
            module_spec = importlib.util.spec_from_file_location('case_'+group, script)
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            with tempfile.TemporaryDirectory() as temp:
                folder = ROOT / 'test' / group / name
                args = ['--scenario',str(folder/'scenario.yaml'),
                        '--topology',str(folder/'topology.yml'),
                        '--artifacts', temp]
                blocked = module.CapturePrerequisiteError('sudo: a password is required')
                with mock.patch.object(module, 'require_packet_access', side_effect=blocked), \
                     mock.patch.object(module.Lab, 'deploy') as deploy, \
                     mock.patch.object(module.Lab, 'collect_logs'):
                    self.assertEqual(module.main(args), 1)
                    deploy.assert_not_called()
                report = yaml.safe_load((Path(temp)/'report.json').read_text())
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertTrue(report['required'])
                self.assertTrue(all(record['status'] == 'BLOCKED' for record
                                    in report['objectives'].values()))


if __name__ == '__main__':
    unittest.main()
