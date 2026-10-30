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

    def test_receiver_ingress_drop_preserves_sender_capture(self):
        # RTP-08 packet-loss tests impair reception, not sender egress:
        # the sender's tcpdump must still see the original UPDATE and retry.
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'ingress.fault.json'
            calls = []
            def run(container, argv):
                calls.append(argv)
                return '[]' if argv[:3] == ['tc', '-j', 'qdisc'] else ''
            with mock.patch.object(network_fault, 'ns_command', side_effect=run):
                data = network_fault.apply('receiver', 'eth1', 'ipv6', 'drop',
                                           state, direction='ingress')
                self.assertEqual(data['direction'], 'ingress')
                self.assertIn('ingress', calls[2])
                self.assertNotIn('egress', calls[2])
                self.assertIn('88', calls[2])
                network_fault.clear(state)
                self.assertEqual(calls[-1][-1], 'clsact')
            self.assertFalse(state.exists())

    def test_ingress_restricted_to_route_packet_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'bad.fault.json'
            with self.assertRaisesRegex(ValueError, 'only supports'):
                network_fault.apply('receiver', 'eth1', 'ipv4', 'ack',
                                    state, ack=500, direction='ingress')
            self.assertFalse(state.exists())

    def test_packet_loss_matches_only_update_opcode_and_preserves_hellos(self):
        # Opcode 1 at byte 21/41 in IPv4/IPv6 EIGRP packets. HELLO opcode 5
        # must remain unfiltered so the adjacency survives an RTP retry test.
        for family, offset in (('ipv4', '21'), ('ipv6', '41')):
            args = network_fault.filters('eth1', family, 'drop')
            self.assertIn(['match','u8','1','0xff','at',offset],
                          [args[i:i+6] for i in range(len(args)-5)])
            self.assertNotIn(['match','u8','5','0xff','at',offset],
                             [args[i:i+6] for i in range(len(args)-5)])

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

    def test_existing_root_qdisc_preserved_on_drop_and_ack(self):
        # Real OE interfaces carry a root scheduler. clsact attaches beside
        # that qdisc, and must never replace or remove it.
        for family, mode, opts in (
                ('ipv4', 'drop', {'direction': 'ingress'}),
                ('ipv4', 'ack', {'ack': 123}),
                ('ipv6', 'drop', {'direction': 'ingress'}),
                ('ipv6', 'ack', {'ack': 456})):
            with self.subTest(family=family, mode=mode):
                with tempfile.TemporaryDirectory() as tmp:
                    state = Path(tmp) / 'case.fault.json'
                    qdiscs = '[{"kind":"fq_codel","root":true,"handle":"0:"}]'
                    calls = []
                    def run(container, argv):
                        calls.append(argv)
                        return qdiscs if argv[:3] == ['tc', '-j', 'qdisc'] else ''
                    with mock.patch.object(network_fault, 'ns_command', side_effect=run):
                        data = network_fault.apply('oe', 'eth1', family, mode,
                                                   state, **opts)
                        self.assertEqual(data['previous_qdisc'], json.loads(qdiscs))
                        network_fault.clear(state)
                    self.assertFalse(state.exists())
                    self.assertIn(['tc', 'qdisc', 'add', 'dev', 'eth1', 'clsact'], calls)
                    self.assertIn(['tc', 'qdisc', 'del', 'dev', 'eth1', 'clsact'], calls)
                    self.assertFalse(any('root' in c and c[:3] == ['tc','qdisc','del']
                                         for c in calls))

    def test_existing_root_qdisc_still_refused_for_netem(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'case.fault.json'
            with mock.patch.object(network_fault, 'ns_command',
                                   return_value='[{"kind":"netem","root":true}]'):
                with self.assertRaisesRegex(RuntimeError, 'cannot be restored'):
                    network_fault.apply('c', 'eth1', 'ipv4', 'delay', p, delay_ms=25)
            self.assertFalse(p.exists())

    def test_shared_clsact_keeps_existing_filters_and_root_qdisc(self):
        for family, mode, opts in (
                ('ipv4', 'drop', {'direction': 'ingress'}),
                ('ipv4', 'ack', {'ack': 123}),
                ('ipv6', 'drop', {'direction': 'ingress'}),
                ('ipv6', 'ack', {'ack': 456})):
            with self.subTest(family=family, mode=mode), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / 'case.fault.json'
                calls = []
                qdiscs = [{'kind': 'fq_codel', 'root': True}, {'kind': 'clsact', 'handle': 'ffff:'}]
                existing = [{'kind': 'u32', 'pref': 100, 'protocol': 'ip', 'chain': 0}]

                def run(container, argv):
                    calls.append(argv)
                    if argv[:3] == ['tc', '-j', 'qdisc']:
                        return json.dumps(qdiscs)
                    if argv[:3] == ['tc', '-j', 'filter']:
                        return json.dumps(existing)
                    return ''

                with mock.patch.object(network_fault, 'ns_command', side_effect=run):
                    data = network_fault.apply('oe', 'eth1', family, mode, p, **opts)
                    self.assertTrue(data['shared_clsact'])
                    self.assertEqual(data['filter_pref'], network_fault.FAULT_PREF)
                    self.assertTrue(p.exists())
                    self.assertIn('49152', calls[-1])
                    network_fault.clear(p)

                self.assertFalse(p.exists())
                self.assertEqual(len([c for c in calls if c[:3] == ['tc', 'filter', 'add']]), 1)
                self.assertEqual(len([c for c in calls if c[:3] == ['tc', 'filter', 'del']]), 1)
                self.assertFalse(any(c[:3] == ['tc', 'qdisc', 'del'] for c in calls))
                self.assertFalse(any(c[:3] == ['tc', 'qdisc', 'add'] for c in calls))
                self.assertEqual(calls[-1][:7], ['tc', 'filter', 'del', 'dev', 'eth1',
                                                   opts.get('direction', 'egress'), 'protocol'])
                self.assertEqual(calls[-1][-2:], ['pref', '49152'])

    def test_shared_clsact_occupied_default_priority_uses_next_free(self):
        # Captured RTP-08 failure: 49152 is already occupied by a classifier
        # in the existing clsact. Allocate a free priority instead of aborting.
        for family in ('ipv4', 'ipv6'):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / 'case.fault.json'
                calls = []
                def run(container, argv):
                    calls.append(argv)
                    if argv[:3] == ['tc', '-j', 'qdisc']:
                        return '[{"kind":"clsact"}, {"kind":"fq_codel", "root":true}]'
                    if argv[:3] == ['tc', '-j', 'filter']:
                        return '[{"kind":"u32","pref":"49152"},' \
                               '{"kind":"u32","pref":49152},' \
                               '{"kind":"flower","pref":100}]'
                    return ''
                with mock.patch.object(network_fault, 'ns_command', side_effect=run):
                    data = network_fault.apply('ce', 'eth1', family, 'drop',
                                               state, direction='ingress')
                    self.assertEqual(data['filter_pref'], 49151)
                    self.assertIn('49151', next(c for c in calls if c[:3] == ['tc', 'filter', 'add']))
                    network_fault.clear(state)
                self.assertEqual(calls[-1][-2:], ['pref', '49151'])
                self.assertFalse(any(c[:3] == ['tc', 'qdisc', 'del'] for c in calls))
                self.assertFalse(state.exists())

    def test_shared_clsact_priority_collision_skips_occupied_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'case.fault.json'
            calls = []
            def run(container, argv):
                calls.append(argv)
                if argv[:3] == ['tc', '-j', 'qdisc']:
                    return '[{"kind":"clsact"}]'
                if argv[:3] == ['tc', '-j', 'filter']:
                    return '[{"pref":49152}, {"pref":49151}, {"pref":49150}]'
                return ''
            with mock.patch.object(network_fault, 'ns_command', side_effect=run):
                result = network_fault.apply('ce', 'eth1', 'ipv4', 'drop', state,
                                             direction='ingress')
                self.assertEqual(result['filter_pref'], 49149)
                network_fault.clear(state)
            self.assertEqual(calls[-1][-2:], ['pref', '49149'])

    def test_shared_clsact_rejects_unsafe_priority_records(self):
        for bad in ('[{"kind":"u32"}]', '[{"pref":"bogus"}]', '[null]'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / 'case.fault.json'
                def run(container, argv):
                    if argv[:3] == ['tc', '-j', 'qdisc']:
                        return '[{"kind":"clsact"}]'
                    if argv[:3] == ['tc', '-j', 'filter']:
                        return bad
                    raise AssertionError('no tc mutation should occur')
                with mock.patch.object(network_fault, 'ns_command', side_effect=run):
                    with self.assertRaisesRegex(RuntimeError, 'priority|record'):
                        network_fault.apply('ce', 'eth1', 'ipv4', 'drop', state,
                                            direction='ingress')
                self.assertFalse(state.exists())

    def test_shared_clsact_no_free_priority_fails_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'case.fault.json'
            calls = []
            def run(container, argv):
                calls.append(argv)
                if argv[:3] == ['tc', '-j', 'qdisc']:
                    return '[{"kind":"clsact"}]'
                if argv[:3] == ['tc', '-j', 'filter']:
                    return json.dumps([{'kind': 'u32', 'pref': p} for p in range(
                        network_fault.FAULT_PREF_LOW, network_fault.FAULT_PREF + 1)])
                raise AssertionError('unexpected tc mutation')
            with mock.patch.object(network_fault, 'ns_command', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'no free tc fault priority'):
                    network_fault.apply('oe', 'eth1', 'ipv4', 'drop', state,
                                        direction='ingress')
            self.assertFalse(state.exists())
            self.assertEqual(len(calls), 2)

    def test_failed_filter_on_shared_clsact_does_not_remove_qdisc(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'case.fault.json'
            calls = []
            def run(container, argv):
                calls.append(argv)
                if argv[:3] == ['tc', '-j', 'qdisc']:
                    return '[{"kind":"clsact"}]'
                if argv[:3] == ['tc', '-j', 'filter']:
                    return '[]'
                if argv[:3] == ['tc', 'filter', 'add']:
                    raise RuntimeError('filter rejected')
                raise AssertionError('unexpected tc mutation')
            with mock.patch.object(network_fault, 'ns_command', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'filter rejected'):
                    network_fault.apply('oe', 'eth1', 'ipv6', 'drop', state,
                                        direction='ingress')
            self.assertFalse(state.exists())
            self.assertFalse(any(c[:3] == ['tc', 'filter', 'del'] for c in calls))
            self.assertFalse(any(c[:3] == ['tc', 'qdisc', 'del'] for c in calls))

    def test_shared_clsact_cleanup_rejects_unowned_priority(self):
        # Fault state can be recovered after a runner interruption, but it
        # must not authorize deleting arbitrary existing tc rules.
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'case.fault.json'
            state.write_text(json.dumps({
                'schema': 'openeigrp-network-fault/v1',
                'container': 'ce', 'interface': 'eth1', 'family': 'ipv4',
                'mode': 'drop', 'direction': 'ingress',
                'shared_clsact': True, 'filter_pref': 300,
            }))
            with mock.patch.object(network_fault, 'ns_command') as run:
                with self.assertRaisesRegex(RuntimeError, 'unrecognized shared fault priority'):
                    network_fault.clear(state)
                run.assert_not_called()
            self.assertTrue(state.exists())

    def test_existing_ingress_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'case.fault.json'
            with mock.patch.object(network_fault, 'ns_command',
                                   return_value='[{"kind":"ingress"}]') as run:
                with self.assertRaisesRegex(RuntimeError, 'cannot be replaced'):
                    network_fault.apply('oe', 'eth1', 'ipv6', 'drop', p,
                                        direction='ingress')
                self.assertEqual(run.call_count, 1)
            self.assertFalse(p.exists())

    def test_ipv6_selector_uses_supported_tc_u32_raw_next_header(self):
        for mode, kwargs in (('drop', {}), ('ack', {'ack': 0x12345})):
            with self.subTest(mode=mode):
                args = network_fault.filters('eth1', 'ipv6', mode, **kwargs)
                self.assertNotIn('nexthdr', args)
                self.assertIn(['match', 'u8', '88', '0xff', 'at', '6'],
                              [args[i:i+6] for i in range(len(args)-5)])
                self.assertIn('ipv6', args)
                self.assertIn(['match', 'u8', '5' if mode == 'ack' else '1',
                               '0xff', 'at', '41'],
                              [args[i:i+6] for i in range(len(args)-5)])
                if mode == 'ack':
                    self.assertIn(['match','u32','74565','0xffffffff','at','52'],
                                  [args[i:i+6] for i in range(len(args)-5)])

    def test_failed_clsact_add_does_not_delete_unowned_qdisc(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'case.fault.json'
            calls = []
            def run(container, argv):
                calls.append(argv)
                if argv[:3] == ['tc', '-j', 'qdisc']:
                    return '[{"kind":"fq_codel","root":true}]'
                if argv[:3] == ['tc', 'qdisc', 'add']:
                    raise RuntimeError('qdisc conflict')
                return ''
            with mock.patch.object(network_fault, 'ns_command', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'qdisc conflict'):
                    network_fault.apply('oe', 'eth1', 'ipv4', 'drop', p)
            self.assertFalse(p.exists())
            self.assertFalse(any(c[:3] == ['tc','qdisc','del'] for c in calls))

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
