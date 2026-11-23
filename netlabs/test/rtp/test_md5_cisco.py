#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable Cisco-only image selection, wire decoder and objective gates."""
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[3]
CASE = ROOT / 'netlabs/test/rtp/2R-md5-cisco'
SCENARIO = yaml.safe_load((CASE / 'scenario.yaml').read_text())
TOPOLOGY = yaml.safe_load((CASE / 'topology.yml').read_text())
sys.path.insert(0, str(CASE))
sys.path.insert(0, str(ROOT / 'netlabs/common'))
import driver
import md5_wire


def make_event(src, dst, opcode, ts, *, sequence=0, ack=0, flags=0, route=False):
    return {'source': src, 'destination': dst, 'opcode': opcode, 'timestamp': ts,
            'rtp_sequence': sequence, 'ack': ack, 'flags': flags,
            'route_tlvs': [0x0102] if route else [], 'key_id': 7,
            'auth_sequence': 0, 'matches_open_basic': opcode == 'HELLO',
            'matches_open_update': False, 'tlv_types': [2]}


def exchange():
    a, b = '10.44.53.1', '10.44.53.2'
    return [
        make_event(a, '224.0.0.10', 'HELLO', 1),
        make_event(b, '224.0.0.10', 'HELLO', 1.5),
        make_event(a, '224.0.0.10', 'HELLO', 2),
        make_event(b, '224.0.0.10', 'HELLO', 2.5),
        make_event(a, b, 'UPDATE', 3, sequence=11, flags=1),
        make_event(b, a, 'HELLO', 3.3, ack=11),
        make_event(b, a, 'UPDATE', 4, sequence=21, flags=1),
        make_event(a, b, 'HELLO', 4.3, ack=21),
        make_event(a, b, 'UPDATE', 5, sequence=13, flags=8),
        make_event(b, a, 'UPDATE', 6, sequence=23, flags=8),
        make_event(a, b, 'UPDATE', 10, sequence=12, route=True),
        make_event(b, a, 'HELLO', 10.5, ack=12),
        make_event(b, a, 'UPDATE', 11, sequence=22, route=True),
        make_event(a, b, 'HELLO', 11.5, ack=22),
    ]


class CiscoControlTests(unittest.TestCase):
    def test_scenario_preflight_accepts_real_two_cisco_topology(self):
        driver.validate(SCENARIO, TOPOLOGY)

    def test_preflight_rejects_open_eigrp_node(self):
        altered = dict(TOPOLOGY)
        altered['nodes'] = dict(TOPOLOGY['nodes'])
        altered['nodes']['csr1'] = {'device': 'frr'}
        with self.assertRaisesRegex(AssertionError, 'two CSR nodes required'):
            driver.validate(SCENARIO, altered)

    def test_control_is_staged_without_sibling_scenario(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'netlabs/test/rtp/2R-md5-cisco'
            shutil.copytree(CASE, target, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(ROOT / 'netlabs/common', root / 'netlabs/common',
                            ignore=shutil.ignore_patterns('__pycache__'))
            result = subprocess.run([sys.executable, str(target / 'driver.py'), '--help'],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--scenario', result.stdout)

    def test_cisco_only_image_select_skips_docker_and_frr(self):
        path = ROOT / 'netlabs/tools/remote/images.py'
        spec = importlib.util.spec_from_file_location('oe03_images', path)
        image_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(image_module)
        with tempfile.TemporaryDirectory() as tmp:
            topo = Path(tmp, 'topology.yml')
            out = Path(tmp, 'image.json')
            before = (CASE / 'topology.yml').read_bytes()
            topo.write_bytes(before)
            with mock.patch.object(image_module, 'docker',
                                   side_effect=AssertionError('unneeded Docker lookup')):
                image_module.prepare(topo, out)
            self.assertEqual(topo.read_bytes(), before)
            evidence = json.loads(out.read_text())
            self.assertEqual(evidence['source'], 'cisco-only')
            self.assertIsNone(evidence['image'])

    def test_cisco_control_keeps_native_frr_image_gate(self):
        path = ROOT / 'netlabs/tools/remote/images.py'
        spec = importlib.util.spec_from_file_location('oe03_images2', path)
        image_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(image_module)
        with tempfile.TemporaryDirectory() as tmp:
            topo = Path(tmp, 'topology.yml')
            out = Path(tmp, 'image.json')
            mixed = dict(TOPOLOGY)
            mixed['nodes'] = {'csr1': {'device': 'frr'}, 'csr2': {'device': 'csr'}}
            topo.write_text(yaml.safe_dump(mixed))
            with mock.patch.object(image_module, 'installed', return_value='0.5.0'), \
                 mock.patch.object(image_module, 'docker',
                                   return_value=json.dumps([{'Id': 'sha256:' + 'a'*64}])):
                image_module.prepare(topo, out)
            self.assertEqual(json.loads(out.read_text())['source'], 'release')

    def test_underlay_configures_both_cisco_interfaces_without_netlab_initial(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = driver.CiscoLab(CASE / 'topology.yml', Path(tmp), SCENARIO)
            with mock.patch.object(lab, 'config') as configure:
                lab.configure_underlay('csr1')
                lab.configure_underlay('csr2')
            self.assertEqual(configure.call_count, 2)
            for node, call in zip(('csr1', 'csr2'), configure.call_args_list):
                self.assertEqual(call.args[0], node)
                lines = call.args[1]
                self.assertIn('interface GigabitEthernet2', lines)
                self.assertIn('no shutdown', lines)
                self.assertIn('ip address ' + SCENARIO['nodes'][node]['address'] +
                              ' 255.255.255.252', lines)
                self.assertIn('interface Loopback0', lines)
                self.assertIn('ip address ' + SCENARIO['nodes'][node]['router_id'] +
                              ' 255.255.255.255', lines)

    def test_deploy_disables_ansible_and_configures_serially(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = driver.CiscoLab(CASE / 'topology.yml', Path(tmp), SCENARIO)
            events = []

            def fake_command(args, **kwargs):
                if args[:3] == ['docker', 'image', 'inspect']:
                    return json.dumps([{'Id': 'sha256:cisco-test'}])
                events.append(('deploy', tuple(args)))
                return 'ok'

            def fake_cli(node, cmd):
                events.append(('cli', node, cmd))
                if cmd == 'show version':
                    return 'Cisco IOS XE Software Version 17.3.6'
                if cmd == 'show ip interface brief':
                    return (f'GigabitEthernet2 {SCENARIO["nodes"][node]["address"]} '
                            'YES manual up up')
                raise AssertionError('unexpected command: ' + cmd)

            def fake_underlay(node):
                events.append(('underlay', node))

            with mock.patch.object(driver, 'command', side_effect=fake_command), \
                 mock.patch.object(lab, 'mgmt', side_effect=lambda n: '172.20.0.1'), \
                 mock.patch.object(lab, 'cli', side_effect=fake_cli), \
                 mock.patch.object(lab, 'configure_underlay', side_effect=fake_underlay):
                lab.deploy()

            self.assertIn(('deploy', ('netlab', 'up', '--no-config')), events)
            self.assertNotIn(('deploy', ('netlab', 'up')), events)
            c1, c2 = events.index(('underlay', 'csr1')), events.index(('underlay', 'csr2'))
            self.assertLess(c1, c2)
            self.assertLess(events.index(('cli', 'csr1', 'show version')), c1)
            self.assertLess(events.index(('cli', 'csr2', 'show version')), c1)
            self.assertIn(('cli', 'csr1', 'show ip interface brief'), events)
            self.assertIn(('cli', 'csr2', 'show ip interface brief'), events)

    def test_underlay_does_not_accept_shutdown_interface(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = driver.CiscoLab(CASE / 'topology.yml', Path(tmp), SCENARIO)
            good = 'GigabitEthernet2 10.44.53.1 YES manual up up'
            bad = 'GigabitEthernet2 10.44.53.1 YES manual administratively down down'
            self.assertTrue(lab.underlay_ready('csr1', good))
            self.assertFalse(lab.underlay_ready('csr1', bad))
            self.assertFalse(lab.underlay_ready('csr1', 'GigabitEthernet2 unassigned YES unset up up'))

    def test_bidirectional_ack_and_route_assertion(self):
        events = exchange()
        detail = md5_wire.evidence(events, ['10.44.53.1','10.44.53.2'], 7,
                                   route_start=9)
        md5_wire.require_exchange(detail)
        for data in detail.values():
            self.assertEqual(data['acked_init'], 1)
            self.assertEqual(data['acked_route'], 1)
            self.assertGreaterEqual(data['zero_auth_seq'], 2)

    def test_missing_init_ack_fails(self):
        events = [e for e in exchange() if e['ack'] != 11]
        summary = md5_wire.evidence(events, ['10.44.53.1', '10.44.53.2'], 7,
                                    route_start=9)
        with self.assertRaisesRegex(AssertionError, 'INIT'):
            md5_wire.require_exchange(summary)

    def test_missing_route_ack_fails(self):
        events = [e for e in exchange() if e['ack'] != 22]
        summary = md5_wire.evidence(events, ['10.44.53.1', '10.44.53.2'], 7,
                                    route_start=9)
        with self.assertRaisesRegex(AssertionError, 'route'):
            md5_wire.require_exchange(summary)

    def test_early_ack_does_not_validate_later_update(self):
        events = exchange()
        for item in events:
            if item['ack'] == 22:
                item['timestamp'] = 9
        summary = md5_wire.evidence(events, ['10.44.53.1', '10.44.53.2'], 7,
                                    route_start=9)
        self.assertEqual(summary['10.44.53.2']['acked_route'], 0)

    def test_execution_requires_acknowledged_routes_and_rib(self):
        class FakeLab:
            def __init__(self, root):
                self.artifacts = root
                self.containers = {'csr1': 'csr1', 'csr2': 'csr2'}
                self.rib_calls = 0
            def deploy(self):
                return 'cisco-image'
            def configure_md5(self, node):
                pass
            def stable_neighbors(self):
                pass
            def add_route(self, node):
                pass
            def remote_routes(self):
                self.rib_calls += 1
            def snapshot(self):
                pass
            def collect_logs(self):
                pass
            def save(self, name, data):
                (self.artifacts / name).write_text(data)
        with tempfile.TemporaryDirectory() as tmp:
            lab = FakeLab(Path(tmp))
            with mock.patch.object(driver.capture, 'start'), \
                 mock.patch.object(driver.capture, 'stop'), \
                 mock.patch.object(driver.time, 'sleep'), \
                 mock.patch.object(driver.time, 'time', return_value=9), \
                 mock.patch.object(driver.md5_wire, 'analyze', return_value=exchange()):
                report = {'objectives': {}}
                self.assertTrue(driver.execute(lab, report, SCENARIO))
                self.assertEqual(lab.rib_calls, 1)
                self.assertEqual([report['objectives'][k]['status'] for k in driver.CASES],
                                 ['PASS'] * len(driver.CASES))

                missing = [e for e in exchange() if e['ack'] != 22]
                report = {'objectives': {}}
                with mock.patch.object(driver.md5_wire, 'analyze', return_value=missing):
                    self.assertFalse(driver.execute(lab, report, SCENARIO))
                self.assertEqual(report['objectives']['C2C-04']['status'], 'FAIL')

                def unavailable():
                    raise AssertionError('RIB did not converge')
                lab.remote_routes = unavailable
                report = {'objectives': {}}
                self.assertFalse(driver.execute(lab, report, SCENARIO))
                self.assertEqual(report['objectives']['C2C-04']['status'], 'FAIL')

    def test_header_only_hello_ack_is_not_an_authenticated_hello(self):
        packet = bytearray(struct.pack('!BBHIIIHH', 2, 5, 0, 0, 0, 4, 0, 4473))
        packet[2:4] = struct.pack('!H', md5_wire.checksum(bytes(packet)))
        event = md5_wire.decode('10.44.53.2', '10.44.53.1', bytes(packet),
                                1.0, SCENARIO['key_string'])
        self.assertTrue(event['checksum_ok'])
        self.assertEqual(event['opcode'], 'HELLO')
        self.assertEqual(event['ack'], 4)
        self.assertEqual(event['packet_length'], 20)
        self.assertEqual(event['tlv_types'], [])
        self.assertIsNone(event['key_id'])
        self.assertIsNone(event['digest_hex'])
        summary = md5_wire.evidence([event], ['10.44.53.1', '10.44.53.2'], 7,
                                    route_start=0)
        self.assertEqual(summary['10.44.53.2']['hellos'], 0)

    def test_header_only_packets_other_than_hello_acks_are_rejected(self):
        for opcode, flags, sequence, ack in ((5, 0, 0, 0),
                                             (5, 1, 0, 1),
                                             (5, 0, 1, 1),
                                             (1, 0, 0, 1)):
            with self.subTest(opcode=opcode, flags=flags,
                              sequence=sequence, ack=ack):
                packet = bytearray(struct.pack('!BBHIIIHH', 2, opcode, 0,
                                               flags, sequence, ack, 0, 4473))
                packet[2:4] = struct.pack('!H', md5_wire.checksum(bytes(packet)))
                with self.assertRaisesRegex(ValueError, 'missing TLVs'):
                    md5_wire.decode('10.44.53.1', '10.44.53.2', bytes(packet),
                                    1.0, SCENARIO['key_string'])

    def test_live_cisco_md5_capture_has_header_only_acks_and_exchange(self):
        pcap = ROOT / 'netlabs/test/rtp/fixtures/csr1000v-17.03.06-md5-c2c-20261122.pcap'
        sources = ['10.44.53.1', '10.44.53.2']
        events = md5_wire.analyze(pcap, SCENARIO['key_string'], sources)
        self.assertEqual(len(events), 88)
        acks = [event for event in events if event['packet_length'] == 20]
        self.assertEqual(len(acks), 9)
        self.assertEqual({event['source'] for event in acks}, set(sources))
        self.assertTrue(all(event['opcode'] == 'HELLO' and event['ack'] > 0
                            and event['tlv_types'] == [] and event['key_id'] is None
                            for event in acks))
        # Loopback100 route additions begin after 16:32:00 UTC in this capture.
        summary = md5_wire.evidence(events, sources, SCENARIO['key_id'],
                                    route_start=1795365120)
        md5_wire.require_exchange(summary)
        self.assertTrue(all(item['acked_init'] and item['acked_route']
                            for item in summary.values()))

    def test_recorded_cisco_capture_passes_scenario_wire_gates(self):
        pcap = ROOT / 'netlabs/test/rtp/fixtures/csr1000v-17.03.06-md5-c2c-20261122.pcap'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(pcap, root / 'cisco-cisco-md5.pcap')
            lab = mock.Mock()
            lab.artifacts = root
            lab.containers = {'csr1': 'csr1'}
            lab.save.side_effect = lambda name, data: (root / name).write_text(data)
            with mock.patch.object(driver.capture, 'start'), \
                 mock.patch.object(driver.capture, 'stop'), \
                 mock.patch.object(driver.time, 'sleep'), \
                 mock.patch.object(driver.time, 'time', return_value=1795365120):
                report = {'objectives': {}}
                self.assertTrue(driver.execute(lab, report, SCENARIO))
            self.assertEqual(report['status'], 'PASS')
            self.assertEqual([report['objectives'][name]['status']
                              for name in driver.CASES], ['PASS'] * 5)
            self.assertEqual(len(json.loads((root / 'cisco-md5-packets.json').read_text())), 88)

    def test_transitional_unauthenticated_hello_is_recorded_not_counted(self):
        packet = bytearray(struct.pack('!BBHIIIHH', 2, 5, 0, 0, 0, 0, 0, 4473))
        packet += struct.pack('!HH6sH', 1, 12, bytes([1, 0, 1, 0, 0, 0]), 15)
        packet[2:4] = struct.pack('!H', md5_wire.checksum(bytes(packet)))
        data = md5_wire.decode('10.44.53.1', '224.0.0.10', bytes(packet),
                               1.0, 'oe03-lab-md5')
        self.assertIsNone(data['key_id'])
        self.assertTrue(data['checksum_ok'])
        self.assertFalse(data['matches_open_basic'])
        summary = md5_wire.evidence([data], ['10.44.53.1', '10.44.53.2'], 7,
                                    route_start=0)
        self.assertEqual(summary['10.44.53.1']['hellos'], 0)

    def test_basic_md5_and_unsupported_candidate_are_observations(self):
        from hashlib import md5
        key = 'oe03-lab-md5'
        base = bytearray(struct.pack('!BBHIIIHH', 2, 5, 0, 0, 0, 0, 0, 4473))
        base += struct.pack('!HHHHII', 2, 40, 2, 16, 7, 0)
        base += b'\0' * 8 + b'\0' * 16
        base += struct.pack('!HH6sH', 1, 12, bytes([1,0,1,0,0,0]), 15)
        base[44:60] = md5(bytes(base[:44]) + key.encode().ljust(16, b'\0')).digest()
        base[2:4] = struct.pack('!H', md5_wire.checksum(bytes(base)))
        event = md5_wire.decode('10.44.53.1', '224.0.0.10', bytes(base), 1.0, key)
        self.assertTrue(event['checksum_ok'])
        self.assertTrue(event['matches_open_basic'])
        self.assertEqual(event['auth_sequence'], 0)
        base[44] ^= 1
        wrong = md5_wire.decode('10.44.53.1', '224.0.0.10', bytes(base), 2.0, key)
        self.assertFalse(wrong['matches_open_basic'])
        # Mismatched digest is reported, not passed off as a different Cisco profile.


    def test_cisco_ssh_transport_timeout_retries_and_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = driver.CiscoLab(CASE / 'topology.yml', Path(tmp), SCENARIO)
            attempts = []

            def unstable():
                attempts.append(1)
                if len(attempts) < 3:
                    raise RuntimeError('Cisco SSH terminated: ssh: connect timed out')
                return 'ready'

            with mock.patch.object(driver.time, 'sleep', return_value=None):
                self.assertEqual(lab.retry_ssh('csr1', unstable, seconds=30), 'ready')
            self.assertEqual(len(attempts), 3)
            log = Path(tmp, 'csr1-ssh-retries.log').read_text()
            self.assertIn('attempt 1', log)
            self.assertIn('attempt 2', log)

    def test_cisco_ssh_does_not_retry_cli_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = driver.CiscoLab(CASE / 'topology.yml', Path(tmp), SCENARIO)
            run = mock.Mock(side_effect=RuntimeError('Cisco invalid input rejected'))
            with self.assertRaisesRegex(RuntimeError, 'invalid input'):
                lab.retry_ssh('csr1', run, seconds=30)
            self.assertEqual(run.call_count, 1)

    def test_cisco_ssh_timeout_is_bounded_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = driver.CiscoLab(CASE / 'topology.yml', Path(tmp), SCENARIO)
            run = mock.Mock(side_effect=RuntimeError('Cisco SSH terminated: timeout'))
            with self.assertRaisesRegex(RuntimeError, 'unavailable after 1 attempts'):
                lab.retry_ssh('csr1', run, seconds=0)
            self.assertEqual(run.call_count, 1)
            self.assertIn('timeout', Path(tmp, 'csr1-ssh-retries.log').read_text())

    def test_cisco_boot_version_is_captured_without_duplicate_ssh(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = driver.CiscoLab(CASE / 'topology.yml', Path(tmp), SCENARIO)
            events = []

            def fake_command(args, **kwargs):
                if args[:3] == ['docker', 'image', 'inspect']:
                    return json.dumps([{'Id': 'sha256:fixture'}])
                return 'ok'

            def fake_cli(node, cmd):
                events.append((node, cmd))
                if cmd == 'show version':
                    return 'Cisco IOS XE Software, Version 17.03.06'
                if cmd == 'show ip interface brief':
                    return (f'GigabitEthernet2 {SCENARIO["nodes"][node]["address"]} '
                            'YES manual up up')
                raise AssertionError(cmd)

            with mock.patch.object(driver, 'command', side_effect=fake_command), \
                 mock.patch.object(lab, 'mgmt', side_effect=lambda n: '172.20.0.1'), \
                 mock.patch.object(lab, 'cli', side_effect=fake_cli), \
                 mock.patch.object(lab, 'configure_underlay'):
                lab.deploy()
            self.assertEqual(events.count(('csr1', 'show version')), 1)
            self.assertEqual(events.count(('csr2', 'show version')), 1)

    def test_cisco_config_retries_partial_idempotent_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = driver.CiscoLab(CASE / 'topology.yml', Path(tmp), SCENARIO)
            lab.addresses = {'csr1': '192.0.2.1'}
            calls = []

            class StubCisco:
                def __init__(self, host, logfile):
                    calls.append(('open', host))
                def __enter__(self):
                    if len(calls) == 1:
                        raise RuntimeError('Cisco SSH terminated: ssh: timeout')
                    return self
                def configure(self, lines):
                    calls.append(('config', tuple(lines)))
                def __exit__(self, *args):
                    pass

            with mock.patch.object(driver, 'Cisco', StubCisco), \
                 mock.patch.object(driver.time, 'sleep', return_value=None):
                lab.config('csr1', ['interface GigabitEthernet2', 'no shutdown'])
            self.assertEqual(len([x for x in calls if x[0] == 'open']), 2)
            self.assertEqual(len([x for x in calls if x[0] == 'config']), 1)


if __name__ == '__main__':
    unittest.main()
