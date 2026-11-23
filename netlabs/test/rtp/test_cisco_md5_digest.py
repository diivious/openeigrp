#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Reproduce Cisco's observed MD5 wire digest using an actual CSR1000v PCAP.

This intentionally tests the wire algorithm, including its security limitations;
it is not a test of authentication strength or of OpenEIGRP's live receive path.
"""
from hashlib import md5
from pathlib import Path
import socket
import struct
import unittest

CAPTURE = (Path(__file__).resolve().parent / 'fixtures' /
           'csr1000v-17.03.06-md5-c2c-20261122.pcap')
KEY = b'oe03-lab-md5'
MIXED_CAPTURE = (CAPTURE.parent / 'md5-interop-0518-add.pcap')


def packets(path=CAPTURE):
    data = path.read_bytes()
    if data[:4] != b'\xd4\xc3\xb2\xa1' or struct.unpack_from('<I', data, 20)[0] != 1:
        raise ValueError('expected little-endian Ethernet PCAP')
    offset = 24
    while offset + 16 <= len(data):
        _, _, length, _ = struct.unpack_from('<IIII', data, offset)
        offset += 16
        frame = data[offset:offset + length]
        offset += length
        if len(frame) < 34 or frame[12:14] != b'\x08\x00':
            continue
        ipv4 = frame[14:]
        header_len = (ipv4[0] & 15) * 4
        total_len = int.from_bytes(ipv4[2:4], 'big')
        if ipv4[9] != 88 or len(ipv4) < total_len:
            continue
        yield (socket.inet_ntoa(ipv4[12:16]),
               bytes(ipv4[header_len:total_len]))
    if offset != len(data):
        raise ValueError('truncated PCAP record')


def authenticated(packet):
    return (len(packet) >= 60 and packet[20:24] == b'\x00\x02\x00\x28' and
            packet[24:28] == b'\x00\x02\x00\x10')


def digest(packet, key=KEY):
    if not authenticated(packet) or len(key) > 16:
        raise ValueError('expected Cisco MD5 packet and at most 16-byte key')
    wire = bytearray(packet)
    wire[2:4] = b'\x00\x00'
    wire[44:60] = key.ljust(16, b'\x00')
    return md5(wire[:-20]).digest()


class CiscoMd5WireTests(unittest.TestCase):
    def test_all_cisco_auth_packets_match_cisco_digest(self):
        observed = list(packets())
        self.assertEqual(len(observed), 88)
        signed = [packet for _, packet in observed if authenticated(packet)]
        self.assertEqual(len(signed), 79)
        self.assertTrue(all(packet[44:60] == digest(packet) for packet in signed))
        self.assertEqual({int.from_bytes(p[32:36], 'big') for p in signed}, {0})
        self.assertEqual(sum(p[1] == 5 for p in signed), 68)
        self.assertEqual(sum(p[1] == 1 for p in signed), 11)

    def test_mixed_peer_live_add_phase_matches_cisco_md5_digest(self):
        # Actual IOS XE 17.3.6 <-> OpenEIGRP 0.5.18 wire evidence.
        observed = list(packets(MIXED_CAPTURE))
        self.assertEqual(len(observed), 45)
        signed = [(src, packet) for src, packet in observed
                  if authenticated(packet)]
        self.assertEqual(len(signed), 40)
        self.assertEqual({src for src, _ in signed},
                         {'10.44.53.1', '10.44.53.2'})
        self.assertEqual(sum(src == '10.44.53.1' for src, _ in signed), 22)
        self.assertEqual(sum(src == '10.44.53.2' for src, _ in signed), 18)
        self.assertTrue(all(packet[44:60] == digest(packet)
                            for _, packet in signed))
        self.assertTrue(any(packet[1] == 1 and len(packet) == 105
                            for src, packet in signed if src == '10.44.53.1'))
        self.assertTrue(any(packet[1] == 1 and len(packet) == 105
                            for src, packet in signed if src == '10.44.53.2'))
        self.assertEqual({int.from_bytes(packet[32:36], 'big')
                          for src, packet in signed if src == '10.44.53.2'},
                         {0})

    def test_header_only_ack_has_no_md5_tlv(self):
        bare = [p for _, p in packets() if len(p) == 20]
        self.assertEqual(len(bare), 9)
        self.assertTrue(all(p[1] == 5 and int.from_bytes(p[12:16], 'big') != 0
                            for p in bare))

    def test_init_digest_does_not_depend_on_key(self):
        init = [p for _, p in packets() if authenticated(p) and len(p) == 60]
        self.assertEqual(len(init), 4)
        for packet in init:
            self.assertEqual(digest(packet), digest(packet, b'not-the-key'))
            self.assertEqual(packet[44:60], digest(packet))

    def test_final_twenty_bytes_are_not_authenticated(self):
        hello = next(p for _, p in packets()
                     if authenticated(p) and len(p) == 80)
        modified = bytearray(hello)
        modified[-1] ^= 0x5a
        self.assertEqual(digest(hello), digest(modified))
        modified = bytearray(hello)
        modified[35] ^= 0x5a
        self.assertNotEqual(digest(hello), digest(modified))


if __name__ == '__main__':
    unittest.main()
