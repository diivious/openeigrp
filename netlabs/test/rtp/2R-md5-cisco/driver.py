#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Known-good Cisco CSR1000v <-> CSR1000v MD5 wire reference.

No OpenEIGRP router participates. A complete INIT/ACK and route UPDATE/ACK
exchange plus installed remote routes gate PASS. Digest hypotheses remain
observations only. Remote runner owns containerlab teardown.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys
import time

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'common'))
import capture
from interop import (Cisco, CapturePrerequisiteError, command,
                     require_packet_access, result, write_report)
import md5_wire

CASES = ('C2C-01', 'C2C-02', 'C2C-03', 'C2C-04', 'C2C-05')
NODES = ('csr1', 'csr2')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def validate(spec, topology):
    require(spec.get('schema') == 'openeigrp-cisco-md5-control/v1', 'invalid scenario schema')
    require(spec.get('name') == '2R-md5-cisco', 'invalid scenario name')
    require(spec.get('family') == 'ipv4' and spec.get('asn') == 4473,
            'unsupported Cisco control family/AS')
    require(tuple(spec.get('objectives', [])) == CASES, 'objective mismatch')
    require(tuple(spec.get('nodes', {})) == NODES, 'unexpected Cisco node roles')
    require(topology.get('name') == 'eigrp-c2c-md5' and
            topology.get('provider') == 'clab', 'unexpected native netlab topology')
    nodes = topology.get('nodes', {})
    require(set(nodes) == set(NODES) and all(nodes[n].get('device') == 'csr'
                                             for n in NODES), 'two CSR nodes required')
    require(re.fullmatch(r'eigrp-[a-z0-9-]{1,10}', topology['name']) is not None,
            'netlab name exceeds native preflight limit')
    require(spec.get('key_id') == 7 and spec.get('keychain') == 'OE03C2C' and
            spec.get('key_string') == 'oe03-lab-md5', 'unexpected lab key fixture')


class CiscoLab:
    def __init__(self, topology, artifacts, spec):
        self.topology = Path(topology)
        self.artifacts = Path(artifacts)
        self.spec = spec
        self.name = yaml.safe_load(self.topology.read_text())['name']
        self.containers = {n: f'clab-{self.name}-{n}' for n in NODES}
        self.addresses = {}

    def save(self, name, content, append=False):
        with (self.artifacts / name).open('a' if append else 'w', encoding='utf8') as f:
            f.write(str(content))

    def mgmt(self, node):
        metadata = json.loads(command(['docker', 'inspect', self.containers[node]]))[0]
        for net in metadata['NetworkSettings']['Networks'].values():
            if net.get('IPAddress'):
                return net['IPAddress']
        raise RuntimeError(f'{node} has no Docker management address')

    def retry_ssh(self, node, action, seconds=180, interval=5):
        """Retry intermittent IOS SSH transport failures, not CLI rejections.

        All configuration actions in this test set explicit values and can be
        safely replayed after a connection loss, including partial application.
        """
        deadline = time.monotonic() + seconds
        failures = 0
        while True:
            try:
                return action()
            except (RuntimeError, TimeoutError, OSError) as exc:
                message = str(exc)
                if not (message.startswith('Cisco SSH terminated:') or
                        message.startswith('Cisco CLI prompt timed out:') or
                        isinstance(exc, OSError)):
                    raise
                failures += 1
                self.save(f'{node}-ssh-retries.log',
                          f'{dt.datetime.now(dt.timezone.utc).isoformat()} '
                          f'attempt {failures}: {message[-1200:]}\n', append=True)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f'{node} SSH unavailable after {failures} '
                                       f'attempts: {message[-1200:]}') from exc
                time.sleep(min(interval, remaining))

    def cli(self, node, text):
        def run():
            with Cisco(self.addresses[node], self.artifacts / f'{node}-cli.log') as router:
                return router.run(text)
        return self.retry_ssh(node, run)

    def config(self, node, lines):
        def run():
            with Cisco(self.addresses[node], self.artifacts / f'{node}-cli.log') as router:
                router.configure(lines)
        return self.retry_ssh(node, run)

    def wait(self, reader, predicate, seconds, interval=3):
        deadline = time.monotonic() + seconds
        last = ''
        while time.monotonic() < deadline:
            try:
                last = reader()
                if predicate(last):
                    return last
            except (RuntimeError, TimeoutError, OSError) as exc:
                last = str(exc)
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        raise AssertionError('Cisco condition not reached: ' + str(last)[-1500:])

    def deploy(self):
        image = yaml.safe_load(self.topology.read_text())['defaults']['devices.csr.clab.image']
        metadata = json.loads(command(['docker', 'image', 'inspect', image], timeout=20))[0]
        self.save('cisco-images.json', json.dumps({'image': image,
                  'id': metadata['Id']}, indent=2) + '\n')
        # Two concurrent CSR1000v instances are resource-intensive. Netlab's
        # parallel Ansible ios_config phase can time out despite both SSH
        # servers being ready. Start the topology without that phase, then
        # configure each IOS router serially through the existing Cisco CLI.
        self.save('deploy.log', command(['netlab', 'up', '--no-config'],
                                        cwd=self.topology.parent, timeout=900))
        self.addresses = {node: self.mgmt(node) for node in NODES}
        for node in NODES:
            version = self.wait(lambda node=node: self.cli(node, 'show version'),
                                lambda s: 'Cisco IOS' in s or 'Cisco IOS XE' in s, 450)
            self.save(f'{node}-version.log', version)
        for node in NODES:
            self.configure_underlay(node)
        for node in NODES:
            output = self.wait(lambda node=node: self.cli(node, 'show ip interface brief'),
                               lambda s, node=node: self.underlay_ready(node, s), 120)
            self.save(f'{node}-underlay.log', output)
        return metadata['Id']

    def configure_underlay(self, node):
        item = self.spec['nodes'][node]
        self.config(node, [
            'interface GigabitEthernet2',
            f'ip address {item["address"]} 255.255.255.252',
            'no shutdown',
            'exit',
            'interface Loopback0',
            f'ip address {item["router_id"]} 255.255.255.255',
            'no shutdown',
            'exit',
        ])

    def underlay_ready(self, node, output):
        address = re.escape(self.spec['nodes'][node]['address'])
        return any(re.search(r'^GigabitEthernet2\s+' + address +
                             r'\s+.*\bup\s+up\s*$', line.strip())
                   for line in output.splitlines())

    def configure_md5(self, node):
        item = self.spec['nodes'][node]
        chain = self.spec['keychain']
        self.config(node, [
            f'key chain {chain}', f'key {self.spec["key_id"]}',
            f'key-string {self.spec["key_string"]}', 'exit', 'exit',
            'interface GigabitEthernet2',
            f'ip authentication key-chain eigrp 4473 {chain}',
            'ip authentication mode eigrp 4473 md5',
            'exit',
            'router eigrp 4473',
            f'eigrp router-id {item["router_id"]}',
            'no auto-summary',
            'network 10.44.53.0 0.0.0.3',
            f'network {item["router_id"]} 0.0.0.0',
        ])
        output = self.cli(node, 'show running-config')
        self.save(node + '-md5-config.log', output)
        require('ip authentication mode eigrp 4473 md5' in output and
                f'ip authentication key-chain eigrp 4473 {chain}' in output and
                'router eigrp 4473' in output,
                f'{node} missing MD5 or router configuration')

    def neighbors(self, node):
        return self.cli(node, 'show ip eigrp neighbors')

    def stable_neighbors(self):
        def observe():
            return {node: self.neighbors(node) for node in NODES}
        def present(state):
            return all(re.search(r'(?<![0-9.])' +
                       re.escape(self.spec['nodes'][node]['neighbor']) +
                       r'(?![0-9.])', state[node]) for node in NODES)
        return self.wait(observe, present, 150)

    def route_observed(self, node, prefix):
        output = self.cli(node, 'show ip route ' + prefix.split('/')[0])
        self.save(node + '-route-' + prefix.split('/')[0] + '.log', output)
        return bool(re.search(r'Known via\s+"?eigrp 4473', output, re.I))

    def add_route(self, node):
        prefix = self.spec['nodes'][node]['advertised']
        address, mask = prefix.split('/')
        require(mask == '32', 'only host prefix stimuli supported')
        self.config(node, [f'interface Loopback100',
                           f'ip address {address} 255.255.255.255',
                           'exit', 'router eigrp 4473',
                           f'network {address} 0.0.0.0'])

    def remote_routes(self):
        def check():
            return {node: self.route_observed(node,
                    self.spec['nodes']['csr2' if node == 'csr1' else 'csr1']['advertised'])
                    for node in NODES}
        return self.wait(check, lambda data: all(data.values()), 150)

    def snapshot(self):
        for node in NODES:
            for name, cmd in (('neighbors', 'show ip eigrp neighbors'),
                              ('rib', 'show ip route eigrp'),
                              ('interfaces', 'show ip interface brief'),
                              ('auth', 'show running-config interface GigabitEthernet2')):
                try:
                    self.save(f'{node}-{name}.log', self.cli(node, cmd))
                except Exception as exc:
                    self.save(f'{node}-{name}.log', str(exc))

    def collect_logs(self):
        for node, container in self.containers.items():
            self.save(node + '-container.log',
                      command(['docker', 'logs', container], timeout=20, check=False))
            self.save(node + '-docker-state.json',
                      command(['docker', 'inspect', '--format', '{{json .State}}', container],
                              timeout=20, check=False))
        self.save('docker-stats.log',
                  command(['docker', 'stats', '--no-stream', '--format',
                           '{{.Name}} CPU={{.CPUPerc}} MEM={{.MemUsage}} '
                           'MEM_PERCENT={{.MemPerc}}',
                           *self.containers.values()], timeout=35, check=False))


def execute(lab, report, spec):
    key = spec['key_string']
    sources = [spec['nodes'][n]['address'] for n in NODES]
    start = lab.artifacts / 'cisco-cisco-md5.pcap'
    state = lab.artifacts / 'cisco-cisco-md5.capture.json'
    captured = False
    events = []
    route_start = float('inf')
    try:
        lab.deploy()
        report['objectives'][CASES[0]] = result('PASS', image='Cisco CSR1000v')
        capture.start('eth1', start, state, container=lab.containers['csr1'])
        captured = True
        for node in NODES:
            lab.configure_md5(node)
        lab.stable_neighbors()
        # Require both neighbors to survive a second Hello interval.
        time.sleep(7)
        lab.stable_neighbors()
        report['objectives'][CASES[1]] = result('PASS', neighbors='both directions')
        route_start = time.time()
        for node in NODES:
            lab.add_route(node)
        lab.remote_routes()
        time.sleep(8)
        lab.stable_neighbors()
        report['rib_verified'] = True
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        if captured:
            try:
                capture.stop(state, require_packets=True)
                events = md5_wire.analyze(start, key, sources)
                lab.save('cisco-md5-packets.json', json.dumps(events, indent=2) + '\n')
            except Exception as exc:
                report['capture_error'] = str(exc)
        lab.snapshot()
        lab.collect_logs()

    if events:
        try:
            summary = md5_wire.evidence(events, sources, spec['key_id'],
                                        route_start=route_start)
            report['wire'] = summary
            require(all(item['hellos'] >= 2 and item['init_updates'] > 0
                        for item in summary.values()),
                    'both Cisco routers must transmit authenticated INIT and Hellos')
            report['objectives'][CASES[4]] = result('PASS', summary=summary)
            require(all(item['acked_init'] and item['eot_updates']
                        for item in summary.values()),
                    'Cisco INIT ACK missing in one direction')
            report['objectives'][CASES[2]] = result('PASS', init_acked='both directions')
            require(all(item['acked_route'] for item in summary.values()),
                    'Cisco route UPDATE/ACK missing in one direction')
            require(report.get('rib_verified') is True,
                    'Cisco route UPDATE seen without verified bidirectional RIB')
            report['objectives'][CASES[3]] = result('PASS',
                                                  remote_routes='both directions',
                                                  acked_updates='both directions')
        except Exception as exc:
            report['wire_error'] = str(exc)

    for case in CASES:
        report['objectives'].setdefault(case, result('FAIL', reason=(
            report.get('wire_error') or report.get('capture_error') or
            report.get('error') or 'missing required evidence')))
    report['status'] = ('PASS' if all(report['objectives'][key]['status'] == 'PASS'
                                        for key in CASES) else 'FAIL')
    return report['status'] == 'PASS'


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--topology', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args(argv)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    report = {'scenario': '2R-md5-cisco', 'objectives': {}, 'status': 'NOT_RUN'}
    try:
        spec = yaml.safe_load(args.scenario.read_text())
        validate(spec, yaml.safe_load(args.topology.read_text()))
        require_packet_access()
        lab = CiscoLab(args.topology, args.artifacts, spec)
        passed = execute(lab, report, spec)
    except CapturePrerequisiteError as exc:
        report['error'] = str(exc)
        report['objectives'] = {key: result('BLOCKED', reason=str(exc)) for key in CASES}
        passed = False
    except Exception as exc:
        report['error'] = str(exc)
        report['objectives'] = {key: result('FAIL', reason=str(exc)) for key in CASES}
        passed = False
    report['required'] = CASES
    report['status'] = 'PASS' if passed else 'FAIL'
    write_report(args.artifacts, report)
    for case in CASES:
        print(report['objectives'][case]['status'], case, flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
