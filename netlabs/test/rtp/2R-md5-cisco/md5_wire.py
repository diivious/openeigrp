#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Minimal wire observations from a Cisco-to-Cisco MD5 PCAP.

Digest candidates are diagnostic only; a mismatch never disqualifies a packet
that Cisco demonstrably accepted in a converged authenticated adjacency.
"""
from collections import Counter
import hashlib
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'common'))
import wire

ROUTE_TLVS = {0x0102, 0x0103, 0x0402, 0x0403, 0x0602, 0x0603}
OPCODES = {1: 'UPDATE', 3: 'QUERY', 4: 'REPLY', 5: 'HELLO',
           7: 'SIA_QUERY', 8: 'SIA_REPLY'}


def checksum(data):
    if len(data) & 1:
        data += b'\0'
    words = struct.unpack('!' + str(len(data) // 2) + 'H', data)
    total = sum(words)
    while total > 0xffff:
        total = (total & 0xffff) + (total >> 16)
    return ~total & 0xffff


def packets(path):
    with Path(path).open('rb') as f:
        header = f.read(24)
        if len(header) != 24 or header[:4] not in wire.PCAP_MAGIC:
            raise ValueError('invalid PCAP header')
        order, scale = wire.PCAP_MAGIC[header[:4]]
        major, minor, _, _, snaplen, linktype = struct.unpack(order + 'HHiIII', header[4:])
        if (major, minor) != (2, 4) or not 0 < snaplen <= 0xfffffff:
            raise ValueError('unsupported PCAP format')
        while True:
            record = f.read(16)
            if not record:
                return
            if len(record) != 16:
                raise ValueError('short PCAP record')
            seconds, fraction, length, original = struct.unpack(order + 'IIII', record)
            if length > original or length > snaplen:
                raise ValueError('invalid PCAP frame length')
            frame = f.read(length)
            if len(frame) != length:
                raise ValueError('short PCAP frame')
            ip = wire.ip_payload(frame, linktype)
            if ip is not None and ip[0] == 4 and ip[3] == 88:
                yield seconds + fraction * scale, ip[1], ip[2], ip[4]


def decode(source, destination, packet, timestamp, key):
    if len(packet) < 20:
        raise ValueError('short EIGRP header')
    version, opcode, _, flags, sequence, ack, _, asn = struct.unpack_from('!BBHIIIHH', packet)
    if version != 2:
        raise ValueError('unsupported EIGRP version')
    tlvs = []
    offset = 20
    while offset < len(packet):
        if offset + 4 > len(packet):
            raise ValueError('short TLV header')
        kind, length = struct.unpack_from('!HH', packet, offset)
        if length < 4 or offset + length > len(packet):
            raise ValueError('invalid TLV framing')
        tlvs.append((kind, offset, length))
        offset += length
    if not tlvs and (opcode != 5 or not ack or sequence or flags):
        raise ValueError('EIGRP packet missing TLVs')
    if not tlvs or tlvs[0][0] != 2:
        # IOS sends header-only Hello ACKs as well as transitional Hellos
        # without MD5. Retain both but never count them as authenticated.
        return {
            'timestamp': timestamp, 'source': source,
            'destination': destination,
            'opcode': OPCODES.get(opcode, str(opcode)), 'flags': flags,
            'rtp_sequence': sequence, 'ack': ack, 'asn': asn,
            'packet_length': len(packet), 'checksum_ok': checksum(packet) == 0,
            'key_id': None, 'auth_sequence': None, 'digest_hex': None,
            'tlv_types': [t[0] for t in tlvs],
            'route_tlvs': [t[0] for t in tlvs if t[0] in ROUTE_TLVS],
            'matches_open_basic': False, 'matches_open_update': False,
        }
    _, first_offset, first_length = tlvs[0]
    if first_offset != 20 or first_length != 40:
        raise ValueError('malformed MD5 Authentication TLV')
    authentication_type, digest_length, key_id, auth_sequence = struct.unpack_from('!HHII', packet, 24)
    if (authentication_type, digest_length) != (2, 16):
        raise ValueError('unexpected authentication type or digest length')
    digest = packet[44:60]
    base = bytearray(packet)
    base[2:4] = b'\0\0'
    base[44:60] = b'\0' * 16
    padded = key.encode('ascii').ljust(16, b'\0')
    if len(padded) != 16:
        raise ValueError('MD5 laboratory secret must be 1..16 ASCII bytes')
    prefix = bytes(base[:44]) + padded
    expected_basic = hashlib.md5(prefix).digest()
    expected_update = hashlib.md5(prefix + bytes(base[60:])).digest()
    return {
        'timestamp': timestamp, 'source': source, 'destination': destination,
        'opcode': OPCODES.get(opcode, str(opcode)), 'flags': flags,
        'rtp_sequence': sequence, 'ack': ack, 'asn': asn,
        'packet_length': len(packet), 'checksum_ok': checksum(packet) == 0,
        'key_id': key_id, 'auth_sequence': auth_sequence,
        'digest_hex': digest.hex(), 'tlv_types': [t[0] for t in tlvs],
        'route_tlvs': [t[0] for t in tlvs if t[0] in ROUTE_TLVS],
        'matches_open_basic': expected_basic == digest,
        'matches_open_update': expected_update == digest,
    }


def analyze(path, key, sources):
    events = []
    for timestamp, source, destination, packet in packets(path):
        if source in sources:
            events.append(decode(source, destination, packet, timestamp, key))
    if not events:
        raise AssertionError('Cisco-to-Cisco capture contains no EIGRP packets')
    if any(not e['checksum_ok'] for e in events):
        raise AssertionError('Cisco-to-Cisco capture contains an invalid EIGRP checksum')
    return events


def evidence(events, sources, key_id, *, route_start):
    by_source = {source: [p for p in events if p['source'] == source]
                 for source in sources}
    result = {}
    for src, dst in ((sources[0], sources[1]), (sources[1], sources[0])):
        out = by_source[src]
        reply = by_source[dst]
        md5 = [p for p in out if p['key_id'] == key_id]
        hellos = [p for p in md5 if p['opcode'] == 'HELLO']
        init = [p for p in md5 if p['opcode'] == 'UPDATE' and p['flags'] & 1]
        eot = [p for p in md5 if p['opcode'] == 'UPDATE' and p['flags'] & 8]
        route = [p for p in md5 if p['opcode'] == 'UPDATE' and p['route_tlvs']
                 and p['timestamp'] >= route_start]
        init_acks = [p for p in init if p['rtp_sequence'] > 0 and
                     any(q['ack'] == p['rtp_sequence'] and
                         q['destination'] == src and q['timestamp'] >= p['timestamp']
                         for q in reply)]
        route_acks = [p for p in route if p['rtp_sequence'] > 0 and
                      any(q['ack'] == p['rtp_sequence'] and
                         q['destination'] == src and q['timestamp'] >= p['timestamp']
                         for q in reply)]
        result[src] = {
            'total': len(out), 'hellos': len(hellos),
            'init_updates': len(init), 'eot_updates': len(eot),
            'acked_init': len(init_acks), 'route_updates': len(route),
            'acked_route': len(route_acks),
            'zero_auth_seq': sum(p['auth_sequence'] == 0 for p in md5),
            'basic_matches': sum(p['matches_open_basic'] for p in md5),
            'update_matches': sum(p['matches_open_update'] for p in md5),
            'tlv_histogram': dict(Counter(hex(t) for p in md5 for t in p['tlv_types'])),
        }
    return result


def require_exchange(summary):
    for sender, seen in summary.items():
        if seen['hellos'] < 2:
            raise AssertionError(sender + ' did not send authenticated Hellos')
        if not seen['init_updates'] or not seen['acked_init'] or not seen['eot_updates']:
            raise AssertionError(sender + ' INIT UPDATE was not acknowledged')
        if not seen['route_updates'] or not seen['acked_route']:
            raise AssertionError(sender + ' route UPDATE was not acknowledged')
