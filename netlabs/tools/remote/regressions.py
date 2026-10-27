#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Run staged netlabs regressions on the Linux test appliance.

Unit tests use the appliance's netlab Python interpreter. Tests run inside the
staged tree and never deploy live routers. Returns status for each independent
regression; a failed prerequisite prevents live qualification in the client.
"""
import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def select(root, selection):
    """Select permanent regression groups without invoking any live driver."""
    tests = []

    def python_suite(directory, name):
        if list((root / directory).glob('test_*.py')):
            tests.append((name, [sys.executable, '-m', 'unittest', 'discover',
                                 '-s', directory, '-p', 'test_*.py']))

    def shell_suite(directory):
        for path in sorted((root / directory).glob('test_*.sh')):
            tests.append((path.relative_to(root).as_posix(), ['bash', str(path)]))

    # Infrastructure validates the runner, transport and tooling before
    # any protocol test; RTP and DUAL then add their own focused suites.
    python_suite('netlabs/test/infra', 'infra')
    shell_suite('netlabs/test/infra')

    if selection in ('all', 'rtp'):
        python_suite('netlabs/test/rtp', 'rtp')
        shell_suite('netlabs/test/rtp')

    if selection in ('all', 'dual'):
        python_suite('netlabs/test/dual', 'dual')
        shell_suite('netlabs/test/dual')

    return tests


def execute(root, selection, artifacts):
    artifacts.mkdir(parents=True, exist_ok=True)
    logs = artifacts / 'regression-logs'
    logs.mkdir(exist_ok=True)
    # Legacy runner test checks that the repository layout has a readable config;
    # never upload the client's credential-containing remote-lab.cfg.
    config = root / 'netlabs/remote-lab.cfg'
    if not config.exists():
        config.write_text('REMOTE_HOST=localhost\nREMOTE_USER=netlab\nREMOTE_SSH_PORT=22\n', encoding='utf8')

    results = []
    for index, (name, args) in enumerate(select(root, selection), 1):
        print(f'\nRunning regression: {name}', flush=True)
        logfile = logs / f'{index:03}.log'
        status, error = 'PASS', None
        try:
            with logfile.open('w', encoding='utf8') as log:
                process = subprocess.Popen(
                    ['timeout', '--foreground', '--signal=TERM', '--kill-after=10s', '600s', *args],
                    cwd=root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors='replace',
                    bufsize=1,
                )
                with process.stdout:
                    for line in process.stdout:
                        print(line, end='', flush=True)
                        log.write(line)
                returncode = process.wait()
            if returncode:
                status = 'FAIL'
                error = f'command exited {returncode}'
        except (OSError, subprocess.SubprocessError) as exc:
            status, error = 'BLOCKED', str(exc)
            logfile.write_text(error + '\n', encoding='utf8')
        print(f'{status}: {name}', flush=True)
        results.append({'kind': 'unit', 'name': name, 'status': status,
                        'log': logfile.relative_to(artifacts).as_posix(), 'error': error})

    result = {'schema': 'openeigrp-netlabs-regressions/v1',
              'timestamp_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'selection': selection,
              'cases': results,
              'status': 'PASS' if results and all(x['status'] == 'PASS' for x in results)
                        else 'BLOCKED' if not results else 'FAIL'}
    (artifacts / 'regressions.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    return 0 if result['status'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--selection', choices=('all', 'rtp', 'dual'), required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    return execute(root, args.selection, args.artifacts)


if __name__ == '__main__':
    sys.exit(main())
