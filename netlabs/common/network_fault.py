#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Scoped protocol-88 drop, delay and exact-ACK suppression for netlab links.

Operates on a single dedicated router interface, in the network namespace of
its Docker container. Requires host sudo -n nsenter, tc, and docker. No SSH
credentials are used. Explicit state permits runner-owned fault recovery.
IPv4 and IPv6 matching assumes no IP extension headers on the impaired link.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

VALID = re.compile(r'^[a-zA-Z0-9_.-]{1,100}$')


def command(argv):
    p = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=15)
    if p.returncode:
        raise RuntimeError(f'{" ".join(argv)}: {p.stderr.strip()}')
    return p.stdout


def ns_command(container, argv):
    if not VALID.fullmatch(container):
        raise ValueError('invalid container')
    pid = command(['docker', 'inspect', '-f', '{{.State.Pid}}', container]).strip()
    if not pid.isdecimal() or int(pid) <= 1:
        raise RuntimeError('container network namespace unavailable')
    return command(['sudo', '-n', 'nsenter', '-t', pid, '-n', '--', *argv])


def ensure_interface(interface):
    if not VALID.fullmatch(interface):
        raise ValueError('invalid interface')


def filters(interface, family, mode, ack=None):
    """Return tc filter arguments; ACK matching requires a known sequence.

    tc/u32 matching is deliberately limited to plain IPv4 (IHL=5) or IPv6
    (no extension headers). Tests must capture the expected ACK sequence first.
    """
    ensure_interface(interface)
    if family not in ('ipv4', 'ipv6'):
        raise ValueError('unknown address family')
    if mode not in ('drop', 'delay', 'ack'):
        raise ValueError('unknown fault mode')
    if mode == 'ack' and (ack is None or not 0 < ack < 2**32):
        raise ValueError('ACK suppression requires an exact nonzero sequence')
    protocol = 'ip' if family == 'ipv4' else 'ipv6'
    match = ['match', 'ip', 'protocol', '88', '0xff'] if family == 'ipv4' \
        else ['match', 'ip6', 'nexthdr', '88', '0xff']
    # Offset is relative to IP header. Avoid pretending to support v4 options
    # or v6 extension chains in packet-level fault selectors.
    offset = 20 if family == 'ipv4' else 40
    if mode == 'ack':
        match += ['match', 'u8', '5', '0xff', 'at', str(offset + 1),
                  'match', 'u32', '0', '0xffffffff', 'at', str(offset + 8),
                  'match', 'u32', str(ack), '0xffffffff', 'at', str(offset + 12)]
    return ['protocol', protocol, 'prio', '17', 'u32', *match]


def apply(container, interface, family, mode, state, *, delay_ms=0, ack=None):
    state = pathlib.Path(state)
    ensure_interface(interface)
    if state.exists():
        raise RuntimeError('fault already recorded; recover it first')
    if mode == 'delay' and not 1 <= delay_ms <= 10000:
        raise ValueError('delay must be 1..10000 ms')
    selector = filters(interface, family, mode, ack)
    state.parent.mkdir(parents=True, exist_ok=True)
    # Fail closed if any existing root/clsact qdisc would be overwritten.
    # These operations are for isolated containerlab interfaces only.
    previous = json.loads(ns_command(container, ['tc', '-j', 'qdisc', 'show', 'dev', interface]))
    for item in previous:
        if item.get('kind') != 'noqueue':
            raise RuntimeError('existing qdisc cannot be restored exactly; refusing fault')
    data = {'schema': 'openeigrp-network-fault/v1', 'container': container,
            'interface': interface, 'family': family, 'mode': mode,
            'ack': ack, 'delay_ms': delay_ms, 'previous_qdisc': previous}
    # The state is written *before* mutation so a failed partial setup is
    # recoverable. Never silently leave a partial tc configuration behind.
    state.write_text(json.dumps(data, indent=2) + '\n', encoding='utf8')
    try:
        if mode == 'delay':
            ns_command(container, ['tc', 'qdisc', 'replace', 'dev', interface,
                                   'root', 'handle', '1:', 'prio', 'bands', '3'])
            ns_command(container, ['tc', 'qdisc', 'add', 'dev', interface,
                                   'parent', '1:3', 'handle', '30:', 'netem',
                                   'delay', f'{delay_ms}ms'])
            ns_command(container, ['tc', 'filter', 'add', 'dev', interface,
                                   'parent', '1:', *selector, 'flowid', '1:3'])
        else:
            ns_command(container, ['tc', 'qdisc', 'add', 'dev', interface, 'clsact'])
            ns_command(container, ['tc', 'filter', 'add', 'dev', interface,
                                   'egress', *selector, 'action', 'drop'])
    except Exception:
        # If cleanup fails, state persists to block silent fault leakage.
        clear(state)
        raise
    return data


def clear(state):
    state = pathlib.Path(state)
    if not state.exists():
        return
    data = json.loads(state.read_text(encoding='utf8'))
    if data.get('schema') != 'openeigrp-network-fault/v1':
        raise RuntimeError('unknown fault state')
    container, interface = data['container'], data['interface']
    target = ['tc', 'qdisc', 'del', 'dev', interface]
    if data['mode'] == 'delay':
        target += ['root']
    else:
        target += ['clsact']
    ns_command(container, target)
    state.unlink()


def cleanup(directory):
    errors = []
    for item in sorted(pathlib.Path(directory).glob('*.fault.json')):
        try:
            clear(item)
        except Exception as exc:
            errors.append(f'{item}: {exc}')
    if errors:
        raise RuntimeError('; '.join(errors))


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='action', required=True)
    a = sub.add_parser('apply')
    for n in ('container', 'interface', 'family', 'mode', 'state'):
        a.add_argument('--' + n, required=True)
    a.add_argument('--delay-ms', type=int, default=0)
    a.add_argument('--ack', type=lambda x: int(x, 0))
    b = sub.add_parser('clear')
    b.add_argument('--state', required=True)
    c = sub.add_parser('cleanup')
    c.add_argument('--directory', required=True)
    args = p.parse_args(argv)
    try:
        if args.action == 'apply':
            print(json.dumps(apply(args.container, args.interface, args.family, args.mode,
                                   args.state, delay_ms=args.delay_ms, ack=args.ack)))
        elif args.action == 'clear':
            clear(args.state)
        else:
            cleanup(args.directory)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'FAIL network fault: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
