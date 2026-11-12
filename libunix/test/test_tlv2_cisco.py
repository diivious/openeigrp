#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Validate virtual-wire extraction of real Cisco TLV2 route destinations."""
import ipaddress
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eigrp_wire import _route_prefixes

# Recorded on 2026-10-27 from CSR1000v 17.03.06, protocol 88.
CISCO_V4 = bytes.fromhex(
    '0602002d000000010aff00020000ff010005ea000000001312d0'
    '0000007a12000000000000000000200aff0002'
)
CISCO_V6 = bytes.fromhex(
    '06020045000000020aff00020000ff010005ea000000001312d00000007a1200'
    '00000000000000000000000000000000000000008020010db8447300ff0000000000000002'
)


def packet(tlv):
    return bytes(20) + tlv


class CiscoTLV2WireTests(unittest.TestCase):
    def test_cisco_ipv4_received_prefix(self):
        self.assertEqual(_route_prefixes(packet(CISCO_V4), 4), ['10.255.0.2/32'])

    def test_cisco_ipv6_received_prefix(self):
        self.assertEqual(_route_prefixes(packet(CISCO_V6), 6),
                         ['2001:db8:4473:ff::2/128'])

    def test_wrong_afi_rejected(self):
        self.assertEqual(_route_prefixes(packet(CISCO_V4), 6), [])
        self.assertEqual(_route_prefixes(packet(CISCO_V6), 4), [])

    def test_missing_next_hop_and_invalid_tlv_rejected(self):
        for raw in (CISCO_V4, CISCO_V6):
            for length in (12, 28, 36, len(raw)-1):
                mutated = raw[:length]
                mutated = mutated[:2] + struct.pack('!H', len(mutated)) + mutated[4:]
                self.assertEqual(_route_prefixes(packet(mutated),
                                                 4 if raw is CISCO_V4 else 6), [])

    def test_unknown_afi_rejected(self):
        mutated = bytearray(CISCO_V4)
        mutated[7] = 3
        self.assertEqual(_route_prefixes(packet(mutated), 4), [])

    def test_zero_next_hop_is_not_prefix(self):
        self.assertNotIn('0.0.0.0/0', _route_prefixes(packet(CISCO_V4), 4))


if __name__ == '__main__':
    unittest.main()
