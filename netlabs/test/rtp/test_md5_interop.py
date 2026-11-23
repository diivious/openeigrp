#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable regression gates for real Cisco/OpenEIGRP MD5 wire scenarios."""
import copy
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[3]
CASE = ROOT / 'netlabs/test/rtp/2R-md5-interop'
FIXTURE = ROOT / 'netlabs/test/rtp/fixtures/csr1000v-17.03.06-md5-c2c-20261122.pcap'
sys.path.insert(0, str(ROOT / 'netlabs/common'))
import wire

spec = importlib.util.spec_from_file_location('md5_interop_driver', CASE / 'driver.py')
interop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(interop)
SCENARIO = yaml.safe_load((CASE / 'scenario.yaml').read_text())
TOPOLOGY = yaml.safe_load((CASE / 'topology.yml').read_text())
STAGES = {'auth': 0, 'add': 100, 'withdraw': 200, 'recover': 300, 'end': 400}
SOURCES = {'oe': '10.44.53.1', 'ce': '10.44.53.2'}


def packet(role, opcode, when, *, seq=0, ack=0, flags=0,
           tlv=None, dest=None):
    """Build packets through the production EIGRP wire decoder."""
    other = 'ce' if role == 'oe' else 'oe'
    body = struct.pack('!BBHIIIHH', 2, opcode, 0, flags, seq, ack, 0, 4473)
    if tlv:
        body += tlv
    return wire.decode_eigrp(body, 4, SOURCES[role],
                             dest or SOURCES[other], when)


def auth(key_id=7):
    value = (struct.pack('!HHII', 2, 16, key_id, 0) +
             b'\0' * 8 + b'\x42' * 16)
    return struct.pack('!HH', 2, len(value) + 4) + value


def route_tlvs():
    """Use real Cisco MP-internal wire bodies, not fabricated route layouts."""
    events, diagnostics = wire.read_pcap(FIXTURE, strict=True)
    if diagnostics:
        raise AssertionError(diagnostics)
    found = {}
    for role, source in SOURCES.items():
        prefix = SCENARIO['nodes'][role]['advertised']
        for e in events:
            if e['source'] != source:
                continue
            for tlv in e['tlvs']:
                if prefix in tlv.get('prefixes', []) and tlv['route_kind'] == 'internal':
                    found[role] = struct.pack('!HH', tlv['type'], tlv['length']) + \
                                  bytes.fromhex(tlv['value_hex'])
                    break
            if role in found:
                break
    if set(found) != {'oe', 'ce'}:
        raise AssertionError('missing original Cisco internal-route fixture')
    return found


def poison(tlv):
    """Encode an unreachable Cisco MP delay at the real metric position."""
    poisoned = bytearray(tlv)
    if poisoned[:2] != b'\x06\x02':
        raise AssertionError('expected Cisco MP-internal route fixture')
    poisoned[20:26] = b'\xff' * 6  # TLV header + 8-byte MP header + metric[8:14]
    return bytes(poisoned)


def exchange():
    routes = route_tlvs()
    events = []
    for role, initseq, baseseq in (('oe', 11, 30), ('ce', 21, 40)):
        other = 'ce' if role == 'oe' else 'oe'
        events.extend([
            packet(role, 5, 10, tlv=auth(), dest=interop.MULTICAST),
            packet(role, 5, 15, tlv=auth(), dest=interop.MULTICAST),
            packet(role, 1, 20, seq=initseq, flags=1, tlv=auth()),
            packet(other, 5, 21, ack=initseq),  # Valid Cisco header-only ACK.
            packet(role, 1, 40, seq=initseq + 1, flags=8, tlv=auth()),
        ])
        for step, when, route in (('add', 110, routes[role]),
                                  ('withdraw', 210, poison(routes[role])),
                                  ('recover', 310, routes[role])):
            seq = baseseq + {'add': 1, 'withdraw': 2, 'recover': 3}[step]
            events.append(packet(role, 1, when, seq=seq, tlv=auth() + route))
            events.append(packet(other, 5, when + 1, ack=seq))
    return sorted(events, key=lambda e: e['timestamp'])


class Md5InteropTests(unittest.TestCase):
    def test_real_cisco_pcap_and_header_only_acks(self):
        events, errors = wire.read_pcap(FIXTURE, strict=True)
        self.assertFalse(errors)
        self.assertEqual(len(events), 88)
        self.assertEqual(sum(not e['tlvs'] for e in events), 9)
        self.assertEqual(sum(interop.auth_id(e) == 7 for e in events), 79)
        for event in (e for e in events if not e['tlvs']):
            self.assertIsNone(interop.auth_id(event))
            self.assertEqual(event['opcode_name'], 'HELLO')
            self.assertGreater(event['ack'], 0)

    def test_scenario_preflight_accepts_both_roles_and_native_addresses(self):
        interop.validate(SCENARIO, TOPOLOGY)
        self.assertEqual(TOPOLOGY['nodes']['oe']['frr.daemons'], ['eigrpd'])

    def test_preflight_rejects_early_protocol_configuration(self):
        bad = copy.deepcopy(TOPOLOGY)
        bad['nodes']['csr']['config'] = ['eigrp-2r-interop']
        with self.assertRaisesRegex(AssertionError, 'after packet capture'):
            interop.validate(SCENARIO, bad)

    def test_preflight_rejects_changed_key_or_link(self):
        bad = copy.deepcopy(SCENARIO)
        bad['origin']['interface'] = 'lo'
        with self.assertRaisesRegex(AssertionError, 'dedicated veth'):
            interop.validate(bad, TOPOLOGY)
        bad = copy.deepcopy(SCENARIO)
        bad['key_id'] = 8
        with self.assertRaisesRegex(AssertionError, 'fixture'):
            interop.validate(bad, TOPOLOGY)
        bad = copy.deepcopy(TOPOLOGY)
        bad['links'][0]['csr']['ipv4'] = '10.44.53.3/30'
        with self.assertRaisesRegex(AssertionError, 'addressing'):
            interop.validate(SCENARIO, bad)

    def test_real_cisco_route_tlvs_and_unreachable_metric(self):
        for role, tlv in route_tlvs().items():
            update = packet(role, 1, 3, seq=1, tlv=auth() + tlv)
            self.assertEqual(interop.auth_id(update), 7)
            self.assertEqual(update['tlvs'][1]['prefixes'],
                             [SCENARIO['nodes'][role]['advertised']])
            poison_update = packet(role, 1, 5, seq=2, tlv=auth() + poison(tlv))
            self.assertEqual(poison_update['tlvs'][1]['metric']['delay'], 0xffffffffffff)

    def test_positive_bidirectional_startup_add_withdraw_recovery(self):
        summary = interop.verify_wire(exchange(), SCENARIO, STAGES)
        self.assertEqual(set(summary), {'oe', 'ce'})
        self.assertEqual(summary['oe']['route']['withdraw'], 1)
        self.assertEqual(summary['ce']['route']['recover'], 1)

    def test_early_failure_preserves_independent_hello_and_init_evidence(self):
        events = [e for e in exchange() if e['timestamp'] < 100]
        stages = {'auth': 0, 'end': 100}
        self.assertEqual(set(interop.verify_wire(events, SCENARIO, stages,
                                                 through='hello')), {'oe', 'ce'})
        self.assertEqual(set(interop.verify_wire(events, SCENARIO, stages,
                                                 through='init')), {'oe', 'ce'})
        with self.assertRaisesRegex(AssertionError, 'missing successful add'):
            interop.verify_wire(events, SCENARIO, stages, through='add')

    def test_missing_keyed_hello_rejected_even_with_init_and_ack(self):
        data = [e for e in exchange() if e['opcode_name'] != 'HELLO' or e['ack']]
        with self.assertRaisesRegex(AssertionError, 'keyed multicast Hellos'):
            interop.verify_wire(data, SCENARIO, STAGES)

    def test_wrong_key_and_malformed_md5_rejected(self):
        data = exchange()
        target = next(e for e in data if e['source'] == SOURCES['oe'] and
                      e['opcode_name'] == 'HELLO' and not e['ack'])
        target['tlvs'][0]['value_hex'] = auth(8)[4:].hex()
        with self.assertRaisesRegex(AssertionError, 'keyed multicast Hellos'):
            # Both OpenEIGRP Hellos must fail the key-ID assertion.
            for e in data:
                if e['source'] == SOURCES['oe'] and e['opcode_name'] == 'HELLO' and not e['ack']:
                    e['tlvs'][0]['value_hex'] = auth(8)[4:].hex()
            interop.verify_wire(data, SCENARIO, STAGES)
        target['tlvs'][0]['length'] = 39
        with self.assertRaisesRegex(AssertionError, 'malformed MD5'):
            interop.auth_id(target)

    def test_missing_or_false_source_init_ack_rejected(self):
        data = exchange()
        data = [e for e in data if not (e['ack'] == 11 and e['source'] == SOURCES['ce'])]
        data.append(packet('oe', 5, 21, ack=11))
        with self.assertRaisesRegex(AssertionError, 'oe missing INIT ACK'):
            interop.verify_wire(data, SCENARIO, STAGES)

    def test_missing_eot_rejected(self):
        data = [e for e in exchange() if not (e['source'] == SOURCES['ce'] and
                e['flags'] & 8)]
        with self.assertRaisesRegex(AssertionError, 'ce missing authenticated EOT'):
            interop.verify_wire(data, SCENARIO, STAGES)

    def test_phase_cannot_reuse_old_update_or_ack(self):
        data = [e for e in exchange() if not (e['source'] == SOURCES['oe'] and
                e['opcode_name'] == 'UPDATE' and 200 <= e['timestamp'] < 300)]
        with self.assertRaisesRegex(AssertionError, 'oe missing keyed withdraw'):
            interop.verify_wire(data, SCENARIO, STAGES)
        data = [e for e in exchange() if not (e['source'] == SOURCES['ce'] and
                e['ack'] == 33)]
        with self.assertRaisesRegex(AssertionError, 'oe missing recover route ACK'):
            interop.verify_wire(data, SCENARIO, STAGES)

    def test_unkeyed_route_update_rejected(self):
        data = exchange()
        target = next(e for e in data if e['source'] == SOURCES['oe'] and
                      e['timestamp'] == 310)
        target['tlvs'] = target['tlvs'][1:]
        with self.assertRaisesRegex(AssertionError, '(missing keyed recover|unauthenticated)'):
            interop.verify_wire(data, SCENARIO, STAGES)

    def test_unpoisoned_route_is_not_a_withdrawal(self):
        data = exchange()
        target = next(e for e in data if e['source'] == SOURCES['ce'] and
                      e['timestamp'] == 210)
        target['tlvs'][1]['metric']['delay'] = 100
        with self.assertRaisesRegex(AssertionError, 'ce missing keyed withdraw'):
            interop.verify_wire(data, SCENARIO, STAGES)

    def test_real_cli_route_origin_and_retirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            with mock.patch.object(interop, 'command') as command, \
                 mock.patch.object(lab, 'oe_config') as oe, \
                 mock.patch.object(lab, 'ce_config') as ce:
                for role in interop.ROLES:
                    lab.change_route(role, add=True)
                    lab.change_route(role, add=False)
                self.assertIn('ip', command.call_args.args[0])
                self.assertEqual(oe.call_count, 2)
                self.assertEqual(ce.call_count, 2)
                self.assertIn('no network 198.18.253.1 0.0.0.0', oe.call_args.args[0])
                self.assertIn('no interface Loopback100', ce.call_args.args[0])

    def test_dedicated_veth_is_created_once_and_kept_for_withdrawal(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            events = []
            def shell(argv):
                events.append(('ip', argv))
                return ''
            def cfg(lines):
                events.append(('config', lines))
            with mock.patch.object(interop, 'command', side_effect=shell), \
                 mock.patch.object(lab, 'oe_config', side_effect=cfg):
                lab.change_route('oe', add=True)
                lab.change_route('oe', add=False)
                lab.change_route('oe', add=True)

            commands = [data for typ, data in events if typ == 'ip']
            self.assertEqual(commands, [
                ['docker', 'exec', lab.oe, 'ip', 'link', 'add', 'md5rt0',
                 'type', 'veth', 'peer', 'name', 'md5rt1'],
                ['docker', 'exec', lab.oe, 'ip', 'link', 'set', 'md5rt1', 'up'],
                ['docker', 'exec', lab.oe, 'ip', 'link', 'set', 'md5rt0', 'up'],
                ['docker', 'exec', lab.oe, 'ip', 'address', 'add',
                 '198.18.253.1/32', 'dev', 'md5rt0'],
            ])
            self.assertTrue(lab.origin_created)
            self.assertEqual([typ for typ, _ in events],
                             ['ip', 'ip', 'ip', 'ip', 'config', 'config', 'config'])
            configs = [data for typ, data in events if typ == 'config']
            self.assertIn('af-interface md5rt0', configs[0])
            self.assertIn('passive-interface', configs[0])
            self.assertIn('network 198.18.253.1 0.0.0.0', configs[0])
            self.assertIn('no network 198.18.253.1 0.0.0.0', configs[1])
            self.assertIn('network 198.18.253.1 0.0.0.0', configs[2])
            self.assertFalse(any('lo' in part for cmd in commands for part in cmd))
            self.assertFalse(any('del' in cmd for cmd in commands))

    def test_origin_is_checked_against_actual_local_topology(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            topology = ('EIGRP-IPv4 Topology Entry for 198.18.253.1/32\n'
                        '  State is Passive (DUAL state 0)\n'
                        '        via Connected, md5rt0\n')
            with mock.patch.object(lab, 'oe_cli', return_value=topology) as cli:
                lab.origin_state(present=True, phase='add')
                self.assertEqual(cli.call_args.args[0],
                                 'show eigrp address-family ipv4 topology 4473 '
                                 '198.18.253.1/32')
            self.assertEqual((Path(tmp) / 'oe-origin-add.log').read_text(), topology)
            with mock.patch.object(lab, 'oe_cli', return_value='Entry not found'):
                lab.origin_state(present=False, phase='withdraw')
            self.assertEqual((Path(tmp) / 'oe-origin-withdraw.log').read_text(),
                             'Entry not found')
            with mock.patch.object(lab, 'oe_cli', return_value=topology):
                lab.origin_state(present=True, phase='recover')
            self.assertTrue((Path(tmp) / 'oe-origin-recover.log').is_file())

    def test_origin_probe_uses_supported_topology_command(self):
        # 0.5.18 interop failed despite both remote routes being installed:
        # FRR rejected "ipv4 4473 topology ..." (AS must follow topology).
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            expected = ('show eigrp address-family ipv4 topology 4473 '
                        '198.18.253.1/32')
            topology = ('EIGRP-IPv4 Topology Entry for 198.18.253.1/32\n'
                        '        via Connected, md5rt0\n')
            calls = []

            def cli(cmd):
                calls.append(cmd)
                if cmd != expected:
                    raise RuntimeError('% Unknown command: ' + cmd)
                return topology if len(calls) != 2 else 'Entry not found'

            def wait_once(read, predicate, seconds):
                output = read()
                self.assertTrue(predicate(output))
                return output

            with mock.patch.object(lab, 'oe_cli', side_effect=cli), \
                 mock.patch.object(lab, 'wait', side_effect=wait_once):
                lab.origin_state(present=True, phase='add')
                lab.origin_state(present=False, phase='withdraw')
                lab.origin_state(present=True, phase='recover')
            self.assertEqual(calls, [expected] * 3)

    def test_unsupported_topology_cli_fails_without_poll_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            error = RuntimeError('% Unknown command: show eigrp address-family')
            with mock.patch.object(lab, 'oe_cli', side_effect=error), \
                 mock.patch.object(lab, 'wait', side_effect=lambda fn, pred, seconds:
                                   fn()):
                with self.assertRaisesRegex(AssertionError,
                                            'unsupported topology CLI'):
                    lab.origin_state(present=True, phase='add')

    def test_real_0518_route_add_capture_and_both_ribs(self):
        # The live run failed its local CLI check AFTER both peers installed
        # each other's route. Preserve that evidence in the regression suite.
        fixtures = ROOT / 'netlabs/test/rtp/fixtures'
        capture, errors = wire.read_pcap(fixtures / 'md5-interop-0518-add.pcap',
                                         strict=True)
        self.assertFalse(errors)
        self.assertEqual(len(capture), 45)
        candidates = [e['timestamp'] for e in capture if any(
            '198.18.253.1/32' in t.get('prefixes', []) or
            '198.19.253.2/32' in t.get('prefixes', []) for t in e['tlvs'])]
        self.assertTrue(candidates)
        stages = {'auth': min(e['timestamp'] for e in capture) - 1,
                  'add': min(candidates) - .1,
                  'withdraw': max(e['timestamp'] for e in capture) + 1,
                  'end': max(e['timestamp'] for e in capture) + 2}
        summary = interop.verify_wire(capture, SCENARIO, stages, through='add')
        self.assertGreaterEqual(summary['oe']['route']['add'], 1)
        self.assertGreaterEqual(summary['ce']['route']['add'], 1)
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            oe = (fixtures / 'md5-interop-0518-oe-rib.log').read_text()
            ce = (fixtures / 'md5-interop-0518-ce-rib.log').read_text()
            with mock.patch.object(lab, 'routes', side_effect=lambda role, family:
                                   oe if role == 'oe' else ce):
                self.assertTrue(all(lab.route_state(present=True).values()))

    def test_origin_probe_rejects_stale_connected_route_after_withdraw(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            topology = 'via Connected, md5rt0'
            def fail_after_probe(reader, predicate, seconds):
                actual = reader()
                self.assertFalse(predicate(actual))
                raise AssertionError('condition never satisfied: ' + actual)
            with mock.patch.object(lab, 'oe_cli', return_value=topology), \
                 mock.patch.object(lab, 'wait', side_effect=fail_after_probe):
                with self.assertRaisesRegex(AssertionError, 'condition never satisfied'):
                    lab.origin_state(present=False, phase='withdraw')
            # Preserve the actual local topology for diagnosis on failure.
            self.assertEqual((Path(tmp) / 'oe-origin-withdraw.log').read_text(),
                             topology)

    def test_full_execution_requires_origin_before_remote_rib(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            report = {'objectives': {}}
            with mock.patch.object(lab, 'deploy'), \
                 mock.patch.object(lab, 'configure_md5'), \
                 mock.patch.object(lab, 'stable_peers'), \
                 mock.patch.object(lab, 'peers'), \
                 mock.patch.object(lab, 'change_route'), \
                 mock.patch.object(lab, 'origin_state', side_effect=AssertionError(
                     'local origin missing')) as origin, \
                 mock.patch.object(lab, 'route_state') as remote, \
                 mock.patch.object(lab, 'snapshot_md5'), \
                 mock.patch.object(interop.capture, 'start'), \
                 mock.patch.object(interop.capture, 'stop'), \
                 mock.patch.object(interop.wire, 'read_pcap',
                                   return_value=(exchange(), [])), \
                 mock.patch.object(interop.time, 'time',
                                   side_effect=[0, 100, 200]), \
                 mock.patch.object(interop.time, 'sleep'):
                self.assertFalse(interop.execute(lab, report, SCENARIO))
                remote.assert_not_called()
                origin.assert_called_once_with(present=True, phase='add')
                self.assertEqual(report['objectives']['MDI-04']['status'], 'PASS')
                self.assertIn('local origin missing', report['error'])
                self.assertEqual(report['objectives']['MDI-05']['status'], 'FAIL')
                self.assertEqual(report['objectives']['MDI-06']['status'], 'BLOCKED')
                self.assertEqual(report['objectives']['MDI-07']['status'], 'BLOCKED')

    def test_cisco_classic_and_oe_named_md5_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            oe_cfg = ('router eigrp OpenEIGRP\nkey chain OE03C2OE\n'
                      'authentication mode md5\nauthentication key-chain OE03C2OE')
            ce_cfg = ('router eigrp 4473\nkey chain OE03C2OE\n'
                      'ip authentication mode eigrp 4473 md5\n'
                      'ip authentication key-chain eigrp 4473 OE03C2OE')
            with mock.patch.object(lab, 'oe_config') as oe, \
                 mock.patch.object(lab, 'ce_config') as ce, \
                 mock.patch.object(lab, 'oe_cli', return_value=oe_cfg), \
                 mock.patch.object(lab, 'ce_cli', return_value=ce_cfg):
                lab.configure_md5()
                self.assertEqual(oe.call_count, 2)
                self.assertEqual(ce.call_count, 2)
                self.assertIn('authentication mode md5', oe.call_args.args[0])
                self.assertIn('ip authentication mode eigrp 4473 md5',
                              ce.call_args_list[0].args[0])
                self.assertIn('router eigrp 4473', ce.call_args_list[1].args[0])

    def test_cisco_and_oe_rib_verification_does_not_accept_local_routes(self):
        oe = 'E[121]>* 198.19.253.2/32 [90/12345] via 10.44.53.2, eth1\n'
        ce = ('      198.18.253.0/32 is subnetted, 1 subnets\n'
              'D        198.18.253.1 [90/12345] via 10.44.53.1\n')
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            with mock.patch.object(lab, 'routes', side_effect=lambda role, family:
                                   oe if role == 'oe' else ce):
                self.assertTrue(all(lab.route_state(present=True).values()))
                with self.assertRaisesRegex(AssertionError, 'did not meet present=False'):
                    # The same learned routes must not be accepted as withdrawn.
                    with mock.patch.object(interop.time, 'sleep'):
                        lab.check_routes('ipv4', {'oe':['198.19.253.2/32']},
                                         'internal', present=False, seconds=.001)

    def test_full_execution_gates_all_seven_objectives(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            report = {'objectives': {}}
            with mock.patch.object(lab, 'deploy'), \
                 mock.patch.object(lab, 'configure_md5'), \
                 mock.patch.object(lab, 'stable_peers'), \
                 mock.patch.object(lab, 'peers'), \
                 mock.patch.object(lab, 'change_route'), \
                 mock.patch.object(lab, 'origin_state'), \
                 mock.patch.object(lab, 'route_state'), \
                 mock.patch.object(lab, 'snapshot_md5'), \
                 mock.patch.object(interop.capture, 'start'), \
                 mock.patch.object(interop.capture, 'stop'), \
                 mock.patch.object(interop.wire, 'read_pcap',
                                   return_value=(exchange(), [])), \
                 mock.patch.object(interop.time, 'time',
                                   side_effect=[0, 100, 200, 300, 400]), \
                 mock.patch.object(interop.time, 'sleep'):
                self.assertTrue(interop.execute(lab, report, SCENARIO))
                self.assertEqual(set(report['objectives']), set(interop.CASES))
                self.assertTrue(all(c['status'] == 'PASS'
                                    for c in report['objectives'].values()))

    def test_broken_capture_fails_closed_even_with_good_rib(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            report = {'objectives': {}}
            with mock.patch.object(lab, 'deploy'), \
                 mock.patch.object(lab, 'configure_md5'), \
                 mock.patch.object(lab, 'stable_peers'), \
                 mock.patch.object(lab, 'peers'), \
                 mock.patch.object(lab, 'change_route'), \
                 mock.patch.object(lab, 'origin_state'), \
                 mock.patch.object(lab, 'route_state'), \
                 mock.patch.object(lab, 'snapshot_md5'), \
                 mock.patch.object(interop.capture, 'start'), \
                 mock.patch.object(interop.capture, 'stop', side_effect=RuntimeError('tcpdump failed')), \
                 mock.patch.object(interop.time, 'time',
                                   side_effect=[0, 100, 200, 300, 400]), \
                 mock.patch.object(interop.time, 'sleep'):
                self.assertFalse(interop.execute(lab, report, SCENARIO))
                self.assertEqual(report['objectives']['MDI-01']['status'], 'PASS')
                self.assertEqual(report['objectives']['MDI-07']['status'], 'FAIL')
                self.assertIn('tcpdump failed', report['capture_error'])


    def test_packet_debug_is_started_after_auth_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            oe_cfg = ('router eigrp OpenEIGRP\nkey chain OE03C2OE\n'
                      'authentication mode md5\nauthentication key-chain OE03C2OE')
            ce_cfg = ('router eigrp 4473\nkey chain OE03C2OE\n'
                      'ip authentication mode eigrp 4473 md5\n'
                      'ip authentication key-chain eigrp 4473 OE03C2OE')
            calls = []
            def ce_command(cmd):
                calls.append(cmd)
                return 'EIGRP packet debugging is on' if cmd.startswith('debug') else ce_cfg
            with mock.patch.object(lab, 'oe_config'), \
                 mock.patch.object(lab, 'ce_config'), \
                 mock.patch.object(lab, 'oe_cli', return_value=oe_cfg), \
                 mock.patch.object(lab, 'ce_cli', side_effect=ce_command):
                lab.configure_md5()
            self.assertEqual(calls, ['show running-config', 'debug eigrp packets'])
            self.assertIn('debugging is on',
                          (Path(tmp) / 'ce-debug-enable.log').read_text())

    def test_optional_debug_failure_does_not_override_uut(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            oe_cfg = ('router eigrp OpenEIGRP\nkey chain OE03C2OE\n'
                      'authentication mode md5\nauthentication key-chain OE03C2OE')
            ce_cfg = ('router eigrp 4473\nkey chain OE03C2OE\n'
                      'ip authentication mode eigrp 4473 md5\n'
                      'ip authentication key-chain eigrp 4473 OE03C2OE')
            def ce_command(cmd):
                if cmd.startswith('debug'):
                    raise RuntimeError('Invalid input')
                return ce_cfg
            with mock.patch.object(lab, 'oe_config'), \
                 mock.patch.object(lab, 'ce_config'), \
                 mock.patch.object(lab, 'oe_cli', return_value=oe_cfg), \
                 mock.patch.object(lab, 'ce_cli', side_effect=ce_command):
                lab.configure_md5()
            self.assertIn('debugging unavailable: Invalid input',
                          (Path(tmp) / 'ce-debug-enable.log').read_text())

    def test_failed_scenario_collects_both_daemons_rejection_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            def ce_command(cmd):
                return {'show logging': 'Cisco: invalid authentication',
                        'show ip eigrp events': 'Cisco: INIT timeout'}.get(cmd, 'ok')
            with mock.patch.object(lab, 'neighbor', return_value='no peer'), \
                 mock.patch.object(lab, 'routes', return_value='no route'), \
                 mock.patch.object(lab, 'oe_cli', return_value='running config'), \
                 mock.patch.object(lab, 'ce_cli', side_effect=ce_command), \
                 mock.patch.object(interop, 'command', return_value='FRR MD5 checksum mismatch') as cmd:
                lab.snapshot_md5()
            self.assertIn('FRR MD5 checksum mismatch',
                          (Path(tmp) / 'oe-daemon.log').read_text())
            self.assertIn('invalid authentication',
                          (Path(tmp) / 'ce-logging.log').read_text())
            self.assertIn('INIT timeout',
                          (Path(tmp) / 'ce-eigrp-events.log').read_text())
            self.assertIn('check=False', str(cmd.call_args_list[-1]))

    def test_failed_diagnostic_collection_is_reported_without_hiding_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = interop.Md5Lab(CASE / 'topology.yml', tmp, SCENARIO)
            def ce_command(cmd):
                if cmd in ('show logging', 'show ip eigrp events'):
                    raise RuntimeError('CLI unavailable')
                return 'ok'
            with mock.patch.object(lab, 'neighbor', return_value='no peer'), \
                 mock.patch.object(lab, 'routes', return_value='no route'), \
                 mock.patch.object(lab, 'oe_cli', return_value='running config'), \
                 mock.patch.object(lab, 'ce_cli', side_effect=ce_command), \
                 mock.patch.object(interop, 'command', return_value=''):
                lab.snapshot_md5()
            self.assertIn('unavailable: CLI unavailable',
                          (Path(tmp) / 'ce-logging.log').read_text())
            self.assertIn('unavailable: CLI unavailable',
                          (Path(tmp) / 'ce-eigrp-events.log').read_text())

if __name__ == '__main__':
    unittest.main()
