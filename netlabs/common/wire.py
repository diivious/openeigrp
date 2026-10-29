#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""EIGRP (IP protocol 88) PCAP reader and bounded packet evidence assertions.

Designed for capture from a Linux host or a router's network namespace. The
PCAP reader rejects malformed link/IP/EIGRP framing; it does not reassemble IP
fragments or guess the meaning of opaque route-metric layouts.
"""
import argparse
import ipaddress
import json
import pathlib
import struct
import sys

OPCODES = {1: 'UPDATE', 2: 'REQUEST', 3: 'QUERY', 4: 'REPLY',
           5: 'HELLO', 6: 'IPXSAP', 7: 'SIA_QUERY', 8: 'SIA_REPLY'}
FLAGS = {0x1: 'INIT', 0x2: 'CR', 0x4: 'RESTART', 0x8: 'EOT'}
TLVS = {0x0001: 'PARAMETER', 0x0002: 'AUTHENTICATION',
        0x0003: 'SEQUENCE', 0x0004: 'SOFTWARE_VERSION',
        0x0005: 'MULTICAST_SEQUENCE', 0x0006: 'PEER_INFORMATION',
        0x0007: 'PEER_TERMINATION', 0x0008: 'PEER_TID_LIST',
        0x0102: 'IPV4_INTERNAL', 0x0103: 'IPV4_EXTERNAL',
        0x0402: 'IPV6_INTERNAL', 0x0403: 'IPV6_EXTERNAL',
        0x0602: 'MULTIPROTOCOL_INTERNAL', 0x0603: 'MULTIPROTOCOL_EXTERNAL'}
PCAP_MAGIC = {b'\xd4\xc3\xb2\xa1': ('<', 1e-6),
              b'\xa1\xb2\xc3\xd4': ('>', 1e-6),
              b'\x4d\x3c\xb2\xa1': ('<', 1e-9),
              b'\xa1\xb2\x3c\x4d': ('>', 1e-9)}

class DecodeError(ValueError):
    """A packet or capture is malformed or unsupported."""


def ip_payload(frame, linktype):
    """Return (family, source, destination, protocol, payload).

    Link types: Ethernet, Linux SLL v1/v2, and raw IPv4/IPv6.
    """
    if linktype == 1:  # Ethernet
        if len(frame) < 14:
            raise DecodeError('short Ethernet frame')
        proto = int.from_bytes(frame[12:14], 'big')
        pos = 14
        for _ in range(2):  # outer and inner VLAN tags
            if proto not in (0x8100, 0x88a8, 0x9100):
                break
            if len(frame) < pos + 4:
                raise DecodeError('short VLAN header')
            proto = int.from_bytes(frame[pos + 2:pos + 4], 'big')
            pos += 4
        frame = frame[pos:]
    elif linktype == 113:  # Linux cooked v1
        if len(frame) < 16:
            raise DecodeError('short SLL header')
        proto = int.from_bytes(frame[14:16], 'big')
        frame = frame[16:]
    elif linktype == 276:  # Linux cooked v2
        if len(frame) < 20:
            raise DecodeError('short SLL2 header')
        proto = int.from_bytes(frame[:2], 'big')
        frame = frame[20:]
    elif linktype in (101, 228, 229):  # raw IP
        proto = 0x0800 if (frame and frame[0] >> 4 == 4) else 0x86dd
    else:
        raise DecodeError(f'unsupported PCAP linktype {linktype}')

    if proto == 0x0800:
        if len(frame) < 20 or frame[0] >> 4 != 4:
            raise DecodeError('short or invalid IPv4 header')
        ihl = (frame[0] & 0xf) * 4
        length = int.from_bytes(frame[2:4], 'big')
        if ihl < 20 or length < ihl or length > len(frame):
            raise DecodeError('invalid IPv4 header length')
        if int.from_bytes(frame[6:8], 'big') & 0x3fff:
            raise DecodeError('IPv4 fragment: reassembly required')
        return (4, str(ipaddress.IPv4Address(frame[12:16])),
                str(ipaddress.IPv4Address(frame[16:20])), frame[9], frame[ihl:length])
    if proto == 0x86dd:
        if len(frame) < 40 or frame[0] >> 4 != 6:
            raise DecodeError('short or invalid IPv6 header')
        length = int.from_bytes(frame[4:6], 'big') + 40
        if length > len(frame):
            raise DecodeError('truncated IPv6 payload')
        nxt, pos = frame[6], 40
        while nxt in (0, 43, 60):  # hop-by-hop, routing, destination options
            if pos + 2 > length:
                raise DecodeError('short IPv6 extension')
            new_nxt, size = frame[pos], (frame[pos + 1] + 1) * 8
            if pos + size > length:
                raise DecodeError('truncated IPv6 extension')
            nxt, pos = new_nxt, pos + size
        if nxt == 44:
            raise DecodeError('IPv6 fragment: reassembly required')
        return (6, str(ipaddress.IPv6Address(frame[8:24])),
                str(ipaddress.IPv6Address(frame[24:40])), nxt, frame[pos:length])
    return None


def decode_prefix(body, pos, family):
    """Decode exactly one wire destination, returning (prefix, end offset)."""
    if pos >= len(body):
        raise DecodeError('route TLV without destination prefix')
    bits = body[pos]
    maxbits = 32 if family == 4 else 128
    if bits > maxbits:
        raise DecodeError('invalid route prefix length')
    # IOS XE follows RFC 7868's IPv6 extra-octet boundary encoding.
    used = (0 if bits == 0 else 16 if bits == 128 else bits // 8 + 1) if family == 6 else (bits + 7) // 8
    start = pos + 1
    if start + used > len(body):
        raise DecodeError('short route prefix')
    width = 4 if family == 4 else 16
    raw = body[start:start + used] + b'\x00' * (width - used)
    prefix = ipaddress.ip_network((ipaddress.ip_address(raw), bits), strict=False)
    return str(prefix), start + used


def route_tlv(kind, body):
    """Decode classic and Cisco multiprotocol route TLV fields and prefixes."""
    mp = kind in (0x0602, 0x0603)
    external = kind in (0x0103, 0x0403, 0x0603)
    if kind not in (0x0102, 0x0103, 0x0402, 0x0403, 0x0602, 0x0603):
        return {}
    item = {'route_kind': 'external' if external else 'internal',
            'codec': 'TLV2' if mp else 'TLV1'}
    if mp:
        if len(body) < 8 + 24:
            raise DecodeError('short multiprotocol route metric')
        topology, afi, rid = struct.unpack_from('!HHI', body)
        if afi not in (1, 2):
            raise DecodeError(f'unsupported multiprotocol AFI {afi}')
        family = 4 if afi == 1 else 6
        item.update(afi=afi, topology_id=topology, router_id=rid)
        metric = body[8:32]
        attrs_len = metric[0] * 2
        pos = 32 + attrs_len
        if pos > len(body):
            raise DecodeError('truncated multiprotocol metric attributes')
        item['extended_attributes_hex'] = body[32:pos].hex()
        item['metric'] = {'tag': metric[1], 'reliability': metric[2],
                          'load': metric[3], 'mtu': int.from_bytes(metric[4:7], 'little'),
                          'hops': metric[7], 'delay': int.from_bytes(metric[8:14], 'big'),
                          'bandwidth': int.from_bytes(metric[14:20], 'big'),
                          'flags': int.from_bytes(metric[22:24], 'big')}
        addr_len = 4 if family == 4 else 16
        if len(body) < pos + addr_len:
            raise DecodeError('short multiprotocol next-hop address')
        item['next_hop'] = str(ipaddress.ip_address(body[pos:pos+addr_len]))
        pos += addr_len
        # Cisco's tagged 20-byte exterior is the only supported MP
        # external layout. Exactly one destination must follow it.
        if external:
            if len(body) < pos + 21:
                raise DecodeError('short Cisco multiprotocol exterior')
            origin, asn, tag, value, reserved, protocol, flags = struct.unpack_from(
                '!IIIIHBB', body, pos)
            if reserved:
                raise DecodeError('invalid Cisco multiprotocol exterior reserved field')
            destination, end = decode_prefix(body, pos + 20, family)
            if end != len(body):
                raise DecodeError('trailing multiprotocol destination bytes')
            item['external'] = {'origin': origin, 'as': asn, 'tag': tag,
                                'metric': value, 'reserved': reserved,
                                'protocol': protocol, 'flags': flags}
        else:
            destination, end = decode_prefix(body, pos, family)
            if end != len(body):
                raise DecodeError('trailing multiprotocol destination bytes')
        item['prefixes'] = [destination]
        return item
    else:
        family = 4 if kind >> 8 == 1 else 6
        addr_len = 4 if family == 4 else 16
        pos = addr_len + (20 if external else 0)
        if len(body) < pos + 16:
            raise DecodeError('short classic route metric')
        item['next_hop'] = str(ipaddress.ip_address(body[:addr_len]))
        if external:
            origin, asn, tag, value, reserved, protocol, flags = struct.unpack_from('!IIIIHBB', body, addr_len)
            item['external'] = {'origin': origin, 'as': asn, 'tag': tag,
                                'metric': value, 'reserved': reserved,
                                'protocol': protocol, 'flags': flags}
        metric = body[pos:pos + 16]
        item['metric'] = {'delay': int.from_bytes(metric[:4], 'big'),
                          'bandwidth': int.from_bytes(metric[4:8], 'big'),
                          'mtu': int.from_bytes(metric[8:11], 'little'),
                          'hops': metric[11], 'reliability': metric[12],
                          'load': metric[13], 'tag': metric[14], 'flags': metric[15]}
        pos += 16
    prefixes = []
    while pos < len(body):
        destination, pos = decode_prefix(body, pos, family)
        prefixes.append(destination)
    if not prefixes:
        raise DecodeError('route TLV without destination prefix')
    item['prefixes'] = prefixes
    return item


def generic_tlv(kind, body):
    """Parse RFC 7868 generic TLVs used to qualify discovery and RTP/CR."""
    if kind == 0x0001:
        if len(body) != 8:
            raise DecodeError('invalid parameter TLV length')
        return {'k_values': list(body[:6]),
                'hold_time': int.from_bytes(body[6:8], 'big')}
    if kind == 0x0003:
        addresses = []
        offset = 0
        while offset < len(body):
            width = body[offset]
            offset += 1
            if not width or offset + width > len(body):
                raise DecodeError('truncated sequence TLV address')
            address = body[offset:offset + width]
            addresses.append(str(ipaddress.ip_address(address)) if width in (4, 16)
                             else address.hex())
            offset += width
        return {'cr_excluded_neighbors': addresses}
    if kind == 0x0004:
        if len(body) < 4:
            raise DecodeError('short software version TLV')
        return {'vendor_version': list(body[:2]), 'eigrp_version': list(body[2:4])}
    if kind == 0x0005:
        if len(body) != 4:
            raise DecodeError('invalid multicast sequence TLV length')
        return {'next_multicast_sequence': int.from_bytes(body, 'big')}
    return {}


def decode_eigrp(packet, family, source, destination, timestamp):
    if len(packet) < 20:
        raise DecodeError('short EIGRP header')
    version, opcode, checksum, flags, seq, ack, vrid, asn = struct.unpack_from('!BBHIIIHH', packet)
    pos, tlvs = 20, []
    while pos < len(packet):
        if pos + 4 > len(packet):
            raise DecodeError('short EIGRP TLV header')
        kind, size = struct.unpack_from('!HH', packet, pos)
        if size < 4 or pos + size > len(packet):
            raise DecodeError(f'invalid TLV length {size} at {pos}')
        body = packet[pos + 4:pos + size]
        item = {'type': kind, 'name': TLVS.get(kind, 'UNKNOWN'),
                'length': size, 'offset': pos, 'value_hex': body.hex()}
        item.update(route_tlv(kind, body))
        item.update(generic_tlv(kind, body))
        tlvs.append(item)
        pos += size
    return {'timestamp': timestamp, 'family': f'ipv{family}',
            'source': source, 'destination': destination,
            'version': version, 'opcode': opcode,
            'opcode_name': OPCODES.get(opcode, 'UNKNOWN'),
            'checksum': checksum, 'flags': flags,
            'flag_names': [name for bit, name in FLAGS.items() if flags & bit],
            'sequence': seq, 'ack': ack, 'vrid': vrid, 'asn': asn,
            'header_length': 20, 'packet_length': len(packet), 'tlvs': tlvs}


def read_pcap(path, *, strict=True):
    """Return (events, diagnostics). Strict mode rejects any malformed frame.

    Non-protocol-88 packets are ignored; malformed frames are never reported as
    successful EIGRP evidence.
    """
    events, errors = [], []
    with pathlib.Path(path).open('rb') as f:
        header = f.read(24)
        if len(header) != 24 or header[:4] not in PCAP_MAGIC:
            raise DecodeError('invalid or unsupported PCAP header')
        order, resolution = PCAP_MAGIC[header[:4]]
        major, minor, _, _, snaplen, linktype = struct.unpack(order + 'HHiIII', header[4:])
        if (major, minor) != (2, 4) or not 0 < snaplen <= 0xfffffff:
            raise DecodeError('invalid PCAP format')
        index = 0
        while True:
            rec = f.read(16)
            if not rec:
                break
            if len(rec) != 16:
                raise DecodeError('truncated PCAP record header')
            sec, fraction, caplen, original = struct.unpack(order + 'IIII', rec)
            if caplen > snaplen or caplen > original:
                raise DecodeError('invalid PCAP captured length')
            packet = f.read(caplen)
            if len(packet) != caplen:
                raise DecodeError('truncated PCAP record')
            index += 1
            try:
                ip = ip_payload(packet, linktype)
                if ip is None or ip[3] != 88:
                    continue
                events.append(decode_eigrp(ip[4], ip[0], ip[1], ip[2], sec + fraction * resolution))
            except DecodeError as exc:
                msg = f'packet {index}: {exc}'
                errors.append(msg)
                if strict:
                    raise DecodeError(msg) from exc
    return events, errors


def evidence(events, *, opcode=None, source=None, destination=None, sequence=None,
             ack=None, tlv=None, prefix=None, family=None, minimum=1):
    """Assert actual packet evidence, never a neighbor/RIB inference."""
    matched = [e for e in events if
               (opcode is None or e['opcode_name'] == opcode.upper()) and
               (source is None or e['source'] == source) and
               (destination is None or e['destination'] == destination) and
               (sequence is None or e['sequence'] == sequence) and
               (ack is None or e['ack'] == ack) and
               (family is None or e['family'] == family) and
               (tlv is None or any(t['type'] == tlv for t in e['tlvs'])) and
               (prefix is None or any(prefix in t.get('prefixes', []) for t in e['tlvs']))]
    if len(matched) < minimum:
        raise AssertionError(f'packet evidence missing: expected {minimum}, observed {len(matched)}')
    return matched


def main(argv=None):
    p = argparse.ArgumentParser(description='Decode protocol-88 PCAP to JSON')
    p.add_argument('pcap', type=pathlib.Path)
    p.add_argument('--output', type=pathlib.Path)
    p.add_argument('--allow-errors', action='store_true')
    p.add_argument('--require-opcode', choices=sorted(OPCODES.values()))
    p.add_argument('--require-tlv', type=lambda s: int(s, 0))
    args = p.parse_args(argv)
    try:
        events, errors = read_pcap(args.pcap, strict=not args.allow_errors)
        if args.require_opcode or args.require_tlv is not None:
            evidence(events, opcode=args.require_opcode, tlv=args.require_tlv)
        output = json.dumps({'schema': 'openeigrp-wire/v1', 'events': events,
                             'errors': errors, 'packets': len(events)}, indent=2) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding='utf8')
        else:
            print(output, end='')
        return 0
    except (OSError, ValueError, AssertionError) as exc:
        print(f'FAIL wire evidence: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
