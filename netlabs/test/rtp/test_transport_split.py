#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Execute single-address-family transport fixture and report selection."""
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent


def load(family):
    source = ROOT / f'2R-transport-{family}/driver.py'
    module_spec = importlib.util.spec_from_file_location(f'rtp_split_{family}', source)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


class SingleAfTransport(unittest.TestCase):
    def test_fixture_selection(self):
        for family in ('ipv4', 'ipv6'):
            with self.subTest(family=family):
                driver = load(family)
                source = ROOT / f'2R-transport-{family}/scenario.yaml'
                spec = yaml.safe_load(source.read_text())
                self.assertEqual(driver.validate(spec, spec['name']), family)
                with self.assertRaisesRegex(ValueError, 'invalid single-AF'):
                    driver.validate({**spec, 'families': ['ipv4', 'ipv6']},
                                    spec['name'])
                with self.assertRaisesRegex(ValueError, 'invalid single-AF'):
                    driver.validate({**spec, 'burst_prefixes': 8}, spec['name'])
                with self.assertRaisesRegex(ValueError, 'invalid single-AF'):
                    driver.validate({**spec, 'name': '2R-other'}, spec['name'])

    def test_full_objectives_remain_per_family(self):
        for family in ('ipv4', 'ipv6'):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as tmp:
                driver = load(family)
                output = []
                cases = ('RTP-01', 'RTP-02', 'RTP-03', 'RTP-04', 'RTP-05',
                         'RTP-06', 'RTP-07', 'RTP-08', 'RTP-11')

                class FakeLab:
                    def __init__(self, topology, artifacts):
                        self.info = {'defaults': {'devices.csr.clab.image': 'csr:test'}}
                        self.name = 'eigrp-2r-rtp'
                    def deploy(self):
                        return 'oe:test'
                    def peers(self, af, present, deadline):
                        self_family.append(af)
                    def collect_logs(self):
                        pass

                self_family = []
                engine = types.SimpleNamespace(
                    Lab=FakeLab, CASES=cases,
                    CapturePrerequisiteError=RuntimeError,
                    require_packet_access=lambda: None,
                    report_base=lambda name, lab: {'scenario': name,
                         'objectives': {}, 'status': 'NOT_RUN'},
                    result=lambda status, **kwargs: dict(status=status, **kwargs),
                    write_report=lambda artifacts, report: output.append(report),
                )

                def run(lab, report, spec):
                    self.assertEqual(spec['families'], [family])
                    for name in report['required']:
                        report['objectives'][name] = {'status': 'PASS'}
                engine.run = run
                with mock.patch.object(driver, 'load_transport_engine',
                                       return_value=engine):
                    source = ROOT / f'2R-transport-{family}'
                    status = driver.main([
                        '--scenario', str(source / 'scenario.yaml'),
                        '--topology', str(source / 'topology.yml'),
                        '--artifacts', tmp])
                self.assertEqual(status, 0)
                self.assertEqual(self_family, [family])
                self.assertEqual(output[0]['status'], 'PASS')
                required = output[0]['required']
                self.assertIn(f'RTP-08-packet-ce/{family}', required)
                self.assertIn(f'RTP-08-ack-oe/{family}', required)
                self.assertIn('RTP-07/duplicate', required)
                other = 'ipv6' if family == 'ipv4' else 'ipv4'
                self.assertFalse(any('/' + other in case for case in required))
                self.assertEqual(len(required), len(cases) - 1 + 4 + 2 + 1)


if __name__ == '__main__':
    unittest.main()
