#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Shared live OE/CSR two-router qualification operations.

Only netlab applies the initial router configuration. These helpers issue
explicit, reversible scenario stimuli and collect independent CLI/wire evidence.
"""
import contextlib
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import subprocess
import time

import capture
import network_fault
import wire

ERROR = re.compile(r'%\s*(?:Invalid input|Incomplete command|Ambiguous command|Error)|unknown command|failed to connect', re.I)
PROMPT = re.compile(r'(?m)(?:^|\n)[A-Za-z][\w.():-]*[>#]\s*$')
# IOS XE distinguishes D and D EX. FRR's Zebra uses E for EIGRP and
# prints route source detail like E[121]. In FRR, the generic Zebra RIB line
# alone does not distinguish internal/external; verify route kind on the wire.
# IOS XE IPv4 often omits /length from individual D EX routes, carrying
# the mask in the preceding ".../32 is subnetted" heading. IOS XE IPv6
# prints external route entries as "EX", without a leading "D".
OE_ROUTE = re.compile(r'^\s*E(?:\[\d+\])?[>*+]*\s+([0-9a-f:.]+/\d{1,3})(?=\s|$)', re.I)
CE_IPV4_ROUTE = re.compile(r'^\s*D[>*+]*\s+(EX\s+)?([0-9.]+(?:/\d{1,2})?)(?=\s|$)', re.I)
CE_IPV4_SUBNET = re.compile(r'^\s*[0-9.]+/(\d{1,2})\s+is\s+subnetted,', re.I)
CE_IPV6_ROUTE = re.compile(r'^\s*(D|EX)\s+([0-9a-f:]+/\d{1,3})(?=\s|$)', re.I)


def rib_prefixes(output, role, family, kind):
    """Read actual OE/IOS XE RIB entries without inferring unreported masks."""
    found = set()
    mask = None
    for line in output.splitlines():
        if role == 'oe':
            match = OE_ROUTE.match(line)
            if not match or 'is directly connected' in line:
                continue
            address = match.group(1)
        elif family == 'ipv4':
            heading = CE_IPV4_SUBNET.match(line)
            if heading:
                mask = heading.group(1)
                continue
            match = CE_IPV4_ROUTE.match(line)
            if not match or (bool(match.group(1)) != (kind == 'external')):
                continue
            address = match.group(2)
            if '/' not in address:
                if mask is None:
                    continue
                address += '/' + mask
        else:
            match = CE_IPV6_ROUTE.match(line)
            if not match or (match.group(1).upper() == 'EX') != (kind == 'external'):
                continue
            address = match.group(2)
        try:
            found.add(ipaddress.ip_network(address, strict=True))
        except ValueError:
            continue
    return found



def command(args, *, timeout=30, check=True, cwd=None):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=timeout, cwd=cwd)
    if check and p.returncode:
        raise RuntimeError(f'{args!r} exited {p.returncode}: {p.stdout[-2500:]}')
    return p.stdout


def assert_cli(output, label):
    if ERROR.search(output):
        raise RuntimeError(f'{label} rejected: {output[-2000:]}')
    return output


class CapturePrerequisiteError(RuntimeError):
    """Host capture/fault privileges are unavailable; the UUT is BLOCKED."""


def require_packet_access():
    """Verify host namespace capture privileges before deploying a costly lab.

    Both capture.py and network_fault.py execute tcpdump/tc in a container's
    network namespace using noninteractive sudo nsenter. Test this exact
    privilege path without touching a qdisc or starting a capture.
    """
    missing = [name for name in ('sudo', 'nsenter', 'tcpdump', 'tc')
               if not shutil.which(name)]
    if missing:
        raise CapturePrerequisiteError('missing host packet tools: ' + ', '.join(missing))
    proc = subprocess.run(['sudo', '-n', 'nsenter', '-t', '1', '-n', '--', 'true'],
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=15)
    if proc.returncode:
        raise CapturePrerequisiteError(
            'netlab user cannot run noninteractive sudo nsenter; '
            'install the dedicated StormTrooper capture permission rule. '
            'Host diagnostic: ' + proc.stdout.strip()[-700:])


class Cisco:
    """Interactive IOS XE SSH transport; no router-shell assumptions."""
    def __init__(self, host, log_file):
        self.host, self.log_file = host, Path(log_file)
        self.fd = self.process = None

    def __enter__(self):
        master, slave = pty.openpty()
        env = os.environ.copy()
        env['SSHPASS'] = 'admin'   # netlab's stock vrnetlab management credential
        argv = ['sshpass', '-e', 'ssh', '-tt', '-o', 'ConnectTimeout=8',
                '-o', 'StrictHostKeyChecking=accept-new',
                '-o', f'UserKnownHostsFile={self.log_file.parent / "known_hosts"}',
                '-o', 'LogLevel=ERROR', '-o', 'NumberOfPasswordPrompts=1',
                f'admin@{self.host}']
        self.process = subprocess.Popen(argv, stdin=slave, stdout=slave,
                                        stderr=slave, close_fds=True, env=env)
        os.close(slave)
        self.fd = master
        try:
            self._read_prompt(30)
            self.run('terminal length 0')
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _read_prompt(self, timeout):
        end = time.monotonic() + timeout
        buf = ''
        while time.monotonic() < end:
            ready, _, _ = select.select([self.fd], [], [], min(.5, max(0, end - time.monotonic())))
            if not ready:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                data = b''
            if not data:
                raise RuntimeError('Cisco SSH terminated: ' + buf[-1500:])
            buf += data.decode('utf8', 'replace').replace('\r', '')
            if PROMPT.search(buf):
                with self.log_file.open('a', encoding='utf8') as log:
                    log.write(buf + '\n')
                return buf
            if len(buf) > 200000:
                buf = buf[-150000:]
        raise TimeoutError('Cisco CLI prompt timed out: ' + buf[-2000:])

    def run(self, cmd):
        os.write(self.fd, (cmd + '\n').encode())
        return assert_cli(self._read_prompt(40), 'Cisco ' + cmd)

    def reload(self):
        """Confirm an IOS VM reload without restarting its Docker namespace.

        Only an explicit IOS confirmation is accepted as a successful stimulus.
        The SSH transport can terminate immediately after acknowledgement.
        """
        os.write(self.fd, b'reload\n')
        deadline = time.monotonic() + 60
        transcript = ''
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [],
                                        min(0.5, max(0, deadline - time.monotonic())))
            if not ready:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                data = b''
            if not data:
                break
            transcript += data.decode('utf8', 'replace').replace('\r', '')
            if ERROR.search(transcript):
                break
            if re.search(r'Save\?\s*\[yes/no\]', transcript, re.I):
                # The caller saved the running configuration before requesting
                # the reload. An unexpected unsaved-change prompt is unsafe.
                break
            if re.search(r'\[confirm\]', transcript, re.I):
                os.write(self.fd, b'\n')
                with self.log_file.open('a', encoding='utf8') as log:
                    log.write('$ reload\n' + transcript + '\n[confirmed]\n')
                return
            if len(transcript) > 200000:
                break
        with self.log_file.open('a', encoding='utf8') as log:
            log.write('$ reload\n' + transcript + '\n[confirmation missing]\n')
        raise RuntimeError('Cisco reload confirmation not observed: ' + transcript[-1000:])

    def configure(self, lines):
        # Individual prompt/command round trips make error attribution exact.
        # Never infer success from an SSH exit code alone.
        try:
            self.run('configure terminal')
            for line in lines:
                self.run(line)
        finally:
            self.run('end')

    def __exit__(self, *_):
        if self.fd is not None:
            try:
                os.write(self.fd, b'exit\n')
            except OSError:
                pass
            os.close(self.fd)
            self.fd = None
        if self.process is not None:
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class Lab:
    """Single 2R live deployment with runner-owned teardown."""
    def __init__(self, topology, artifacts):
        self.topology = Path(topology).resolve()
        self.artifacts = Path(artifacts).resolve()
        self.info = __import__('yaml').safe_load(self.topology.read_text())
        self.name = self.info['name']
        self.oe = f'clab-{self.name}-oe'
        self.ce = f'clab-{self.name}-csr'
        self.csr_ip = None

    def oe_cli(self, cmd):
        output = command(['docker', 'exec', self.oe, 'vtysh', '-c', cmd], timeout=25)
        assert_cli(output, cmd)
        self.save('oe-cli.log', f'$ {cmd}\n{output}\n', append=True)
        return output

    def ce_cli(self, cmd):
        with Cisco(self.csr_ip, self.artifacts / 'cisco-cli.log') as router:
            return router.run(cmd)

    def ce_reload(self):
        with Cisco(self.csr_ip, self.artifacts / 'cisco-cli.log') as router:
            router.reload()

    def ce_config(self, lines):
        with Cisco(self.csr_ip, self.artifacts / 'cisco-cli.log') as router:
            router.configure(lines)

    def oe_config(self, lines):
        argv = ['docker', 'exec', self.oe, 'vtysh', '-c', 'configure terminal']
        for line in lines:
            argv += ['-c', line]
        return assert_cli(command(argv, timeout=50), 'OE config')

    def save(self, name, value, *, append=False):
        with (self.artifacts / name).open('a' if append else 'w', encoding='utf8') as f:
            f.write(value)

    def deploy(self):
        if (self.info.get('provider') != 'clab' or
            set(self.info.get('nodes', {})) != {'oe', 'csr'} or
            not re.fullmatch(r'eigrp-[A-Za-z0-9_-]{1,10}', self.name)):
            raise ValueError('unsupported 2R native netlab topology')
        if not shutil.which('sshpass'):
            raise RuntimeError('sshpass unavailable')
        image = self.info['defaults']['devices.frr.clab.image']
        if not (re.fullmatch(r'ghcr\.io/diivious/openeigrp-frr:\d+\.\d+\.\d+', image)
                or re.fullmatch(r'openeigrp:[a-zA-Z0-9][a-zA-Z0-9_.-]*', image)):
            raise ValueError('OE image must be a locally installed release tag')
        for img in (image, self.info['defaults']['devices.csr.clab.image']):
            output = command(['docker', 'image', 'inspect', img], timeout=20)
            data = json.loads(output)[0]
            self.save('image-' + ('oe' if img == image else 'ce') + '.json',
                      json.dumps({'image': img, 'id': data.get('Id'),
                                  'repo_digests': data.get('RepoDigests')}, indent=2) + '\n')
        self.save('deploy.log', command(['netlab', 'up'], cwd=self.topology.parent, timeout=900))
        self._wait_oe(150)
        self.csr_ip = self._csr_address()
        self._wait_ce(450)
        self.save('cisco-version.log', self.ce_cli('show version'))
        self.save('oe-version.log', self.oe_cli('show version'))
        return image

    def _csr_address(self):
        info = json.loads(command(['docker', 'inspect', self.ce]))[0]
        for net in info['NetworkSettings']['Networks'].values():
            if net.get('IPAddress'):
                return net['IPAddress']
        raise RuntimeError('CSR management address unavailable')

    def _wait_oe(self, seconds):
        self.wait(lambda: self.oe_cli('show version'), lambda s: bool(s.strip()), seconds)

    def _wait_ce(self, seconds):
        self.wait(lambda: self.ce_cli('show running-config'),
                  lambda s: 'router eigrp OpenEIGRP' in s and
                  'address-family ipv4 unicast autonomous-system 4473' in s and
                  'address-family ipv6 unicast autonomous-system 4473' in s, seconds)

    @staticmethod
    def wait(reader, predicate, seconds, *, interval=3):
        deadline = time.monotonic() + seconds
        last = ''
        while True:
            try:
                last = reader()
                if predicate(last):
                    return last
            except (RuntimeError, OSError, TimeoutError, subprocess.SubprocessError) as exc:
                last = str(exc)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f'condition never satisfied: {str(last)[-1200:]}')
            time.sleep(min(interval, remaining))

    def neighbor(self, role, family):
        if role == 'oe':
            return self.oe_cli(f'show eigrp address-family {family} 4473 neighbors')
        return self.ce_cli(f'show {"ip" if family == "ipv4" else "ipv6"} eigrp neighbors')

    def peer_address(self, role, family):
        if family == 'ipv4':
            return '10.44.53.2' if role == 'oe' else '10.44.53.1'
        # Read observed link-local addresses on each end, never hardcode them.
        if role == 'oe':
            show = self.ce_cli('show ipv6 interface GigabitEthernet2')
        else:
            show = command(['docker', 'exec', self.oe, 'ip', '-6', 'addr', 'show',
                            'dev', 'eth1', 'scope', 'link'])
        match = re.search(r'(?i)fe80:(?:[0-9a-f]{0,4}:){1,6}[0-9a-f]{1,4}', show)
        if not match:
            raise AssertionError('peer link-local address unavailable')
        return match.group().lower()

    def has_peer(self, role, family, *, peer=None):
        out = self.neighbor(role, family)
        if peer is None:
            peer = self.peer_address(role, family)
        if family == 'ipv6':
            return bool(re.search(r'(?<![0-9a-f:])' + re.escape(peer) + r'(?![0-9a-f:])', out.lower()))
        return bool(re.search(r'(?<![0-9.])' + re.escape(peer) + r'(?![0-9.])', out))

    def peers(self, family, present=True, seconds=110, *, addresses=None):
        # A transit-interface shutdown can remove its IPv6 link-local address
        # from `ip addr`. Use peer addresses learned while the link was up for
        # loss checks instead of trying to discover them during the outage.
        def check():
            return {role: self.has_peer(role, family,
                    peer=addresses['ce' if role == 'oe' else 'oe'][family]
                    if addresses is not None else None) for role in ('oe', 'ce')}
        return self.wait(check, lambda values: all(v == present for v in values.values()), seconds)

    def routes(self, role, family):
        # FRR 10.7 Zebra provides `show ipv6 route`, not the IOS/other-FRR
        # protocol-filtered `show ipv6 route eigrp` syntax on this OE image.
        # For OE IPv6, match exact EIGRP entries in the unfiltered Zebra RIB.
        if role == 'oe' and family == 'ipv6':
            return self.oe_cli('show ipv6 route')
        cmd = 'show ' + ('ip' if family == 'ipv4' else 'ipv6') + ' route eigrp'
        return self.oe_cli(cmd) if role == 'oe' else self.ce_cli(cmd)

    def snapshot(self, label, families=('ipv4','ipv6')):
        for family in families:
            for role in ('oe','ce'):
                for kind, reader in (('neighbors', lambda: self.neighbor(role, family)),
                                     ('routes', lambda: self.routes(role, family))):
                    self.save(f'{label}-{role}-{family}-{kind}.log', reader())

    def cisco_af(self, family, command_line):
        self.ce_config(['router eigrp OpenEIGRP',
                        f'address-family {family} unicast autonomous-system 4473',
                        command_line])

    def route(self, role, family, prefix, *, add):
        """Test stimulus: add/remove static blackhole route, with redistribution preconfigured."""
        if role not in ('oe','ce'):
            raise ValueError('unknown router role')
        net = ipaddress.ip_network(prefix)
        if (net.version == 4) != (family == 'ipv4'):
            raise ValueError('family/prefix mismatch')
        if family == 'ipv4':
            route_line = f'ip route {net.network_address} {net.netmask} Null0 tag 7'
        else:
            route_line = f'ipv6 route {net} Null0 tag 7'
        if role == 'oe':
            self.oe_config([route_line if add else 'no ' + route_line])
        else:
            self.ce_config([route_line if add else 'no ' + route_line])

    def routes_change(self, role, family, prefixes, *, add):
        # All route origins are explicitly declared by the scenario; never
        # treat a kernel route as FRR's configured static redistribution.
        if role not in ('oe', 'ce'):
            raise ValueError('unknown router role')
        lines = []
        for prefix in prefixes:
            net = ipaddress.ip_network(prefix)
            if (net.version == 4) != (family == 'ipv4'):
                raise ValueError('family/prefix mismatch')
            text = (f'ip route {net.network_address} {net.netmask} Null0 tag 7'
                    if family == 'ipv4' else f'ipv6 route {net} Null0 tag 7')
            lines.append(text if add else 'no ' + text)
        if role == 'ce':
            # One SSH/config session for the full controlled route burst.
            self.ce_config(lines)
        elif role == 'oe':
            # Limit vtysh -c argument lists for portability and diagnostics.
            for start in range(0, len(lines), 24):
                self.oe_config(lines[start:start + 24])

    def route_present(self, role, family, prefix, kind):
        if role not in ('oe', 'ce') or kind not in ('internal', 'external'):
            raise ValueError('invalid role/route kind')
        return ipaddress.ip_network(prefix) in rib_prefixes(
            self.routes(role, family), role, family, kind)

    def check_routes(self, family, prefixes, kind, *, present=True, seconds=100):
        if kind not in ('internal', 'external'):
            raise ValueError('invalid route kind')

        def check():
            states = {}
            for role, requested in prefixes.items():
                if role not in ('oe', 'ce'):
                    raise ValueError('invalid router role')
                # One CLI/RIB read per router per poll, even for route bursts.
                found = rib_prefixes(self.routes(role, family), role, family, kind)
                for prefix in requested:
                    states[f'{role}:{prefix}'] = ipaddress.ip_network(prefix) in found
            return states
        try:
            return self.wait(check, lambda states: all(x == present for x in states.values()),
                             seconds)
        except AssertionError as exc:
            states = check()
            wrong = [prefix for prefix, exists in states.items() if exists != present]
            raise AssertionError(f'{len(wrong)} of {len(states)} routes did not meet '
                                 f'present={present}; examples: {wrong[:8]}') from exc

    def collect_logs(self):
        for node in (self.oe, self.ce):
            try:
                text = command(['docker', 'logs', node], timeout=20, check=False)
            except Exception as exc:
                text = str(exc)
            self.save(f'{node}.log', text)


class Trace:
    """Host-side capture in a router's dataplane network namespace."""
    def __init__(self, lab, name, *, target='oe'):
        self.lab, self.name, self.target = lab, name, target
        self.pcap = lab.artifacts / f'{name}.pcap'
        self.state = lab.artifacts / f'{name}.capture.json'
        self.events = None

    def __enter__(self):
        capture.start('eth1', self.pcap, self.state,
                      container=self.lab.oe if self.target == 'oe' else self.lab.ce)
        return self

    def __exit__(self, exc_type, exc, tb):
        # A fault-installation or route-configuration failure can occur before
        # even one packet arrives. Do not replace its diagnostic with the
        # secondary "empty capture" error from the context cleanup.
        try:
            capture.stop(self.state, require_packets=exc_type is None)
            self.events, errors = wire.read_pcap(self.pcap)
            self.lab.save(self.name + '.json', json.dumps({'events': self.events,
                          'errors': errors}, indent=2) + '\n')
            if errors:
                raise AssertionError('packet decode errors: ' + str(errors))
        except Exception as capture_error:
            if exc_type is None:
                raise
            self.lab.save(self.name + '.capture-error.log',
                          f'Capture cleanup/decode after {exc_type.__name__}: '
                          f'{capture_error}\n')
        return False


@contextlib.contextmanager
def fault(lab, name, role, family, mode, *, ack=None, direction='egress'):
    state = lab.artifacts / (name + '.fault.json')
    container = lab.oe if role == 'oe' else lab.ce
    network_fault.apply(container, 'eth1', family, mode, state, ack=ack,
                        direction=direction)
    try:
        yield
    finally:
        network_fault.clear(state)


def result(status, **fields):
    if status not in ('PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'NOT_IMPLEMENTED'):
        raise ValueError(status)
    return dict(status=status, **fields)


def write_report(artifacts, report):
    Path(artifacts, 'report.json').write_text(json.dumps(report, indent=2) + '\n')


def report_base(name, lab):
    return {'scenario': name, 'timestamp_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'mix': {'oe': 1, 'ce': 1}, 'topology': lab.name,
            'objectives': {}, 'status': 'NOT_RUN'}
