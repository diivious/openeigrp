#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise the real PCAP decoder on constructed protocol-88 frames."""
import importlib.util
import ipaddress
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wire', ROOT / 'common/wire.py')
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)


def eigrp(opcode=5, flags=0, seq=0, ack=0, tlvs=b''):
    return struct.pack('!BBHIIIHH', 2, opcode, 0x1234, flags, seq, ack, 0, 4473) + tlvs


def v4(data, options=b''):
    header_len = 20 + len(options)
    head = (struct.pack('!BBHHHBBH', 0x40 | (header_len // 4), 0, header_len + len(data),
                        1, 0, 64, 88, 0) +
            ipaddress.IPv4Address('10.1.1.1').packed +
            ipaddress.IPv4Address('224.0.0.10').packed + options)
    return b'\x00'*12 + b'\x08\x00' + head + data


def v6(data):
    return (b'\x00'*12 + b'\x86\xdd' + struct.pack('!IHBB', 6 << 28, len(data), 88, 64)
            + ipaddress.IPv6Address('fe80::1').packed
            + ipaddress.IPv6Address('ff02::a').packed + data)


def save_pcap(path, frames, linktype=1):
    with open(path, 'wb') as f:
        f.write(struct.pack('<IHHiiii', 0xa1b2c3d4, 2, 4, 0, 0, 65535, linktype))
        for i, frame in enumerate(frames):
            f.write(struct.pack('<IIII', 10 + i, 100, len(frame), len(frame)))
            f.write(frame)


class DecoderTests(unittest.TestCase):
    def test_ipv4_ipv6_opcodes_flags_ack_and_tlvs(self):
        param = struct.pack('!HH', 1, 12) + bytes([1, 0, 1, 0, 0, 0]) + b'\x00\x0f'
        mp_body = struct.pack('!HHI', 1, 0, 0x01020304) + bytes(24) + bytes([24, 10, 20, 30])
        mp = struct.pack('!HH', 0x0602, 4 + len(mp_body)) + mp_body
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'eigrp.pcap'
            save_pcap(p, [v4(eigrp(opcode=1, flags=0x9, seq=123, tlvs=param + mp)),
                          v6(eigrp(opcode=5, ack=123))])
            events, errors = wire.read_pcap(p)
        self.assertEqual(errors, [])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['opcode_name'], 'UPDATE')
        self.assertEqual(events[0]['flag_names'], ['INIT', 'EOT'])
        self.assertEqual(events[0]['sequence'], 123)
        self.assertEqual([t['type'] for t in events[0]['tlvs']], [1, 0x0602])
        self.assertEqual(events[0]['tlvs'][1]['prefixes'], ['10.20.30.0/24'])
        self.assertEqual(events[1]['family'], 'ipv6')
        self.assertEqual(events[1]['ack'], 123)
        wire.evidence(events, opcode='UPDATE', sequence=123, destination='224.0.0.10', tlv=0x0602, prefix='10.20.30.0/24')
        wire.evidence(events, opcode='HELLO', ack=123)
        with self.assertRaises(AssertionError):
            wire.evidence(events, opcode='REPLY')

    def test_classic_routes_and_generic_rtp_tlvs(self):
        def tlv(kind, value):
            return struct.pack('!HH', kind, len(value) + 4) + value
        param = tlv(1, bytes([1, 0, 1, 0, 0, 0]) + b'\x00\x0f')
        seq = tlv(3, bytes([4]) + ipaddress.IPv4Address('10.1.1.2').packed
                  + bytes([16]) + ipaddress.IPv6Address('fe80::2').packed)
        mcast = tlv(5, struct.pack('!I', 99))
        ver = tlv(4, bytes([17, 3, 2, 0]))
        addr = ipaddress.IPv4Address('0.0.0.0').packed
        metric = struct.pack('!II', 100, 200) + b'\xdc\x05\x00' + bytes([1, 255, 1, 0, 0])
        route = tlv(0x0102, addr + metric + bytes([24, 192, 0, 2]))
        ext = struct.pack('!IIIIHBB', 0x01020304, 4473, 7, 1234, 0, 2, 0)
        external = tlv(0x0103, addr + ext + metric + bytes([24, 203, 0, 113]))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'classic.pcap'
            save_pcap(p, [v4(eigrp(opcode=1, seq=10, tlvs=param+seq+mcast+ver+route+external))])
            event = wire.read_pcap(p)[0][0]
        items = {t['type']: t for t in event['tlvs']}
        self.assertEqual(items[1]['hold_time'], 15)
        self.assertEqual(items[3]['cr_excluded_neighbors'], ['10.1.1.2', 'fe80::2'])
        self.assertEqual(items[5]['next_multicast_sequence'], 99)
        self.assertEqual(items[4]['eigrp_version'], [2, 0])
        self.assertEqual(items[0x0102]['prefixes'], ['192.0.2.0/24'])
        self.assertEqual(items[0x0102]['metric']['mtu'], 1500)
        self.assertEqual(items[0x0103]['external']['tag'], 7)
        self.assertEqual(items[0x0103]['prefixes'], ['203.0.113.0/24'])

    def test_malformed_generic_sequence_fails_closed(self):
        bad = struct.pack('!HH', 3, 6) + bytes([4, 1])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'badseq.pcap'
            save_pcap(p, [v4(eigrp(tlvs=bad))])
            with self.assertRaisesRegex(wire.DecodeError, 'truncated sequence'):
                wire.read_pcap(p)

    def test_vlan_options_and_linux_cooked(self):
        original = v4(eigrp(), options=b'\x01\x01\x01\x01')
        vlan = original[:12] + b'\x81\x00\x00\x01' + original[12:]
        sll = b'\x00'*14 + b'\x08\x00' + original[14:]
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp)/'vlan.pcap', Path(tmp)/'sll.pcap'
            save_pcap(a, [vlan])
            save_pcap(b, [sll], linktype=113)
            self.assertEqual(len(wire.read_pcap(a)[0]), 1)
            self.assertEqual(len(wire.read_pcap(b)[0]), 1)

    def test_bad_tlv_rejected_in_strict_mode(self):
        bad = struct.pack('!HH', 0x0102, 99) + b'\x00'
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'bad.pcap'
            save_pcap(p, [v4(eigrp(tlvs=bad))])
            with self.assertRaisesRegex(wire.DecodeError, 'invalid TLV'):
                wire.read_pcap(p)
            data, errors = wire.read_pcap(p, strict=False)
            self.assertEqual(data, [])
            self.assertEqual(len(errors), 1)

    def test_fragments_rejected(self):
        packet = bytearray(v4(eigrp()))
        packet[14+6:14+8] = b'\x20\x00'
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'fragment.pcap'
            save_pcap(p, [packet])
            with self.assertRaisesRegex(wire.DecodeError, 'reassembly'):
                wire.read_pcap(p)

    def test_empty_capture_never_satisfies_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'empty.pcap'
            save_pcap(p, [])
            events, _ = wire.read_pcap(p)
            with self.assertRaises(AssertionError):
                wire.evidence(events, opcode='HELLO')

    def test_truncation_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'broken.pcap'
            save_pcap(p, [v4(eigrp())])
            p.write_bytes(p.read_bytes()[:-3])
            with self.assertRaisesRegex(wire.DecodeError, 'truncated'):
                wire.read_pcap(p)


if __name__ == '__main__':
    unittest.main()
