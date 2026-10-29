#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise 2R DUAL wire, provenance, metric and topology validators."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for name in ('capture','network_fault','wire'):
    if not (ROOT/'common'/(name+'.py')).exists():
        sys.modules[name] = types.ModuleType(name)
spec = importlib.util.spec_from_file_location('live_dual_2r',
                ROOT/'test/dual/2R-routes/driver.py')
dual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dual)
SRC = {'oe':{'ipv4':'10.44.53.1'}, 'ce':{'ipv4':'10.44.53.2'}}


def item(sender, prefix, kind, *, metric=None, tag=7):
    metric = metric or {'delay':10,'bandwidth':100000,'mtu':1500,'reliability':255,
                        'tag':tag}
    return {'family':'ipv4', 'source':SRC[sender]['ipv4'],
            'opcode_name':'UPDATE', 'sequence':100, 'asn':4473,
            'tlvs':[{'type':0x0103 if kind == 'external' else 0x0102,
                     'route_kind':kind, 'prefixes':[prefix], 'metric':metric,
                     'next_hop':'0.0.0.0',
                     'external':{'origin':0x0aff0001,'as':4473,
                                 'metric':0,'protocol':2,'tag':tag}}]}


class DualValidators(unittest.TestCase):
    def test_internal_route_type_and_exact_prefix(self):
        routes = {'oe':'10.255.0.1/32','ce':'10.255.0.2/32'}
        packets = [item(r,p,'internal') for r,p in routes.items()]
        out = dual.route_wire(packets,'ipv4','internal',routes,SRC)
        self.assertEqual(out['oe']['types'],[0x0102])
        with self.assertRaisesRegex(AssertionError, 'did not advertise'):
            dual.route_wire([item('oe',routes['oe'],'external'),packets[1]],
                            'ipv4','internal',routes,SRC)
        with self.assertRaisesRegex(AssertionError, 'unsequenced'):
            dual.route_wire([dict(p,sequence=0) for p in packets],
                            'ipv4','internal',routes,SRC)

    def test_external_provenance_tag_metrics_required(self):
        routes = {'oe':'198.18.255.1/32','ce':'198.19.255.2/32'}
        packets = [item(r,p,'external') for r,p in routes.items()]
        self.assertEqual(dual.provenance(packets,'ipv4',routes,SRC)['ce']['provenance'][0]['tag'],7)
        bad = [item('oe',routes['oe'],'external',tag=8),packets[1]]
        with self.assertRaisesRegex(AssertionError,'tag corrupted'):
            dual.provenance(bad,'ipv4',routes,SRC)
        missing = [dict(packets[0],tlvs=[dict(packets[0]['tlvs'][0],external=None)]), packets[1]]
        with self.assertRaisesRegex(AssertionError, 'provenance absent'):
            dual.provenance(missing,'ipv4',routes,SRC)

    def test_fd_cd_rd_no_fake_feasibility(self):
        self.assertEqual(dual.numeric_metric('FD is 28160\n via (28160/2560)'),
                         {'fd':28160,'cd':28160,'rd':2560})
        with self.assertRaisesRegex(AssertionError,'machine-verifiable'):
            dual.numeric_metric('Topology was empty')

    def test_cisco_historical_fd_on_metric_increase(self):
        # IOS XE 17.3.6 live DUAL-03 capture from 20261028T191453Z-Qa9iRVis.
        # FD stays at its historical minimum when current CD and RD increase.
        for family, prefix, display in (
                ('ipv4', '198.18.255.1/32', '198.18.255.1/32'),
                ('ipv6', '2001:db8:4473:100::ff/128',
                 '2001:DB8:4473:100::FF/128')):
            with self.subTest(family=family):
                class Lab:
                    output = ''
                    def ce_cli(self, command):
                        return self.output
                lab = Lab()
                def topology(cd, rd):
                    return (f'P {display}, 1 successors, FD is 26255360, '
                            'tag is 7, serno 10\n'
                            f'    via fe80::1 ({cd}/{rd}), GigabitEthernet2\n')
                lab.output = topology(26255360, 25600000)
                before = dual.topology_metrics(lab, family, prefix, 'ce')
                lab.output = topology(26255366, 25600006)
                after = dual.topology_metrics(lab, family, prefix, 'ce')
                self.assertEqual(before['fd'], after['fd'])
                self.assertGreater(after['cd'], after['fd'])
                dual.require_distance_change(before, after)

    def test_metric_distance_rejects_unchanged_and_invalid_values(self):
        class Lab:
            def __init__(self, fd, cd, rd):
                self.fd, self.cd, self.rd = fd, cd, rd
            def ce_cli(self, command):
                return (f'P 198.18.255.1/32, 1 successors, FD is {self.fd}\n'
                        f'    via 10.44.53.1 ({self.cd}/{self.rd}), Gi2\n')
        prefix = '198.18.255.1/32'
        with self.assertRaisesRegex(AssertionError, 'exceeds computed distance'):
            dual.topology_metrics(Lab(1100, 1000, 900), 'ipv4', prefix, 'ce')
        before = {'fd': 1000, 'cd': 1000, 'rd': 900}
        with self.assertRaisesRegex(AssertionError, 'computed distance unchanged'):
            dual.require_distance_change(before, before)
        with self.assertRaisesRegex(AssertionError, 'reported distance unchanged'):
            dual.require_distance_change(before, {'fd': 1000, 'cd': 1100, 'rd': 900})

    def test_next_hop_must_be_on_selected_route(self):
        output = 'D EX 198.19.255.2/32 [170/100] via 10.44.53.2, eth1'
        self.assertIn('10.44.53.2',
                      dual.actual_next_hop(output,'ipv4','198.19.255.2/32','10.44.53.2'))
        oe = 'E[121]  10.255.0.2/32 [90/321] via 10.44.53.2, eth1'
        self.assertIn('10.44.53.2',
                      dual.actual_next_hop(oe,'ipv4','10.255.0.2/32','10.44.53.2'))
        with self.assertRaisesRegex(AssertionError, 'next hop'):
            dual.actual_next_hop(output,'ipv4','198.19.255.2/32','10.44.53.3')

    def test_ios_ipv6_uppercase_prefix_and_nexthop(self):
        # Exact observed IOS XE format from UUT 20261028T174847Z-Ta1CqidO.
        output = ('D   2001:DB8:4473:FF::1/128 [90/205121]\n'
                  '     via FE80::963E:40F0:C5DB:57A8, GigabitEthernet2\n')
        matched = dual.actual_next_hop(output, 'ipv6',
            '2001:db8:4473:ff::1/128', 'fe80::963e:40f0:c5db:57a8')
        self.assertIn('GigabitEthernet2', matched)
        with self.assertRaisesRegex(AssertionError, 'next hop'):
            dual.actual_next_hop(output, 'ipv6',
                '2001:db8:4473:ff::11/128', 'fe80::963e:40f0:c5db:57a8')

        class Lab:
            def ce_cli(self, command):
                self.command = command
                return ('P 2001:DB8:4473:FF::1/128, 1 successors, '
                        'FD is 26255527, serno 5\n'
                        '        via FE80::963E:40F0:C5DB:57A8 '
                        '(26255527/25600167), GigabitEthernet2\n')
        lab = Lab()
        self.assertEqual(dual.topology_metrics(lab, 'ipv6',
            '2001:db8:4473:ff::1/128', 'ce'),
            {'fd':26255527, 'cd':26255527, 'rd':25600167})
        self.assertEqual(lab.command, 'show ipv6 eigrp topology all-links')

    def test_failure_diagnostics_preserve_cli_failures(self):
        class Lab:
            def __init__(self):
                self.files = {}
                self.commands = []
            def oe_cli(self, command):
                self.commands.append(('oe', command))
                if command == 'show running-config':
                    raise RuntimeError('OE CLI unavailable')
                return 'OE SNAPSHOT'
            def ce_cli(self, command):
                self.commands.append(('ce', command))
                return 'CE SNAPSHOT'
            def save(self, name, text):
                self.files[name] = text
        lab = Lab()
        dual.external_diagnostics(lab, 'ipv6', 'dual02-failure')
        self.assertEqual(len(lab.files), 7)
        self.assertIn('OE CLI unavailable', lab.files['dual02-failure-ipv6-oe-configuration.log'])
        self.assertIn('show eigrp address-family ipv6 topology 4473 all-links',
                      lab.files['dual02-failure-ipv6-oe-topology.log'])
        self.assertIn('show eigrp address-family ipv6 4473 events',
                      lab.files['dual02-failure-ipv6-oe-events.log'])
        self.assertIn('show ipv6 eigrp topology all-links',
                      lab.files['dual02-failure-ipv6-ce-topology.log'])

    def test_failure_diagnostics_capture_frr_redistribution_errors(self):
        class Lab:
            oe = 'clab-test-oe'
            def __init__(self):
                self.files = {}
            def oe_cli(self, command):
                return 'OE SNAPSHOT'
            def ce_cli(self, command):
                return 'CE SNAPSHOT'
            def save(self, name, text):
                self.files[name] = text
        lab = Lab()
        with mock.patch.object(sys.modules['interop'], 'command', return_value='Zebra redistribute route failed') as cmd:
            dual.external_diagnostics(lab, 'ipv4', 'dual02-failure')
        self.assertIn('Zebra redistribute route failed',
                      lab.files['dual02-failure-ipv4-oe-frr.log'])
        self.assertEqual(cmd.call_args.args[0], ['docker', 'exec', lab.oe,
            'tail', '-n', '400', '/var/log/frr/frr.log'])

    def test_failure_diagnostics_preserve_frr_read_error(self):
        class Lab:
            oe = 'clab-test-oe'
            def __init__(self):
                self.files = {}
            def oe_cli(self, command):
                return 'OE'
            def ce_cli(self, command):
                return 'CE'
            def save(self, name, text):
                self.files[name] = text
        lab = Lab()
        with mock.patch.object(sys.modules['interop'], 'command', side_effect=RuntimeError('file missing')):
            dual.external_diagnostics(lab, 'ipv6', 'dual02-failure')
        self.assertIn('file missing', lab.files['dual02-failure-ipv6-oe-frr.log'])

    def test_next_hop_must_not_match_a_different_rib_entry(self):
        lines = ('D   2001:DB8:4473:FF::1/128 [90/100]\n'
                 '     via FE80::1, GigabitEthernet2\n'
                 'D   2001:DB8:4473:FF::2/128 [90/100]\n'
                 '     via FE80::2, GigabitEthernet2')
        with self.assertRaisesRegex(AssertionError, 'next hop'):
            dual.actual_next_hop(lines, 'ipv6',
                '2001:db8:4473:ff::1/128', 'fe80::2')
        self.assertIn('FE80::2', dual.actual_next_hop(lines, 'ipv6',
            '2001:db8:4473:ff::2/128', 'fe80::2'))

    def test_metric_change_must_really_change_advertised_values(self):
        prefix='198.18.255.1/32'
        before=[item('oe',prefix,'external')]
        after=[item('oe',prefix,'external',metric={'delay':100,'bandwidth':100000,
                      'mtu':1500,'reliability':255,'tag':7})]
        self.assertNotEqual(dual.verify_change(before,after,'ipv4','oe',prefix,SRC)['before'],
                            dual.verify_change(before,after,'ipv4','oe',prefix,SRC)['after'])
        with self.assertRaisesRegex(AssertionError, 'did not change'):
            dual.verify_change(before,before,'ipv4','oe',prefix,SRC)

    def test_withdrawal_requires_poison_then_readvertisement(self):
        prefix='198.18.255.1/32'
        announced=item('oe',prefix,'external')
        poison=item('oe',prefix,'external',metric={'delay':0xffffffff,
                      'bandwidth':100000,'mtu':1500,'reliability':255,'tag':7})
        sequence=[announced,poison,announced]
        self.assertEqual(dual.lifecycle_evidence(sequence,'ipv4','oe',prefix,SRC)['poisoned'],1)
        with self.assertRaisesRegex(AssertionError,'withdrawal'):
            dual.lifecycle_evidence([announced,announced],'ipv4','oe',prefix,SRC)
        with self.assertRaisesRegex(AssertionError,'re-advertised'):
            dual.lifecycle_evidence([announced,poison],'ipv4','oe',prefix,SRC)

    def test_cisco_query_withdrawal_and_recovery(self):
        # Observed in IOS XE 17.3.6, UUT 20261028T194215Z-5QQdqNit:
        # CE UPDATE reachable, CE QUERY unreachable, CE UPDATE reachable.
        # The peer's RIB absence/recovery is checked by the live driver.
        for family, prefix in (
                ('ipv4', '198.19.255.2/32'),
                ('ipv6', '2001:db8:4473:200::ff/128')):
            with self.subTest(family=family):
                sources = {'ce': {family: '10.44.53.2' if family == 'ipv4'
                                  else 'fe80::2'}}
                def event(opcode, delay):
                    return {'family': family,
                            'source': sources['ce'][family],
                            'opcode_name': opcode, 'sequence': 21,
                            'asn': 4473,
                            'tlvs': [{'type': 0x0603, 'route_kind': 'external',
                                      'prefixes': [prefix],
                                      'metric': {'delay': delay}}]}
                initial = event('UPDATE', 100000000)
                query = event('QUERY', 0xffffffffffff)
                restored = event('UPDATE', 100000000)
                got = dual.lifecycle_evidence(
                    [initial, query, restored], family, 'ce', prefix, sources)
                self.assertEqual(got['withdrawal_opcodes'], ['QUERY'])
                self.assertEqual(got['poisoned'], 1)
                self.assertEqual(got['advertisements'], 2)
                with self.assertRaisesRegex(AssertionError, 'withdrawal'):
                    dual.lifecycle_evidence([initial, restored], family,
                                            'ce', prefix, sources)
                with self.assertRaisesRegex(AssertionError, 're-advertised'):
                    dual.lifecycle_evidence([initial, query], family,
                                            'ce', prefix, sources)
                with self.assertRaisesRegex(AssertionError, 'preceding'):
                    dual.lifecycle_evidence([query, restored], family,
                                            'ce', prefix, sources)
                bad = dict(query, sequence=0)
                with self.assertRaisesRegex(AssertionError, 'withdrawal'):
                    dual.lifecycle_evidence([initial, bad, restored], family,
                                            'ce', prefix, sources)
                # A reachable QUERY cannot masquerade as re-advertisement.
                with self.assertRaisesRegex(AssertionError, 're-advertised'):
                    dual.lifecycle_evidence([initial, query,
                                             event('QUERY', 100000000)],
                                            family, 'ce', prefix, sources)

    def test_oe_topology_uses_registered_cli_order(self):
        class Lab:
            def __init__(self):
                self.commands = []
            def oe_cli(self, cmd):
                self.commands.append(cmd)
                return 'P 10.255.0.2/32, 1 successors, FD is 1100\n        via 10.44.53.2 (1100/1000), eth1'
        lab = Lab()
        self.assertEqual(dual.topology_metrics(lab, 'ipv4', '10.255.0.2/32', 'oe'),
                         {'fd': 1100, 'cd': 1100, 'rd': 1000})
        self.assertEqual(lab.commands,
                         ['show eigrp address-family ipv4 topology 4473 all-links'])
        dual.topology_metrics(lab, 'ipv6', '10.255.0.2/32', 'oe')
        self.assertEqual(lab.commands[-1],
                         'show eigrp address-family ipv6 topology 4473 all-links')

    def test_ipv6_peer_reset_caches_addresses_and_uses_surviving_capture(self):
        from interop import result
        events = [
            item('oe', '10.255.0.1/32', 'internal'),
            item('ce', '10.255.0.2/32', 'internal'),
        ]
        for ev in events:
            ev['family'] = 'ipv6'
            ev['source'] = 'fe80::1' if ev['source'] == '10.44.53.1' else 'fe80::2'
            ev['tlvs'][0]['prefixes'] = [
                '2001:db8:4473:ff::1/128' if ev['source']=='fe80::1'
                else '2001:db8:4473:ff::2/128']
        class Capture:
            def __init__(self, lab, name, *, target='oe'):
                self.target = target
                self.events = events
                lab.capture_target = target
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
        class Lab:
            def __init__(self):
                self.calls = []
                self.capture_target = None
                self.oe = 'clab-test-oe'
            def peer_address(self, role, family):
                return {'oe':'fe80::2', 'ce':'fe80::1'}[role]
            def peers(self, family, present=True, seconds=110, *, addresses=None):
                self.calls.append((family, present, addresses))
            def snapshot(self, label):
                pass
            def routes(self, role, family):
                if role == 'oe':
                    return 'E[121]>* 2001:db8:4473:ff::2/128 [90/1000] via fe80::2, eth1'
                return 'D   2001:db8:4473:ff::1/128 [90/1000]\n via fe80::1, Gi2'
        lab = Lab()
        report = {'objectives': {}}
        def run_case(report, name, family, callback):
            if name == 'DUAL-01':
                report['objectives'][name+'/'+family] = result('PASS', **callback())
        with mock.patch.object(dual, 'Trace', Capture), \
             mock.patch.object(dual, 'record', side_effect=run_case), \
             mock.patch.object(dual, 'route_rib'), \
             mock.patch.object(dual.time, 'sleep'), \
             mock.patch('interop.command') as command:
            dual.run(lab, report, {'families':['ipv6']})
        self.assertEqual(report['objectives']['DUAL-01/ipv6']['status'], 'PASS')
        self.assertEqual(lab.capture_target, 'ce')
        self.assertEqual(lab.calls[1], ('ipv6', False,
            {'oe':{'ipv4':'10.44.53.1','ipv6':'fe80::1'},
             'ce':{'ipv4':'10.44.53.2','ipv6':'fe80::2'}}))
        self.assertEqual([args.args[0][-2:] for args in command.call_args_list],
                         [['eth1', 'down'], ['eth1', 'up']])

    def test_dual_fixture(self):
        import yaml
        root=ROOT/'test/dual/2R-routes'
        scenario=yaml.safe_load((root/'scenario.yaml').read_text())
        topology=yaml.safe_load((root/'topology.yml').read_text())
        self.assertEqual(scenario['objectives'],['DUAL-01','DUAL-02','DUAL-03','DUAL-04'])
        self.assertEqual({d['device'] for d in topology['nodes'].values()}, {'csr','frr'})
        self.assertEqual(topology['defaults']['devices.csr.clab.image'],'vrnetlab/vr-csr:17.03.06')


if __name__ == '__main__':
    unittest.main()
