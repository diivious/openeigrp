#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable checks of the isolated RTP-12 restart sequencing and gates."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import TestCase, main, mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / 'test/rtp/2R-restart'
module_spec = importlib.util.spec_from_file_location('live_restart', SPEC / 'driver.py')
restart = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(restart)


class FakeLab:
    def __init__(self):
        self.oe = 'clab-oe'
        self.ce = 'clab-ce'
        self.topology = SPEC / 'topology.yml'
        self.info = {'defaults': {'devices.csr.clab.image': 'cisco'}}
        self.deploy_count = 0
        self.peer_checks = []
        self.route_checks = []
        self.alive = True
        self.saved = {}
        self.cli_commands = []
        self.reloaded = False

    def save(self, name, value, *, append=False):
        self.saved[name] = self.saved.get(name, '') + value if append else value

    def ce_cli(self, cmd):
        self.cli_commands.append(('ce', cmd))
        return 'Building configuration...\n[OK]' if cmd == 'write memory' else 'Cisco status for ' + cmd

    def ce_reload(self):
        self.reloaded = True
        self.alive = False

    def oe_cli(self, cmd):
        self.cli_commands.append(('oe', cmd))
        return 'OE status for ' + cmd

    def deploy(self):
        self.deploy_count += 1
        return 'open-eigrp'

    def peer_address(self, role, family):
        return {'oe': 'fe80::2', 'ce': 'fe80::1'}[role]

    def peers(self, family, present, seconds):
        self.peer_checks.append((family, present))

    def has_peer(self, role, family, *, peer=None):
        return self.alive

    def wait(self, action, predicate, seconds):
        value = action()
        if not predicate(value):
            raise AssertionError('peer loss not observed')
        return value

    def check_routes(self, family, prefixes, kind, *, present=True, seconds=0):
        self.route_checks.append((family, prefixes, kind, present))

    def _csr_address(self):
        return '192.0.2.1'

    def _wait_oe(self, seconds):
        pass

    def _wait_ce(self, seconds):
        pass


class FakeTrace:
    def __init__(self, lab, name, *, target='oe'):
        self.name = name
        self.target = target
        self.events = []
        self.pcap = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=25))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class RestartValidators(TestCase):
    def test_connectivity_requires_physical_ping_both_families_and_peers(self):
        lab = FakeLab()
        with mock.patch.object(restart, 'command') as cmd:
            restart.connectivity(lab)
        self.assertEqual(len(cmd.call_args_list), 2)
        self.assertIn('10.44.53.2', cmd.call_args_list[0].args[0])
        self.assertIn('fe80::2', cmd.call_args_list[1].args[0])
        self.assertEqual(lab.peer_checks, [('ipv4', True), ('ipv6', True)])

    def test_connectivity_waits_for_real_l3_success_without_masking_failure(self):
        lab = FakeLab()
        # Exercise the real bounded polling loop, not a fake success callback.
        lab.wait = restart.Lab.wait
        attempts = []

        def probe(argv, **kwargs):
            attempts.append(argv)
            if argv[-1] == '10.44.53.2' and len([a for a in attempts
                        if a[-1] == '10.44.53.2']) < 3:
                raise RuntimeError('CSR dataplane not ready')
            return '2 packets received'

        with mock.patch.object(restart, 'command', side_effect=probe), \
             mock.patch.object(restart.time, 'sleep'):
            restart.connectivity(lab, seconds=1)
        self.assertEqual(len([a for a in attempts if a[-1] == '10.44.53.2']), 3)
        self.assertEqual(lab.peer_checks, [('ipv4', True), ('ipv6', True)])
        self.assertNotIn('connectivity-diagnostics.log', lab.saved)

    def test_connectivity_retries_unavailable_ipv6_link_local(self):
        lab = FakeLab()
        lab.wait = restart.Lab.wait
        addr = mock.Mock(side_effect=[RuntimeError('link-local not ready'),
                                      'fe80::2'])
        with mock.patch.object(restart, 'command', return_value='2 packets received'), \
             mock.patch.object(lab, 'peer_address', side_effect=addr), \
             mock.patch.object(restart.time, 'sleep'):
            restart.connectivity(lab, seconds=1)
        self.assertEqual(addr.call_count, 2)
        self.assertEqual(lab.peer_checks, [('ipv4', True), ('ipv6', True)])

    def test_connectivity_times_out_and_captures_interface_evidence(self):
        lab = FakeLab()
        lab.wait = restart.Lab.wait

        def unreachable(argv, **kwargs):
            if argv[0:3] == ['docker', 'exec', lab.oe] and 'ping' in argv:
                raise RuntimeError('No IPv4 replies from Cisco')
            return 'interface diagnostic output'

        with mock.patch.object(restart, 'command', side_effect=unreachable), \
             mock.patch.object(restart.time, 'sleep'):
            with self.assertRaisesRegex(AssertionError, 'No IPv4 replies'):
                restart.connectivity(lab, seconds=.02, stage='preflight-oe-ipv4')
        diag = lab.saved['connectivity-diagnostics.log']
        self.assertIn('preflight-oe-ipv4', diag)
        self.assertIn('OE IPv4 route', diag)
        self.assertIn('OE neighbor cache', diag)
        self.assertIn('Cisco show interfaces GigabitEthernet2', diag)
        self.assertEqual(lab.peer_checks, [])

    def test_oe_restart_keeps_container_up_and_process_restarts_after_loss(self):
        lab = FakeLab()
        commands, traces = [], []
        pids = {'watchfrr': '41', 'eigrpd': '61'}

        def command(argv, **kwargs):
            commands.append(argv)
            if argv[0:3] == ['docker', 'exec', lab.oe]:
                if argv[3] == 'pidof':
                    return pids[argv[4]]
                if argv[3] == 'kill':
                    if argv[4] == '-KILL':
                        lab.alive = False
                    elif argv[4] == '-CONT':
                        pids['eigrpd'] = '82'
                    return ''
            return ''

        class Tracer(FakeTrace):
            def __enter__(self):
                traces.append((self.name, self.target))
                return self

        with mock.patch.object(restart, 'Trace', Tracer), \
             mock.patch.object(restart, 'command', side_effect=command), \
             mock.patch.object(restart, 'connectivity'), \
             mock.patch.object(restart, 'startup', return_value={'wire_events': 4}) as startup, \
             mock.patch.object(restart.time, 'sleep'):
            evidence = restart.cold_restart(lab, 'oe', 'ipv4')
        self.assertEqual(evidence, {'wire_events': 4})
        self.assertEqual([x[0] for x in traces], ['loss-oe-ipv4', 'recovery-oe-ipv4'])
        self.assertTrue(all(x[1] == 'ce' for x in traces))
        self.assertIn(('oe', 'write memory'), lab.cli_commands)
        self.assertFalse(any(x[:2] in (['docker', 'stop'], ['docker', 'start'])
                             for x in commands))
        signals = [x[4] for x in commands if x[3] == 'kill']
        self.assertEqual(signals, ['-STOP', '-KILL', '-CONT'])
        self.assertIn('old_pid=61 new_pid=82', lab.saved['restart-daemon.log'])
        self.assertIn(('ipv4', {'ce': ['10.255.0.1/32']}, 'internal', False), lab.route_checks)
        startup.assert_called_once()

    def test_oe_watcher_resumes_after_loss_capture_failure(self):
        lab = FakeLab()
        pids = {'watchfrr': '41', 'eigrpd': '61'}
        commands = []
        def command(argv, **kwargs):
            commands.append(argv)
            return pids[argv[4]] if argv[3] == 'pidof' else ''
        def wait(action, predicate, seconds):
            if seconds == 25:  # Loss capture has recorded its pre-stimulus packet.
                return action()
            lab.alive = False
            raise AssertionError('loss check failed')
        lab.wait = wait
        with mock.patch.object(restart, 'Trace', FakeTrace), \
             mock.patch.object(restart, 'command', side_effect=command), \
             mock.patch.object(restart.time, 'sleep'):
            with self.assertRaisesRegex(AssertionError, 'loss check failed'):
                restart.cold_restart(lab, 'oe', 'ipv4')
        self.assertEqual([x[4] for x in commands if x[3] == 'kill'],
                         ['-STOP','-KILL','-CONT'])

    def test_no_restart_without_live_packet_capture(self):
        lab = FakeLab()
        commands = []
        class EmptyTrace(FakeTrace):
            def __init__(self, lab, name, *, target='oe'):
                super().__init__(lab, name, target=target)
                self.pcap = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=24))
        def fail_wait(action, predicate, seconds):
            if not predicate(action()):
                raise AssertionError('no packets captured')
        lab.wait = fail_wait
        with mock.patch.object(restart, 'Trace', EmptyTrace), \
             mock.patch.object(restart, 'command', side_effect=lambda argv, **kwargs: commands.append(argv) or '41'):
            with self.assertRaisesRegex(AssertionError, 'no packets captured'):
                restart.cold_restart(lab, 'oe', 'ipv4')
        self.assertFalse(any(x[3] == 'kill' for x in commands))

    def test_cisco_ios_reload_preserves_container_namespace(self):
        lab = FakeLab()
        commands, traces = [], []
        class Tracer(FakeTrace):
            def __enter__(self):
                traces.append((self.name, self.target))
                return self
        with mock.patch.object(restart, 'Trace', Tracer), \
             mock.patch.object(restart, 'command', side_effect=lambda argv, **kw: commands.append(argv)), \
             mock.patch.object(restart, 'connectivity'), \
             mock.patch.object(restart, 'startup', return_value={'wire_events': 4}), \
             mock.patch.object(restart.time, 'sleep'):
            restart.cold_restart(lab, 'ce', 'ipv6')
        self.assertTrue(lab.reloaded)
        self.assertEqual(traces, [('loss-ce-ipv6', 'oe'), ('recovery-ce-ipv6', 'oe')])
        self.assertIn(('ce', 'write memory'), lab.cli_commands)
        self.assertFalse(any(x[:2] in (['docker','stop'], ['docker','start']) for x in commands))
        self.assertIn(('ipv6', {'oe': ['2001:db8:4473:ff::2/128']}, 'internal', False), lab.route_checks)

    def test_cisco_ipv6_loss_probe_does_not_ssh_to_restarting_router(self):
        """Execute the actual peer matcher while IOS SSH is unavailable."""
        import interop

        class OfflineCiscoLab(FakeLab):
            has_peer = interop.Lab.has_peer
            offline = False

            def ce_reload(self):
                super().ce_reload()
                self.offline = True

            def _wait_ce(self, seconds):
                self.offline = False

            def peer_address(self, role, family):
                if role == 'oe' and family == 'ipv6' and self.offline:
                    raise RuntimeError('Cisco SSH unavailable during IOS reload')
                return super().peer_address(role, family)

            def neighbor(self, role, family):
                self.peer_checks.append((role, family))
                return ('EIGRP neighbor fe80::2 on eth1' if self.alive else
                        'No EIGRP neighbors matched')

        lab = OfflineCiscoLab()
        with mock.patch.object(restart, 'Trace', FakeTrace), \
             mock.patch.object(restart, 'connectivity'), \
             mock.patch.object(restart, 'startup', return_value={'wire_events': 4}), \
             mock.patch.object(restart.time, 'sleep'):
            evidence = restart.cold_restart(lab, 'ce', 'ipv6')

        self.assertEqual(evidence['wire_events'], 4)
        self.assertTrue(lab.reloaded)
        self.assertEqual(lab.peer_checks, [('oe', 'ipv6')])
        self.assertIn(('ipv6', {'oe': ['2001:db8:4473:ff::2/128']},
                       'internal', False), lab.route_checks)

    def test_peer_matcher_still_uses_live_discovery_by_default(self):
        import interop
        lab = FakeLab()
        lab.neighbor = lambda role, family: 'EIGRP neighbor fe80::2 on eth1'
        with mock.patch.object(lab, 'peer_address', return_value='fe80::2') as lookup:
            self.assertTrue(interop.Lab.has_peer(lab, 'oe', 'ipv6'))
            lookup.assert_called_once_with('oe', 'ipv6')

    def test_redeploy_each_case_and_block_on_failed_connectivity(self):
        import yaml
        spec = yaml.safe_load((SPEC / 'scenario.yaml').read_text())
        lab, report = FakeLab(), {'objectives': {}}
        with mock.patch.object(restart, 'command') as command, \
             mock.patch.object(restart, 'connectivity') as connectivity, \
             mock.patch.object(restart, 'cold_restart', return_value={'wire_events': 5}) as cold:
            restart.run(lab, report, spec)
        self.assertEqual(lab.deploy_count, 4)
        self.assertEqual(cold.call_args.kwargs['connectivity_seconds'], 120)
        self.assertEqual(cold.call_args.kwargs['capture_ready_seconds'], 25)
        self.assertEqual(len([x for x in command.call_args_list
                              if x.args[0][:2] == ['netlab', 'down']]), 3)
        self.assertEqual(connectivity.call_count, 4)
        self.assertEqual(cold.call_count, 4)
        self.assertEqual(set(x['status'] for x in report['objectives'].values()), {'PASS'})

        lab, report = FakeLab(), {'objectives': {}}
        with mock.patch.object(restart, 'command') as command, \
             mock.patch.object(restart, 'connectivity', side_effect=RuntimeError('IPv6 ping failed')):
            restart.run(lab, report, spec)
        self.assertEqual(lab.deploy_count, 1)
        self.assertEqual(len(command.call_args_list), 0)
        self.assertEqual([x['status'] for x in report['objectives'].values()],
                         ['FAIL','BLOCKED','BLOCKED','BLOCKED'])

    def test_cisco_reload_requires_explicit_confirmation(self):
        import interop
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'cisco.log'
            cisco = interop.Cisco('192.0.2.1', log)
            cisco.fd = 42
            with mock.patch.object(interop.select, 'select', return_value=([42], [], [])), \
                 mock.patch.object(interop.os, 'read', return_value=b'Proceed with reload? [confirm]'), \
                 mock.patch.object(interop.os, 'write') as write:
                cisco.reload()
            self.assertEqual([call.args[1] for call in write.call_args_list],
                             [b'reload\n', b'\n'])
            self.assertIn('[confirmed]', log.read_text())

    def test_cisco_reload_rejects_unexpected_save_prompt(self):
        import interop
        with tempfile.TemporaryDirectory() as tmp:
            cisco = interop.Cisco('192.0.2.1', Path(tmp) / 'cisco.log')
            cisco.fd = 42
            with mock.patch.object(interop.select, 'select', return_value=([42], [], [])), \
                 mock.patch.object(interop.os, 'read', return_value=b'System configuration has been modified. Save? [yes/no]'), \
                 mock.patch.object(interop.os, 'write') as write:
                with self.assertRaisesRegex(RuntimeError, 'confirmation not observed'):
                    cisco.reload()
            write.assert_called_once_with(42, b'reload\n')

    def test_wire_startup_requires_real_both_side_init_ack_eot(self):
        sources = {'oe': {'ipv4': '10.44.53.1'},
                   'ce': {'ipv4': '10.44.53.2'}}
        events = []
        for role, receiver, seq in (('oe','ce',7),('ce','oe',11)):
            events.extend([
                {'family':'ipv4','source':sources[role]['ipv4'],
                 'destination':'224.0.0.10','opcode_name':'HELLO',
                 'sequence':0,'ack':0,'flag_names':[],'tlvs':[]},
                {'family':'ipv4','source':sources[role]['ipv4'],
                 'destination':sources[receiver]['ipv4'],'opcode_name':'UPDATE',
                 'sequence':seq,'ack':0,'flag_names':['INIT'],'tlvs':[]},
                {'family':'ipv4','source':sources[role]['ipv4'],
                 'destination':sources[receiver]['ipv4'],'opcode_name':'UPDATE',
                 'sequence':seq+1,'ack':0,'flag_names':['EOT'],'tlvs':[]},
                {'family':'ipv4','source':sources[receiver]['ipv4'],
                 'destination':sources[role]['ipv4'],'opcode_name':'HELLO',
                 'sequence':0,'ack':seq,'flag_names':[],'tlvs':[]}])
        self.assertEqual(restart.startup(events,'ipv4',sources)['wire_events'],8)
        with self.assertRaisesRegex(AssertionError,'INIT ACK'):
            restart.startup([e for e in events if not e['ack']],'ipv4',sources)
        with self.assertRaisesRegex(AssertionError,'EOT'):
            restart.startup([e for e in events if 'EOT' not in e['flag_names']],
                            'ipv4',sources)


if __name__ == '__main__':
    main()
