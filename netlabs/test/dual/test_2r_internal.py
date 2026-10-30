#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Regression coverage for the baseline 2R-internal live driver and actual OE RIB format."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('live_internal_2r', ROOT / 'uut/2R-internal.py')
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)

OE = ('Codes: E - EIGRP\nIPv4 unicast VRF default:\n'
      'E[121]   10.44.53.0/30 [90/33554431] is directly connected, eth1\n'
      'E[121]   10.255.0.1/32 [90/33554431] is directly connected, lo\n')
CE = ('Codes: D - EIGRP, EX - EIGRP external\n'
      'Gateway of last resort is not set\ncsr#\n')


class InternalLiveDriverTests(unittest.TestCase):
    def test_ipv6_loopback_requires_runtime_not_only_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / 'oe-ipv6-eigrp-interfaces.log'
            artifact.write_text('Interface Source Peers\nlo config -\neth1 runtime 1\n')
            self.assertEqual(live.ipv6_passive_runtime(Path(temp), 'lo')['status'], 'FAIL')
            artifact.write_text('Interface Source Peers\nlo runtime 0\neth1 runtime 1\n')
            self.assertEqual(live.ipv6_passive_runtime(Path(temp), 'lo')['status'], 'PASS')
            self.assertEqual(live.ipv6_passive_runtime(Path(temp), 'lo0')['status'], 'FAIL')

    def test_real_oe_connected_route_does_not_satisfy_learned_prefix(self):
        self.assertFalse(live.internal_route_present(OE, '10.255.0.2/32', 'oe'))
        self.assertFalse(live.internal_route_present(OE, '10.255.0.1/32', 'oe'))
        self.assertFalse(live.internal_route_present(CE, '10.255.0.1/32', 'csr'))

    def test_exact_internal_routes_from_both_router_formats(self):
        learned_oe = OE + 'E[121]   10.255.0.2/32 [90/50000] via 10.44.53.2, eth1\n'
        learned_ce = CE + 'D 10.255.0.1/32 [90/50000] via 10.44.53.1, GigabitEthernet2\n'
        self.assertTrue(live.internal_route_present(learned_oe, '10.255.0.2/32', 'oe'))
        self.assertTrue(live.internal_route_present(learned_ce, '10.255.0.1/32', 'csr'))
        self.assertFalse(live.internal_route_present(learned_ce, '10.255.0.11/32', 'csr'))
        self.assertFalse(live.internal_route_present(
            learned_ce + 'D EX 198.18.0.0/24 [170/100] via 10.44.53.1\n',
            '198.18.0.0/24', 'csr'))

    def test_unsupported_oe_ipv6_filter_is_never_issued(self):
        class Cisco:
            def __init__(self, *args):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def run(self, cmd):
                return 'D 2001:db8::1/128 [90/1234] via FE80::1\n'
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(live, 'command', return_value='E[121]   2001:db8::2/128 via fe80::2') as exec_cmd, \
                 mock.patch.object(live, 'Cisco', Cisco):
                oe, ce = live.route_tables('ipv6', '192.0.2.2', Path(temp))
            self.assertIn('2001:db8::2/128', oe)
            self.assertIn('2001:db8::1/128', ce)
            self.assertEqual(exec_cmd.call_args.args[0][-1], 'show ipv6 route')
            self.assertIn('$ show ipv6 route\n', (Path(temp)/'frr-ipv6-routes.log').read_text())
            self.assertIn('$ show ipv6 route eigrp\n', (Path(temp)/'csr-ipv6-routes.log').read_text())

    def test_captured_updates_require_actual_originator_and_reachable_metric(self):
        routes = {'oe': {'ipv4': '10.255.0.1/32', 'ipv6': '2001:db8::1/128'},
                  'csr': {'ipv4': '10.255.0.2/32', 'ipv6': '2001:db8::2/128'}}
        sources = {'oe': {'ipv4': '10.44.53.1', 'ipv6': 'fe80::1'},
                   'csr': {'ipv4': '10.44.53.2', 'ipv6': 'fe80::2'}}

        def packet(af, role, prefix, kind='internal', delay=15):
            return {'family': af, 'source': sources[role][af],
                    'opcode_name': 'UPDATE',
                    'tlvs': [{'route_kind': kind, 'prefixes': [prefix],
                              'metric': {'delay': delay}}]}

        def check(events):
            return live.internal_wire_evidence(events, routes,
                                               ['ipv4', 'ipv6'], sources)

        self.assertEqual(check([])['status'], 'FAIL')
        # Neither a redistributed prefix nor poison reverse counts as
        # origination. Receiving another router's own prefix does not count.
        wrong = [packet('ipv4', 'csr', routes['oe']['ipv4']),
                 packet('ipv4', 'oe', routes['oe']['ipv4'], delay=0xffffffffffff),
                 packet('ipv6', 'oe', routes['oe']['ipv6'], kind='external')]
        self.assertEqual(check(wrong)['missing_originated_internal_prefixes']
                         ['ipv4']['oe'], routes['oe']['ipv4'])
        good = [packet(af, role, routes[role][af])
                for af in ('ipv4', 'ipv6') for role in ('oe', 'csr')]
        self.assertEqual(check(good)['status'], 'PASS')
        partial = check(good[:-1])
        self.assertEqual(partial['status'], 'FAIL')
        self.assertEqual(partial['missing_originated_internal_prefixes']
                         ['ipv6'], {'csr': routes['csr']['ipv6']})

    def test_capture_wrapper_writes_wire_evidence_json(self):
        # Trace actually executes its exit path; the fixture must implement
        # the save protocol, not merely carry container attributes.
        from interop import Trace
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            lab = live.capture_context(path)
            events = [{'family': 'ipv4', 'opcode_name': 'UPDATE',
                       'source': '10.44.53.1', 'tlvs': []}]
            with mock.patch('interop.capture.start') as start, \
                 mock.patch('interop.capture.stop') as stop, \
                 mock.patch('interop.wire.read_pcap', return_value=(events, [])):
                with Trace(lab, 'internal-reinitialize') as trace:
                    self.assertIs(trace.events, None)
            start.assert_called_once()
            stop.assert_called_once()
            self.assertEqual(json.loads((path/'internal-reinitialize.json').read_text())
                             ['events'], events)
            self.assertEqual(trace.events, events)

    def test_real_cisco_oe_capture_proves_ipv6_origination_missing(self):
        from wire import read_pcap
        fixture = Path(__file__).parent / 'fixtures' / '2r-internal-cisco-oe-20261028.pcap'
        events, errors = read_pcap(fixture)
        self.assertEqual(errors, [])
        self.assertEqual(len(events), 42)
        routes = {'oe': {'ipv4': '10.255.0.1/32',
                         'ipv6': '2001:db8:4473:ff::1/128'},
                  'csr': {'ipv4': '10.255.0.2/32',
                          'ipv6': '2001:db8:4473:ff::2/128'}}
        sources = {'oe': {'ipv4': '10.44.53.1',
                          'ipv6': 'fe80::dbc1:27d7:f399:322d'},
                   'csr': {'ipv4': '10.44.53.2',
                           'ipv6': 'fe80::a8c1:abff:fede:1dad'}}
        evidence = live.internal_wire_evidence(events, routes,
                                                ['ipv4', 'ipv6'], sources)
        self.assertEqual(evidence['missing_originated_internal_prefixes']['ipv4'], {})
        self.assertEqual(evidence['missing_originated_internal_prefixes']['ipv6'],
                         {'oe': '2001:db8:4473:ff::1/128'})
        self.assertEqual(evidence['status'], 'FAIL')

    def test_capture_diagnostics_do_not_mask_missing_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(live, 'command', return_value='interface evidence') as cmd:
                live.ipv6_origin_diagnostics(Path(tmp), 4473)
            self.assertEqual(cmd.call_count, 4)
            self.assertIn('interface evidence',
                          (Path(tmp)/'oe-ipv6-loopback-addresses.log').read_text())
            self.assertIn('interface evidence',
                          (Path(tmp)/'oe-ipv6-eigrp-interfaces.log').read_text())

    def test_oe_topology_uses_supported_ipv4_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(live, 'command', return_value='P 10.255.0.1/32, FD is 1') as command, \
                 mock.patch.object(live, 'Cisco') as cisco:
                cisco.return_value.__enter__.return_value.run.return_value = 'P 10.255.0.2/32'
                live.route_diagnostics('ipv4', '192.0.2.2', Path(temp))
            self.assertEqual(command.call_args.args[0][-1], 'show ip eigrp topology 4473 all-links')
            self.assertIn('10.255.0.1', (Path(temp)/'oe-ipv4-topology.log').read_text())

    def test_absent_routes_still_fail_and_request_topology_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(live, 'route_tables', return_value=(OE, CE)), \
                 mock.patch.object(live, 'route_diagnostics') as diagnostic, \
                 mock.patch.object(live.time, 'sleep'):
                result = live.assess_internal('ipv4', '192.0.2.2', Path(temp),
                                              '10.255.0.1/32', '10.255.0.2/32', .001)
            self.assertEqual(result['status'], 'FAIL')
            diagnostic.assert_called_once()


if __name__ == '__main__':
    unittest.main()
