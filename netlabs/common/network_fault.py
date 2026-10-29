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
# Prefer the existing test priority, but do not assume it is free on a
# pre-existing clsact. Only one fault per state is active at a time.
FAULT_PREF = 49152
FAULT_PREF_LOW = 49120


def unused_filter_pref(existing):
    """Choose a vacant clsact priority without replacing existing filters."""
    used = set()
    for item in existing:
        if not isinstance(item, dict):
            raise RuntimeError('unexpected tc filter record')
        value = item.get('pref')
        if value is None:
            raise RuntimeError('tc filter has no priority; refusing shared fault')
        try:
            priority = int(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError('invalid tc filter priority') from exc
        used.add(priority)
    for pref in range(FAULT_PREF, FAULT_PREF_LOW - 1, -1):
        if pref not in used:
            return pref
    raise RuntimeError('no free tc fault priority on shared clsact')


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


def filters(interface, family, mode, ack=None, *, pref=17):
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
    # iproute2 tc/u32 has no 'match ip6 nexthdr' keyword. Match the raw
    # IPv6 Next Header byte at offset 6. Our test link carries EIGRP
    # directly (no extension headers), matching the documented restriction.
    match = ['match', 'ip', 'protocol', '88', '0xff'] if family == 'ipv4' \
        else ['match', 'u8', '88', '0xff', 'at', '6']
    # Offset is relative to IP header. Avoid pretending to support v4 options
    # or v6 extension chains in packet-level fault selectors.
    offset = 20 if family == 'ipv4' else 40
    if mode == 'drop':
        # Only impair reliable UPDATEs. Dropping HELLOs can expire the
        # adjacency before retransmission is observable, invalidating RTP-08.
        match += ['match', 'u8', '1', '0xff', 'at', str(offset + 1)]
    elif mode == 'ack':
        match += ['match', 'u8', '5', '0xff', 'at', str(offset + 1),
                  'match', 'u32', '0', '0xffffffff', 'at', str(offset + 8),
                  'match', 'u32', str(ack), '0xffffffff', 'at', str(offset + 12)]
    return ['protocol', protocol, 'prio', str(pref), 'u32', *match]


def apply(container, interface, family, mode, state, *, delay_ms=0, ack=None,
          direction='egress'):
    state = pathlib.Path(state)
    ensure_interface(interface)
    if state.exists():
        raise RuntimeError('fault already recorded; recover it first')
    if direction not in ('ingress', 'egress'):
        raise ValueError('unknown fault direction')
    if direction == 'ingress' and mode != 'drop':
        raise ValueError('ingress fault only supports UPDATE drops')
    if mode == 'delay' and not 1 <= delay_ms <= 10000:
        raise ValueError('delay must be 1..10000 ms')
    state.parent.mkdir(parents=True, exist_ok=True)
    # clsact and a root scheduler can coexist. An existing clsact is not
    # ours to replace or delete: isolate the fault under a reserved priority
    # on its existing ingress/egress hook instead.
    previous = json.loads(ns_command(container, ['tc', '-j', 'qdisc', 'show', 'dev', interface]))
    if not isinstance(previous, list):
        raise RuntimeError('unexpected tc qdisc response')
    kinds = [item.get('kind') for item in previous]
    if mode == 'delay':
        if any(kind not in ('noqueue', 'ingress', 'clsact') for kind in kinds):
            raise RuntimeError('existing root qdisc cannot be restored exactly; refusing fault')
    elif 'ingress' in kinds:
        # An ingress-only qdisc cannot provide egress ACK suppression; do not
        # mutate or replace it in order to create the clsact hooks.
        raise RuntimeError('existing ingress qdisc cannot be replaced safely')
    shared = mode != 'delay' and 'clsact' in kinds
    pref = 17
    if shared:
        existing = json.loads(ns_command(container, [
            'tc', '-j', 'filter', 'show', 'dev', interface, direction]))
        if not isinstance(existing, list):
            raise RuntimeError('unexpected tc filter response')
        # A pre-existing tc/u32 classifier may already use 49152, sometimes
        # with several JSON entries at that priority. Never replace it.
        pref = unused_filter_pref(existing)
    selector = filters(interface, family, mode, ack, pref=pref)
    data = {'schema': 'openeigrp-network-fault/v1', 'container': container,
            'interface': interface, 'family': family, 'mode': mode,
            'ack': ack, 'delay_ms': delay_ms, 'direction': direction,
            'previous_qdisc': previous, 'shared_clsact': shared,
            'filter_pref': pref if shared else None}
    # Record ownership before the mutation, so an interrupted runner can
    # clean up. Shared-qdisc cleanup is limited to our reserved priority.
    state.write_text(json.dumps(data, indent=2) + '\n', encoding='utf8')
    created = False
    try:
        if mode == 'delay':
            ns_command(container, ['tc', 'qdisc', 'replace', 'dev', interface,
                                   'root', 'handle', '1:', 'prio', 'bands', '3'])
            created = True
            ns_command(container, ['tc', 'qdisc', 'add', 'dev', interface,
                                   'parent', '1:3', 'handle', '30:', 'netem',
                                   'delay', f'{delay_ms}ms'])
            ns_command(container, ['tc', 'filter', 'add', 'dev', interface,
                                   'parent', '1:', *selector, 'flowid', '1:3'])
        else:
            if not shared:
                ns_command(container, ['tc', 'qdisc', 'add', 'dev', interface, 'clsact'])
                created = True
            ns_command(container, ['tc', 'filter', 'add', 'dev', interface,
                                   direction, *selector, 'action', 'drop'])
            if shared:
                created = True
    except Exception:
        # Failed filter insertion into an existing clsact must not delete
        # that qdisc or filters belonging to another subsystem.
        if created:
            clear(state)
        else:
            state.unlink()
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
    elif data.get('shared_clsact'):
        # Delete only the priority reserved by this test, never the shared
        # clsact or any pre-existing ingress/egress filter.
        pref = data.get('filter_pref')
        if type(pref) is not int or not FAULT_PREF_LOW <= pref <= FAULT_PREF:
            raise RuntimeError('unrecognized shared fault priority')
        target = ['tc', 'filter', 'del', 'dev', interface, data['direction'],
                  'protocol', 'ip' if data['family'] == 'ipv4' else 'ipv6',
                  'pref', str(pref)]
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
    a.add_argument('--direction', choices=('ingress', 'egress'), default='egress')
    a.add_argument('--ack', type=lambda x: int(x, 0))
    b = sub.add_parser('clear')
    b.add_argument('--state', required=True)
    c = sub.add_parser('cleanup')
    c.add_argument('--directory', required=True)
    args = p.parse_args(argv)
    try:
        if args.action == 'apply':
            print(json.dumps(apply(args.container, args.interface, args.family, args.mode,
                                   args.state, delay_ms=args.delay_ms, ack=args.ack,
                                   direction=args.direction)))
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
