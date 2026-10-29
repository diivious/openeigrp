#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Regression checks for actual 2R stimulus, capture and fault cleanup paths."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
# importlib loads interop.py as a standalone file, not as an installed package.
# Its sibling capture/network_fault/wire modules must resolve from this tree.
sys.path.insert(0, str(ROOT / 'common'))
spec = importlib.util.spec_from_file_location('interop', ROOT/'common/interop.py')
interop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(interop)
TOPO = ROOT/'test/dual/2R-routes/topology.yml'


class InteropRuntime(unittest.TestCase):
    def test_imports_real_common_helpers(self):
        # A missing or shadowed helper must fail infrastructure qualification.
        for name in ('capture', 'network_fault', 'wire'):
            actual = Path(getattr(interop, name).__file__).resolve()
            self.assertEqual(actual, (ROOT / 'common' / (name + '.py')).resolve())

    def lab(self, folder):
        return interop.Lab(TOPO, Path(folder))

    def test_topology_names_rejected_if_netlab_limit_exceeded(self):
        import re
        import yaml
        for group, name in [('rtp', '2R-transport'), ('dual', '2R-routes')]:
            path = ROOT / 'test' / group / name / 'topology.yml'
            topology = yaml.safe_load(path.read_text())
            self.assertLessEqual(len(topology['name']), 16)
            self.assertRegex(topology['name'], r'^eigrp-[A-Za-z0-9_-]+$')
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / 'topology.yml'
            bad.write_text('name: eigrp-2r-transport\nprovider: clab\nnodes:\n'
                           '  oe: {}\n  csr: {}\n')
            lab = interop.Lab(bad, Path(temp))
            with mock.patch.object(interop.shutil, 'which', return_value='/usr/bin/sshpass'):
                with self.assertRaisesRegex(ValueError, 'unsupported 2R'):
                    lab.deploy()

    def test_ipv6_oe_uses_supported_unfiltered_zebra_command(self):
        with tempfile.TemporaryDirectory() as temp:
            lab = self.lab(temp)
            with mock.patch.object(lab, 'oe_cli', return_value='OE') as oe, \
                 mock.patch.object(lab, 'ce_cli', return_value='CE') as ce:
                self.assertEqual(lab.routes('oe', 'ipv6'), 'OE')
                oe.assert_called_once_with('show ipv6 route')
                self.assertEqual(lab.routes('oe', 'ipv4'), 'OE')
                self.assertEqual(lab.routes('ce', 'ipv6'), 'CE')
                self.assertEqual(ce.call_args.args, ('show ipv6 route eigrp',))

    def test_exact_learned_oe_and_ce_rib_entries(self):
        oe = ('Codes: E - EIGRP\nIPv4 unicast VRF default:\n'
              'E[121]   10.255.0.1/32 [90/33554431] is directly connected, lo\n'
              'E[121]   10.255.0.2/32 [90/12345] via 10.44.53.2, eth1\n')
        ce = ('D     10.255.0.1/32 [90/12345] via 10.44.53.1\n'
              'D EX  198.18.255.1/32 [170/222] via 10.44.53.1\n')
        with tempfile.TemporaryDirectory() as temp:
            lab = self.lab(temp)
            with mock.patch.object(lab, 'routes', side_effect=lambda role, family:
                                   oe if role == 'oe' else ce):
                self.assertTrue(lab.route_present('oe', 'ipv4', '10.255.0.2/32', 'internal'))
                self.assertFalse(lab.route_present('oe', 'ipv4', '10.255.0.1/32', 'internal'))
                self.assertFalse(lab.route_present('oe', 'ipv4', '10.255.0.22/32', 'internal'))
                self.assertTrue(lab.route_present('ce', 'ipv4', '10.255.0.1/32', 'internal'))
                self.assertFalse(lab.route_present('ce', 'ipv4', '198.18.255.1/32', 'internal'))
                self.assertTrue(lab.route_present('ce', 'ipv4', '198.18.255.1/32', 'external'))

    def test_actual_ios_xe_external_rib_formats_both_families(self):
        # Exact output forms from the 2026-10-28 Cisco UUT report.
        v4 = ('Gateway of last resort is not set\n'
              '      198.18.255.0/32 is subnetted, 1 subnets\n'
              'D EX     198.18.255.1 [170/205120] via 10.44.53.1, GigabitEthernet2\n'
              'csr#\n')
        v6 = ('D   2001:DB8:4473:FF::1/128 [90/205121]\n'
              '     via FE80::57AA:3A7B:F6C0:C2C9, GigabitEthernet2\n'
              'EX  2001:DB8:4473:100::FF/128 [170/205120], tag 7\n'
              '     via FE80::57AA:3A7B:F6C0:C2C9, GigabitEthernet2\ncsr#')
        self.assertEqual(interop.rib_prefixes(v4, 'ce', 'ipv4', 'external'),
                         {interop.ipaddress.ip_network('198.18.255.1/32')})
        self.assertEqual(interop.rib_prefixes(v6, 'ce', 'ipv6', 'external'),
                         {interop.ipaddress.ip_network('2001:db8:4473:100::ff/128')})
        self.assertEqual(interop.rib_prefixes(v6, 'ce', 'ipv6', 'internal'),
                         {interop.ipaddress.ip_network('2001:db8:4473:ff::1/128')})
        self.assertEqual(interop.rib_prefixes(v4, 'ce', 'ipv4', 'internal'), set())
        self.assertEqual(interop.rib_prefixes(v6, 'ce', 'ipv6', 'external') &
                         interop.rib_prefixes(v6, 'ce', 'ipv6', 'internal'), set())
        self.assertEqual(interop.rib_prefixes('D EX 198.18.255.1 [170/100] via 10.0.0.1',
                                             'ce', 'ipv4', 'external'), set())
        with tempfile.TemporaryDirectory() as temp:
            lab = self.lab(temp)
            def routes(role, family):
                if role == 'ce':
                    return v4 if family == 'ipv4' else v6
                return ('E[121]>* 198.19.255.2/32 via 10.44.53.2\n'
                        if family == 'ipv4' else
                        'E[121]>* 2001:db8:4473:200::ff/128 via fe80::2\n')
            with mock.patch.object(lab, 'routes', side_effect=routes):
                self.assertTrue(all(lab.check_routes('ipv4',
                    {'oe':['198.19.255.2/32'], 'ce':['198.18.255.1/32']},
                    'external', seconds=.01).values()))
                self.assertTrue(all(lab.check_routes('ipv6',
                    {'oe':['2001:db8:4473:200::ff/128'],
                     'ce':['2001:db8:4473:100::ff/128']},
                    'external', seconds=.01).values()))
                with self.assertRaisesRegex(AssertionError, '1 of 1'):
                    lab.check_routes('ipv6', {'ce':['2001:db8:4473:100::fe/128']},
                                     'external', seconds=.001)

    def test_oe_routes_are_frr_static_not_linux_kernel_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            lab=self.lab(temp)
            with mock.patch.object(lab,'oe_config') as configure, \
                 mock.patch.object(interop,'command',side_effect=AssertionError('raw Linux route')):
                lab.route('oe','ipv4','198.18.0.1/32',add=True)
                lab.route('oe','ipv4','198.18.0.1/32',add=False)
                lab.route('oe','ipv6','2001:db8::1/128',add=True)
            self.assertEqual(configure.call_args_list[0].args[0],
                             ['ip route 198.18.0.1 255.255.255.255 Null0 tag 7'])
            self.assertEqual(configure.call_args_list[1].args[0],
                             ['no ip route 198.18.0.1 255.255.255.255 Null0 tag 7'])
            self.assertEqual(configure.call_args_list[2].args[0],
                             ['ipv6 route 2001:db8::1/128 Null0 tag 7'])

    def test_burst_route_changes_use_config_transactions_and_validate_all(self):
        with tempfile.TemporaryDirectory() as temp:
            lab=self.lab(temp)
            routes=[f'198.18.0.{i}/32' for i in range(1,52)]
            with mock.patch.object(lab,'oe_config') as oe, \
                 mock.patch.object(lab,'ce_config') as ce:
                lab.routes_change('oe','ipv4',routes,add=True)
                self.assertEqual(oe.call_count,3)  # bounded vtysh CLI calls
                self.assertEqual(sum(len(call.args[0]) for call in oe.call_args_list),51)
                lab.routes_change('ce','ipv4',routes,add=False)
                ce.assert_called_once()
                self.assertEqual(len(ce.call_args.args[0]),51)
                with self.assertRaisesRegex(ValueError,'family/prefix mismatch'):
                    lab.routes_change('oe','ipv4',['198.18.0.1/32',
                                      '2001:db8::1/128'],add=True)
                self.assertEqual(oe.call_count,3)
                with self.assertRaisesRegex(ValueError,'unknown router role'):
                    lab.routes_change('unknown','ipv4',routes,add=True)

    def test_invalid_route_family_cannot_modify_router(self):
        with tempfile.TemporaryDirectory() as temp:
            lab=self.lab(temp)
            with mock.patch.object(lab,'oe_config') as configured:
                with self.assertRaisesRegex(ValueError,'family/prefix mismatch'):
                    lab.route('oe','ipv4','2001:db8::1/128',add=True)
                configured.assert_not_called()

    def test_route_wait_requires_every_prefix_both_directions(self):
        oe = ("E[121]>* 198.19.0.1/32 [170/12345] via 10.44.53.2, eth1\n"
              "E[121]   198.19.0.2/32 [170/12345] is directly connected, lo\n")
        ce = "D EX 198.18.0.1/32 [170/222] via 10.44.53.1\n"
        with tempfile.TemporaryDirectory() as temp:
            lab = self.lab(temp)
            with mock.patch.object(lab, 'routes', side_effect=lambda role, af:
                                   oe if role == 'oe' else ce) as routes:
                found = lab.check_routes('ipv4', {'oe': ['198.19.0.1/32'],
                                    'ce': ['198.18.0.1/32']}, 'external', seconds=.02)
                self.assertEqual(len(found), 2)
                # One actual RIB read per router, rather than one per prefix.
                self.assertEqual(routes.call_count, 2)
                with self.assertRaisesRegex(AssertionError, 'examples:'):
                    lab.check_routes('ipv4', {'oe': ['198.19.0.1/32',
                                                   '198.19.0.2/32']}, 'external',
                                     seconds=.001)
                with self.assertRaisesRegex(AssertionError, '1 of 1'):
                    lab.check_routes('ipv4', {'ce': ['198.18.0.1/32']}, 'external',
                                     present=False, seconds=.001)

    def test_burst_route_check_does_not_issue_cli_per_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            lab = self.lab(temp)
            oe = ''.join(f'E[121]>* 198.19.0.{i}/32 via 10.44.53.2\n'
                         for i in range(1, 129))
            ce = ''.join(f'D EX 198.18.0.{i}/32 via 10.44.53.1\n'
                         for i in range(1, 129))
            with mock.patch.object(lab, 'routes', side_effect=lambda role, af:
                                   oe if role == 'oe' else ce) as routes:
                actual = lab.check_routes('ipv4', {
                    'oe': [f'198.19.0.{i}/32' for i in range(1, 129)],
                    'ce': [f'198.18.0.{i}/32' for i in range(1, 129)]},
                    'external', seconds=.02)
                self.assertEqual(len(actual), 256)
                self.assertTrue(all(actual.values()))
                self.assertEqual(routes.call_count, 2)

    def test_ipv6_loss_wait_uses_cached_address_during_interface_outage(self):
        with tempfile.TemporaryDirectory() as temp:
            lab = self.lab(temp)
            peers = {'oe': {'ipv6': 'fe80::1'}, 'ce': {'ipv6': 'fe80::2'}}
            with mock.patch.object(lab, 'peer_address',
                                   side_effect=AssertionError('interface down')) as discover, \
                 mock.patch.object(lab, 'neighbor', return_value='') as neighbor:
                result = lab.peers('ipv6', False, .02, addresses=peers)
                self.assertEqual(result, {'oe': False, 'ce': False})
                self.assertEqual(neighbor.call_count, 2)
                discover.assert_not_called()

    def test_capture_writes_wire_events_and_clears_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            lab=self.lab(temp)
            sample=[{'source':'10.44.53.1','family':'ipv4','opcode_name':'UPDATE'}]
            with mock.patch.object(interop.capture,'start',return_value={'pid':123},create=True) as start, \
                 mock.patch.object(interop.capture,'stop',create=True) as stop, \
                 mock.patch.object(interop.wire,'read_pcap',return_value=(sample,[]),create=True):
                with interop.Trace(lab,'test') as capture:
                    self.assertEqual(capture.state.name,'test.capture.json')
                self.assertEqual(capture.events,sample)
                self.assertEqual(start.call_args.kwargs['container'],lab.oe)
                stop.assert_called_once()
            self.assertIn('UPDATE',(Path(temp)/'test.json').read_text())

    def test_fault_cleanup_occurs_on_body_error(self):
        with tempfile.TemporaryDirectory() as temp:
            lab=self.lab(temp)
            with mock.patch.object(interop.network_fault,'apply',create=True) as apply, \
                 mock.patch.object(interop.network_fault,'clear',create=True) as clear:
                with self.assertRaisesRegex(RuntimeError,'stimulus failed'):
                    with interop.fault(lab,'loss','ce','ipv4','ack',ack=42):
                        raise RuntimeError('stimulus failed')
                self.assertEqual(apply.call_args.kwargs['ack'],42)
                clear.assert_called_once_with(Path(temp)/'loss.fault.json')

    def test_rejected_cli_output_is_not_success(self):
        for text in ('% Invalid input detected', '% Incomplete command',
                     'unknown command'):
            with self.assertRaisesRegex(RuntimeError,'rejected'):
                interop.assert_cli(text,'router config')


if __name__ == '__main__':
    unittest.main()
