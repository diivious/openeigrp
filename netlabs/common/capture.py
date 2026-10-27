#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Capture protocol 88 before an event; stop and validate PCAP evidence.

Capture uses tcpdump on an explicitly named host interface. The runner calls
cleanup() even when a scenario fails or times out. PCAP files are never
fabricated from logs or neighbor tables.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

NAME = re.compile(r'^[a-zA-Z0-9_.:-]{1,64}$')
BPF = 'ip proto 88 or ip6 proto 88'
_PROCESSES = {}


def _starttime(pid):
    # Linux /proc stat field 22. Parenthesized executable names may include ')'.
    return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19])


def start(interface, pcap, state, *, container=None):
    if not NAME.fullmatch(interface):
        raise ValueError('invalid capture interface')
    pcap, state = Path(pcap).resolve(), Path(state).resolve()
    if state.exists():
        raise RuntimeError('capture is already recorded; stop it first')
    pcap.parent.mkdir(parents=True, exist_ok=True)
    state.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['tcpdump', '-i', interface, '-U', '-s', '0', '-n', '-w', str(pcap), BPF]
    if container is not None:
        if not NAME.fullmatch(container):
            raise ValueError('invalid container')
        pid = subprocess.run(['docker', 'inspect', '-f', '{{.State.Pid}}', container],
                             capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        if not pid.isdecimal() or int(pid) <= 1:
            raise RuntimeError('container network namespace unavailable')
        cmd = ['sudo', '-n', 'nsenter', '-t', pid, '-n', '--', *cmd]
    with (pcap.parent / (pcap.name + '.tcpdump.log')).open('w') as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=log,
                                start_new_session=True, stdin=subprocess.DEVNULL)
    _PROCESSES[proc.pid] = proc
    try:
        for _ in range(60):
            if proc.poll() is not None:
                raise RuntimeError(f'tcpdump failed to start (exit {proc.returncode})')
            if pcap.is_file() and pcap.stat().st_size >= 24:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError('tcpdump did not produce a PCAP header')
        data = {'schema': 'openeigrp-capture/v1', 'pid': proc.pid,
                'starttime': _starttime(proc.pid), 'interface': interface,
                'pcap': str(pcap), 'bpf': BPF, 'container': container}
        state.write_text(json.dumps(data, indent=2) + '\n', encoding='utf8')
    except BaseException:
        # A failed start must never strand a capture without a state marker.
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGINT)
        try:
            proc.wait(timeout=3)
        finally:
            _PROCESSES.pop(proc.pid, None)
        raise
    return data


def stop(state, *, require_packets=False):
    state = Path(state)
    if not state.exists():
        return None
    data = json.loads(state.read_text(encoding='utf8'))
    if data.get('schema') != 'openeigrp-capture/v1':
        raise RuntimeError('unrecognized capture state')
    pid = int(data['pid'])
    try:
        if _starttime(pid) == data['starttime']:
            cmdline = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
            if not any(b'tcpdump' in item for item in cmdline) or \
                    str(data['pcap']).encode() not in cmdline:
                raise RuntimeError('capture PID identity mismatch; refusing to signal')
            os.killpg(pid, signal.SIGINT)
            for _ in range(60):
                if not Path(f'/proc/{pid}').exists() or \
                        Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError('capture did not terminate after SIGINT')
    except FileNotFoundError:
        pass
    finally:
        child = _PROCESSES.pop(pid, None)
        if child is not None:
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _PROCESSES[pid] = child
    pcap = Path(data['pcap'])
    if not pcap.is_file() or pcap.stat().st_size < 24:
        raise RuntimeError('capture file missing or truncated')
    if require_packets and pcap.stat().st_size == 24:
        raise RuntimeError('capture has no packets; evidence unavailable')
    state.unlink()
    return data


def cleanup(directory):
    errors = []
    for item in sorted(Path(directory).glob('*.capture.json')):
        try:
            stop(item)
        except Exception as exc:
            errors.append(f'{item}: {exc}')
    if errors:
        raise RuntimeError('; '.join(errors))


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='action', required=True)
    begin = sub.add_parser('start')
    for opt in ('interface', 'pcap', 'state'):
        begin.add_argument('--' + opt, required=True)
    begin.add_argument('--container', help='Capture in Docker node network namespace')
    end = sub.add_parser('stop')
    end.add_argument('--state', required=True)
    end.add_argument('--require-packets', action='store_true')
    clean = sub.add_parser('cleanup')
    clean.add_argument('--directory', required=True)
    args = p.parse_args(argv)
    try:
        if args.action == 'start':
            print(json.dumps(start(args.interface, args.pcap, args.state, container=args.container)))
        elif args.action == 'stop':
            stop(args.state, require_packets=args.require_packets)
        else:
            cleanup(args.directory)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'FAIL capture: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
