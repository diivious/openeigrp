#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Real driver-side AddPath wire decoding and route evidence guards."""
import importlib.util
import ipaddress
from pathlib import Path
import struct
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'common'))
try:
    import interop
    import capture
    import wire
except ImportError:
    # The standalone regression can run without deploying the remote image.
    # The inspected functions still execute the actual scenario driver.
    interop = types.ModuleType('interop')
    for name in ('Cisco', 'Lab', 'assert_cli', 'command', 'report_base',
                 'require_packet_access', 'result', 'rib_prefixes', 'write_report'):
        setattr(interop, name, lambda *a, **k: None)
    interop.CapturePrerequisiteError = type('CapturePrerequisiteError', (Exception,), {})
    interop.result = lambda status, **fields: dict(status=status, **fields)
    capture = types.ModuleType('capture')
    wire = types.ModuleType('wire')
    sys.modules.update({'interop': interop, 'capture': capture, 'wire': wire})

path = ROOT / 'test/dual/4R-addpath-compat/driver.py'
spec = importlib.util.spec_from_file_location('addpath_compat', path)
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


def tlv(family, count):
    size = 4 if family == 'ipv4' else 16
    address = ipaddress.ip_address('192.0.2.3' if size == 4 else 'fe80::3')
    payload = (address.packed + struct.pack('!II', 0x10203040, 99) + b'\x03\x01')
    metric = {'delay': 110, 'bandwidth': 500, 'mtu': 1500}
    return {'codec': 'TLV2', 'route_kind': 'external',
            'prefixes': ['198.18.44.0/24' if size == 4 else
                         '2001:db8:4473:44::/64'],
            'next_hop': '192.0.2.2' if size == 4 else 'fe80::2',
            'extended_attributes_hex':
                ((bytes([7, len(payload) // 2]) + payload) * count).hex(),
            'metric': metric}


class AddpathCompatibility(unittest.TestCase):
    def test_real_attribute_parser_both_families(self):
        for family in compat.FAMILIES:
            with self.subTest(family=family):
                self.assertEqual(len(compat.attributes(tlv(family, 1), family)), 1)
                self.assertEqual(compat.attributes(tlv(family, 0), family), [])
                with self.assertRaisesRegex(AssertionError, 'duplicate'):
                    compat.attributes(tlv(family, 2), family)
                bad = tlv(family, 1)
                bad['extended_attributes_hex'] = bad['extended_attributes_hex'][:-2]
                with self.assertRaisesRegex(AssertionError, 'malformed'):
                    compat.attributes(bad, family)

    def test_exact_prefix_sender_and_replacement(self):
        family = 'ipv6'
        prefix = '2001:db8:4473:44::/64'
        def event(t):
            return {'family': family, 'source': 'fe80::1',
                    'opcode_name': 'UPDATE', 'sequence': 31,
                    'tlvs': [t]}
        initial = compat.require_update([event(tlv(family, 1))],
                                       family, prefix, 'fe80::1', True)
        self.assertEqual(len(initial['addpaths']), 1)
        withdrawn = compat.require_update([event(tlv(family, 0))],
                                         family, prefix, 'fe80::1', False)
        self.assertEqual(initial['metric'], withdrawn['metric'])
        with self.assertRaisesRegex(AssertionError, 'missing'):
            compat.require_update([event(tlv(family, 0))], family,
                                  prefix, 'fe80::1', True)
        with self.assertRaisesRegex(AssertionError, 'missing'):
            compat.require_update([event(tlv(family, 1))], family,
                                  prefix, 'fe80::2', True)
        with self.assertRaisesRegex(AssertionError, 'missing'):
            compat.require_update([event(tlv(family, 1))], family,
                                  '2001:db8:4473:45::/64', 'fe80::1', True)

    def test_partial_withdrawal_and_recovery_orchestration(self):
        family = 'ipv6'
        prefix = '2001:db8:4473:44::/64'

        class FakeLab:
            def __init__(self, metric_change=False):
                self.actions = []
                self.metric_change = metric_change

            def hub_address(self, family):
                return 'fe80::1'

            def origin_set(self, role, family, prefix, add=True):
                self.actions.append((role, add))

            def addpath_set(self, family, enabled):
                self.actions.append(('config', enabled))

            def snapshot(self, label):
                pass

            def spokes_wait(self, *args):
                pass

            def link(self, up):
                self.actions.append(('link', up))

            def neighbor_wait(self, *args):
                pass

            def route_wait(self, *args):
                pass

            def wait_update(self, *args, **kwargs):
                pass

            def external_route(self, family, prefix):
                return True

            def evidence(self, phase, family, action):
                action('fake.pcap')
                data = tlv(family, 0 if phase == 'partial-withdraw' else 1)
                if phase == 'partial-withdraw' and self.metric_change:
                    data['metric']['delay'] = 111
                return [{'family': family, 'source': 'fe80::1',
                         'opcode_name': 'UPDATE', 'sequence': 100,
                         'flag_names': ['EOT'], 'tlvs': [data]}]

        lab = FakeLab()
        report = {'objectives': {}}
        compat.run_family(lab, report, family, prefix, 4)
        self.assertTrue(all(value['status'] == 'PASS'
                            for value in report['objectives'].values()))
        self.assertEqual(lab.actions.count(('link', False)), 4)
        self.assertIn(('r3', False), lab.actions)
        self.assertEqual(lab.actions.count(('r3', True)), 2)

        failed = {'objectives': {}}
        compat.run_family(FakeLab(metric_change=True), failed, family, prefix, 2)
        self.assertEqual(failed['objectives']['AP-INIT/ipv6']['status'], 'PASS')
        self.assertEqual(failed['objectives']['AP-WITHDRAW/ipv6']['status'], 'FAIL')
        self.assertEqual(failed['objectives']['AP-RECOVER/ipv6']['status'], 'BLOCKED')

    def test_native_netlab_topology(self):
        import yaml
        topology = yaml.safe_load((path.parent / 'topology.yml').read_text())
        self.assertEqual(set(topology['nodes']), {'hub', 'r2', 'r3', 'csr'})
        self.assertEqual(len(topology['links']), 3)
        self.assertEqual([list(link) for link in topology['links']],
                         [['hub', 'r2'], ['hub', 'r3'], ['hub', 'csr']])
        for role in ('hub', 'r2', 'r3'):
            self.assertEqual(topology['nodes'][role]['device'], 'frr')
        self.assertEqual(topology['nodes']['csr']['device'], 'csr')

    def test_scenario_schema(self):
        import yaml
        data = yaml.safe_load((path.parent / 'scenario.yaml').read_text())
        compat.validate(data, '4R-addpath-compat')
        with self.assertRaises(AssertionError):
            compat.validate({**data, 'ipv6_sync_repeats': 1}, '4R-addpath-compat')
        with self.assertRaises(AssertionError):
            compat.validate({**data, 'families': ['ipv4']}, '4R-addpath-compat')


if __name__ == '__main__':
    unittest.main()
