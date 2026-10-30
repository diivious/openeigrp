#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Isolated EIGRP cold-restart qualification with persistent dataplane links.

Restart the OE daemon or IOS VM, never the Docker container whose network
namespace contains the containerlab link and the surviving packet capture.
Each role/AF case still uses a fresh native netlab deployment.
"""
import argparse
from pathlib import Path
import sys
import time

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'common'))
from interop import (Lab, Trace, CapturePrerequisiteError, command,
                     report_base, require_packet_access, result, write_report)

PREFIXES = {'oe': {'ipv4': '10.255.0.1/32',
                   'ipv6': '2001:db8:4473:ff::1/128'},
            'ce': {'ipv4': '10.255.0.2/32',
                   'ipv6': '2001:db8:4473:ff::2/128'}}
MULTICAST = {'ipv4': '224.0.0.10', 'ipv6': 'ff02::a'}


def require(value, message):
    if not value:
        raise AssertionError(message)


def sources_for(lab):
    return {'oe': {'ipv4': '10.44.53.1', 'ipv6': lab.peer_address('ce', 'ipv6')},
            'ce': {'ipv4': '10.44.53.2', 'ipv6': lab.peer_address('oe', 'ipv6')}}


def startup(events, family, sources):
    """Fresh bidirectional INIT, ACK, and EOT on the post-restart wire."""
    for sender, receiver in (('oe', 'ce'), ('ce', 'oe')):
        packets = [e for e in events if e['family'] == family and
                   e['source'] == sources[sender][family]]
        replies = [e for e in events if e['family'] == family and
                   e['source'] == sources[receiver][family]]
        require(any(e['opcode_name'] == 'HELLO' and e['sequence'] == 0 and
                    e['destination'] == MULTICAST[family] for e in packets),
                sender + ' discovery HELLO missing after restart')
        inits = [e for e in packets if e['opcode_name'] == 'UPDATE' and
                 'INIT' in e['flag_names'] and e['sequence'] != 0 and
                 e['destination'] == sources[receiver][family] and
                 not any(t.get('route_kind') for t in e['tlvs'])]
        require(inits, sender + ' null INIT missing after restart')
        require(all(any(e['ack'] == update['sequence'] and
                        e['destination'] == sources[sender][family]
                        for e in replies) for update in inits),
                sender + ' INIT ACK missing after restart')
        require(any(e['opcode_name'] == 'UPDATE' and 'EOT' in e['flag_names']
                    for e in packets), sender + ' EOT missing after restart')
    return {'wire_events': sum(e['family'] == family for e in events)}


def connectivity_diagnostics(lab, stage, failure):
    """Preserve data-plane evidence before teardown after an L3 gate failure."""
    lines = [f'[{stage}] {failure}']
    checks = [
        ('OE link', ['docker', 'exec', lab.oe, 'ip', '-s', 'link', 'show', 'dev', 'eth1']),
        ('OE IPv4', ['docker', 'exec', lab.oe, 'ip', '-4', 'address', 'show', 'dev', 'eth1']),
        ('OE IPv6', ['docker', 'exec', lab.oe, 'ip', '-6', 'address', 'show', 'dev', 'eth1']),
        ('OE IPv4 route', ['docker', 'exec', lab.oe, 'ip', '-4', 'route', 'get', '10.44.53.2']),
        ('OE neighbor cache', ['docker', 'exec', lab.oe, 'ip', 'neigh', 'show', 'dev', 'eth1']),
    ]
    for label, argv in checks:
        try:
            output = command(argv, timeout=15, check=False)
        except Exception as exc:
            output = str(exc)
        lines.extend((f'$ {label}: {argv!r}', output))
    for cmd in ('show ip interface brief', 'show interfaces GigabitEthernet2',
                'show ip arp', 'show ipv6 interface brief'):
        try:
            output = lab.ce_cli(cmd)
        except Exception as exc:
            output = str(exc)
        lines.extend((f'$ Cisco {cmd}', output))
    lab.save('connectivity-diagnostics.log', '\n'.join(lines) + '\n', append=True)


def connectivity(lab, *, seconds=120, stage='preflight'):
    """Wait for actual two-family L3 connectivity and both adjacencies.

    CSR configuration completion does not establish that its data-plane NIC,
    Linux neighbor resolution, and IOS forwarding have finished starting.
    """
    try:
        ipv4 = ['docker', 'exec', lab.oe, 'ping', '-n', '-c', '2', '-W', '2',
                '10.44.53.2']
        lab.wait(lambda: command(ipv4, timeout=15), lambda output: True, seconds)

        def ipv6_probe():
            # The peer's link-local may not exist during initial CSR boot.
            peer = lab.peer_address('oe', 'ipv6')
            argv = ['docker', 'exec', lab.oe, 'ping', '-6', '-n', '-c', '2',
                    '-W', '2', '-I', 'eth1', peer]
            return command(argv, timeout=15)

        lab.wait(ipv6_probe, lambda output: True, seconds)
        for family in ('ipv4', 'ipv6'):
            lab.peers(family, True, 150)
    except Exception as exc:
        connectivity_diagnostics(lab, stage, str(exc))
        raise


def daemon_pid(lab, name):
    """Require one named FRR process, never a guessed or shell-matched PID."""
    output = command(['docker', 'exec', lab.oe, 'pidof', name], timeout=15)
    pids = output.split()
    require(len(pids) == 1 and pids[0].isdigit() and int(pids[0]) > 1,
            f'expected exactly one {name} daemon, got {output!r}')
    return pids[0]


def oe_signal(lab, signal_name, pid):
    require(signal_name in ('STOP', 'CONT', 'KILL'), 'unsupported daemon signal')
    require(pid.isdigit() and int(pid) > 1, 'invalid daemon PID')
    command(['docker', 'exec', lab.oe, 'kill', '-' + signal_name, pid], timeout=15)


def cold_restart(lab, role, family, *, connectivity_seconds=120,
                 capture_ready_seconds=25):
    other = 'ce' if role == 'oe' else 'oe'
    # Save the complete running configuration *before* terminating anything.
    # The FRR daemon needs vtysh -b after watchfrr starts a replacement;
    # IOS reload must boot with the test configuration still available.
    if role == 'oe':
        watcher = daemon_pid(lab, 'watchfrr')
        old_daemon = daemon_pid(lab, 'eigrpd')
        lab.oe_cli('write memory')
    else:
        watcher = old_daemon = None
        output = lab.ce_cli('write memory')
        require('[OK]' in output or 'Building configuration' in output,
                'Cisco running configuration was not saved before reload')

    # Both endpoints already have this learned route, not just an adjacency.
    lab.check_routes(family, {other: [PREFIXES[role][family]]},
                     'internal', seconds=120)
    # Record the observed peer before taking it offline. For IPv6 the OE
    # neighbor's link-local address is obtained via Cisco CLI, which must not
    # be queried while IOS is rebooting. Recovery re-discovers it normally.
    loss_peer = lab.peer_address(other, family)
    watcher_paused = False
    try:
        with Trace(lab, f'loss-{role}-{family}', target=other) as loss:
            # Packet-level evidence that the surviving capture interface is
            # operating before we intentionally make the neighbor silent.
            lab.wait(lambda: loss.pcap.stat().st_size,
                     lambda size: size > 24, capture_ready_seconds)
            if role == 'oe':
                oe_signal(lab, 'STOP', watcher)
                watcher_paused = True
                oe_signal(lab, 'KILL', old_daemon)
                # watchfrr remains suspended until the neighbor and route
                # have actually expired. It will reap and restart the daemon
                # only after recovery capture is active.
            else:
                lab.ce_reload()
            lab.wait(lambda: lab.has_peer(other, family, peer=loss_peer),
                     lambda present: not present, 100)
            lab.check_routes(family, {other: [PREFIXES[role][family]]},
                             'internal', present=False, seconds=100)

        # The network interfaces never disappear. Start recovery capture
        # before releasing the daemon watchdog or before IOS has rebooted.
        with Trace(lab, f'recovery-{role}-{family}', target=other) as recovery:
            if role == 'oe':
                oe_signal(lab, 'CONT', watcher)
                watcher_paused = False
                new_daemon = lab.wait(lambda: daemon_pid(lab, 'eigrpd'),
                                      lambda pid: pid != old_daemon, 150)
                command(['docker', 'exec', lab.oe, 'vtysh', '-b'], timeout=120)
                lab.save('restart-daemon.log',
                         f'eigrpd old_pid={old_daemon} new_pid={new_daemon}\n',
                         append=True)
            else:
                lab._wait_ce(450)
            connectivity(lab, seconds=connectivity_seconds,
                         stage=f'recovery-{role}-{family}')
            lab.check_routes(family, {other: [PREFIXES[role][family]]},
                             'internal', seconds=120)
            time.sleep(7)
        return startup(recovery.events, family, sources_for(lab))
    finally:
        if watcher_paused:
            # Never leave the supervisor suspended if capture or loss fails.
            oe_signal(lab, 'CONT', watcher)


def run(lab, report, spec):
    keys = [f'RTP-12-{role}/{family}'
            for family in spec['families'] for role in spec['restart_roles']]
    report['required'] = keys
    for index, key in enumerate(keys):
        role, family = key.split('/')[0].split('-')[-1], key.split('/')[1]
        try:
            # Never reuse a post-restart containerlab wiring state.
            # netlab down/up restores the actual link endpoints and IP fabric.
            if index:
                command(['netlab', 'down'], cwd=lab.topology.parent, timeout=600)
            report['oe_image'] = lab.deploy()
            report['ce_image'] = lab.info['defaults']['devices.csr.clab.image']
            connectivity(lab, seconds=spec['connectivity_timeout_seconds'],
                         stage=f'preflight-{role}-{family}')
            evidence = cold_restart(lab, role, family,
                                    connectivity_seconds=spec['connectivity_timeout_seconds'],
                                    capture_ready_seconds=spec['loss_capture_ready_seconds'])
            report['objectives'][key] = result('PASS', **evidence)
        except Exception as exc:
            report['objectives'][key] = result('FAIL', error=str(exc))
            for rest in keys[index+1:]:
                report['objectives'][rest] = result(
                    'BLOCKED', reason='preceding restart or connectivity failed; '
                                      'a subsequent AF must not use this lab')
            break
        print(f"{report['objectives'][key]['status']} {key}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--topology', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args(argv)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    spec = yaml.safe_load(args.scenario.read_text())
    if (spec.get('schema') != 'openeigrp-live-rtp-restart-2r/v1' or
        spec.get('name') != '2R-restart' or spec.get('group') != 'rtp' or
        spec.get('families') != ['ipv4', 'ipv6'] or
        spec.get('restart_roles') != ['oe', 'ce'] or
        type(spec.get('connectivity_timeout_seconds')) is not int or
        not 30 <= spec['connectivity_timeout_seconds'] <= 300 or
        spec.get('restart_modes') != {'oe': 'eigrpd-process', 'ce': 'ios-vm'} or
        type(spec.get('loss_capture_ready_seconds')) is not int or
        not 10 <= spec['loss_capture_ready_seconds'] <= 60):
        parser.error('invalid isolated restart fixture')
    lab = Lab(args.topology, args.artifacts)
    report = report_base(spec['name'], lab)
    report['required'] = [f'RTP-12-{role}/{af}' for af in spec['families']
                          for role in spec['restart_roles']]
    try:
        require_packet_access()
        run(lab, report, spec)
    except CapturePrerequisiteError as exc:
        report['error'] = str(exc)
        for key in report['required']:
            report['objectives'].setdefault(key, result('BLOCKED', reason=str(exc)))
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        lab.collect_logs()
        for key in report['required']:
            report['objectives'].setdefault(key, result('NOT_RUN'))
        states = [report['objectives'][key]['status'] for key in report['required']]
        report['status'] = ('PASS' if all(s == 'PASS' for s in states)
                            else 'BLOCKED' if all(s == 'BLOCKED' for s in states)
                            else 'FAIL')
        write_report(args.artifacts, report)
    print(report['status'], '2R RTP cold restart', flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
