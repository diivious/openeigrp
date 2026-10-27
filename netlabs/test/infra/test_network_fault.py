#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Verify network fault setup, ACK selection, and reliable restoration."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'network_fault', ROOT / 'common/network_fault.py'
)
network_fault = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network_fault)


class NetworkFaultTests(unittest.TestCase):
    def apply(self, mode, **opts):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'case.fault.json'
            calls = []

            # The fake namespace only accepts calls through ns_command; the
            # real helper constructs the same tc operation sequence.
            def run(container, argv):
                calls.append(argv)
                return '[]' if argv[:3] == ['tc', '-j', 'qdisc'] else ''

            with mock.patch.object(network_fault, 'ns_command', side_effect=run):
                network_fault.apply('clab-eigrp-chain-r3', 'eth1', 'ipv4', mode, state, **opts)
                self.assertTrue(state.is_file())
                data = json.loads(state.read_text())
                self.assertEqual(data['mode'], mode)
                network_fault.cleanup(tmp)
                self.assertFalse(state.exists())
            return calls

    def test_drop_scoped_to_proto88(self):
        calls = self.apply('drop')
        self.assertIn('egress', calls[2])
        self.assertIn('88', calls[2])
        self.assertEqual(calls[-1][-1], 'clsact')

    def test_delay_uses_netem_only_on_filtered_band(self):
        calls = self.apply('delay', delay_ms=175)
        self.assertIn('175ms', calls[2])
        self.assertEqual(calls[3][-2:], ['flowid', '1:3'])
        self.assertEqual(calls[-1][-1], 'root')

    def test_ack_requires_exact_sequence_and_hello_opcode(self):
        with self.assertRaises(ValueError):
            network_fault.filters('eth1', 'ipv4', 'ack')
        calls = self.apply('ack', ack=0xfeed)
        filt = calls[2]
        self.assertIn('65261', filt)
        self.assertIn('32', filt)
        self.assertIn('5', filt)

    def test_ipv6_and_invalid_arguments(self):
        self.assertIn('ipv6', network_fault.filters('eth1', 'ipv6', 'drop'))
        with self.assertRaises(ValueError):
            network_fault.filters('eth1;echo bad', 'ipv4', 'drop')
        with self.assertRaises(ValueError):
            network_fault.filters('eth1', 'ipv7', 'drop')

    def test_unknown_qdisc_refused_and_no_fault_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'case.fault.json'
            with mock.patch.object(network_fault, 'ns_command', return_value='[{"kind":"netem"}]'):
                with self.assertRaisesRegex(RuntimeError, 'cannot be restored'):
                    network_fault.apply('c', 'eth1', 'ipv4', 'drop', p)
            self.assertFalse(p.exists())

    def test_partial_apply_restores_egress_qdisc(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'case.fault.json'
            calls = []
            def run(container, argv):
                calls.append(argv)
                if argv[:3] == ['tc', '-j', 'qdisc']:
                    return '[]'
                if 'filter' in argv and 'add' in argv:
                    raise RuntimeError('filter not accepted')
                return ''
            with mock.patch.object(network_fault, 'ns_command', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'filter not accepted'):
                    network_fault.apply('node', 'eth1', 'ipv4', 'ack', state, ack=10)
            self.assertFalse(state.exists())
            self.assertIn(['tc', 'qdisc', 'del', 'dev', 'eth1', 'clsact'], calls)

    def test_cleanup_failure_retains_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'case.fault.json'
            p.write_text(json.dumps({
                'schema': 'openeigrp-network-fault/v1',
                'container': 'c',
                'interface': 'eth1',
                'mode': 'drop',
            }))
            with mock.patch.object(network_fault, 'ns_command', side_effect=RuntimeError('namespace lost')):
                with self.assertRaisesRegex(RuntimeError, 'namespace lost'):
                    network_fault.cleanup(tmp)
            self.assertTrue(p.exists())


if __name__ == '__main__':
    unittest.main()
