#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Run the complete existing 2R RTP gate for one address family."""
import argparse
import importlib.util
from pathlib import Path
import sys

import yaml


def load_transport_engine():
    path = Path(__file__).resolve().parents[1] / '2R-transport' / 'driver.py'
    if not path.is_file():
        raise RuntimeError('2R transport validation engine was not staged')
    spec = importlib.util.spec_from_file_location('openeigrp_rtp_transport', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(spec, name):
    af = name.removeprefix('2R-transport-')
    if (af not in ('ipv4', 'ipv6') or
            spec.get('schema') != 'openeigrp-live-rtp-2r/v1' or
            spec.get('name') != name or spec.get('group') != 'rtp' or
            spec.get('families') != [af] or
            not isinstance(spec.get('burst_prefixes'), int) or
            spec['burst_prefixes'] < 96):
        raise ValueError('invalid single-AF RTP fixture')
    return af


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', required=True, type=Path)
    parser.add_argument('--topology', required=True, type=Path)
    parser.add_argument('--artifacts', required=True, type=Path)
    args = parser.parse_args(argv)
    name = Path(__file__).resolve().parent.name
    spec = yaml.safe_load(args.scenario.read_text())
    if not isinstance(spec, dict):
        parser.error('invalid RTP scenario document')
    try:
        family = validate(spec, name)
    except ValueError as exc:
        parser.error(str(exc))

    engine = load_transport_engine()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    lab = engine.Lab(args.topology, args.artifacts)
    report = engine.report_base(spec['name'], lab)
    required = [f'{case}/{family}' for case in engine.CASES
                if case != 'RTP-08']
    required += [f'RTP-08-{tag}-{role}/{family}'
                 for tag in ('ack', 'packet') for role in ('oe', 'ce')]
    required += [f'RTP-02-{kind}/{family}' for kind in ('K-oe', 'AS')]
    required += ['RTP-07/duplicate']
    report['required'] = required
    try:
        engine.require_packet_access()
        report['oe_image'] = lab.deploy()
        report['ce_image'] = lab.info['defaults']['devices.csr.clab.image']
        lab.peers(family, True, 140)
        engine.run(lab, report, spec)
        duplicate = report['objectives'].get('RTP-07/duplicate')
        if duplicate is not None:
            duplicate['reason'] = (
                f'duplicate UPDATE reception and ACK-loss qualification ' 
                f'for {family} only')
    except engine.CapturePrerequisiteError as exc:
        report['error'] = str(exc)
        for case in required:
            report['objectives'].setdefault(
                case, engine.result('BLOCKED', reason=str(exc)))
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        lab.collect_logs()
        for case in required:
            report['objectives'].setdefault(case, engine.result('NOT_RUN'))
        statuses = [report['objectives'][case]['status'] for case in required]
        report['status'] = ('PASS' if all(s == 'PASS' for s in statuses)
                            else 'BLOCKED' if all(s == 'BLOCKED' for s in statuses)
                            else 'FAIL')
        engine.write_report(args.artifacts, report)
    print(report['status'], name, flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
