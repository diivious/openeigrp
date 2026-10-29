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


CISCO_V4 = bytes.fromhex(
    '0602002d000000010aff00020000ff010005ea000000001312d0'
    '0000007a12000000000000000000200aff0002'
)
CISCO_V6 = bytes.fromhex(
    '06020045000000020aff00020000ff010005ea000000001312d00000007a1200'
    '00000000000000000000000000000000000000008020010db8447300ff0000000000000002'
)


CISCO_TAGGED_V4 = bytes.fromhex(
    "06030047000000010aff00020300ff010005dc00000005f5e1000000000186a000000000020200000007000000000aff00020000000000000007000000000000030020c613ff02"
)
CISCO_TAGGED_V6 = bytes.fromhex(
    "0603005900000002000000000000ff010005dc00000005f5e1000000000186a000000000000000000000000000000000000000000aff0002000000000000000700000000000003008020010db84473020000000000000000ff"
)
OE_LEGACY_V4 = bytes.fromhex(
    "0603003d000000010aff00010000ff010005dc0000000000000a00000000640000000000000000000aff000100000000000000000000030020c612ff01"
)
OE_LEGACY_V6 = bytes.fromhex(
    "06030071000000020aff00010e00ff010005dc0000000000000a00000000640000000000070d000000000000000000000000000000000aff0001000000070300000000000000000000000000000000000aff00010000000000000000000003008020010db84473010000000000000000ff"
)

class DecoderTests(unittest.TestCase):
    def test_ipv4_ipv6_opcodes_flags_ack_and_tlvs(self):
        param = struct.pack('!HH', 1, 12) + bytes([1, 0, 1, 0, 0, 0]) + b'\x00\x0f'
        mp_body = (struct.pack('!HHI', 0, 1, 0x01020304) + bytes(24)
                   + bytes(4) + bytes([24, 10, 20, 30]))
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

    def test_cisco_ipv4_ipv6_multiprotocol_vectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'cisco.pcap'
            save_pcap(p, [v4(eigrp(opcode=1, tlvs=CISCO_V4)),
                          v6(eigrp(opcode=1, tlvs=CISCO_V6))])
            events, errors = wire.read_pcap(p)
        self.assertFalse(errors)
        self.assertEqual([item['tlvs'][0]['prefixes'] for item in events],
                         [['10.255.0.2/32'], ['2001:db8:4473:ff::2/128']])
        self.assertEqual([item['tlvs'][0]['next_hop'] for item in events],
                         ['0.0.0.0', '::'])

    def test_cisco_tagged_external_exactly_one_destination(self):
        vectors = ((CISCO_TAGGED_V4, 4, '198.19.255.2/32', 7),
                   (CISCO_TAGGED_V6, 6, '2001:db8:4473:200::ff/128', 7))
        for raw, family, expected, tag in vectors:
            with self.subTest(family=family, expected=expected):
                event = wire.decode_eigrp(eigrp(opcode=1, tlvs=raw), family,
                                          '10.44.53.2', '224.0.0.10', 0)
                tlv = event['tlvs'][0]
                self.assertEqual(tlv['prefixes'], [expected])
                self.assertEqual(tlv['external'].get('tag'), tag)
                self.assertEqual(tlv['external']['protocol'], 3)
                self.assertEqual(tlv['external']['reserved'], 0)
                self.assertEqual(len(tlv['prefixes']), 1)

                # Unreachable-wide-metric withdrawal preserves provenance.
                withdrawn = bytearray(raw)
                withdrawn[20:26] = b'\xff' * 6
                tlv = wire.decode_eigrp(eigrp(opcode=1, tlvs=withdrawn), family,
                                        '10.44.53.2', '224.0.0.10', 0)['tlvs'][0]
                self.assertEqual(tlv['metric']['delay'], 0xffffffffffff)
                self.assertEqual(tlv['prefixes'], [expected])

    def test_legacy_exterior_is_rejected(self):
        for raw, family in ((OE_LEGACY_V4, 4), (OE_LEGACY_V6, 6),
                            (OE_LEGACY_V4[:-1] + b'\x00', 4)):
            with self.subTest(family=family, size=len(raw)):
                with self.assertRaises(wire.DecodeError):
                    wire.decode_eigrp(eigrp(opcode=1, tlvs=raw), family,
                                      '10.44.53.1', '224.0.0.10', 0)

    def test_mp_external_malformed_length_is_not_extra_prefixes(self):
        for raw, family in ((CISCO_TAGGED_V4, 4), (CISCO_TAGGED_V6, 6)):
            with self.subTest(family=family, size=len(raw)):
                for count in (1, 2, 3, 4):
                    shortened = bytearray(raw[:-count])
                    shortened[2:4] = struct.pack('!H', len(shortened))
                    with self.assertRaises(wire.DecodeError):
                        wire.decode_eigrp(eigrp(opcode=1, tlvs=shortened), family,
                                          '10.44.53.2', '224.0.0.10', 0)
                wrong_length = bytearray(raw)
                wrong_length[2:4] = struct.pack('!H', len(raw) - 1)
                with self.assertRaises(wire.DecodeError):
                    wire.decode_eigrp(eigrp(opcode=1, tlvs=wrong_length), family,
                                      '10.44.53.2', '224.0.0.10', 0)
                bad_attr = bytearray(raw)
                bad_attr[12] = 255
                with self.assertRaises(wire.DecodeError):
                    wire.decode_eigrp(eigrp(opcode=1, tlvs=bad_attr), family,
                                      '10.44.53.2', '224.0.0.10', 0)
                extra = bytearray(raw + b'\x01')
                extra[2:4] = struct.pack('!H', len(extra))
                with self.assertRaises(wire.DecodeError):
                    wire.decode_eigrp(eigrp(opcode=1, tlvs=extra), family,
                                      '10.44.53.2', '224.0.0.10', 0)
                bad_prefix = bytearray(raw)
                offset = len(raw) - (17 if family == 6 else 5)
                bad_prefix[offset] = 129 if family == 6 else 33
                with self.assertRaises(wire.DecodeError):
                    wire.decode_eigrp(eigrp(opcode=1, tlvs=bad_prefix), family,
                                      '10.44.53.2', '224.0.0.10', 0)

    def test_reject_prior_open_eigrp_header_order(self):
        legacy = bytearray(CISCO_V4)
        legacy[4:8] = b'\x00\x01\x00\x00'
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'legacy.pcap'
            save_pcap(p, [v4(eigrp(opcode=1, tlvs=bytes(legacy)))])
            with self.assertRaisesRegex(wire.DecodeError, 'AFI 0'):
                wire.read_pcap(p)

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
