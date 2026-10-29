#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable assertions for live 2R wire/transport scenario validators."""
from collections import Counter
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
# The ordinary checkout has the tested capture/fault/wire helpers. Standalone
# overlay validation only stubs *imports*; no wire assertion is mocked.
for name in ('capture', 'network_fault', 'wire'):
    if not (ROOT / 'common' / (name + '.py')).exists():
        sys.modules[name] = types.ModuleType(name)
        if name == 'wire':
            sys.modules[name].TLVS = {0x0102:'IPV4_INTERNAL',0x0103:'IPV4_EXTERNAL',
                                      0x0602:'MP_INTERNAL',0x0603:'MP_EXTERNAL'}
SCRIPT = ROOT / 'test/rtp/2R-transport/driver.py'
spec = importlib.util.spec_from_file_location('live_rtp_2r', SCRIPT)
rtp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rtp)
SRC = {'oe':{'ipv4':'10.44.53.1'},'ce':{'ipv4':'10.44.53.2'}}


def event(role, opcode, *, seq=0, ack=0, dst=None, flags=(), tlvs=()):
    receiver = 'ce' if role == 'oe' else 'oe'
    return {'family':'ipv4', 'source':SRC[role]['ipv4'],
            'destination':dst or SRC[receiver]['ipv4'], 'opcode_name':opcode,
            'sequence':seq, 'ack':ack, 'flag_names':list(flags),
            'tlvs':list(tlvs), 'asn':4473, 'packet_length':100}


def route_tlv(prefix, kind='internal'):
    return {'type':0x0102 if kind == 'internal' else 0x0103,
            'route_kind':kind,'prefixes':[prefix],
            'metric':{'delay':10,'bandwidth':100000,'mtu':1500,'reliability':255}}


class RtpValidators(unittest.TestCase):
    def test_startup_requires_both_init_ack_eot_and_multicast_hello(self):
        data = []
        for role in ('oe','ce'):
            other = 'ce' if role == 'oe' else 'oe'
            data.extend([event(role,'HELLO',dst='224.0.0.10'),
                         event(role,'UPDATE',seq=12 if role=='oe' else 15,flags=['INIT']),
                         event(role,'UPDATE',seq=13 if role=='oe' else 16,flags=['EOT']),
                         event(role,'HELLO',ack=15 if role=='oe' else 12)])
        self.assertEqual(rtp.startup(data,'ipv4',SRC)['events'],8)
        with self.assertRaisesRegex(AssertionError, 'ACK'):
            rtp.startup([e for e in data if not e['ack']],'ipv4',SRC)
        with self.assertRaisesRegex(AssertionError, 'EOT'):
            rtp.startup([e for e in data if 'EOT' not in e['flag_names']],'ipv4',SRC)
        with self.assertRaisesRegex(AssertionError, 'multicast'):
            rtp.startup([e for e in data if e['opcode_name'] != 'HELLO' or e['ack']],'ipv4',SRC)

    def test_kvalue_mismatch_requires_actual_two_k1_values(self):
        mismatch = [event('oe','HELLO',dst='224.0.0.10',
                          tlvs=[{'type':1,'k_values':[1,0,1,0,0,0]}]),
                    event('ce','HELLO',dst='224.0.0.10',
                          tlvs=[{'type':1,'k_values':[2,0,1,0,0,0]}])]
        self.assertEqual(rtp.mismatch(mismatch,'ipv4',SRC)['ce_k1'],2)
        with self.assertRaisesRegex(AssertionError, 'HELLO K1'):
            rtp.mismatch(mismatch[:1],'ipv4',SRC)
        goodbye = [event('oe','HELLO',dst='224.0.0.10',
                         tlvs=[{'type':1,'k_values':[1,0,1,0,0,0]}]),
                   event('ce','HELLO',dst='224.0.0.10',
                         tlvs=[{'type':1,'k_values':[255]*6}])]
        with self.assertRaisesRegex(AssertionError, 'Cisco HELLO K1'):
            rtp.mismatch(goodbye,'ipv4',SRC)
        unicast = [dict(e, destination=SRC['oe']['ipv4']) for e in mismatch]
        with self.assertRaisesRegex(AssertionError, 'Cisco HELLO K1'):
            rtp.mismatch(unicast,'ipv4',SRC)

    def test_packetization_checks_tlv_boundaries_size_and_all_prefixes(self):
        prefixes = {'oe':['198.18.0.1/32','198.18.0.2/32'],
                    'ce':['198.19.0.1/32','198.19.0.2/32']}
        burst = []
        startup = []
        for role in ('oe','ce'):
            a,b = prefixes[role]
            startup.append(event(role,'UPDATE',seq=1,tlvs=[route_tlv(a)]))
            # A real 128-prefix burst is allowed to batch every packet.
            burst.extend([event(role,'UPDATE',seq=2,tlvs=[route_tlv(a),route_tlv(b)]),
                          event(role,'UPDATE',seq=3,tlvs=[route_tlv(a),route_tlv(b)])])
        out = rtp.packetization(burst,'ipv4',prefixes,SRC,
                                startup_events=startup)
        self.assertEqual(out['oe']['single'],1)
        self.assertEqual(out['oe']['multi'],2)
        self.assertEqual(out['ce']['route_packets'],2)
        with self.assertRaisesRegex(AssertionError, 'single-TLV'):
            rtp.packetization(burst,'ipv4',prefixes,SRC)

        with self.assertRaisesRegex(AssertionError, 'did not advertise'):
            rtp.routes_in_wire(burst,'ipv4',{'oe':['192.0.2.1/32']},SRC)
        large = [dict(e,packet_length=1490) for e in burst]
        with self.assertRaisesRegex(AssertionError, 'exceeds'):
            rtp.packetization(large,'ipv4',prefixes,SRC,startup_events=startup)

    def test_reliable_ack_and_order_are_bidirectional(self):
        data = [event('oe','UPDATE',seq=1),event('ce','HELLO',ack=1),
                event('ce','UPDATE',seq=3),event('oe','HELLO',ack=3)]
        self.assertEqual(rtp.reliability(data,'ipv4',SRC)['oe']['unique_sequences'],1)
        with self.assertRaisesRegex(AssertionError, 'missing ACK'):
            rtp.reliability([d for d in data if not d['ack']],'ipv4',SRC)
        with self.assertRaisesRegex(AssertionError, 'out of order'):
            rtp.reliability([event('oe','UPDATE',seq=4), event('oe','UPDATE',seq=2),
                             event('ce','HELLO',ack=4), event('ce','HELLO',ack=2),
                             event('ce','UPDATE',seq=3),event('oe','HELLO',ack=3)],
                            'ipv4',SRC)

    def test_addressing_requires_four_independent_modes(self):
        data = []
        for role in ('oe','ce'):
            other = 'ce' if role == 'oe' else 'oe'
            data += [event(role,'HELLO',dst='224.0.0.10'),
                     event(role,'UPDATE',seq=2,dst='224.0.0.10'),
                     event(role,'UPDATE',seq=3),event(role,'HELLO',ack=2)]
        self.assertEqual(len(rtp.addressing(data,'ipv4',SRC)),2)
        unicast_burst = [e for e in data if not
                         (e['opcode_name']=='UPDATE' and e['destination']=='224.0.0.10')]
        with self.assertRaisesRegex(AssertionError, 'reliable multicast'):
            rtp.addressing(unicast_burst,'ipv4',SRC)
        self.assertEqual(len(rtp.addressing(unicast_burst + data,'ipv4',SRC)),2)

    def test_packet_loss_and_ack_loss_require_different_receiver_evidence(self):
        tlvs=[route_tlv('198.18.0.1/32','external')]
        tx = [event('oe','UPDATE',seq=9,tlvs=tlvs),event('oe','UPDATE',seq=9,tlvs=tlvs),
              event('ce','HELLO',ack=9)]
        rx_packet = [event('oe','UPDATE',seq=9,tlvs=tlvs),event('ce','HELLO',ack=9)]
        rx_ack = [event('oe','UPDATE',seq=9,tlvs=tlvs),event('oe','UPDATE',seq=9,tlvs=tlvs),
                  event('ce','HELLO',ack=9)]
        self.assertEqual(rtp.retry(tx,rx_packet,'ipv4','oe',SRC,'198.18.0.1/32',lost_ack=False)['loss_case'],'packet')
        self.assertEqual(rtp.retry(tx,rx_ack,'ipv4','oe',SRC,'198.18.0.1/32',lost_ack=True)['loss_case'],'ACK')
        with self.assertRaisesRegex(AssertionError,'final ACK'):
            rtp.retry(tx[:-1],rx_ack,'ipv4','oe',SRC,'198.18.0.1/32',lost_ack=True)
        with self.assertRaisesRegex(AssertionError,'duplicate'):
            rtp.retry(tx,rx_packet,'ipv4','oe',SRC,'198.18.0.1/32',lost_ack=True)
        # After the original was proven lost by the active tc/capture gate,
        # later duplicate deliveries are legitimate retries; RIB uniqueness
        # remains a separate requirement.
        self.assertEqual(rtp.retry(tx,rx_ack,'ipv4','oe',SRC,
                          '198.18.0.1/32',lost_ack=False)['loss_case'],'packet')
        with self.assertRaisesRegex(AssertionError,'receiver did not get'):
            rtp.retry(tx,[event('ce','HELLO',ack=9)],'ipv4','oe',SRC,
                      '198.18.0.1/32',lost_ack=False)

    def test_loss_requires_actual_tc_action_drops(self):
        from unittest import mock
        with mock.patch.object(rtp.network_fault, 'ns_command',
                               return_value='action order 1: gact action drop\n '
                                            'Sent 100 bytes 2 pkt (dropped 2, overlimits 0)',
                               create=True):
            self.assertEqual(rtp.fault_drops('router'), 2)
        with mock.patch.object(rtp.network_fault, 'ns_command',
                               return_value='Sent 0 bytes 0 pkt (dropped 0, overlimits 0)',
                               create=True):
            with self.assertRaisesRegex(AssertionError, 'dropped no packets'):
                rtp.fault_drops('router')
            self.assertEqual(rtp.fault_drops('router', require_positive=False), 0)

    def test_fault_wait_requires_target_update_and_actual_drop(self):
        from unittest import mock
        target = '198.18.3.231/32'
        sent = event('oe', 'UPDATE', seq=503,
                     tlvs=[route_tlv(target, 'external')])
        rtp_time = iter((0, 0, 0))
        with mock.patch.object(rtp.wire, 'read_pcap', side_effect=[
                ([], []), ([sent], []), ([], [])], create=True), \
             mock.patch.object(rtp, 'fault_drops', return_value=1), \
             mock.patch.object(rtp.time, 'sleep', return_value=None), \
             mock.patch.object(rtp.time, 'monotonic', side_effect=rtp_time):
            self.assertEqual(rtp.fault_update_wait(
                'tx.pcap', 'rx.pcap', 'ipv4', 'oe', SRC,
                target, 'router', lost_ack=False, timeout=10), (503, 1))

        # Receiver AF_PACKET may see an ingress packet before tc drops it.
        # A frame in rx.pcap is not evidence that EIGRP accepted the route.
        with mock.patch.object(rtp.wire, 'read_pcap', side_effect=[
                ([sent], []), ([sent], [])], create=True), \
             mock.patch.object(rtp, 'fault_drops', return_value=2):
            self.assertEqual(rtp.fault_update_wait('tx.pcap', 'rx.pcap',
                             'ipv4', 'oe', SRC, target, 'router',
                             lost_ack=False), (503, 2))
        # An ACK from the receiver while the filter is active proves the
        # stimulus did not suppress the target UPDATE.
        with mock.patch.object(rtp.wire, 'read_pcap', side_effect=[
                ([sent], []), ([sent, event('ce', 'HELLO', ack=503)], [])],
                create=True), \
             mock.patch.object(rtp, 'fault_drops', return_value=2):
            with self.assertRaisesRegex(AssertionError, 'receiver ACKed'):
                rtp.fault_update_wait('tx.pcap','rx.pcap','ipv4','oe',
                                      SRC,target,'router',lost_ack=False)

    def test_fault_wait_keeps_filter_active_until_tc_reports_drop(self):
        from unittest import mock
        target = '198.18.3.231/32'
        sent = event('oe', 'UPDATE', seq=503,
                     tlvs=[route_tlv(target, 'external')])
        # The route may be visible in the sender capture before the kernel
        # completes its tc action accounting. Zero is not an immediate failure.
        with mock.patch.object(rtp.wire, 'read_pcap', side_effect=[
                ([sent], []), ([sent], []), ([], [])], create=True), \
             mock.patch.object(rtp, 'fault_drops', side_effect=[0, 1]) as drops, \
             mock.patch.object(rtp.time, 'sleep', return_value=None), \
             mock.patch.object(rtp.time, 'monotonic', side_effect=[0,0,1]):
            self.assertEqual(rtp.fault_update_wait('tx.pcap', 'rx.pcap',
                'ipv4', 'oe', SRC, target, 'router', lost_ack=False,
                timeout=10), (503,1))
            self.assertEqual(drops.call_count, 2)

    def test_fault_wait_no_update_cannot_be_a_valid_retransmission(self):
        from unittest import mock
        target = '198.18.3.231/32'
        # A fixed 11-second timer would have cleared the filter even though
        # Zebra had not yet delivered the new prefix to EIGRP.
        with mock.patch.object(rtp.wire, 'read_pcap', return_value=([], []), create=True), \
             mock.patch.object(rtp.time, 'sleep', return_value=None), \
             mock.patch.object(rtp.time, 'monotonic', side_effect=[0, 1, 2]):
            with self.assertRaisesRegex(AssertionError, 'target UPDATE not yet observed'):
                rtp.fault_update_wait('tx.pcap', 'rx.pcap', 'ipv4', 'oe', SRC,
                                      target, 'router', lost_ack=False, timeout=1)

    def test_fault_wait_ack_must_have_arrived_at_receiver(self):
        from unittest import mock
        target = '198.19.3.232/32'
        sent = event('ce', 'UPDATE', seq=244,
                     tlvs=[route_tlv(target, 'external')])
        with mock.patch.object(rtp.wire, 'read_pcap', side_effect=[
                ([sent], []), ([sent], [] )], create=True), \
             mock.patch.object(rtp, 'fault_drops', return_value=10):
            self.assertEqual(rtp.fault_update_wait(
                'tx.pcap','rx.pcap','ipv4','ce', SRC,
                target,'router',lost_ack=True), (244, 10))

    def test_queue_drain_requires_real_header_and_neighbor(self):
        self.assertEqual(rtp.inspect_queue('Address Interface Q Cnt Seq Num\n'
                       '0 10.44.53.2 eth1 40 500 0 77'),1)
        with self.assertRaisesRegex(AssertionError,'Q Cnt'):
            rtp.inspect_queue('No neighbors')
        with self.assertRaisesRegex(AssertionError,'drain'):
            rtp.inspect_queue('Address Interface Q Cnt Seq Num\n'
                              '0 10.44.53.2 eth1 40 500 1 77')
        self.assertEqual(rtp.inspect_queue(
            'H   Address Interface Hold Uptime SRTT RTO Q  Seq\n'
            '                (sec)        (ms)    Cnt Num\n'
            '0   10.44.53.1  Gi2  14 00:00:25 1 100 0 507'),1)
        self.assertEqual(rtp.inspect_queue(
            'H   Address Interface Hold Uptime SRTT RTO Q  Seq\n'
            '                (sec)        (ms)    Cnt Num\n'
            '0   Link-local address: Gi2 12 00:01:32 1 100 0 24\n'
            '    FE80::D649:9D5F:42D2:E6C7'),1)
        with self.assertRaisesRegex(AssertionError,'drain'):
            rtp.inspect_queue('H Address Interface Q Seq\n'
                '                   Cnt Num\n'
                '0 Link-local address: Gi2 12 00:00:10 1 100 1 24')

    def test_duplicate_does_not_create_multiple_observable_rib_entries(self):
        single='D EX 198.18.0.1/32 [170/256] via 10.44.53.1, GigabitEthernet2'
        self.assertEqual(rtp.unique_external_rib(single,'198.18.0.1/32','ce','ipv4'),1)
        with self.assertRaisesRegex(AssertionError,'exactly one'):
            rtp.unique_external_rib(single+'\n'+single,'198.18.0.1/32','ce','ipv4')
        with self.assertRaisesRegex(AssertionError,'exactly one'):
            rtp.unique_external_rib(single,'198.18.0.2/32','ce','ipv4')
        # The actual Cisco /32 heading and FRR E[121] spelling from the report.
        cisco = ('198.18.3.0/32 is subnetted, 1 subnets\n'
                 'D EX     198.18.3.231 [170/205120] via 10.44.53.1, GigabitEthernet2')
        frr = 'E[121]>* 198.19.3.232/32 [170/32432960] via 10.44.53.2, eth1'
        self.assertEqual(rtp.unique_external_rib(cisco,'198.18.3.231/32','ce','ipv4'),1)
        self.assertEqual(rtp.unique_external_rib(frr,'198.19.3.232/32','oe','ipv4'),1)
        with self.assertRaisesRegex(AssertionError,'exactly one'):
            rtp.unique_external_rib(cisco.replace('198.18.3.0/32','198.18.3.0/24'),
                                    '198.18.3.231/32','ce','ipv4')
        ipv6 = 'EX 2001:DB8:4473:100::3E5/128 [170/205120] via FE80::1'
        self.assertEqual(rtp.unique_external_rib(ipv6,
            '2001:db8:4473:100::3e5/128','ce','ipv6'),1)
        with self.assertRaisesRegex(AssertionError,'exactly one'):
            rtp.unique_external_rib('S 198.18.3.231/32',
                                    '198.18.3.231/32','ce','ipv4')

    def test_link_down_cases_capture_on_surviving_router(self):
        from unittest import mock
        class StopAfterMismatch(Exception):
            pass
        startup = []
        for role in ('oe', 'ce'):
            startup.extend([
                event(role, 'HELLO', dst='224.0.0.10'),
                event(role, 'UPDATE', seq=12 if role == 'oe' else 15, flags=['INIT']),
                event(role, 'UPDATE', seq=13 if role == 'oe' else 16, flags=['EOT']),
                event(role, 'HELLO', ack=15 if role == 'oe' else 12)])
        mismatch = [event('oe','HELLO',dst='224.0.0.10',
                          tlvs=[{'type':1,'k_values':[1,0,1,0,0,0]}]),
                    event('ce','HELLO',dst='224.0.0.10',
                          tlvs=[{'type':1,'k_values':[2,0,1,0,0,0]}])]
        captures = []
        command_history = []
        class RecordedTrace:
            def __init__(self, lab, name, *, target='oe'):
                captures.append((name,target))
                self.name = name
                self.events = mismatch if name.startswith('rtp02-') else startup
            def __enter__(self):
                if self.name.startswith('rtp02-'):
                    # The second capture must start before the link is down.
                    self_at_entry.append(list(command_history))
                return self
            def __exit__(self, *_):
                pass
        def selected_case(report, case, family, action):
            if case not in ('RTP-01', 'RTP-02'):
                raise StopAfterMismatch()
            action()   # Run the real case stimulus and wire validator.
            return True
        class LabStub:
            oe = 'oe'
            def snapshot(self, *_):
                pass
            def peers(self, *args, **kwargs):
                checks.append((args,kwargs))
            def cisco_af(self, *_):
                pass
            def ce_cli(self, *_):
                return 'metric weights 0 2 0 1 0 0 0\n'
        checks=[]
        self_at_entry=[]
        def record_command(args, **kwargs):
            command_history.append(args)
            return ''
        with mock.patch.object(rtp, 'sources_for', return_value=SRC), \
             mock.patch.object(rtp, 'Trace', RecordedTrace), \
             mock.patch.object(rtp, 'new_case', side_effect=selected_case), \
             mock.patch.object(rtp, 'command', side_effect=record_command), \
             mock.patch.object(rtp.time, 'sleep'):
            with self.assertRaises(StopAfterMismatch):
                rtp.run(LabStub(), {}, {'families':['ipv4']})
        self.assertEqual(captures, [('rtp01-ipv4','ce'), ('rtp02-ipv4','ce')])
        self.assertEqual(self_at_entry, [command_history[:2]])  # only RTP-01 down/up
        down = [(a,k) for a,k in checks if a[1] is False]
        self.assertEqual(len(down),4)
        self.assertTrue(all(k.get('addresses') is SRC for _,k in down))

    def test_rtp08_fault_direction_preserves_sender_capture(self):
        from contextlib import contextmanager
        from unittest import mock
        class StopAfterLoss(Exception):
            pass
        class FakeTrace:
            def __init__(self, lab, name, *, target='oe'):
                self.pcap = name + '.pcap'
                self.events = [event('oe', 'UPDATE', seq=500,
                                     tlvs=[route_tlv('198.18.3.229/32', 'external')])]
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        class FakeLab:
            oe, ce = 'oe-container', 'ce-container'
            def snapshot(self, *args):
                pass
            def peers(self, family, expected, *args, **kwargs):
                checks.append((family, expected))
            def route(self, *args, **kwargs):
                mutations.append((args, kwargs))
            def check_routes(self, *args, **kwargs):
                pass
            def route_present(self, *args):
                # An UPDATE dropped at ingress cannot install a route until
                # the tc fault is cleared and the retry is delivered.
                return not (faults and faults[-1][1] == 'drop'
                            and active_fault[0])
            def routes(self, *args):
                return 'route present'
        @contextmanager
        def injected_fault(lab, name, role, family, mode, *, ack=None,
                           direction='egress'):
            faults.append((role, mode, direction, ack))
            active_fault[0] = True
            try:
                yield
            finally:
                active_fault[0] = False
        for loss_type in ('packet', 'ack'):
            checks, mutations, faults, gates = [], [], [], []
            active_fault = [False]
            target = f'RTP-08-{loss_type}-oe'
            def select_case(report, case, family, action):
                if case == target:
                    action()
                    raise StopAfterLoss()
                return False
            def gate(*args, **kwargs):
                gates.append((args, kwargs))
                return (501, 1)
            with self.subTest(loss_type=loss_type), \
                 mock.patch.object(rtp, 'sources_for', return_value=SRC), \
                 mock.patch.object(rtp, 'Trace', FakeTrace), \
                 mock.patch.object(rtp, 'new_case', side_effect=select_case), \
                 mock.patch.object(rtp, 'fault', injected_fault), \
                 mock.patch.object(rtp, 'fault_update_wait', side_effect=gate), \
                 mock.patch.object(rtp, 'unique_external_rib', return_value=1), \
                 mock.patch.object(rtp, 'retry', return_value={
                     'retried_sequences':[501], 'loss_case': loss_type}), \
                 mock.patch.object(rtp.time, 'sleep'):
                with self.assertRaises(StopAfterLoss):
                    rtp.run(FakeLab(), {'objectives':{}},
                            {'families':['ipv4'], 'burst_prefixes':128})
            self.assertEqual(faults, [('ce', 'ack' if loss_type=='ack' else 'drop',
                                       'egress' if loss_type=='ack' else 'ingress',
                                       501 if loss_type=='ack' else None)])
            self.assertTrue(checks and checks[-1] == ('ipv4', True))
            self.assertEqual(gates[0][1]['lost_ack'], loss_type=='ack')
            self.assertEqual(gates[0][0][6], 'ce-container')
            self.assertTrue(any(k['add'] is True for _,k in mutations))
            self.assertTrue(any(k['add'] is False for _,k in mutations))

    def test_capture_does_not_mask_fault_installation_error(self):
        from tempfile import TemporaryDirectory
        from unittest import mock
        import interop
        with TemporaryDirectory() as root:
            class Lab:
                artifacts = Path(root)
                oe = 'oe'
                def save(self, name, text):
                    (self.artifacts / name).write_text(text)
            trace = rtp.Trace(Lab(), 'fault-setup')
            with mock.patch.object(interop.capture, 'start'), \
                 mock.patch.object(interop.capture, 'stop',
                                   side_effect=RuntimeError('capture has no packets')):
                with self.assertRaisesRegex(RuntimeError, 'tc ingress filter rejected'):
                    with trace:
                        raise RuntimeError('tc ingress filter rejected')
            diag = (Path(root) / 'fault-setup.capture-error.log').read_text()
            self.assertIn('capture has no packets', diag)
            with mock.patch.object(interop.capture, 'start'), \
                 mock.patch.object(interop.capture, 'stop',
                                   side_effect=RuntimeError('capture has no packets')):
                with self.assertRaisesRegex(RuntimeError, 'capture has no packets'):
                    with rtp.Trace(Lab(), 'genuinely-empty'):
                        pass

    def test_rtp04_single_route_stimulus_also_qualifies_rtp06_multicast(self):
        from unittest import mock
        class StopAfterRtp06(Exception):
            pass
        def packet(role, seq, count):
            routes = [route_tlv(rtp.prefix(role, 'ipv4', i), 'external')
                      for i in range(1, count + 1)]
            return event(role, 'UPDATE', seq=seq, tlvs=routes)
        burst_events = [packet(role, seq, 2)
                        for role in ('oe', 'ce') for seq in (8, 9)]
        burst_events += [e for role in ('oe', 'ce') for e in (
            event(role, 'HELLO', dst='224.0.0.10'),
            event(role, 'HELLO', ack=8))]
        single = {role: [event(role, 'UPDATE', seq=11,
                              dst='224.0.0.10',
                              tlvs=[route_tlv(rtp.prefix(role, 'ipv4', 998),
                                              'external')])]
                  for role in ('oe', 'ce')}
        captures, mutations, checks = [], [], []
        class FakeTrace:
            def __init__(self, lab, name, *, target='oe'):
                captures.append((name, target))
                if name == 'burst-ipv4':
                    self.events = burst_events
                elif name.startswith('rtp04-single-'):
                    role = name.split('-')[2]
                    self.events = single[role]
                else:
                    self.events = []
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
        class FakeLab:
            oe = 'oe'
            def snapshot(self, *_):
                pass
            def peers(self, *_args, **_kwargs):
                pass
            def routes_change(self, *args, **kwargs):
                pass
            def route(self, *args, **kwargs):
                mutations.append((args, kwargs))
            def check_routes(self, *args, **kwargs):
                checks.append((args, kwargs))
        def run_selected(report, case, family, action):
            if case in ('RTP-BURST-SETUP', 'RTP-04'):
                out = action()
                if case == 'RTP-04':
                    self.assertEqual(out['packetization']['oe']['single'], 1)
                    self.assertEqual(out['packetization']['ce']['single'], 1)
            if case == 'RTP-06':
                out = action()
                self.assertEqual(len(out['addressing']), 2)
                raise StopAfterRtp06()
            return True
        with mock.patch.object(rtp, 'sources_for', return_value=SRC), \
             mock.patch.object(rtp, 'Trace', FakeTrace), \
             mock.patch.object(rtp, 'new_case', side_effect=run_selected), \
             mock.patch.object(rtp, 'command', return_value=''), \
             mock.patch.object(rtp.time, 'sleep'):
            with self.assertRaises(StopAfterRtp06):
                rtp.run(FakeLab(), {'objectives': {}},
                        {'families': ['ipv4'], 'burst_prefixes': 2})
        self.assertEqual([name for name, _ in captures],
            ['burst-ipv4', 'rtp04-single-oe-ipv4', 'rtp04-single-ce-ipv4'])
        self.assertEqual([kwargs['add'] for _, kwargs in mutations],
                         [True, False, True, False])
        self.assertTrue(any(kwargs.get('present') is False for _, kwargs in checks))

    def test_spec_and_grouped_fixture(self):
        import yaml
        root = ROOT / 'test/rtp/2R-transport'
        spec = yaml.safe_load((root/'scenario.yaml').read_text())
        topo = yaml.safe_load((root/'topology.yml').read_text())
        self.assertEqual(spec['objectives'],list(rtp.CASES))
        self.assertEqual((topo['provider'],len(topo['nodes'])),('clab',2))
        self.assertEqual({x['device'] for x in topo['nodes'].values()}, {'csr','frr'})


if __name__ == '__main__':
    unittest.main()


class RtpBurstDiagnostics(unittest.TestCase):
    def test_external_diagnostics_uses_real_topology_command_and_records_ingress(self):
        class Lab:
            oe = 'clab-test-oe'
            def __init__(self):
                self.saved = {}
            def oe_cli(self, cmd):
                return 'oe: ' + cmd
            def ce_cli(self, cmd):
                return 'ce: ' + cmd
            def save(self, name, value):
                self.saved[name] = value
        lab = Lab()
        original = rtp.command
        try:
            rtp.command = lambda *args, **kwargs: 'framed daemon log'
            rtp.external_diagnostics(lab, 'ipv6', 'burst')
        finally:
            rtp.command = original
        self.assertIn('show eigrp address-family ipv6 topology 4473 all-links',
                      lab.saved['burst-ipv6-oe-topology.log'])
        self.assertIn('show eigrp address-family ipv6 4473 events',
                      lab.saved['burst-ipv6-oe-events.log'])
        self.assertIn('framed daemon log', lab.saved['burst-ipv6-oe-frr.log'])
        self.assertEqual(len(lab.saved), 8)

class RtpRequiredObjectiveGate(unittest.TestCase):
    """The multiprotocol transport gate must not claim TLV1 qualification."""

    def test_tlv2_route_kind_and_prefix_are_required_for_each_family(self):
        for family in ('ipv4', 'ipv6'):
            sources = {'oe': {family: '10.44.53.1' if family == 'ipv4'
                                    else 'fe80::1'},
                       'ce': {family: '10.44.53.2' if family == 'ipv4'
                                    else 'fe80::2'}}
            for kind, required_type, legacy_type in (
                    ('internal', 0x0602, 0x0102),
                    ('external', 0x0603, 0x0103)):
                prefixes = {
                    'oe': ['198.18.1.1/32' if family == 'ipv4'
                           else '2001:db8:4473:1::1/128'],
                    'ce': ['198.19.1.1/32' if family == 'ipv4'
                           else '2001:db8:4473:2::1/128']}
                events = [dict(event(role, 'UPDATE', seq=10,
                                     tlvs=[{'type': required_type,
                                            'route_kind': kind,
                                            'prefixes': prefixes[role]}]),
                               family=family, source=sources[role][family])
                          for role in ('oe', 'ce')]
                qualified = rtp.codecs(events, family, prefixes, sources,
                                       required_type)
                self.assertEqual(qualified['oe']['codecs'], ['TLV2'])
                legacy = list(events)
                legacy[0] = dict(legacy[0], tlvs=[{'type': legacy_type,
                                                   'route_kind': kind,
                                                   'prefixes': prefixes['oe']}])
                with self.assertRaisesRegex(AssertionError, 'missing TLV2 routes'):
                    rtp.codecs(legacy, family, prefixes, sources, required_type)
                wrong_prefix = list(events)
                wrong_prefix[0] = dict(events[0], tlvs=[{'type': required_type,
                                       'route_kind': kind,
                                       'prefixes': prefixes['ce']}])
                with self.assertRaises(AssertionError):
                    rtp.codecs(wrong_prefix, family, prefixes, sources,
                               required_type)

    def test_tlv1_matrix_is_reported_but_not_required(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from tempfile import TemporaryDirectory
        from unittest import mock

        class Lab:
            def __init__(self, *_):
                self.info = {'defaults': {'devices.csr.clab.image': 'csr-test'}}
            def deploy(self):
                return 'oe-test'
            def peers(self, *_):
                pass
            def collect_logs(self):
                pass

        captured = []
        def qualify(lab, report, spec):
            for objective in report['required']:
                report['objectives'][objective] = rtp.result('PASS')
            report['objectives']['RTP-03/codec-matrix'] = rtp.result(
                'BLOCKED', required=False,
                missing=['oe/internal/TLV1', 'ce/internal/TLV1'])

        def store(_path, report):
            captured.append(report)

        with TemporaryDirectory() as temp, \
             mock.patch.object(rtp, 'Lab', Lab), \
             mock.patch.object(rtp, 'run', side_effect=qualify), \
             mock.patch.object(rtp, 'require_packet_access'), \
             mock.patch.object(rtp, 'report_base', return_value={'objectives': {}}), \
             mock.patch.object(rtp, 'write_report', side_effect=store), \
             redirect_stdout(StringIO()) as output:
            status = rtp.main(['--scenario', str(ROOT / 'test/rtp/2R-transport/scenario.yaml'),
                               '--topology', str(ROOT / 'test/rtp/2R-transport/topology.yml'),
                               '--artifacts', temp])
        self.assertEqual(status, 0)
        self.assertIn('PASS 2R RTP', output.getvalue())
        report = captured[0]
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['objectives']['RTP-03/codec-matrix']['status'],
                         'BLOCKED')
        self.assertNotIn('RTP-03/codec-matrix', report['required'])
        self.assertIs(report['objectives']['RTP-03/codec-matrix']['required'], False)
        self.assertIn('RTP-07/duplicate', report['required'])
        for family in ('ipv4', 'ipv6'):
            self.assertIn(f'RTP-03/{family}', report['required'])
            for role in ('oe', 'ce'):
                for mode in ('packet', 'ack'):
                    self.assertIn(f'RTP-08-{mode}-{role}/{family}',
                                  report['required'])

    def test_required_objective_failure_still_fails_scenario(self):
        from tempfile import TemporaryDirectory
        from unittest import mock

        class Lab:
            info = {'defaults': {'devices.csr.clab.image': 'csr-test'}}
            def __init__(self, *_):
                pass
            def deploy(self):
                return 'oe-test'
            def peers(self, *_):
                pass
            def collect_logs(self):
                pass

        captured = []
        def qualify(lab, report, spec):
            for objective in report['required']:
                report['objectives'][objective] = rtp.result('PASS')
            report['objectives']['RTP-08-packet-oe/ipv6'] = rtp.result(
                'FAIL', error='retransmission missing')
            report['objectives']['RTP-03/codec-matrix'] = rtp.result(
                'BLOCKED', required=False)

        from contextlib import redirect_stdout
        from io import StringIO
        with TemporaryDirectory() as temp, \
             mock.patch.object(rtp, 'Lab', Lab), \
             mock.patch.object(rtp, 'run', side_effect=qualify), \
             mock.patch.object(rtp, 'require_packet_access'), \
             mock.patch.object(rtp, 'report_base', return_value={'objectives': {}}), \
             mock.patch.object(rtp, 'write_report',
                               side_effect=lambda _path, report: captured.append(report)), \
             redirect_stdout(StringIO()):
            status = rtp.main(['--scenario', str(ROOT / 'test/rtp/2R-transport/scenario.yaml'),
                               '--topology', str(ROOT / 'test/rtp/2R-transport/topology.yml'),
                               '--artifacts', temp])
        self.assertEqual(status, 1)
        self.assertEqual(captured[0]['status'], 'FAIL')

class RemoteResultQualification(unittest.TestCase):
    """Execute the remote runner's actual result-aggregation code."""

    def test_informational_codec_matrix_does_not_block_remote_result(self):
        import json
        import subprocess
        from tempfile import TemporaryDirectory

        runner = ROOT / 'tools/remote/runner.sh'
        body = runner.read_text()
        marker = 'python3 - "$report" "$name" "$rc" "$clean_rc" "$image_rc" "$group" <<\'PY\''
        self.assertIn(marker, body)
        code = body.split(marker, 1)[1].split('\n', 1)[1].split('\nPY\n', 1)[0]
        for failed in (False, True):
            with TemporaryDirectory() as temp:
                folder = Path(temp)
                (folder / 'started-at.txt').write_text('2026-10-29T14:30:36Z\n')
                objectives = {
                    'RTP-03/ipv4': {'status': 'PASS'},
                    'RTP-03/ipv6': {'status': 'PASS'},
                    'RTP-07/duplicate': {'status': 'PASS'},
                    'RTP-08-packet-oe/ipv6': {
                        'status': 'FAIL' if failed else 'PASS'},
                    'RTP-03/codec-matrix': {'status': 'BLOCKED',
                                            'required': False,
                                            'missing': ['oe/internal/TLV1']},
                }
                (folder / 'report.json').write_text(json.dumps({
                    'status': 'FAIL' if failed else 'PASS',
                    'objectives': objectives,
                }))
                args = [sys.executable, '-', str(folder), '2R-transport',
                        '1' if failed else '0', '0', '0', 'rtp']
                run = subprocess.run(args, input=code, text=True,
                                     capture_output=True, check=False)
                self.assertEqual(run.returncode, 1 if failed else 0,
                                 run.stderr)
                result = json.loads((folder / 'result.json').read_text())
                self.assertEqual(result['status'], 'FAIL' if failed else 'PASS')
                self.assertNotIn('BLOCKED', result['required_objective_statuses'])
